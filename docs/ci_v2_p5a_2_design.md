# P5A.2 schema-2 manual workflow and diagnostic attribution

The original P5A schema-1 CLI, runtime lock and registry remain intact. The manual
workflow now routes to `scripts/ci_v2_schema2_research.py`. Only workflow_dispatch
is enabled; no hosted run, release upload, schedule or production update is part
of local qualification. P2/P3/P4.1/R2/P5A.1/Q1 implementations remain byte-identical.

The schema-2 registry is deliberately empty. Each future entry must contain a
reviewed `reference`, its separately pinned `expected_reference_sha256`, a
repository-relative `build_policy_path`, and `build_policy_sha256`. The build
policy is the existing P5A.1 schema-2 genuine-upstream authorization, including
exact parent/target states, Git revisions and publication receipts. Planning
checks these pins before provisioning parsers. Revalidation precedes execution.

Trust references bind exact package format/schema, node ID, logical package
checksum, sealed manifest SHA256, transport SHA256, selected-source state,
upstream pair, unchanged build fingerprint, corpus SHA256, parent R2 rows SHA256,
explicit experiment/reuse authorization, and trusted storage. P2 authorization
is `EXPLICIT_P2_BOOTSTRAP`; it is never P4.1 approval. Schema-2 research parents
require actual P4.1 package verification; candidates and schema-1 research nodes
are rejected. Their workflow execution remains blocked until a separate parent
retrieval anchor is qualified: `PARENT_RETRIEVAL_ANCHOR_NOT_QUALIFIED`.

Storage is public GitHub immutable Release assets. Bind repository, numeric
release/asset IDs, exact tag/name and expected SHA256. Require immutable=true,
draft=false and actual asset membership in that release before downloading.
Only public credential-free GET is implemented. A checksum proves integrity;
reviewed trust configuration supplies scoped authorization. No latest tags,
write API, automatic replacement, approval or publication operation exists.
The remote registry remains BOOTSTRAP_REQUIRED until an operator explicitly
selects and authorizes a published package. Actions artifacts hold candidate
evidence temporarily; they are not the durable trusted baseline store.

GitHub documents immutability of published assets/tags and numeric asset reads:
[Immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases),
[Release assets](https://docs.github.com/en/rest/releases/assets).
Remote configuration/permissions and hosted qualification are deferred to P5A.3.

Local orchestration reuses P5A.1 build/full/parity, unchanged R2 evaluation and
P4.1 rescore plus actual independent replay. Mandatory structural/formal checks
fail closed. Quality remains QUALITY_REVIEW_REQUIRED; structural parity does
not imply quality acceptance, research approval or production permission.

Q1 V1 is used only when an externally pinned historical input pair binds exact
current parent/candidate IDs, source states, corpus hashes and raw row digests.
Otherwise emit Q1_NOT_QUALIFIED_FOR_VERSION_PAIR. Optional diagnostics cannot
override mandatory failures. Q1 is not generalized to later generations here.

The small attribution summary preserves raw rows and emits multiple labels per
question/mode and ALL_OF requirement, with separate Top5/Top10 explanations.
Ranks come from verified original returned records, not aggregate differences.
Absence from Top10 is not an invented rank 11. Ranking regression needs valid,
unchanged and available evidence plus a measured requirement threshold crossing.
Record question-level PASS-to-FAIL separately: an already failing ALL_OF question
can acquire an additional requirement ranking loss. Historical failures are separate. Stale locators, source
changes/removal, verified fragmentation and full coverage absence retain their
Q1 evidence. Partial literal byte gaps remain uncertain because frozen metadata
can still match. A locator explanation at question level requires all failed
requirements at that K to be explained and mapped evidence actually retrieved.
Other requirement failures remain visible. Every review recommendation has a
PENDING human decision; no reviewer identity or approval is generated.

Safe recovery verifies compressed SHA, bounds expanded size, permits only new
regular files and rejects traversal, links, duplicates and noncanonical names.
Package validation occurs in isolated staging before exposing a restored parent.
Candidate archives retain exact results, parity evidence, verified packages,
diagnostics and checksums. Restored packages can replay frozen evaluation offline.
