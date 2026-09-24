#!/usr/bin/env python3
"""Minimal local Gradio V1 for the formal repository-grounded assistant."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import sys
from typing import Any
from uuid import uuid4

import gradio as gr
from markdown_it import MarkdownIt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.grounded_answer_v1 import DEFAULT_TOP_K, GroundedAnswerV1, source_preview  # noqa: E402
from src.user_testing_logging_v1 import (  # noqa: E402
    DEFAULT_TESTER_ID,
    FEEDBACK_CATEGORIES,
    FeedbackPersistenceError,
    append_successful_interaction,
    mark_interaction_helpful,
    save_needs_review_feedback,
    save_suggested_correction,
)


from scripts.submit_user_testing_session import (  # noqa: E402
    PushError,
    SubmissionError,
    available_remotes,
    submit_session,
)

CORPUS_PATH = PROJECT_ROOT / "data/chunks/source_chunks_v1_combined.jsonl"
MARKDOWN_RENDERER = MarkdownIt("commonmark", {"html": False}).enable("table")
CODE_COPY_JS = """
element.addEventListener("click", (event) => {
    const button = event.target.closest(".copy-code");
    if (!button) return;
    const code = button.parentElement.querySelector("code");
    if (!code || !navigator.clipboard) return;
    navigator.clipboard.writeText(code.innerText).then(() => {
        const original = button.textContent;
        button.textContent = "Copied";
        setTimeout(() => { button.textContent = original; }, 1200);
    });
});
"""


def _source_markdown(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return "No retrieval evidence was returned."
    lines = []
    for rank, source in enumerate(sources, start=1):
        label = source["symbol_or_section_title"] or "(unnamed chunk)"
        lines.extend(
            [
                f"{rank}. **{source['repository']}** | `{source['source_role']}`",
                f"   `{source['source_path']}`",
                f"   lines {source['start_line']}-{source['end_line']} | {label}",
            ]
        )
    return "\n".join(lines)


def _evidence_markdown(assistant: GroundedAnswerV1, query: str, top_k: int) -> str:
    results = assistant.retriever.search(query, top_k)
    if not results:
        return "No retrieved evidence."
    blocks = []
    for rank, result in enumerate(results, start=1):
        source = result.record
        label = source.get("symbol") or source.get("section_title") or "(unnamed chunk)"
        blocks.append(
            "\n".join(
                [
                    f"**{rank}. {source['repository']} | {source['source_role']}**",
                    f"`{source['source_path']}`",
                    f"Lines {source['start_line']}-{source['end_line']} | {label}",
                    source_preview(result),
                ]
            )
        )
    return "\n\n---\n\n".join(blocks)


def _technical_markdown(result: dict[str, Any], top_k: int) -> str:
    lines = [
        f"- **LLM model:** `{result.get('model') or '(not configured)'}`",
        f"- **LLM base URL:** `{result.get('base_url') or 'OpenAI SDK default'}`",
        f"- **Top-K:** {top_k}",
    ]
    if result.get("error"):
        lines.append(f"- **Warning/error:** {result['error']}")
    else:
        lines.append("- **Status:** successful")
    return "\n".join(lines)


def _history_markdown(history: list[dict[str, Any]]) -> str:
    print(f"History render count={len(history)}", flush=True)
    if not history:
        return "_No queries in this browser session._"
    entries = []
    for index, entry in enumerate(history, start=1):
        status = "success" if entry["success"] else f"warning: {entry['error']}"
        entries.extend(
            [
                f"## Interaction {index}",
                f"**Question:** {entry['query']}",
                f"**Timestamp:** {entry['timestamp']}",
                f"**Model:** `{entry.get('model') or '(not configured)'}`",
                f"**Status:** {status}",
            ]
        )
        if entry["success"]:
            entries.extend(["### Final answer", entry["answer"] or "_No answer returned._"])
        entries.append("---")
    return "\n\n".join(entries)


def _status_markdown(result: dict[str, Any]) -> str:
    if result.get("success"):
        return "**Status:** Completed"
    error = str(result.get("error") or "LLM returned no displayable answer.")
    return f"**Status:** {error[:240]}"


def render_markdown_for_html(markdown_text: str) -> str:
    """Render trusted Markdown syntax safely for presentation only."""
    rendered_html = MARKDOWN_RENDERER.render(markdown_text)
    rendered_html = re.sub(
        r"(<pre><code.*?</code></pre>)",
        r'<div class="answer-code-block"><button class="copy-code" type="button">Copy code</button>\1</div>',
        rendered_html,
        flags=re.DOTALL,
    )
    return f'<div class="repository-markdown">{rendered_html}</div>'


def build_app() -> gr.Blocks:
    assistant = GroundedAnswerV1(CORPUS_PATH)
    # Gradio can cancel queued jobs, but a synchronous HTTP call already running
    # in a worker is allowed to finish. Keep a small per-request marker so a
    # late result cannot overwrite the UI or be logged as successful.
    cancelled_requests: dict[str, bool] = {}

    def request_received(question: str):
        normalized_question = question.strip()
        query_fingerprint = hashlib.sha256(normalized_question.encode("utf-8")).hexdigest()[:12]
        request_id = uuid4().hex
        cancelled_requests[request_id] = False
        print(
            "Gradio request received: "
            f"request_id={request_id[:12]}, query_sha256={query_fingerprint}, "
            f"query_len={len(normalized_question)}",
            flush=True,
        )
        return (
            "**Status:** Request received. Retrieving evidence and waiting for the LLM...",
            gr.Button(interactive=False),
            gr.Button(interactive=True),
            {"request_id": request_id},
        )

    def stop_request(request_control: dict[str, str] | None):
        request_id = (request_control or {}).get("request_id")
        if request_id:
            cancelled_requests[request_id] = True
            print(f"Gradio request cancelled: request_id={request_id[:12]}", flush=True)
        return (
            gr.Button(interactive=True),
            gr.Button(interactive=False),
            "**Status:** Cancelled",
            request_control,
        )

    def request_was_cancelled(request_control: dict[str, str] | None) -> bool:
        request_id = (request_control or {}).get("request_id")
        return bool(request_id and cancelled_requests.get(request_id, False))

    def cancelled_outputs():
        """Leave existing successful UI values untouched after a Stop action."""
        return tuple(gr.skip() for _ in range(13))

    def _feedback_target(current_interaction: dict[str, str] | None) -> tuple[Path, str]:
        if not isinstance(current_interaction, dict):
            raise FeedbackPersistenceError("No logged interaction is available for feedback.")
        session_file = current_interaction.get("session_file")
        interaction_id = current_interaction.get("interaction_id")
        if not isinstance(session_file, str) or not isinstance(interaction_id, str):
            raise FeedbackPersistenceError("No logged interaction is available for feedback.")
        return Path(session_file), interaction_id

    def save_helpful_feedback(current_interaction: dict[str, str] | None) -> str:
        try:
            session_file, interaction_id = _feedback_target(current_interaction)
            mark_interaction_helpful(session_file=session_file, interaction_id=interaction_id)
            return "**Feedback saved:** Helpful"
        except FeedbackPersistenceError as error:
            return f"**Feedback not saved:** {error}"
        except Exception as error:
            print(f"Helpful feedback persistence failed: {type(error).__name__}", flush=True)
            return "**Feedback not saved:** The interaction session could not be updated safely."

    def show_review_form():
        return gr.Accordion(visible=True, open=True), gr.Accordion(visible=False), ""

    def save_review_feedback(
        current_interaction: dict[str, str] | None, category: str | None, comment: str
    ) -> str:
        try:
            session_file, interaction_id = _feedback_target(current_interaction)
            save_needs_review_feedback(
                session_file=session_file,
                interaction_id=interaction_id,
                category=category or "",
                comment=comment,
            )
            return "**Feedback saved:** Needs review"
        except FeedbackPersistenceError as error:
            return f"**Feedback not saved:** {error}"
        except Exception as error:
            print(f"Review feedback persistence failed: {type(error).__name__}", flush=True)
            return "**Feedback not saved:** The interaction session could not be updated safely."

    def show_correction_form():
        return gr.Accordion(visible=False), gr.Accordion(visible=True, open=True), ""

    def save_correction_feedback(current_interaction: dict[str, str] | None, correction: str) -> str:
        try:
            session_file, interaction_id = _feedback_target(current_interaction)
            save_suggested_correction(
                session_file=session_file,
                interaction_id=interaction_id,
                correction=correction,
            )
            return "**Feedback saved:** Suggested correction"
        except FeedbackPersistenceError as error:
            return f"**Feedback not saved:** {error}"
        except Exception as error:
            print(f"Correction feedback persistence failed: {type(error).__name__}", flush=True)
            return "**Feedback not saved:** The interaction session could not be updated safely."

    def submission_started():
        return gr.Button(interactive=False), "**Submission:** Preparing current session..."

    def submit_current_session(
        current_interaction: dict[str, str] | None, remote: str | None, mode: str
    ):
        if not isinstance(current_interaction, dict) or not isinstance(current_interaction.get("session_file"), str):
            return "**Submission:** No logged session is available for submission.", gr.Button(interactive=True)
        if not remote:
            return "**Submission:** Select an available Git remote.", gr.Button(interactive=True)

        try:
            result = submit_session(
                session_argument=current_interaction["session_file"],
                remote=remote,
                dry_run=mode == "dry-run",
                no_push=mode == "no-push",
            )
        except PushError as error:
            return (
                "**Submission failed:** Push failed; local commit preserved "
                f"`{error.commit[:7]}`. Check authentication, permissions, and remote state.",
                gr.Button(interactive=True),
            )
        except SubmissionError as error:
            return f"**Submission failed:** {error}", gr.Button(interactive=True)
        except Exception as error:
            print(f"Submission UI failed: {type(error).__name__}", flush=True)
            return "**Submission failed:** The session could not be submitted safely.", gr.Button(interactive=True)

        if result.push_status == "nothing to submit":
            message = "**Submission:** Nothing to submit; this session is already committed."
        elif result.dry_run:
            message = "**Submission prepared**"
        else:
            message = "**Submission succeeded**"
        lines = [
            message,
            f"- **Tester:** {result.session.tester_id}",
            f"- **Session:** {result.session.session_id}",
            f"- **Interactions:** {result.session.interaction_count}",
        ]
        if result.commit:
            lines.append(f"- **Commit:** `{result.commit[:7]}`")
        lines.extend(
            [
                f"- **Remote:** {result.remote}/{result.branch}",
                f"- **Push status:** {result.push_status}",
            ]
        )
        return "\n".join(lines), gr.Button(interactive=True)

    def ask(
        question: str,
        model: str,
        base_url: str,
        top_k: int,
        history: list[dict[str, Any]],
        tester_id: str,
        logging_state: dict[str, Any] | None,
        request_control: dict[str, str] | None,
        current_interaction: dict[str, str] | None,
    ):
        if request_was_cancelled(request_control):
            return cancelled_outputs()
        session_history = list(history) if history is not None else []
        question = question.strip()
        if not question:
            warning = "Please enter a question."
            warning_markdown = f"> **Warning:** {warning}"
            callback_outputs = (
                render_markdown_for_html(warning_markdown),
                warning_markdown,
                "",
                "",
                f"- **Warning/error:** {warning}",
                render_markdown_for_html(_history_markdown(session_history)),
                session_history,
                logging_state,
                f"**Status:** {warning}",
            )
            return (*callback_outputs, gr.Button(interactive=True), gr.Button(interactive=False), None, None)
        query_fingerprint = hashlib.sha256(question.encode("utf-8")).hexdigest()[:12]
        print(
            f"Gradio request: query_sha256={query_fingerprint}, query_len={len(question)}",
            flush=True,
        )
        result = assistant.answer(
            question,
            int(top_k),
            model_override=model.strip() or None,
            base_url_override=base_url.strip() or None,
            use_environment_base_url=False,
        )
        if request_was_cancelled(request_control):
            request_id = (request_control or {}).get("request_id", "")
            print(f"Gradio cancelled request finished late: request_id={request_id[:12]}", flush=True)
            return cancelled_outputs()
        answer_value = result.get("answer")
        answer_is_populated = isinstance(answer_value, str) and bool(answer_value.strip())
        sources = result["sources"]
        first_source = sources[0]["source_path"] if sources else "(none)"
        print(
            "Gradio answer callback: "
            f"answer_type={type(answer_value).__name__}, "
            f"answer_is_none={answer_value is None}, "
            f"answer_len={len(answer_value) if isinstance(answer_value, str) else 0}, "
            f"success={result.get('success')}",
            flush=True,
        )
        print(f"Gradio result: first_source={first_source}", flush=True)
        answer_markdown = (
            answer_value
            if result.get("success") and answer_is_populated
            else f"> **Warning:** {result.get('error') or 'LLM returned no displayable answer.'}"
        )
        answer_html = render_markdown_for_html(answer_markdown)
        next_logging_state = logging_state
        next_current_interaction: dict[str, str] | None = None
        if result.get("success") and answer_is_populated:
            try:
                next_logging_state, session_file, interaction = append_successful_interaction(
                    session_state=logging_state,
                    tester_id=tester_id,
                    query=question,
                    raw_answer=answer_value,
                    sources=sources,
                    model=result.get("model"),
                    base_url=result.get("base_url"),
                    top_k=int(top_k),
                )
                print(
                    "User-testing interaction logged: "
                    f"interaction_id={interaction['interaction_id']}, session_file={session_file}",
                    flush=True,
                )
                next_current_interaction = {
                    "session_file": str(session_file),
                    "interaction_id": interaction["interaction_id"],
                }
            except Exception as error:
                print(f"User-testing logging failed: {type(error).__name__}", flush=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "query": question,
            "model": result.get("model"),
            "answer": answer_value,
            "sources": sources,
            "success": result["success"],
            "error": result.get("error"),
        }
        print(f"History before append: count={len(session_history)}", flush=True)
        updated_history = [*session_history, entry]
        print(f"History after append: count={len(updated_history)}", flush=True)
        callback_outputs = (
            answer_html,
            answer_markdown,
            _source_markdown(sources),
            _evidence_markdown(assistant, question, int(top_k)),
            _technical_markdown(result, int(top_k)),
            render_markdown_for_html(_history_markdown(updated_history)),
            updated_history,
            next_logging_state,
            _status_markdown(result),
            gr.Button(interactive=True),
            gr.Button(interactive=False),
            None,
            next_current_interaction,
        )
        print(f"Gradio answer callback: output_count={len(callback_outputs)}", flush=True)
        return callback_outputs

    def clear_history():
        print("History cleared: count=0", flush=True)
        return [], ""

    with gr.Blocks(title="SPHinXsys / SPHinXsim Repository Assistant") as app:
        gr.Markdown("# SPHinX AI Assistant")
        gr.Markdown("Repository-grounded assistant for SPHinXsys and SPHinXsim")
        history_state = gr.State(value=[])
        logging_state = gr.State(value=None)
        request_state = gr.State(value=None)
        current_interaction_state = gr.State(value=None)
        question = gr.Textbox(label="Question", lines=4, placeholder="Ask a repository-grounded question...")
        with gr.Row():
            tester_id = gr.Textbox(label="Tester ID", value=DEFAULT_TESTER_ID)
            ask_button = gr.Button("Ask", variant="primary")
            stop_button = gr.Button("Stop", interactive=False)
            status = gr.Markdown("**Status:** Ready")
        with gr.Accordion("Advanced Settings", open=False):
            with gr.Row():
                model = gr.Textbox(label="Model", value=os.environ.get("LLM_MODEL", ""))
                base_url = gr.Textbox(label="Base URL", value=os.environ.get("LLM_BASE_URL", ""))
                top_k = gr.Slider(label="Top-K", minimum=1, maximum=10, value=DEFAULT_TOP_K, step=1)

        answer = gr.HTML(
            label="Answer",
            show_label=True,
            container=True,
            elem_id="rendered-answer",
            js_on_load=CODE_COPY_JS,
        )
        with gr.Row():
            helpful_button = gr.Button("Helpful")
            needs_review_button = gr.Button("Needs review")
            suggest_correction_button = gr.Button("Suggest correction")
        feedback_status = gr.Markdown()
        with gr.Accordion("Needs review feedback", open=False, visible=False) as review_form:
            feedback_category = gr.Dropdown(label="Feedback category", choices=sorted(FEEDBACK_CATEGORIES))
            feedback_comment = gr.Textbox(label="Comment (optional)", lines=3)
            save_review_button = gr.Button("Save review")
        with gr.Accordion("Suggested correction", open=False, visible=False) as correction_form:
            correction_text = gr.Textbox(label="Suggested correction", lines=4)
            save_correction_button = gr.Button("Save correction")
        remote_names = available_remotes()
        with gr.Accordion("Submission", open=False):
            with gr.Row():
                submission_remote = gr.Dropdown(
                    label="Remote",
                    choices=remote_names,
                    value=remote_names[0] if remote_names else None,
                )
                submission_mode = gr.Radio(
                    label="Submission mode",
                    choices=["dry-run", "no-push", "push"],
                    value="dry-run",
                )
                submission_button = gr.Button("Submit current session")
            submission_status = gr.Markdown("**Submission:** Ready")

        with gr.Accordion("Raw Markdown fallback", open=False):
            answer_fallback = gr.Textbox(label="Raw answer", interactive=False, lines=18, buttons=["copy"])
        gr.Markdown("## Sources")
        sources = gr.Markdown()
        with gr.Accordion("Retrieved Evidence", open=False):
            evidence = gr.Markdown()
        with gr.Accordion("Technical Details", open=False):
            technical = gr.Markdown()
        with gr.Accordion("Validation History", open=False):
            history_markdown = gr.HTML(
                label="Validation History",
                show_label=True,
                container=True,
                elem_id="rendered-history",
                js_on_load=CODE_COPY_JS,
            )
            clear_button = gr.Button("Clear history")

        ask_stage_a = ask_button.click(
            request_received,
            inputs=[question],
            outputs=[status, ask_button, stop_button, request_state],
            queue=False,
            trigger_mode="once",
        )
        ask_stage_b = ask_stage_a.then(
            ask,
            inputs=[
                question,
                model,
                base_url,
                top_k,
                history_state,
                tester_id,
                logging_state,
                request_state,
                current_interaction_state,
            ],
            outputs=[
                answer,
                answer_fallback,
                sources,
                evidence,
                technical,
                history_markdown,
                history_state,
                logging_state,
                status,
                ask_button,
                stop_button,
                request_state,
                current_interaction_state,
            ],
            queue=True,
            trigger_mode="once",
            concurrency_limit=1,
            concurrency_id="grounded-answer-v1",
        )
        submit_stage_a = question.submit(
            request_received,
            inputs=[question],
            outputs=[status, ask_button, stop_button, request_state],
            queue=False,
            trigger_mode="once",
        )
        submit_stage_b = submit_stage_a.then(
            ask,
            inputs=[
                question,
                model,
                base_url,
                top_k,
                history_state,
                tester_id,
                logging_state,
                request_state,
                current_interaction_state,
            ],
            outputs=[
                answer,
                answer_fallback,
                sources,
                evidence,
                technical,
                history_markdown,
                history_state,
                logging_state,
                status,
                ask_button,
                stop_button,
                request_state,
                current_interaction_state,
            ],
            queue=True,
            trigger_mode="once",
            concurrency_limit=1,
            concurrency_id="grounded-answer-v1",
        )
        stop_button.click(
            stop_request,
            inputs=[request_state],
            outputs=[ask_button, stop_button, status, request_state],
            queue=False,
            cancels=[ask_stage_b, submit_stage_b],
        )
        helpful_button.click(save_helpful_feedback, inputs=[current_interaction_state], outputs=[feedback_status])
        needs_review_button.click(
            show_review_form,
            outputs=[review_form, correction_form, feedback_status],
        )
        save_review_button.click(
            save_review_feedback,
            inputs=[current_interaction_state, feedback_category, feedback_comment],
            outputs=[feedback_status],
        )
        suggest_correction_button.click(
            show_correction_form,
            outputs=[review_form, correction_form, feedback_status],
        )
        save_correction_button.click(
            save_correction_feedback,
            inputs=[current_interaction_state, correction_text],
            outputs=[feedback_status],
        )
        submission_stage_a = submission_button.click(
            submission_started,
            outputs=[submission_button, submission_status],
            queue=False,
            trigger_mode="once",
        )
        submission_stage_a.then(
            submit_current_session,
            inputs=[current_interaction_state, submission_remote, submission_mode],
            outputs=[submission_status, submission_button],
            queue=True,
            trigger_mode="once",
            concurrency_limit=1,
            concurrency_id="user-testing-submission-v1",
        )
        clear_button.click(clear_history, outputs=[history_state, history_markdown])
    return app


def main() -> None:
    build_app().launch(server_name="127.0.0.1", share=False)


if __name__ == "__main__":
    main()
