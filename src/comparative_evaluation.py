"""Step 1.8B evaluation only: source proofs and frozen annotation scoring.

Never imported by the Retriever or Skill. Retrieval consumes question text only;
these source-bound requirements are used in availability/scoring after selection.
"""
from __future__ import annotations
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any
from src.skill_guided_retrieval.scope import checked_path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(value: Any) -> str:
    return sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,sort_keys=True,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def intervals_union(intervals):
    out=[]
    for start,end in sorted(intervals):
        if start>=end:continue
        if out and start<=out[-1][1]:out[-1][1]=max(out[-1][1],end)
        else:out.append([start,end])
    return out


def covers(intervals,start,end):
    return any(a<=start and end<=b for a,b in intervals_union(intervals))


def intersection(intervals,annotations):
    return intervals_union((max(a,c),min(b,d)) for a,b in intervals for c,d in annotations if max(a,c)<min(b,d))


def physical_lines(raw):
    """LF physical lines, preserving CRLF and final unterminated bytes exactly."""
    pieces=raw.split(b'\n');lines=[];offset=0
    for i,piece in enumerate(pieces):
        if i==len(pieces)-1 and not piece:break
        end=offset+len(piece)+(i<len(pieces)-1)
        lines.append((offset,end));offset=end
    return lines


class Sources:
    """Trusted evaluation/oracle audit, not a query-time Skill authorization bypass."""
    def __init__(self,project):self.project=Path(project).resolve();self.cache={}

    def read(self,repository,path):
        if repository not in {'SPHinXsys','SPHinXsim'}:raise ValueError('Unknown repository')
        key=(repository,path)
        if key not in self.cache:
            f=checked_path(self.project/'repos'/repository,path)
            self.cache[key]=f.read_bytes()
        return self.cache[key]

    @staticmethod
    def split_path(repository,path):
        if path.startswith('repos/'):
            prefix='repos/'+repository+'/'
            if not path.startswith(prefix):raise ValueError('Repository/path identity mismatch')
            path=path[len(prefix):]
        p=PurePosixPath(path)
        if not path or p.is_absolute() or '..' in p.parts or '\\' in path or '\0' in path:raise ValueError('Unsafe source path')
        return p.as_posix()

    def validate(self,record,kind):
        """Return evidence's *actual* byte interval, never inferred whole-line coverage."""
        if kind=='skill' and record.get('_representation')=='returned-reference-snippet':
            return self.validate(record,'bm25')
        repository=record.get('repository');path=record.get('path') if kind=='skill' else record.get('source_path')
        text=record.get('text') if kind=='skill' else record.get('content')
        proof={'valid':False,'repository':repository,'path':path,'evidence_id':record.get('evidence_id',record.get('chunk_id'))}
        try:
            path=self.split_path(repository,path);proof['path']=path
            raw=self.read(repository,path);lines=physical_lines(raw)
            supplied=text.encode('utf-8');location=record.get('location',{})
            start=location.get('start_line') if kind=='skill' else record.get('start_line')
            end=location.get('end_line') if kind=='skill' else record.get('end_line')
            if type(start) is not int or type(end) is not int or not 1<=start<=end<=len(lines):raise ValueError('Invalid line metadata')
            if kind=='skill':
                a,b=lines[start-1][0],lines[end-1][1]
                if raw[a:b]!=supplied:raise ValueError('Returned lines differ from original bytes')
                if record.get('sha256')!=sha256(raw):raise ValueError('Skill source checksum mismatch')
                citation=f'repos/{repository}/{path}:L{start}-L{end}'
                if record.get('citation')!=citation:raise ValueError('Skill citation/location mismatch')
                method='exact-original-physical-lines'
            else:
                a,b=record.get('byte_start'),record.get('byte_end')
                usable=type(a) is int and type(b) is int and 0<=a<b<=len(raw)
                if usable:
                    if raw[a:b]!=supplied:raise ValueError('Content differs from declared original bytes')
                    method='exact-original-byte-range'
                else:
                    window_start,window_end=lines[start-1][0],lines[end-1][1]
                    window=raw[window_start:window_end];first=window.find(supplied)
                    if not supplied or first<0 or window.find(supplied,first+1)>=0:raise ValueError('Absent/unusable bytes; exact line-window match absent or ambiguous')
                    a=window_start+first;b=a+len(supplied);method='unique-exact-substring-within-reported-lines'
                    proof['limitation']='Byte metadata unavailable/unusable; unique source substring checked within reported line window.'
                actual_start=raw[:a].count(b'\n')+1;actual_end=raw[:b-1].count(b'\n')+1
                if not start<=actual_start<=actual_end<=end:raise ValueError('Line metadata does not contain actual byte span')
                proof['line_metadata_policy']='Reported inclusive lines contain exact byte span; no extra reported line is credited as returned evidence.'
            proof.update(valid=True,byte_start=a,byte_end=b,method=method,source_sha256=sha256(raw),returned_characters=len(text))
        except (OSError,ValueError,UnicodeError,TypeError,KeyError) as e:
            proof['error']=type(e).__name__+': '+str(e)
        return proof

    def anchors(self,repository,path,start,end):
        raw=self.read(repository,path);lines=physical_lines(raw)
        if not 1<=start<=end<=len(lines):raise ValueError('Oracle line range outside source')
        spans=[]
        for a,b in lines[start-1:end]:
            fragment=raw[a:b];left=len(fragment)-len(fragment.lstrip());right=len(fragment.rstrip())
            if right>left:spans.append({'byte_start':a+left,'byte_end':a+right,'text':raw[a+left:a+right].decode('utf-8')})
        if not spans:raise ValueError('Oracle has no non-whitespace source anchors')
        return {'repository':repository,'path':path,'start_line':start,'end_line':end,'source_sha256':sha256(raw),'anchors':spans}


