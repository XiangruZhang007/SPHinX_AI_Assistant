"""Q1 V1: deterministic evidence applicability, never scoring or approval.

Frozen match/outcome functions are read-only dependencies. Source-level mapping
and corpus byte coverage are independent; report fields cannot promote a baseline.
"""
from __future__ import annotations

import base64
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from .corpus_snapshot import (SnapshotError, checked_file, digest, file_digest,
    read_json, read_records, write_json, source_bytes, _source_span, verify_snapshot)
from .incremental_corpus import _lines as corpus_lines, current_contract
from .retrieval_evaluation_criteria import matches
from .retrieval_evaluation_io import validate_structure
from .approval_evidence import rescore, BENCHMARK_SHA, frozen_implementation
from .research_quality import oracle_applicability
from . import upstream_provenance as provenance

VERSION = 'version-aware-evidence-v1'
FILES = ('src/version_aware_evidence.py', 'scripts/version_aware_evidence.py',
         'tests/test_version_aware_evidence.py', 'docs/version_aware_evidence_v1.md')
SUPPORTED = {'line_range', 'source_range', 'passage', 'section_title', 'symbol'}
RESTRICTIONS = {'diagnostic_only': True, 'automatic_research_approval': False,
                'production_promotion': 'NOT_AUTHORIZED', 'official_score_adjustment': False}


def need(ok, message):
    if not ok: raise SnapshotError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def implementation(project):
    dependencies = ('src/retrieval_evaluation_criteria.py', 'src/retrieval_evaluation_io.py',
                    'src/corpus_snapshot.py', 'src/research_quality.py', 'src/approval_evidence.py',
                    'src/upstream_provenance.py')
    return {'version': VERSION, 'files': {n: file_digest(Path(project) / n) for n in (*FILES, *dependencies)}}


def _path(item):
    return 'repos/' + item['repository'] + '/' + item['path']


@dataclass
class State:
    """Verified state view. Controlled fixtures never claim genuine publication."""
    origin: dict
    selection: list
    sources: dict
    records: list
    tree_proofs: dict
    controlled_absences: tuple = ()

    def validate(self):
        need(self.origin.get('verification') in ('VERIFIED_P2_P5A1_PAIR', 'CONTROLLED_TEST_FIXTURE'), 'Unverified source origin')
        need(provenance.state(self.selection) == self.origin['source_state_id'], 'Incorrect source-state identity')
        need(set(self.sources) == {_path(i) for i in self.selection}, 'Incomplete selected-source bytes')
        need(sha(corpus_lines(self.records)) == self.origin['corpus_sha256'], 'Wrong corpus association')
        self.by_path = {_path(i): i for i in self.selection}
        self.by_records = defaultdict(list)
        for item in self.selection:
            raw = self.sources[_path(item)]
            need(sha(raw) == item['source_sha256'] and len(raw) == item['source_bytes'] and
                 provenance._object(raw, 'blob') == item['git_blob'], 'Selected raw bytes/hash/blob mismatch')
        seen = set()
        for record in self.records:
            path = record['source_path']; item = self.by_path.get(path)
            need(item is not None and record['repository'] == item['repository'] and
                 record['source_role'] == item['source_role'], 'Wrong record source/role')
            need(record['chunk_id'] not in seen, 'Duplicate record identity'); seen.add(record['chunk_id'])
            _source_span(record, source_bytes(self.sources[path], item['component'] == 'documentation'))
            self.by_records[path].append(record)
        if self.origin['verification'] == 'VERIFIED_P2_P5A1_PAIR':
            need(not self.controlled_absences and set(self.tree_proofs) == set(provenance.REPOSITORIES), 'Missing genuine tree proofs')
            for repo, proof in self.tree_proofs.items():
                need(proof['repository'] == repo and provenance.verify_proof(proof, self.selection) == self.origin['upstreams'][repo], 'Wrong Git provenance')
        else:
            need(self.origin.get('provenance_type') == 'CONTROLLED_TEST_FIXTURE' and not self.tree_proofs,
                 'A controlled fixture cannot claim genuine Git provenance')
        return self

    def eligible(self, path, mode, role=None):
        return [r for r in self.by_records.get(path, []) if
                (mode != 'documentation_only' or r['source_role'] == 'documentation') and
                (role is None or r['source_role'] == role)]

    def absence(self, path):
        if self.origin['verification'] == 'CONTROLLED_TEST_FIXTURE':
            return 'ABSENT' if path in self.controlled_absences else 'UNKNOWN'
        parts = path.split('/'); proof = self.tree_proofs.get(parts[1])
        if proof is None: return 'UNKNOWN'
        current, _ = provenance._commit(provenance._decoded(proof['commit_raw'], 'commit', proof['commit']))
        for index, name in enumerate(parts[2:]):
            if current not in proof['trees']: return 'UNKNOWN'
            entries = provenance._tree(provenance._decoded(proof['trees'][current], 'tree', current))
            if name not in entries: return 'ABSENT'
            mode, current = entries[name]
            if index == len(parts[2:]) - 1: return 'PRESENT'
            if mode != '40000': return 'UNKNOWN'
        return 'UNKNOWN'


