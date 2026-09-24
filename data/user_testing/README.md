# User-Testing Data

This directory is reserved for interaction data from real testers, such as
supervisors and colleagues using the Gradio assistant.

**User-testing schema version: v1.** This is the initial session/feedback
schema. Future schema changes must be documented explicitly, for example:
`v1` for the initial session/feedback schema and `v2` for future
reviewed/approved-correction fields. Version 2 is not implemented here.

It is intentionally separate from `data/evaluation/`:

- `data/evaluation/` contains developer-created benchmarks, regression tests,
  smoke tests, and manual retrieval/grounded-answer validation.
- `data/user_testing/` contains real tester sessions, ratings, comments, and
  suggested corrections.

## Directory convention

Each tester uses a separate directory. One Gradio testing session corresponds
to one JSONL file:

```text
data/user_testing/
  <tester_id>/
    session_<timestamp>.jsonl
```

For example:

```text
data/user_testing/
  teacher_01/
    session_2026-09-25_1030.jsonl
  colleague_01/
    session_2026-09-25_1100.jsonl
```

Tester-specific directories are not created until real testing begins. Using a
separate `tester_id` directory reduces Git conflicts when session files are
shared with the project maintainer.

Use a collision-resistant interaction identifier based on:

```text
<tester_id>_<session_id>_<sequence>
```

For example: `teacher_01_session_20260925_1030_0001`. The per-session sequence
avoids identical interaction IDs when multiple testers work concurrently.

## JSONL interaction format

Each line in a session file is one interaction. Multiple questions from the
same Gradio session are appended as separate JSONL lines to the same session
file. A question must not create its own directory or file.

The proposed minimum record schema is:

| Field | Purpose / initial value |
| --- | --- |
| `interaction_id` | Collision-resistant `<tester_id>_<session_id>_<sequence>` identifier. |
| `session_id` | Identifier shared by all lines from one testing session. |
| `tester_id` | Non-personal tester directory identifier. |
| `timestamp` | Interaction timestamp in ISO 8601 form. |
| `query` | Original tester question. |
| `raw_answer` | Authoritative raw model output; preserved without replacing it with rendered output. |
| `rendered_answer` | Optional/derived HTML or plain-text presentation value; it need not be stored when it can be regenerated from `raw_answer`. |
| `sources` | Source metadata displayed to the tester. |
| `retrieved_evidence_metadata` | Available retrieval/evidence metadata, without requiring full chunk content. |
| `model` | Model identifier used for the interaction. |
| `base_url` | Non-secret provider base URL, when applicable. |
| `top_k` | Retrieval Top-K value. |
| `prompt_version` | Grounded-answer prompt/policy version used for the interaction. |
| `retrieval_version` | Optional future retrieval-version identifier. |
| `chunking_version` | Optional future chunking-version identifier. |
| `status` | Result status for the interaction. |
| `rating` | Initially `null`; allowed initial values are `null`, `"helpful"`, and `"needs_review"`. |
| `feedback_category` | Initially nullable; use the controlled vocabulary below when set. |
| `feedback_comment` | Initially nullable. |
| `suggested_correction` | Initially nullable. |
| `review_status` | Initially `"unreviewed"`. |
| `sphinxsys_commit` | Exact SPHinXsys commit used during testing. |
| `sphinxsim_commit` | Exact SPHinXsim commit used during testing. |

### Retrieved-evidence metadata

`retrieved_evidence_metadata` is a list of compact evidence descriptors. A
recommended item structure is:

```json
{
  "rank": 1,
  "repository": "SPHinXsys",
  "source_path": "repos/SPHinXsys/...",
  "source_role": "core_library",
  "symbol_or_section_title": "getPressure",
  "line_start": 17,
  "line_end": 20
}
```

Full chunk content is not required in this field unless a later use case
requires it. This metadata supports later attribution of retrieval failure,
ranking failure, and answer-synthesis failure.

### Feedback categories

`feedback_category` remains nullable. When a tester selects a category, it
must use one of the following values rather than a free-form category name:

- `incorrect`
- `unsupported_claim`
- `incomplete`
- `irrelevant`
- `wrong_source`
- `unclear`
- `formatting`
- `other`

Free-text explanation belongs in `feedback_comment`.

Example JSONL interaction (shown as one line, formatted here for readability):

