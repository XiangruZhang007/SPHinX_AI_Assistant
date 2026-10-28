"""Frozen evaluation matching only; never used for retrieval or ranking."""
def matches(record, criterion):
    if record['source_path'] != criterion['path']:
        return False
    if criterion.get('role') and record['source_role'] != criterion['role']:
        return False
    if criterion.get('kind') == 'section_title':
        value = str(record.get('section_title') or record.get('symbol') or '')
        if criterion['value'].casefold() not in value.casefold():
            return False
    elif criterion.get('kind') == 'passage':
        if criterion['value'].casefold() not in record['content'].casefold():
            return False
    elif criterion.get('kind') == 'symbol':
        if record.get('symbol') != criterion['symbol']:
            return False
    if 'range' in criterion:
        a, b = criterion['range']
        if not record['start_line'] <= a <= b <= record['end_line']:
            return False
    return all(text in record['content'] for text in criterion.get('contains_all', []))


def union_covers(records, path, start, end):
    cursor = start
    for a, b in sorted((max(start, r['start_line']), min(end, r['end_line']))
                       for r in records if r['source_path'] == path
                       and r['end_line'] >= start and r['start_line'] <= end):
        if a > cursor:
            return False
        cursor = max(cursor, b + 1)
    return cursor > end


def outcome(records, requirements):
    ranks = [next((i for i, r in enumerate(records, 1) if matches(r, c)), None)
             for c in requirements]
    result = {'evidence_ranks': ranks}
    for k in (5, 10):
        hit = sum(rank is not None and rank <= k for rank in ranks)
        result['hit_at_' + str(k)] = hit == len(requirements)
        result['partial_coverage_at_' + str(k)] = hit / len(requirements)
    result['reciprocal_rank_at_5_single_only'] = ((1 / ranks[0]) if ranks[0] and ranks[0] <= 5 else 0) if len(ranks) == 1 else None
    return result