def _coords(raw, documentation):
    normalized = source_bytes(raw, documentation)
    nstarts = [0, *[m.end() for m in re.finditer(b'\n', normalized)]]
    rstarts = [0, *[m.end() for m in re.finditer(b'\n' if documentation else br'\r\n|\r|\n', raw)]]
    need(len(nstarts) == len(rstarts), 'Unsupported source coordinate normalization')
    def n_to_r(offset):
        i = bisect_right(nstarts, offset) - 1
        need(0 <= offset <= len(normalized), 'Invalid normalized offset')
        return rstarts[i] + offset - nstarts[i]
    def r_to_n(offset):
        i = bisect_right(rstarts, offset) - 1
        result = nstarts[i] + offset - rstarts[i]
        need(0 <= offset <= len(raw) and result <= len(normalized) and n_to_r(result) == offset,
             'Locator crosses a newline normalization boundary')
        return result
    return normalized, nstarts, rstarts, n_to_r, r_to_n


def _fragment(state, path, a, b):
    raw = state.sources[path]; doc = state.by_path[path]['component'] == 'documentation'
    normalized, starts, _, _, to_n = _coords(raw, doc)
    na, nb = to_n(a), to_n(b)
    need(0 <= a < b <= len(raw), 'Empty/invalid evidence passage')
    return {'path': path, 'raw_byte_start': a, 'raw_byte_end': b,
            'byte_start': na, 'byte_end': nb, 'offset_basis': 'existing source_bytes UTF8 contract',
            'line_range': [bisect_right(starts, na), bisect_right(starts, max(na, nb - 1))],
            'raw_sha256': sha(raw[a:b]), 'normalized_sha256': sha(normalized[na:nb]),
            'raw_base64': base64.b64encode(raw[a:b]).decode('ascii'), 'text': raw[a:b].decode('utf-8')}


def _range(state, path, interval):
    raw = state.sources[path]; doc = state.by_path[path]['component'] == 'documentation'
    normalized, starts, _, to_r, _ = _coords(raw, doc)
    first, last = interval
    need(1 <= first <= last <= len(starts), 'Source range outside verified file')
    a = starts[first - 1]; b = starts[last] if last < len(starts) else len(normalized)
    if normalized[a:b].endswith(b'\n'):
        b -= 1
        if doc and normalized[a:b].endswith(b'\r'): b -= 1
    return _fragment(state, path, to_r(a), to_r(b))


def _occurrences(raw, needle):
    need(bool(needle), 'Empty evidence anchor')
    result = []; cursor = 0
    while (at := raw.find(needle, cursor)) >= 0:
        result.append((at, at + len(needle))); cursor = at + 1
    return result


def _associations(records, criterion):
    return [{'chunk_id': r['chunk_id'], 'byte_start': r['byte_start'], 'byte_end': r['byte_end'],
             'line_range': [r['start_line'], r['end_line']], 'symbol': r.get('symbol')}
            for r in records if matches(r, criterion)]


