# P5A.3-B — GitHub-hosted Runner Verification

## 1. Experiment Identity

- GitHub Repository: XiangruZhang007/SPHinX_AI_Assistant
- Workflow Run ID: 41617493805
- Job ID: 125854498628
- Execution Commit: 53f0d2642e149c40729f5cb24036148fd29a80b0
- Trigger: workflow_dispatch
- Run Status: success
- Artifact ID: 13223351750
- Artifact Name: ci-v2-research-41617493805-1
- Artifact SHA-256: c7b68d90feb620d020f58d6ac52ce2becf14ab03943e5bd8b5f3fcbf08613cfc
- Runner Log SHA-256: 7834b2f657f902b2db9d1f2ebfbd8df661248176fd16ae37572e0cdbf0ce388b

## 2. Source Versions

### Parent

- SPHinXsys: 4e686ee357de1cb798ac47eb760dd79e2fb407ff
- SPHinXsim: 9cdae783e3ef3f8926c2174a0db89e96846befad
- Parent Snapshot: sha256-798d0d1284c7dbba06d5788a062973bf7290cbbfdef28150037371fa78b7188a

### Target

- SPHinXsys: 351368e108f47f8d15f89baac96683ecadaee32c
- SPHinXsim: dceacd4e509a8592b802cf44bb2885bea7ddfdb3

### Candidate

- Candidate ID: sha256-d7c95bbf42703d209a5a20f60540cf04b7134736ead650d8c95d66adaee724b8
- Corpus SHA-256: 224fd0723fd0d77a3f8ad7cd300ecd31856fd689b0fc5e6c942b8d691ef63f8d
- Provenance: GENUINE_UPSTREAM

## 3. Technical Validation

- GitHub Job: SUCCESS
- Remote Baseline: PINNED_REFERENCE_VERIFIED
- Local Integration: LOCAL_VALIDATED
- Structural Parity: PASS
- Independent Full Rebuild: True
- Parity Report: PARITY_PASS

Incremental and full builds produced byte-identical
corpus.jsonl, inventory.json, selection.json,
and extraction-graph.json for this frozen version pair.

## 4. Incremental Reuse

- Selected Files: 1001
- Unchanged Files: 995
- Modified Files: 6
- Added Files: 0
- Deleted Files: 0
- Reused Files: 995
- Reused Chunks: 11213
- Extraction Invocations: 6

## 5. Corpus Coverage

Final Candidate Corpus: 11,371 chunks.

Observed coverage from direct corpus inspection:

- SPHinXsys: 9,911 chunks
- SPHinXsim: 1,460 chunks
- Documentation source role: 354 chunks
- CLI source role: 86 chunks
- SPHinXsim sphinxsim/cli.py: 71 chunks
- SPHinXsim docs/cli-usage.md: 30 chunks
- SPHinXsys tutorials/: 202 chunks

These results confirm indexing of the checked sources,
not exhaustive coverage of every eligible upstream file.

## 6. Retrieval Evaluation

- Combined Primary Hit@5: 58/84
- Combined Primary Hit@10: 62/84
- Historical Parent Hit@5: 59/84
- Retrieval Quality: QUALITY_REVIEW_REQUIRED
- Retrieval Quality Accepted: False

DOC-Q27 is the identified new loss.
The frozen line-range oracle points to lines 101–104
of SPHinXsim/docs/installation-windows.md.

Source comparison reported that the same evidence moved
to lines 103–106 without textual change.

The frozen benchmark remains unchanged.
The failure is retained pending oracle applicability review.

Q1 status: Q1_NOT_QUALIFIED_FOR_VERSION_PAIR

## 7. Approval and Deployment Boundaries

- Hosted Validation: PENDING_HOSTED_REVIEW
- Research Baseline Approved: False
- Automatic Research Approval: False
- Automatic Reuse: False
- Research Baseline Created: False
- Production Promotion: NOT_AUTHORIZED
- Scheduled Execution: False

The candidate has not been approved for research baseline
promotion or production deployment.

## 8. Evidence Files

- Original GitHub Actions Artifact ZIP
- logs/github_runner_41617493805.log
- metadata/github_run.json
- metadata/github_job.json
- metadata/github_artifacts.json
- inspection/plan.json
- inspection/candidate.json
- inspection/parity.json
- inspection/result.json
- inspection/quality-report.json
- inspection/oracle-applicability.json
- inspection/review-summary.json

## 9. Acceptance Status

Technical build validation: PASS for the tested version pair.

Formal archival acceptance: PENDING final checksum
manifest and independent backup.

Retrieval quality approval: NOT GRANTED.

Research baseline promotion: NOT AUTHORIZED.

This report documents a research experiment.
It does not modify or approve the frozen candidate.
