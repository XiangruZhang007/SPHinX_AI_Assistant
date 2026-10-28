# Session workflow and accounting

All commands are implemented in `scripts/explore_doc_cli_skill.py`; consult each subcommand's `--help`. Paths passed with `--path` are repository-relative. The CLI finds the assistant project root from its own file, independently of the current working directory.

- `audit --output-dir NEW_DIRECTORY`: administrative inventory plus separate curated/expanded manifests. Never run this to widen a denied question's scope.
- `formats --scope curated|expanded`: full capability registry, presence and per-scope counts.
- `create --output-dir NEW_DIRECTORY --scope curated|expanded`: freeze scope, revisions, hashes and budget before a question.
- `search --session-dir DIRECTORY --query TEXT [--repository SPHinXsys|SPHinXsim] [--category documentation|cli_source] [--limit 6] [--context 1]`: deterministic lexical line/filename search; score is a fixed heuristic, not confidence.
- `read --session-dir DIRECTORY --repository REPO --path PATH [--start 1 --end N --context 0]`: default at most 80 source lines.
- `section ... --line N`: section containing a source line, including its heading; maximum section characters apply.
- `symbol ... --name QUALIFIED_NAME`: Python AST or existing optional C++ Tree-sitter. Other source languages return a clearly labeled lexical name window.
- `references ...`: static Python imports/simple same-file call candidates or Markdown relative links; not a runtime dependency graph.
- `follow ... --index N [--depth 1]`: recheck approval and reference chain depth, then read target. Denied references require a separate reviewed scope change.
- `inspect ... [--cell N]`: stdlib structure summary or exact decoded notebook cell text with cell identity. Never executes cells.
- `explore --query TEXT --output-dir NEW_DIRECTORY --scope curated|expanded`: lightweight deterministic search/read/reference plan. Alternatively `--session-dir DIRECTORY` resumes the fixed scope and budget. Agent-selected tool use is a separate, unevaluated capability.

At creation set limits with `--max-search-operations`, `--max-read-operations`, `--max-files-explored`, `--max-evidence-records`, `--max-returned-characters`, `--max-section-characters`, and `--max-reference-depth`. Defaults: 4 searches, 8 explicit read operations, 64 distinct files, 24 distinct retained evidence records, 16,000 returned characters, 4,000 characters per section/symbol/cell, depth 1. Existing sessions never reset or accept new limits. Resume is explicit. Sessions are single-writer; a lock rejects concurrent operations. A process crash may leave a lock and requires checking the session before manual recovery.

A search consumes one search operation; internal source scans consume the distinct-file limit and are measured as `characters_scanned` in the trace, rather than explicit read operations. Every direct read, section, symbol, references or inspect call consumes one read operation. A follow consumes one to inspect the referring source and one to read its target. Repeated reads consume operations and returned-character cost again. Failed or denied explicit reads consume an operation, while authorization denial precedes source content access.

Every returned evidence text character, including search snippets and context, consumes the shared character budget. Reference lists and structure summaries conservatively consume their serialized character length. JSON framing, evidence metadata, duplicate copies of persisted state, and the user's query are not source-character charges; this is an output-evidence measure, not a token or total I/O limit. Administrative manifest scanning is recorded separately before the query and must be amortized or included explicitly in future comparisons. Query scans are recorded separately; do not hide them when comparing cost with BM25.

Evidence is deduplicated by repository, relative path, source checksum, exact location and exact content. Its ID excludes absolute paths and retrieval method. Repeated returns retain their trace and charge again even when only one evidence record is retained. Overlapping windows can be distinct records. Limits on evidence records count retained unique records.

Reads truncate only at complete original lines. CRLF, Unicode and an absent final newline remain intact. A line that cannot fit produces no text and an explicit truncation. A partial section must never be presented as the complete section. Section truncation can interrupt a code block; it is an exact bounded source view, not a new self-contained document chunk.

Each operation saves `operations/NNNN.json`, `trace.json`, `evidence.json`, updated `state.json` and separate volatile `runtime/NNNN.json`. The manifest and scope audit remain available. Substantive output, evidence IDs and traces are deterministic at identical sources, configuration, query, parameters, budget and implementation. Timing is intentionally separate. `exploration-summary.json` describes the latest deterministic plan; operation history remains cumulative.

Report evidence found, not found, partially retrieved, errors and budget exhaustion distinctly. Stop on exhaustion rather than opening a new session for the same question. Do not infer semantic completeness from a successful read.

Future same-scope comparisons must additionally verify which lines are represented in C1: equal files alone do not prove equal available evidence. Account for distractor scopes, actual scans, setup costs and any LLM planning separately. Step 1.8B evaluation and hybrid retrieval are outside this prototype.

`references --limit N` returns a prefix of at most 20 candidates by default (maximum 100), with original zero-based list indices valid for `follow`. If its list or character limit truncates the candidates, the result and trace mark that explicitly; no half-reference is returned. Increase this limit only within the session's fixed character/read budget.