def original_anchor(state, criterion, mode):
    kind = criterion.get('kind'); path = criterion['path']
    if kind not in SUPPORTED: return None, [], 'UNSUPPORTED_REQUIREMENT_TYPE'
    if path not in state.sources: return None, [], 'BASE_SOURCE_NOT_SELECTED'
    records = state.eligible(path, mode, criterion.get('role'))
    associated = [r for r in records if matches(r, criterion)]
    if kind in ('line_range', 'source_range') or 'range' in criterion:
        if 'range' not in criterion: return None, [], 'UNSUPPORTED_REQUIREMENT_TYPE'
        try: passage = _range(state, path, criterion['range'])
        except SnapshotError as e: return None, [], str(e)
        if not passage['text'].strip(): return None, [], 'NO_DISTINCT_SOURCE_ANCHOR'
        return passage, [], 'EXACT_FROZEN_LINE_RANGE'
    if kind == 'passage':
        value = criterion.get('value')
        if not isinstance(value, str) or not value: return None, [], 'INVALID_PASSAGE_LOCATOR'
        candidates = []
        for a, b in _occurrences(state.sources[path], value.encode()):
            try: f = _fragment(state, path, a, b)
            except SnapshotError: continue
            if any(r['byte_start'] <= f['byte_start'] and f['byte_end'] <= r['byte_end'] for r in associated): candidates.append(f)
    else:
        raw = state.sources[path]; doc = state.by_path[path]['component'] == 'documentation'
        _, _, _, to_r, _ = _coords(raw, doc)
        spans = sorted({(r['byte_start'], r['byte_end']) for r in associated})
        candidates = [_fragment(state, path, to_r(a), to_r(b)) for a, b in spans]
    if len(candidates) != 1:
        return None, candidates, 'AMBIGUOUS_BASE_ANCHOR' if candidates else 'NO_EXACT_BASE_ANCHOR'
    return candidates[0], [], 'UNIQUE_EXACT_BASE_LOCATOR'


def coverage(state, passage, mode, role=None):
    if passage is None: return {'state': 'NOT_ASSESSED', 'single_chunk_ids': [], 'covering_chunk_ids': [], 'intersections': []}
    a, b = passage['byte_start'], passage['byte_end']
    records = state.eligible(passage['path'], mode, role)
    relevant = sorted((r for r in records if r['byte_start'] < b and r['byte_end'] > a),
                      key=lambda r: (r['byte_start'], -r['byte_end'], r['chunk_id']))
    singles = [r['chunk_id'] for r in relevant if r['byte_start'] <= a and b <= r['byte_end']]
    cursor = a; used = []
    while cursor < b:
        choices = [r for r in relevant if r['byte_start'] <= cursor < r['byte_end']]
        if not choices: break
        best = min(choices, key=lambda r: (-r['byte_end'], r['chunk_id']))
        used.append(best['chunk_id']); cursor = best['byte_end']
    return {'state': 'SINGLE_CHUNK' if singles else ('SPLIT' if cursor >= b else ('PARTIAL' if relevant else 'ABSENT')),
            'single_chunk_ids': singles, 'covering_chunk_ids': singles[:1] if singles else (used if cursor >= b else []),
            'literal_range_byte_coverage': 'COMPLETE' if cursor >= b else 'INCOMPLETE',
            'intersections': [{'chunk_id': r['chunk_id'], 'byte_start': max(a, r['byte_start']), 'byte_end': min(b, r['byte_end']),
                              'record_line_range': [r['start_line'], r['end_line']]} for r in relevant]}


def _target_paths(base, target, path):
    if path in target.sources: return [path], 'SAME_PATH'
    old = base.by_path[path]
    paths = [p for p, item in target.by_path.items() if item['repository'] == old['repository'] and
             item['source_sha256'] == old['source_sha256'] and target.sources[p] == base.sources[path]]
    base_duplicates = [p for p, raw in base.sources.items() if base.by_path[p]['repository'] == old['repository'] and raw == base.sources[path]]
    if len(paths) == 1 and len(base_duplicates) == 1: return paths, 'UNIQUE_IDENTICAL_FILE_BLOB_RELOCATION'
    return paths, 'AMBIGUOUS_FILE_CORRESPONDENCE' if paths else 'NO_SELECTED_PATH_CORRESPONDENCE'


