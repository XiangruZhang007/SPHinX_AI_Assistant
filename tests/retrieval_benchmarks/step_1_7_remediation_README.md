# Step 1.7 controlled remediation

The frozen benchmark remains `step_1_6_frozen_evaluation_v2` (schema 1, 88 questions,
128 evaluations). Its criteria and historical runner are unchanged. Existing
uncommitted project files were copied in full to an immutable S0 snapshot before
editing; issue-specific incremental patches and final S0/S1 hashes are archived.

Permanent experiment evidence:
`/home/zhang/Projects/SPHinX_AI_Assistant_artifacts/remediation_experiments/step_1_7_20261027T224836Z/`

Verified C1, generated independently twice:
`/home/zhang/Projects/SPHinX_AI_Assistant_artifacts/candidate_baselines/step_1_7_post_p1_20261027T224836Z/build1/expanded-candidate.jsonl`

C1 has 11,370 records; SHA-256:
`54f382056e2eeae4477f4f1a544d5dd2cb1bc9057f3d2ec58d54b578ab66c9f8`.
All retained B0 record IDs preserve their complete contents. One incomplete
constructor fragment is replaced. There are 15 added JSON formatter records and
17 struct/enum records. Original CLI 71 records and documentation 42 files / 354
chunks are preserved; documentation JSONL bytes are identical.

The original Retriever deduplicated function/method records by repository,
source path and name. Different physical definitions now retain distinct source
ranges/content; identical physical records still collapse. Other record types,
ranking weights and preprocessing are unchanged. Confirmed baseline examples:
C-24 target before dedup rank 2; C-26 rank 3; C-27 rank 35. The last example also
has a low-rank problem, so preserving it does not guarantee a Top-10 hit.

Coverage changes:

- Explicitly include only `sphinxsim/json_format.py` and assign its CLI helper role.
- Add field-only structs and named enums in already-discovered SPHinXsim simulator
  C++ files; do not duplicate whole classes containing methods.
- Recover adjacent ERROR/signature plus initializer-function nodes only when a
  template-closer spacing validation yields one complete qualified constructor.
  Retrieved content and offsets use original source bytes; the spaced validation
  copy is never stored as source evidence. Original parser warnings remain.

Use the existing Python environment; install nothing:

```bash
PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 tools/treesitter-chunker/.venv/bin/python -B \
  scripts/evaluate_remediation_step_1_7.py \
  --benchmark tests/retrieval_benchmarks/step_1_6_combined_frozen_v2.json \
  --corpus /home/zhang/Projects/SPHinX_AI_Assistant_artifacts/candidate_baselines/step_1_7_post_p1_20261027T224836Z/build1/expanded-candidate.jsonl \
  --documentation-corpus /home/zhang/Projects/SPHinX_AI_Assistant_artifacts/candidate_baselines/recovered-20261027T205511Z/documentation.jsonl \
  --manifest /home/zhang/Projects/SPHinX_AI_Assistant_artifacts/remediation_experiments/step_1_7_20261027T224836Z/experiments/B2-targeted-combined/experiment-lock-v1.json \
  --output-dir /path/to/a/new/persistent/result-directory
```

The manifest pins current corpus counts/hashes, the selected Retriever and
complete evaluation modules. `--retriever` can select the archived R0 module;
its bytes must match that experiment's lock. The frozen specification's config
text describes B0, including historical deduplication and paths; actual modified
implementation identity and its diff are authoritative for B1/B2. Numerical
BM25/boost configuration, scoring criteria and primary denominators stay fixed.

The new entry point passed C0/R0 equivalence: all 128 raw result rows match B0
byte-for-byte, and all numerical aggregation matches. Experiment result changes
are reported, not rejected as failed historical reproduction. Historical
availability fields remain in the unchanged benchmark; current availability is
assessed before ranking and reported separately. D4 never enter primary scores.
No numerical tolerances or post-retrieval oracle changes are used.

A range spanning several separate definitions can remain strictly unavailable
in any *single* chunk after its individual declarations are indexed. In
particular, D-01 spans RawFloat and its loader, and D-03 spans two hook enums.
Report any union evidence only as supplementary diagnostics; do not merge chunks
or relax criteria merely to turn those historical probes into strict passes.

Source chunk IDs retain the existing absolute-input-path convention; relocation
of upstreams may change newly generated source IDs. Source chunking retains text
newline normalization and legitimate overlap. Documentation uses original bytes
and its separate partition invariants. Current parser syntax warnings do not
constitute compiler/simulation validation; neither was executed.

See the permanent report for the actual B0/B1/B1.5/B2 matrix, all regressions,
remaining failures, full commands, dependency versions, tests and archive checks.
No production corpus is replaced, and no CI V2 work is included.
