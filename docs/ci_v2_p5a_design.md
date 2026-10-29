# P5A pre-implementation decisions

A. Runner: propose ubuntu-24.04-arm, exact CPython 3.12.3 and existing pinned Tree-sitter packages. Public platform documentation/API records are capability evidence only. Actual repository/org runner policy and hosted compatibility remain NOT_VERIFIED. The exact interpreter may require an independently qualified ARM64 3.12.3 installation if setup-python has no matching binary; never change fingerprints. No local dependency installation.

B. Storage/bootstrap: separate candidate Actions artifacts (30-day requested retention) from approved immutable baseline storage and a reviewed versioned trusted reference. Release assets are a proposed storage locator, SHA/ID must be pinned in reviewed repository config, not copied from a downloaded package. No remote upload or activation is authorized. Registry initially empty => BOOTSTRAP_REQUIRED / CONFIGURATION_REQUIRED. The genuine P2 anchor may be explicitly authorized by an externally checksum-pinned local research bootstrap policy; it is not P4.1 approved. Synthetic S4 is ineligible as real upstream state. Local independent-process restore is not remote validation.

C. Authorization: manual-run automation may verify, construct, compare, evaluate and package candidates. No automatic approval/pointer update, scheduling, production/Gradio integration or structural fallback. Full parity plus 128+128 actual R2 evaluations are required for formal validation; diagnostic/pinned-byte experiments retain NOT_ASSESSED for formal real-upstream acceptance.

D. Provenance: P3 records target_is_derived_fixture_not_upstream_commit=True and base_upstream_revisions inherited from the anchor; P4 verifies all ancestor upstream dictionaries equal. Byte-change support does not establish new real target Git revision provenance. Live changed commits therefore fail UPSTREAM_PROVENANCE_BLOCKED. No P2/P3/P4 contracts will change. Safe implementation: independent orchestration, strict input/trust/runtime gates, manual workflow with blocked live path, real pinned-byte local full/R2 validation and negative tests. Live contract evolution is a separate proposal, not implemented.

Design gate: implement safe components only. HOSTED_NOT_RUN; no production or automatic research promotion.

## Current external evidence

Public unauthenticated GitHub API confirmed origin Xiangyu-Hu/SPHinXsistant and backup XiangruZhang007/SPHinX_AI_Assistant are public, default branch main. Upstream default branches: SPHinXsys/master, SPHinXsim/main. No org settings, paid allowance or actual hosted execution were accessed.

GitHub's public Runner table lists ubuntu-24.04-arm with 4 CPUs, 16 GB RAM and 14 GB SSD; actual availability/organization policy still needs a hosted run. The official actions/python-versions manifest includes exact 3.12.3 Linux ARM64 tarballs for Ubuntu 22.04 and 24.04. The proposed workflow uses setup-python with 3.12.3/arm64; installation and numerical equivalence are NOT_VERIFIED on a hosted Runner.

Sources: https://docs.github.com/en/actions/reference/runners/github-hosted-runners ; https://github.com/actions/python-versions/blob/main/versions-manifest.json ; https://github.com/actions/setup-python .

## Storage and activation

Candidate outputs use Actions artifacts with explicit 30-day retention (subject to repository policy), unique run ID/attempt names and upload failure treated as job failure. Missing upload does not become remote success. Approved baseline transport is proposed as a separately published immutable release asset with independently pinned SHA-256, logical package checksum, source-state and authorization hash in reviewed registry configuration. Release immutability and availability must be verified before configuring a locator. No remote baseline exists in the initial empty registry. No upload, release creation or pointer change is performed by this task.

Workflow activation requires a separately authorized push/review/merge making this workflow available on the remote default branch. Only then select the trusted baseline key and request a manual run; inspect actual ARM64/runtime, restore evidence and outputs. Local YAML creation does not activate remote Actions. See https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch .

## Implementation boundary

The formal/live profile always fails UPSTREAM_PROVENANCE_BLOCKED until a separately authorized versioned provenance contract is implemented. The implemented pinned-research runner profile runs all structural/full/retrieval checks only with an explicitly authorized P2 anchor; research-parent execution additionally needs a separately bound parent-retrieval anchor and remains unqualified, retaining formal acceptance NOT_ASSESSED and no new research approval. The initial empty registry prevents remote bootstrap. Local experiments authorize only isolated pinned byte copies in the persistent experiment archive, not publication or a global approved pointer.

All frozen algorithm files and fingerprints remain byte-identical. The local P2 bootstrap authorization is a scoped P5A experiment policy, not P4.1 approval. Snapshot transport hashes must come from the reviewed external registry/policy, never extracted package self-declarations.

## Verified-source materialization after restoration

The first isolated P5A restore exposed one executable source file in P2's verified selection whose on-disk permissions are not preserved by the historical P2 source transport. P5A reconstructs a new source copy from verified source bytes and `git_mode`, then requires exact selection equality before generation. It never changes the sealed P2 snapshot or ignores a mode mismatch. The original failed run remains archived; an executable-mode package/restore test covers the correction.

## Actual hash-seed qualification

A clean validation process must preserve `PYTHONHASHSEED=0`. Python `-I`/`-E` ignores that setting, so P5A additionally checks actual interpreter flags (`hash_randomization == 0`, `ignore_environment == 0`). Local qualification uses a minimal explicit environment and `-s -B`, not `-I`; hosted execution uses the normal interpreter with hash seed 0. Matching an environment string alone is insufficient.
