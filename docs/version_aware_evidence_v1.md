# Q1 V1: version-aware evidence applicability (diagnostics only)

## Architecture assessment before implementation

Frozen requirements have five observed kinds: `line_range`, `source_range`,
`passage`, `section_title`, `symbol`. The frozen matcher combines exact source
path, optional role, inclusive line containment and the kind-specific predicate;
`contains_all` applies to the containing chunk, not necessarily the requested
source lines. ALL_OF outcomes belong to a question/mode, not one requirement.
The benchmark has 88 questions / 128 question-mode combinations; the number of
requirements and exact duplicate locators must be counted independently.

P2 supplies a sealed base snapshot, original source bytes, selection, inventory,
native extraction graph and corpus. P5A.1 supplies a schema-2 target candidate,
base/target source-state IDs, exact upstream commits and portable Git object
proofs. Its verified public commit receipts establish the genuine experimental
pair; source bytes alone never identify a commit. Both contracts can be read
without changing them. Frozen P4.1 `rescore` verifies archived raw outcomes;
Q1 need not run a Retriever or rebuild chunks.

Existing `research_quality.oracle_applicability` compares frozen matches, source
bytes and relaxed range predicates. It reports conservative potential stale
oracles, but does not recover and uniquely map original passages, prove byte
coverage or resolve deletion versus modification. Reuse its output for comparison;
do not change it or treat its heuristic as Q1 ground truth.

## Independent contract

Use separate Q1 module, CLI and report; all existing files remain byte-identical.
Known range locators extract exactly their inclusive physical lines, omitting
only the final line terminator after the requested last line. No context is
added to the evidence. Record exact raw source bytes and normalized chunker
byte coordinates separately. Line mapping follows existing `source_bytes`
normalization; unsupported newline/Unicode coordinate cases remain explicit.

Literal passage locators require a unique exact occurrence associated with an
original frozen matching chunk. Case-fold-only matching without an exact raw
anchor remains unresolved. Symbol/section locators require a unique matching
base chunk span. Multiple candidate spans remain ambiguous; no arbitrary section
expansion is allowed. Unknown kinds are UNSUPPORTED_REQUIREMENT_TYPE, retained
in every denominator. Identical canonical frozen criteria may share a locator
ID, while every question/mode/requirement-index instance remains in the report.

An unchanged whole file preserves its original coordinates even when its text
repeats. In changed files, exact passage search must be unique. File relocation
is supported only by unique identical full selected-file bytes within the same
repository; this proves content-addressed correspondence, not Git rename intent.
Other cross-file or modified-file rename claims remain uncertain.

If exact text disappeared, modification/removal requires both immediately
flanking full source lines to be nonempty, unique in each version, unchanged,
and ordered. The old passage must occupy the entire interval between anchors.
The new interval is then a contextual replacement (MODIFIED), or an empty
interval is verified removal (REMOVED). Otherwise return UNRESOLVED. File
removal requires verified absence from Git tree proofs; missing selected input
alone is insufficient. Never infer semantic equivalence from text similarity.

Source state, source location, corpus coverage, uniqueness, frozen locator state,
criterion applicability and semantic review are independent dimensions. Coverage
uses validated source byte spans and role/mode eligible chunks. SPLIT requires
complete byte coverage of this exact mapped passage by multiple actual chunks;
no uncovered gaps or arbitrary neighboring chunks are accepted. Literal byte gaps
(including whitespace or a trailing semicolon outside a parser node) are reported
separately from frozen metadata matching. A frozen predicate can remain applicable
while literal-range coverage is PARTIAL; Q1 must not declare it a stale oracle. Record whether
fragmentation already existed in the base.

Primary precedence: unsupported; ambiguous; unresolved correspondence; removed;
modified; absent/partial corpus -> unresolved; split; moved; unchanged. A moved
and split passage is SPLIT with a separate MOVED location dimension. `MOVED` is
the primary enum (the prompt's `EVIDENCE_MOVED` describes the same diagnostic).
Modified/removed content requires semantic review; uncertain mappings require
applicability review. No Q1 result changes frozen scores or grants approval.

## Input and report integrity

The production CLI accepts an externally pinned relocatable input manifest with
exact P2 ID, P5A.1 candidate ID, both source-state IDs, upstream pairs, corpus
hashes, benchmark hash and archived result hashes. Verify original packages,
Git proofs, source bytes, corpus spans and P4.1 raw-result rescoring before use.
Controlled in-memory fixtures are separately labeled and cannot claim genuine
Git provenance. Report identities include only logical identities and relative
source paths; absolute transport paths are outside the logical report.

Deterministic verification regenerates the report and compares complete JSON
objects and Markdown bytes. Restored reports use the same pinned inputs after
relocation. Raw passages/checksums and all ambiguous candidates are preserved.
Exact matching is deliberately conservative; uncertainty is a valid diagnostic,
never an adjusted official Hit@K. Existing P5A.1 results remain 58/84 and 62/84
combined, and 37/40 documentation-only. DOC-Q27's FAIL remains unchanged.

Q1 does not approve baselines, migrate benchmarks, change ranking, configure
remote storage, activate workflows or begin P5A.2/P5B/Gradio work.