def proof_intervals(proofs):
    grouped={}
    for p in proofs:
        if p['valid']:grouped.setdefault((p['repository'],p['path']),[]).append((p['byte_start'],p['byte_end']))
    return {key:intervals_union(v) for key,v in grouped.items()}


def component_result(component,intervals):
    """ALL_OF pieces per option; ANY_OF frozen alternative options per component."""
    options=[]
    for option in component['alternatives']:
        flags=[]
        for piece in option:
            available=intervals.get((piece['repository'],piece['path']),[])
            flags.extend(covers(available,a['byte_start'],a['byte_end']) for a in piece['anchors'])
        options.append({'complete':all(flags),'anchors_returned':sum(flags),'anchors_required':len(flags),
                        'anchor_fraction':sum(flags)/len(flags)})
    best=max(options,key=lambda x:(x['complete'],x['anchor_fraction']))
    return {'id':component['id'],'satisfied':any(x['complete'] for x in options),'best_anchor_fraction':best['anchor_fraction'],'alternatives':options}


def annotation_intervals(question):
    grouped={}
    # Positive relevance annotations are frozen source spans, not retrospective topic judgments.
    for component in question['components']:
        for option in component['alternatives']:
            for piece in option:
                grouped.setdefault((piece['repository'],piece['path']),[]).extend((a['byte_start'],a['byte_end']) for a in piece['anchors'])
    return {k:intervals_union(v) for k,v in grouped.items()}


def score(question,records,kind,sources,returned_stream=None):
    proofs=[sources.validate(r,kind) for r in records]
    spans=proof_intervals(proofs)
    components=[component_result(c,spans) for c in question['components']]
    gold=annotation_intervals(question);relevant=0;relevant_chars=0;all_chars=0;files=set()
    unit_details=[]
    for record,proof in zip(records,proofs):
        text=record['text'] if kind=='skill' else record['content'];all_chars+=len(text)
        positive=False;count=0
        if proof['valid']:
            key=(proof['repository'],proof['path']);files.add(key);raw=sources.read(*key)
            overlap=intersection([[proof['byte_start'],proof['byte_end']]],gold.get(key,[]))
            count=sum(len(raw[a:b].decode('utf-8')) for a,b in overlap)
            positive=any(covers([[proof['byte_start'],proof['byte_end']]],a,b) for a,b in gold.get(key,[]))
        relevant+=positive;relevant_chars+=count
        unit_details.append({'evidence_id':proof['evidence_id'],'annotated_relevant':positive,'annotated_relevant_characters':count})
    if returned_stream is not None:
        relevant_chars=0;all_chars=0
        for record in returned_stream:
            text=record['text'] if kind=='skill' else record['content'];all_chars+=len(text)
            proof=sources.validate(record,kind)
            if proof['valid']:
                key=(proof['repository'],proof['path']);raw=sources.read(*key)
                relevant_chars+=sum(len(raw[a:b].decode('utf-8')) for a,b in intersection(
                    [[proof['byte_start'],proof['byte_end']]],gold.get(key,[])))
    n=len(components);hits=sum(x['satisfied'] for x in components)
    # Required source sets use one complete alternative per component; full-file presence is a separate diagnostic.
    file_presence=all(any(all((p['repository'],p['path']) in files for p in option) for option in c['alternatives']) for c in question['components'])
    return {'complete_evidence':hits==n,'components_satisfied':hits,'components_required':n,'component_fraction':hits/n,
        'partial_evidence':any(x['best_anchor_fraction']>0 for x in components) and hits<n,
        'all_required_files_present':file_presence,
        'multi_file_evidence_complete':hits==n if question['multi_file_required'] else None,
        'citation_valid':sum(p['valid'] for p in proofs),'citation_total':len(proofs),
        'citation_accuracy':sum(p['valid'] for p in proofs)/len(proofs) if proofs else None,
        'evidence_unit_precision':relevant/len(records) if records else None,
        'source_character_precision':relevant_chars/all_chars if all_chars else None,
        'precision_interpretation':'Frozen positive-source-annotation coverage, not exhaustive semantic relevance; unannotated alternatives may be relevant.',
        'returned_evidence_characters':all_chars,'annotated_relevant_characters':relevant_chars,'retained_units':len(records),'source_files':len(files),
        'components':components,'citation_proofs':proofs,'unit_precision_details':unit_details}