```json
{
  "interaction_id": "teacher_01_session_20260925_1030_0001",
  "session_id": "session_20260925_1030",
  "tester_id": "teacher_01",
  "timestamp": "2026-09-25T10:30:15Z",
  "query": "What does getPressure do in WeaklyCompressibleFluid?",
  "raw_answer": "The supplied SPHinXsys evidence shows getPressure returning p0_ * (rho / rho0_ - 1.0).",
  "rendered_answer": null,
  "sources": [
    {
      "repository": "SPHinXsys",
      "source_path": "repos/SPHinXsys/src/shared/materials/weakly_compressible_fluid.cpp",
      "symbol_or_section_title": "getPressure"
    }
  ],
  "retrieved_evidence_metadata": [
    {
      "rank": 1,
      "repository": "SPHinXsys",
      "source_path": "repos/SPHinXsys/src/shared/materials/weakly_compressible_fluid.cpp",
      "source_role": "core_library",
      "symbol_or_section_title": "getPressure",
      "line_start": 17,
      "line_end": 20
    }
  ],
  "model": "example-model",
  "base_url": "https://example.invalid/v1",
  "top_k": 5,
  "prompt_version": "grounded_answer_v1",
  "retrieval_version": null,
  "chunking_version": null,
  "status": "successful",
  "rating": "needs_review",
  "feedback_category": "unsupported_claim",
  "feedback_comment": "Please verify the constructor-to-member mapping against direct source evidence.",
  "suggested_correction": null,
  "review_status": "unreviewed",
  "sphinxsys_commit": "<SPHinXsys commit hash>",
  "sphinxsim_commit": "<SPHinXsim commit hash>"
}
```

An actual JSONL file stores this record on a single physical line.

## Design principles

1. One Gradio testing session maps to one JSONL file.
2. Multiple questions in that session append separate JSONL lines.
3. One question never creates its own directory or file.
4. Separate `tester_id` directories reduce Git conflicts.
5. Raw model output is authoritative and must be preserved. Rendered HTML or
   plain-text presentation is optional/derived and must not replace `raw_answer`.
6. User feedback is not automatically trusted knowledge:
   `rating`, `feedback_comment`, and `suggested_correction` do not equal an
   approved correction.
7. A future human-review process may move a correction to an approved-answer
   store. That process is not implemented here.
8. Both repository commit hashes are recorded so feedback can be associated
   with the exact SPHinXsys and SPHinXsim snapshots used during testing.
9. Do not store API keys, authorization headers, secrets, or full environment
   variables.
10. Files under `data/user_testing/` are intended to be Git-shareable between
    testers and the project maintainer.

## Current scope

The prototype implements automatic successful-interaction logging and tester
feedback persistence for `helpful`, `needs_review`, and suggested corrections.
Feedback updates rewrite the affected session JSONL file atomically while
preserving one interaction per line. Feedback remains unreviewed tester input;
reviewer workflows, approved-answer lookup, SQLite, and central-server
synchronization are not implemented.

## Review-batch aggregation

Create a reviewer batch with:

```bash
python scripts/aggregate_user_testing_feedback.py
```

Use `--only-unreviewed` to restrict selection to records whose
`review_status` is `unreviewed`. Aggregation selects interactions rated
`needs_review` or carrying a non-empty `suggested_correction`, and writes a
JSONL and Markdown batch to `data/user_testing/review_batches/`. It is
read-only with respect to source session files and does not approve, reject, or
otherwise promote tester feedback into knowledge.

## Markdown session exports

JSONL session files remain the authoritative structured records. Markdown
exports are deterministic, generated human-readable views for supervisors and
expert reviewers; they are not approved repository knowledge, and suggested
corrections remain unreviewed until human review.

Export one session:

```bash
tools/treesitter-chunker/.venv/bin/python \
  scripts/export_user_testing_session_md.py \
  --session data/user_testing/<tester_id>/session_<...>.jsonl
```

Export all sessions for one tester, or all non-development tester sessions:

```bash
tools/treesitter-chunker/.venv/bin/python \
  scripts/export_user_testing_session_md.py \
  --input-dir data/user_testing/<tester_id>/

tools/treesitter-chunker/.venv/bin/python \
  scripts/export_user_testing_session_md.py \
  --all-sessions
```

Derived reports are written under `data/user_testing/markdown_exports/`. The
exporter never modifies the source JSONL session files.