def _contextual_change(base, target, passage, target_path):
    """No similarity threshold: exact, unique, immediately flanking full lines."""
    old_path = passage['path']; interval = passage['line_range']
    # Context replacement is supported only for complete original physical lines.
    if _range(base, old_path, interval) != passage: return None, 'PARTIAL_LINE_CONTEXT_UNSUPPORTED', None
    old_doc = base.by_path[old_path]['component'] == 'documentation'; new_doc = target.by_path[target_path]['component'] == 'documentation'
    old_norm, old_starts, _, _, _ = _coords(base.sources[old_path], old_doc)
    new_norm, new_starts, _, to_r, _ = _coords(target.sources[target_path], new_doc)
    old_lines = old_norm.splitlines(keepends=True); new_lines = new_norm.splitlines(keepends=True)
    first, last = interval
    if first < 2 or last >= len(old_lines): return None, 'MISSING_TWO_SIDED_CONTEXT', None
    left, right = old_lines[first - 2], old_lines[last]
    if not left.strip() or not right.strip() or left == right: return None, 'NON_DISTINCT_CONTEXT', None
    if any(lines.count(anchor) != 1 for lines in (old_lines, new_lines) for anchor in (left, right)):
        return None, 'NON_UNIQUE_CONTEXT', None
    li, ri = new_lines.index(left), new_lines.index(right)
    if li >= ri: return None, 'CONTEXT_ORDER_CHANGED', None
    proof = {'method': 'EXACT_UNIQUE_TWO_SIDED_LINE_CONTEXT', 'left_line_sha256': sha(left), 'right_line_sha256': sha(right),
             'base_anchor_lines': [first - 1, last + 1], 'target_anchor_lines': [li + 1, ri + 1]}
    if ri == li + 1: return None, 'REMOVED', proof
    a = new_starts[li + 1]; b = new_starts[ri]
    if new_norm[a:b].endswith(b'\n'):
        b -= 1
        if new_doc and new_norm[a:b].endswith(b'\r'): b -= 1
    if b <= a: return None, 'REMOVED', proof
    return _fragment(target, target_path, to_r(a), to_r(b)), 'MODIFIED', proof


