# Recovered Step1.3G documentation benchmark

This frozen specification contains40 exact historical questions and evidence expectations, reconstructed from the supplied Step1.3G report. Status: reconstructed_from_step_1_3G_historical_report. It is not a byte-for-byte recovery of the lost retrieval-expectations.json.

Question text, repository/path, titles/passages/ranges and ALL_OF evidence requirements are preserved. DOC-Q01 requires both Generate and Validate; DOC-Q30 requires its original51-55 interval within a single chunk. Do not revise expectations to improve scores. Historical ranks and PASS/FAIL outcomes are not ground truth and are stored separately in ignored artifacts.

Full report: /home/zhang/Projects/SPHinX_AI_Assistant_ci/doc/artifacts/recovery-2026-10-27/historical_benchmark_recovery/original_inputs/SPHinXsistant_Step1_3G_Full_Report.txt
Historical outcomes: /home/zhang/Projects/SPHinX_AI_Assistant_ci/doc/artifacts/recovery-2026-10-27/historical_benchmark_recovery/documentation_40/historical_results.json
Recovery validation: /home/zhang/Projects/SPHinX_AI_Assistant_ci/doc/artifacts/recovery-2026-10-27/historical_benchmark_recovery/documentation_40/validation_report.md
Candidate: /home/zhang/Projects/SPHinX_AI_Assistant_artifacts/candidate_baselines/recovered-20261027T205511Z/expanded-candidate.jsonl

Historical38PASS/2FAIL belongs to the old documentation-only run. No new evaluation was run during recovery. Future combined retrieval results must be stored separately and must not overwrite historical outcomes. Q38/Q39 have documented range-precision caveats; keep original frozen values and state any later approved revision separately.

Large corpora/reports stay under ignored doc/artifacts/ or the external artifact root. These two small files are intended for later Git tracking; no staging or commit performed.


## Formal Step 1.6 freeze: authoritative v2

`step_1_6_combined_frozen_v2.json` is a byte-identical copy of the authoritative
`step_1_6_frozen_evaluation_v2` benchmark. Its `schema_version: 1` describes JSON
structure, not the benchmark's semantic version. Its SHA-256 is:

```text
bac0b31b74410eaff00246e628187ee07c4aaefb8747ad076f090393b71d1ab7
```

The 88 unique questions are A40 documentation, B14 legacy source/case/CLI, C30 new
combined questions (18 single/12 multi), D4 P1 diagnostics. A runs against both
354-record documentation and 11,338-record combined inputs: 128 combinations.
D stays out of ordinary success denominators. DOC-Q01 remains Generate AND
Validate; DOC-Q30 still requires one complete51–55 chunk (union is diagnostic).
Q38/Q39 original ranges remain frozen; separate semantic anchors do not replace
strict scores. Q40 retains CPU-initialization semantics. Legacy broad-query and
post-run interpretation uncertainty remains separate from source availability.

### Portable entry point and dependency closure

The project-local entry is `scripts/evaluate_frozen_step_1_6.py`, importing:

- `src/retrieval_v1.py`: unchanged production lexical Retriever.
- `src/retrieval_evaluation_v2.py`: preserved evaluation body with runtime paths injected.
- `src/retrieval_evaluation_criteria.py`: byte-identical original criteria.
- `src/retrieval_evaluation_reports.py`: original metric, aggregation and diagnostic logic.
- `src/retrieval_evaluation_io.py`: input/lock validation and exact comparisons.

`step_1_6_frozen_v2_manifest.json` records the dependency hashes and points to
`step_1_6_frozen_v2_support/`. Support files retain the original lock, source
review, semantic supplement, historical outcomes, compact128-row reference
fingerprints, metric/artifact checksums, original environment and transformation
record. No import or support-file access depends on a private artifact directory.
Large corpora and raw historical reports remain in ignored/external storage.

The copied spec retains all fields, including historical absolute location hints
and provenance. They are **not mandatory runtime paths**: independent CLI inputs
select the actual files. No JSON normalization, oracle change or lock hash
replacement was needed. The original lock checks the same original spec,
source-review and semantic hashes. Exact reference snapshots also remain in the
persistent Step1.6F evidence directory.

Required CLI arguments: `--benchmark`, `--corpus`, `--documentation-corpus`,
`--output-dir`. Optional `--manifest` defaults to the manifest beside the benchmark.
The output directory must not already exist. Checksums, code identity, version,
question inventory and documentation component equality are checked before ranking.

### Checked reproduction command

Run from the project root with the existing Python3.12.3 environment. This exact
interface is used for the acceptance reproduction; choose a fresh output directory
for a later run, since the recorded output below must not be overwritten:

```bash
PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 tools/treesitter-chunker/.venv/bin/python -B scripts/evaluate_frozen_step_1_6.py \
  --benchmark tests/retrieval_benchmarks/step_1_6_combined_frozen_v2.json \
  --corpus /home/zhang/Projects/SPHinX_AI_Assistant_artifacts/benchmark_freezes/step_1_6_frozen_v2_20261027T215400Z-verified/relocated_inputs/combined.jsonl \
  --documentation-corpus /home/zhang/Projects/SPHinX_AI_Assistant_artifacts/benchmark_freezes/step_1_6_frozen_v2_20261027T215400Z-verified/relocated_inputs/documentation.jsonl \
  --output-dir /home/zhang/Projects/SPHinX_AI_Assistant_artifacts/benchmark_freezes/step_1_6_frozen_v2_20261027T215400Z-verified/reproduction1
```

These paths are one recorded example, not implementation defaults. You may supply
other paths holding exactly the same bytes. Documentation bytes are reused from
the preserved original component, never regenerated for this freeze. The original
unrelocated inputs remain under `/home/zhang/Projects/SPHinX_AI_Assistant_artifacts/candidate_baselines/recovered-20261027T205511Z`.

Verify the benchmark with `sha256sum`, or inspect the manifest and generated
`effective-inputs.json`. Set `PYTHONHASHSEED=0` before Python starts. Full raw
scores (including hexadecimal float identity), chunk IDs/order, strict/semantic
outcomes and aggregate diagnostics are compared without tolerance. A nonexact
run exits nonzero and keeps its discrepancy evidence; aggregate matches alone
never establish exact reproduction. `results.json` contains the complete128 rows.

Original Step1.6 report and results:
`/home/zhang/Projects/SPHinX_AI_Assistant_artifacts/retrieval_evaluation_results/step_1_6_combined_20261027T211957Z`.
New Step1.6F reproduction evidence:
`/home/zhang/Projects/SPHinX_AI_Assistant_artifacts/benchmark_freezes/step_1_6_frozen_v2_20261027T215400Z`.

The strict default manifest pins the original Retriever and evaluation files.
A future remediation must use explicit, versioned experimental provenance and
retain this original baseline; do not change expected answers to match new ranks.
This task prepares assets for Git tracking, without staging or committing them.
