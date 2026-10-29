# P5A.1: real upstream provenance and versioned lineage

## Inspection and decisions (before implementation)

P3 `build_incremental` calls `_read_base`, obtains upstreams from its parent,
and writes `target_is_derived_fixture_not_upstream_commit: true`. Its candidate
ID binds the parent, selection, P2 fingerprint and P3 orchestration. P4
`verify_baseline` requires every ancestor's upstream dictionary to equal the
current dictionary. P5A `provenance_gate` therefore correctly rejects formal
real-commit transitions. Removing that check alone would mislabel provenance.

The P2 build contract does **not** include upstream SHAs. They belong to the
snapshot identity, separately from build compatibility. No change to P2's
fingerprint or extraction is necessary.

Use an additive schema-2 orchestration module and explicit CLI. Preserve all
schema-1 readers and P2/P3/P4.1/P5A fingerprints and behavior. Reuse P3's
classification, changed-file extraction, source copying, serialization and
materialized structural verification; reuse P2's independent full builder and
P4.1's original evidence, rescoring and independent replay. No alternative
discovery, extraction, retrieval or scoring implementation is introduced.

Three separate identities:

* Git provenance: approved repository URL and exact 40-hex commit pair, including
  raw content-addressed commit/tree proofs. Genuine publication additionally
  requires independently frozen public GitHub response hashes in the run policy.
* Source state: `sha256-` plus the canonical digest of the existing ordered
  selection (path, raw bytes hash, blob ID, mode, language, role, component).
  A commit outside indexing scope changes provenance but not this state.
* Build compatibility: the unchanged exact P2 contract digest. New orchestration
  is separately fingerprinted and bound to schema-2 nodes.

Schema 2 binds base and target verified provenance, immediate parent ID/logical
package checksum, generation, complete ancestry receipts, selection contract,
build/orchestration identities and deterministic payload descriptors. Reject
unknown schemas. Legacy artifacts stay readable through original readers and
are never relabeled or rewritten. Only a verified P2 anchor can bootstrap this
version; legacy experimental P4 generations cannot silently cross into it.

Only same-commit and verified fast-forward transitions are supported. Reject
rollback, divergence, missing history and unverifiable relationships explicitly.
Git proofs preserve selected blob correspondence and all intervening directory
tree hashes; stored commit-parent proofs support offline ancestry verification.
Selected documents must belong to the declared tree. External documents are
unsupported and rejected. Symlinks, gitlinks and nonregular selected paths are
rejected. Executable bits are verified and preserved, never silently normalized.

P2 is an explicitly authorized historical anchor, not P4.1-approved. Normal
children require a verified schema-2 approved baseline. An independently hashed,
candidate/parent/state-bound **controlled chain fixture** may be used to test
multi-generation behavior; it never becomes an approved baseline. Candidate
generation cannot approve, publish, update a pointer or promote production.
The separate approval function must use unchanged P4.1 original-evidence checks
and replay for evaluated policies. Structural fixture policies remain explicitly
structural, with quality review required.

The old P5A manual profile and workflow remain intentionally blocked. The new
local CLI is the explicit schema-2 provenance gate; activating hosted execution,
remote bootstrap and storage are subsequent tasks.

## Amendments to the supplied instructions

1. Preserve historical implementation bytes through additive versioning; do not
   replace historical contracts just to update orchestration metadata.
2. Multi-generation candidate chaining is a separately frozen research fixture,
   never automatic P4.1 approval.
3. Git object identity plus a remote URL does not prove publication. Pin public
   commit-response bytes independently of candidate packages for genuine runs.
4. Same/fast-forward only is the initial supported transition policy. Rollback
   and divergence remain explicit rejections, not automatic fallback.

## Validation

Use synthetic Git history for additions, deletion, rename, mode and invalid
provenance cases, clearly labeled controlled fixtures. Independently verify
published commits for genuine changes. Build fresh full references without
incremental records, byte-compare corpus/selection/inventory/native graph, and
run 128 frozen combinations per candidate and reference. Preserve unchanged
historical denominators and ALL_OF oracles; classify applicability separately.
Test different processes/roots and offline relocation. Do not infer hosted
readiness from local verification.