def map_requirement(base, target, criterion, mode):
    passage, candidates, anchor_reason = original_anchor(base, criterion, mode)
    original_records = base.eligible(criterion['path'], mode, criterion.get('role')) if criterion['path'] in base.sources else []
    result = {'original_passage': passage, 'target_passage': None, 'candidate_passages': candidates,
              'base_file_metadata': base.by_path.get(criterion['path']), 'target_file_metadata': target.by_path.get(criterion['path']),
              'original_chunk_associations': _associations(original_records, criterion),
              'base_corpus_coverage': coverage(base, passage, mode, criterion.get('role')),
              'target_corpus_coverage': coverage(target, None, mode), 'source_content_state': 'UNKNOWN',
              'source_location_state': 'UNKNOWN', 'mapping_uniqueness': 'UNRESOLVED',
              'file_change_state': ('UNCHANGED' if criterion['path'] in base.sources and criterion['path'] in target.sources and base.sources[criterion['path']] == target.sources[criterion['path']] else
                                    'MODIFIED' if criterion['path'] in base.sources and criterion['path'] in target.sources else
                                    'NOT_PRESENT_IN_SELECTED_TARGET' if criterion['path'] in base.sources else 'BASE_SOURCE_NOT_SELECTED'), 'frozen_locator_state': 'NOT_ASSESSED',
              'frozen_criterion_applicability': 'REVIEW_REQUIRED', 'semantic_review': 'NOT_ASSESSED',
              'classification': 'UNRESOLVED', 'mapping_evidence': {'original_anchor': anchor_reason},
              'review_required': True}
    if anchor_reason == 'UNSUPPORTED_REQUIREMENT_TYPE':
        result['classification'] = 'UNSUPPORTED_REQUIREMENT_TYPE'; return result
    if passage is None:
        if candidates: result.update(classification='AMBIGUOUS_MAPPING', mapping_uniqueness='AMBIGUOUS')
        return result
    path = passage['path']; paths, path_reason = _target_paths(base, target, path)
    result['mapping_evidence']['path_correspondence'] = path_reason
    result['mapping_evidence']['candidate_target_paths'] = paths
    needle = base.sources[path][passage['raw_byte_start']:passage['raw_byte_end']]
    found = []
    for p in paths:
        if path_reason == 'SAME_PATH' and base.sources[path] == target.sources[p]:
            found.append(_fragment(target, p, passage['raw_byte_start'], passage['raw_byte_end']))
        else:
            for a, b in _occurrences(target.sources[p], needle):
                try: found.append(_fragment(target, p, a, b))
                except SnapshotError: continue
    result['candidate_passages'] = found
    if path_reason == 'AMBIGUOUS_FILE_CORRESPONDENCE' or len(found) > 1:
        result.update(classification='AMBIGUOUS_MAPPING', mapping_uniqueness='AMBIGUOUS'); return result
    if not paths:
        absence = target.absence(path); result['mapping_evidence']['target_git_path_presence'] = absence
        if absence == 'ABSENT':
            result.update(classification='REMOVED', source_content_state='REMOVED', source_location_state='REMOVED',
                          file_change_state='VERIFIED_REMOVED_FROM_DECLARED_PATH', mapping_uniqueness='UNIQUE',
                          frozen_locator_state='STALE', frozen_criterion_applicability='STALE_LOCATOR', semantic_review='SEMANTIC_REVIEW_REQUIRED')
        return result
    target_path = paths[0]
    result['target_file_metadata'] = target.by_path[target_path]
    result['file_change_state'] = ('RENAMED_IDENTICAL_BYTES' if path_reason != 'SAME_PATH' else
                                  ('UNCHANGED' if base.sources[path] == target.sources[target_path] else 'MODIFIED'))
    if found:
        mapped = found[0]; content_state = 'UNCHANGED'; method = 'EXACT_RAW_PASSAGE'
    else:
        mapped, content_state, proof = _contextual_change(base, target, passage, target_path)
        result['mapping_evidence']['context'] = proof
        result['mapping_evidence']['context_result'] = content_state
        if content_state not in ('MODIFIED', 'REMOVED'): return result
        if content_state == 'REMOVED':
            result.update(classification='REMOVED', source_content_state='REMOVED', source_location_state='REMOVED', mapping_uniqueness='UNIQUE',
                          frozen_locator_state='STALE', frozen_criterion_applicability='STALE_LOCATOR', semantic_review='SEMANTIC_REVIEW_REQUIRED'); return result
        method = 'EXACT_CONTEXTUAL_REPLACEMENT'
    moved = mapped['path'] != passage['path'] or mapped['line_range'] != passage['line_range'] or mapped['raw_byte_start'] != passage['raw_byte_start']
    result.update(target_passage=mapped, source_content_state=content_state, source_location_state='MOVED' if moved else 'UNCHANGED',
                  mapping_uniqueness='UNIQUE', semantic_review='SEMANTIC_REVIEW_REQUIRED' if content_state == 'MODIFIED' else 'NOT_REQUIRED')
    result['mapping_evidence']['correspondence_method'] = method
    target_records = target.eligible(target_path, mode, criterion.get('role'))
    result['target_corpus_coverage'] = cov = coverage(target, mapped, mode, criterion.get('role'))
    strict_matches = _associations(target_records, criterion)
    result['target_frozen_chunk_associations'] = strict_matches
    if content_state == 'MODIFIED': locator = 'REVIEW_REQUIRED'
    elif mapped['path'] != path: locator = 'STALE'
    elif 'range' in criterion:
        try: still = _range(target, path, criterion['range'])
        except SnapshotError: still = None
        locator = 'VALID' if still is not None and still['raw_sha256'] == passage['raw_sha256'] and still['line_range'] == mapped['line_range'] else 'STALE'
    else:
        locator = 'VALID' if any(s['byte_start'] <= mapped['byte_start'] and mapped['byte_end'] <= s['byte_end'] for s in strict_matches) else 'REVIEW_REQUIRED'
    result['frozen_locator_state'] = locator
    applicability = ('STALE_LOCATOR' if locator == 'STALE' else 'REVIEW_REQUIRED' if locator != 'VALID' else
                     'APPLICABLE' if strict_matches else
                     'SINGLE_CHUNK_UNSATISFIED' if cov['state'] == 'SPLIT' else
                     'PARTIAL_LITERAL_SOURCE_COVERAGE' if cov['state'] == 'PARTIAL' else 'EVIDENCE_NOT_INDEXED')
    result['frozen_criterion_applicability'] = applicability
    result['frozen_predicate_has_matches'] = bool(strict_matches)
    result['frozen_match_vs_literal_coverage'] = ('FROZEN_MATCH_WITH_PARTIAL_LITERAL_RANGE_BYTES'
        if strict_matches and cov['state'] == 'PARTIAL' else 'NO_PREDICATE_BYTE_COVERAGE_DISCREPANCY')
    classification = ('MODIFIED' if content_state == 'MODIFIED' else 'UNRESOLVED' if cov['state'] in ('ABSENT', 'PARTIAL') else
                      'SPLIT' if cov['state'] == 'SPLIT' else 'MOVED' if moved else 'UNCHANGED')
    result['classification'] = classification
    result['fragmentation_preexisted'] = cov['state'] == 'SPLIT' and result['base_corpus_coverage']['state'] == 'SPLIT'
    result['review_required'] = applicability != 'APPLICABLE' or classification not in ('UNCHANGED', 'MOVED')
    return result