def availability(question,records,sources,allowed=None):
    proofs=[sources.validate(r,'bm25') for r in records] if records is not None else []
    if records is None:
        spans={}
        for component in question['components']:
            for option in component['alternatives']:
                for p in option:
                    key=(p['repository'],p['path'])
                    if key in allowed:spans[key]=[[0,len(sources.read(*key))]]
    else:spans=proof_intervals(proofs)
    details=[component_result(c,spans) for c in question['components']]
    if all(x['satisfied'] for x in details):status='EVIDENCE_AVAILABLE'
    elif any(x['best_anchor_fraction']>0 for x in details):status='PARTIALLY_AVAILABLE'
    elif records is None:status='OUT_OF_SCOPE'
    elif any(not p['valid'] for p in proofs):status='UNCERTAIN'
    else:status='NOT_INDEXED'
    return {'status':status,'components':details,'invalid_corpus_records':sum(not p['valid'] for p in proofs)}


def whole_line_budget(records,kind,sources,limit):
    """Post-retrieval diagnostic only: fixed order, original complete lines, no added source."""
    out=[];used=0
    for record in records:
        proof=sources.validate(record,kind)
        if not proof['valid']:continue
        raw=sources.read(proof['repository'],proof['path']);kept=[];blocked=False
        for a,b in physical_lines(raw):
            if a<proof['byte_start'] or b>proof['byte_end']:continue
            length=len(raw[a:b].decode('utf-8'))
            if used+length>limit:blocked=True;break
            kept.append((a,b));used+=length
        if kept:
            a,b=kept[0][0],kept[-1][1];start=raw[:a].count(b'\n')+1;end=raw[:b-1].count(b'\n')+1
            out.append({'chunk_id':proof['evidence_id']+'-bounded','repository':proof['repository'],
                'source_path':'repos/'+proof['repository']+'/'+proof['path'],'content':raw[a:b].decode('utf-8'),
                'byte_start':a,'byte_end':b,'start_line':start,'end_line':end})
        if used==limit or blocked:break
    return out


def skill_evidence_stream(session):
    """Recover actually returned texts, including static-import snippets, without new reads."""
    session=Path(session);state=json.loads((session/'state.json').read_text());stream=[]
    manifest={(f['repository'],f['path']):f for f in state['manifest']['files']}
    for trace in state['trace']:
        response=json.loads((session/'operations'/f"{trace['sequence']:04d}.json").read_text());result=response['result']
        if result.get('evidence'):stream.append(result['evidence'])
        stream.extend(result.get('matches',[]))
        for ref in result.get('references',[]):
            text=ref.get('evidence')
            if not text:continue # A path/name without returned source content is navigation only.
            repo=trace['parameters']['repo'];path=trace['parameters']['path'];start=ref['line'];end=start+text.count('\n')
            entry=manifest[repo,path]
            identity={'repository':repo,'path':path,'sha256':entry['sha256'],'start_line':start,'end_line':end,'text':text}
            identifier='reference-'+canonical_sha256(identity)
            stream.append({'_representation':'returned-reference-snippet','evidence_id':identifier,'chunk_id':identifier,
               'repository':repo,'path':path,'source_path':f'repos/{repo}/{path}','sha256':entry['sha256'],
               'start_line':start,'end_line':end,'text':text,'content':text,'method':'returned-static-reference-source',
               'citation':f'repos/{repo}/{path}:L{start}-L{end}'})
    retained={}
    for evidence in stream:retained.setdefault(evidence['evidence_id'],evidence)
    return list(retained.values()),stream
