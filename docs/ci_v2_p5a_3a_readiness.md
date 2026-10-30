# P5A.3-A: pre-publication qualification

This stage prepares an operator decision package. It does not publish Releases,
create tags, push code, change repository settings or dispatch workflows.
The active schema-2 trust registry remains empty. P2 is an initial bootstrap
anchor, not a P4.1-approved research baseline or production authorization.

Publishable-object proposal uses the exact existing P2 tar and a separately
pinned parent R2 results asset. The full P5A.2 research archive is unnecessary
for the Runner. Real release/asset IDs and final reference pins stay unresolved
until explicitly authorized publication and independent verification.

Keep existing runtime authorization fields. Operator scope INITIAL_BOOTSTRAP_ONLY
maps to EXPLICIT_P2_BOOTSTRAP, the exact P2 package and a separately pinned fixed
parent/target build policy. Existing runtime auth.scope remains
ISOLATED_RESEARCH_PARENT_REUSE. No new registry schema or approval system.
Retries of the same fixed authorized experiment are possible; a single-use
authorization counter is not implemented or implied.

The local origin and current Release allowlist are different repositories.
Their publication/deployment roles require an explicit operator decision.
Remote admin settings must remain NOT_VERIFIED without privileged evidence.
The first hosted manual run also requires workflow deployment on the default
branch and complete code/config on the selected execution ref. This stage
does not push/merge/deploy either repository.

One demonstrated recovery gap is repaired in the existing downloader:
atomic-no-clobber-v2 stages each response privately, verifies complete length
and SHA256, then atomically creates a new final path without overwriting a
concurrently created file. Normal exceptions remove this attempt's staging.
Stale downloads are rejected; unrelated staging files are never consumed or
deleted. Abrupt process termination can leave an unreferenced staging directory,
but it is never selected as a trusted download. Metadata, host, immutable asset,
package and authorization validations remain unchanged. No new download client.

Record the new source digest separately from historical P5A.2 qualification.
Preserve the archived original implementation and rerun its complete test suite
with pinned historical inputs, plus focused interruption/race/cleanup tests.
No fresh chunking or retrieval experiment is needed for this transport-only fix.

General schema-2 parent R2 qualification remains blocked. Remote execute-plan
currently supplies no Q1 input descriptor; optional applicability/attribution
therefore remains explicitly unqualified until separately wired for the exact
historical pair. Frozen scoring, Q1 and attribution algorithms are unchanged.

The exact historical transport contains local-path observations. Preparation
must report bounded confidentiality findings and require disclosure review;
it must not sanitize/rebuild the sealed object while retaining its identity.

Publication-ready does not mean published, operator-authorized, live recovery
verified or GitHub-hosted validated. Persistent reports separate these gates.