def inventory(spec, base):
    rows = []
    for q in spec['questions']:
        need(q['evidence_mode'] == 'ALL_OF', 'Unsupported evidence operator')
        for mode in ('combined', 'documentation_only') if q['group'] == 'A' else ('combined',):
            for index, c in enumerate(q['requirements']):
                locator_id = 'sha256-' + digest({'benchmark_id': spec['benchmark_id'], 'criterion': c})
                rows.append({'benchmark_id': spec['benchmark_id'], 'question_id': q['id'], 'mode': mode, 'requirement_index': index,
                    'locator_id': locator_id, 'requirement_id': 'sha256-' + digest([locator_id, q['id'], mode, index]),
                    'repository': c['path'].split('/')[1], 'source_path': c['path'], 'frozen_requirement': c,
                    'evidence_operator': 'ALL_OF', 'base_source_state_id': base.origin['source_state_id'],
                    'base_corpus_sha256': base.origin['corpus_sha256']})
    return rows


def analyze(spec, base, target, *, benchmark_sha256, implementation_contract, base_rows=None, target_rows=None):
    base.validate(); target.validate()
    need(base.origin['verification'] == target.origin['verification'], 'Mixed genuine and controlled inputs')
    need(base.origin['build_fingerprint'] == target.origin['build_fingerprint'], 'Incompatible evidence states')
    entries = inventory(spec, base)
    original_outcomes = {(r['mode'], r['id']): r for r in base_rows or []}
    target_outcomes = {(r['mode'], r['id']): r for r in target_rows or []}
    memo = {}; results = []
    for item in entries:
        c = item['frozen_requirement']; cache_key = (item['locator_id'], item['mode'])
        if cache_key not in memo: memo[cache_key] = map_requirement(base, target, c, item['mode'])
        detail = memo[cache_key]
        outcome_rows = {}
        for label, lookup in [('base', original_outcomes), ('target', target_outcomes)]:
            r = lookup.get((item['mode'], item['question_id']))
            outcome_rows[label] = None if r is None else {'strict': r['strict'], 'evidence_available': r['evidence_available']}
        ids = set(detail['target_corpus_coverage']['single_chunk_ids'])
        row = target_outcomes.get((item['mode'], item['question_id']))
        observed = None if row is None else [x['rank'] for x in row['top_10'] if x['chunk_id'] in ids]
        results.append({**item, **detail, 'target_source_state_id': target.origin['source_state_id'],
                        'base_upstreams': base.origin['upstreams'], 'target_upstreams': target.origin['upstreams'],
                        'target_corpus_sha256': target.origin['corpus_sha256'],
                        'frozen_question_outcomes': outcome_rows, 'diagnostic_mapped_single_chunk_top10_ranks': observed})
    counts = Counter(r['classification'] for r in results)
    summary = {'question_mode_combinations': len({(r['mode'], r['question_id']) for r in entries}),
               'evidence_requirement_instances': len(entries), 'unique_frozen_locators': len({r['locator_id'] for r in entries}),
               'supported_instances': sum(r['classification'] != 'UNSUPPORTED_REQUIREMENT_TYPE' for r in results),
               'unsupported_instances': counts['UNSUPPORTED_REQUIREMENT_TYPE'],
               'uniquely_mapped_instances': sum(r['mapping_uniqueness'] == 'UNIQUE' for r in results),
               'ambiguous_instances': counts['AMBIGUOUS_MAPPING'], 'unresolved_instances': counts['UNRESOLVED'],
               'classification_distribution': dict(sorted(counts.items())),
               'semantic_review_instances': sum(r['semantic_review'] == 'SEMANTIC_REVIEW_REQUIRED' for r in results),
               'applicability_review_instances': sum(r['review_required'] for r in results),
               'source_content_distribution': dict(sorted(Counter(r['source_content_state'] for r in results).items())),
               'source_location_distribution': dict(sorted(Counter(r['source_location_state'] for r in results).items())),
               'target_corpus_coverage_distribution': dict(sorted(Counter(r['target_corpus_coverage']['state'] for r in results).items()))}
    logical = {'schema_version': 1, 'version': VERSION, 'benchmark_id': spec['benchmark_id'], 'benchmark_sha256': benchmark_sha256,
               'base': base.origin, 'target': target.origin, 'implementation': implementation_contract,
               'archived_rows_digests': {'base': None if base_rows is None else digest(base_rows), 'target': None if target_rows is None else digest(target_rows)},
               'summary': summary, 'requirements': results, **RESTRICTIONS}
    return {'report_id': 'sha256-' + digest(logical), **logical}


def markdown(report):
    s = report['summary']
    lines = ['# Q1 V1 evidence applicability diagnostics', '', 'Diagnostic only. Frozen outcomes remain unchanged; no adjusted official Hit@K.', '',
             'Report ID: `' + report['report_id'] + '`', '',
             f"{s['question_mode_combinations']} question/mode combinations; {s['evidence_requirement_instances']} requirement instances; {s['unique_frozen_locators']} exact unique frozen locators.", '',
             '| Classification | Requirement instances |', '|---|---:|']
    lines += [f'| {k} | {v} |' for k, v in s['classification_distribution'].items()]
    lines += ['', f"Ambiguous: {s['ambiguous_instances']}; unresolved: {s['unresolved_instances']}; semantic review: {s['semantic_review_instances']}; applicability review: {s['applicability_review_instances']}.", '', '## DOC-Q27', '']
    for r in report['requirements']:
        if r['question_id'] != 'DOC-Q27': continue
        a, b = r['original_passage'], r['target_passage']
        lines += [f"- {r['mode']}: {r['classification']}; original range {None if a is None else a['line_range']}; target range {None if b is None else b['line_range']}; coverage {r['target_corpus_coverage']['state']}; frozen outcomes " + str(r['frozen_question_outcomes'])]
    lines += ['', 'All unsupported, ambiguous and unresolved requirements remain in the denominator. Modified text never implies semantic equivalence. No approval or production privilege is provided.', '']
    return '\n'.join(lines)


def load_inputs(project, manifest_path, expected_sha256):
    """Fail-closed production boundary. No API calls or new retrieval executions."""
    project, manifest_path = Path(project), Path(manifest_path)
    need(not manifest_path.is_symlink() and file_digest(manifest_path) == expected_sha256, 'Independent input-manifest checksum mismatch')
    config = read_json(manifest_path); root = manifest_path.parent
    need(config.get('schema_version') == 1 and config.get('kind') == 'q1-verified-inputs', 'Unsupported Q1 input contract')
    paths = {k: checked_file(root, v) if k not in ('base', 'target') else root / v for k, v in config['paths'].items()}
    for k in ('base', 'target'):
        p = paths[k]; need(not p.is_symlink() and p.resolve().is_relative_to(root.resolve()) and p.is_dir(), 'Unsafe input directory')
    pins = config['pins']; fp = pins['build_fingerprint']
    need(digest(current_contract(project)) == fp, 'Incompatible current builder')
    base_info = verify_snapshot(paths['base'], fp)
    need(base_info['snapshot_id'] == pins['base_snapshot_id'] and base_info['build_compatibility'] == 'COMPATIBLE', 'Incorrect base snapshot')
    target_info = provenance.verify(project, paths['target'], fp, expected_id=pins['target_candidate_id'])
    m = read_json(paths['target'] / 'upstream-candidate.json')['identity']
    base_core = read_json(paths['base'] / 'manifest.json')['identity']
    need(m['base_snapshot_id'] == base_info['snapshot_id'] and m['base_upstreams'] == base_core['upstreams'], 'Wrong authoritative version pair')
    need(target_info['provenance_type'] == 'GENUINE_UPSTREAM', 'Q1 historical analysis requires genuine upstream inputs')
    proofs = read_json(paths['target'] / 'git-proofs.json')
    states = []
    for label, info in [('base', base_info), ('target', target_info)]:
        folder = paths[label]; selection = read_json(folder / 'selection.json')
        state_id = provenance.state(selection)
        need(state_id == pins[label + '_source_state_id'] == m['base_source_state' if label == 'base' else 'target_source_state'], 'Incorrect source-state identity')
        records = read_records(folder / 'corpus.jsonl')
        docs = read_records(folder / 'components/documentation.jsonl')
        need([r for r in records if r['source_role'] == 'documentation'] == docs, 'Documentation subset mismatch')
        for name, file in [('corpus_sha256', folder / 'corpus.jsonl'), ('documentation_sha256', folder / 'components/documentation.jsonl')]:
            need(file_digest(file) == pins[label + '_' + name], 'Wrong target/base corpus checksum')
        upstreams = base_core['upstreams'] if label == 'base' else m['upstreams']
        need(upstreams == pins[label + '_upstreams'], 'Incorrect pinned upstream pair')
        sources = {_path(i): checked_file(folder, ('sources/' + i['repository'] + '/' + i['path']) if label == 'base' else 'input/' + _path(i)).read_bytes() for i in selection}
        origin = {'verification': 'VERIFIED_P2_P5A1_PAIR', 'provenance_type': 'GENUINE_UPSTREAM',
                  'source_state_id': state_id, 'upstreams': upstreams, 'repository_identities': provenance.REPOSITORIES,
                  'package_id': pins['base_snapshot_id' if label == 'base' else 'target_candidate_id'],
                  'build_fingerprint': fp, 'corpus_sha256': pins[label + '_corpus_sha256'],
                  'documentation_sha256': pins[label + '_documentation_sha256'],
                  'archived_rows_sha256': pins[label + '_rows_sha256']}
        states.append(State(origin, selection, sources, records, proofs[label]).validate())
    spec = read_json(paths['benchmark']); validate_structure(spec)
    need(file_digest(paths['benchmark']) == pins['benchmark_sha256'] == BENCHMARK_SHA, 'Frozen benchmark mismatch')
    lock = read_json(project / 'configs/ci_v2_runtime_lock.json')
    frozen_implementation(project, paths['benchmark'], lock['quality_policy'])
    rows = {}
    for label, state in zip(('base', 'target'), states):
        need(file_digest(paths[label + '_rows']) == pins[label + '_rows_sha256'], 'Archived result checksum mismatch')
        rows[label] = read_json(paths[label + '_rows'])
        rescore(rows[label], spec, state.records, [r for r in state.records if r['source_role'] == 'documentation'])
    return spec, states[0], states[1], rows, pins


def generate(project, manifest_path, expected_sha256):
    spec, base, target, rows, pins = load_inputs(project, manifest_path, expected_sha256)
    return analyze(spec, base, target, benchmark_sha256=pins['benchmark_sha256'], implementation_contract=implementation(project),
                   base_rows=rows['base'], target_rows=rows['target'])


def write_report(output, report):
    output = Path(output)
    need(not output.exists() and not output.is_symlink() and output.parent.is_dir(), 'Report output must be new')
    output.mkdir()
    write_json(output / 'version_aware_evidence_report.json', report)
    (output / 'summary.md').write_text(markdown(report), encoding='utf-8')
    write_json(output / 'checksums.json', {n: file_digest(output / n) for n in ('version_aware_evidence_report.json', 'summary.md')})


def verify_report(project, manifest_path, expected_sha256, report_root):
    report_root = Path(report_root); checks = read_json(checked_file(report_root, 'checksums.json'))
    need(set(checks) == {'version_aware_evidence_report.json', 'summary.md'} and
         {p.relative_to(report_root).as_posix() for p in report_root.rglob('*') if p.is_file()} == set(checks) | {'checksums.json'}, 'Invalid report payload')
    for n, h in checks.items(): need(file_digest(checked_file(report_root, n)) == h, 'Report checksum mismatch')
    report = read_json(report_root / 'version_aware_evidence_report.json')
    regenerated = generate(project, manifest_path, expected_sha256)
    need(digest(report) == digest(regenerated) and (report_root / 'summary.md').read_bytes() == markdown(regenerated).encode(), 'Report regeneration mismatch')
    return {'status': 'VERIFIED', 'report_id': report['report_id'], **RESTRICTIONS}


def analyze_to(project, manifest_path, expected_sha256, output):
    output = Path(output)
    for protected in (Path(project), Path(manifest_path).parent):
        need(not output.resolve().is_relative_to(protected.resolve()) and
             not protected.resolve().is_relative_to(output.resolve()), 'Diagnostic output overlaps protected inputs/project')
    report = generate(project, manifest_path, expected_sha256)
    write_report(output, report)
    return report
