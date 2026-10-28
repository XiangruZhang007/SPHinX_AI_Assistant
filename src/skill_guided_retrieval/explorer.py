"""Persistent, single-writer sessions shared by independent tool invocations."""
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
import datetime
import json
import os
import re
import tempfile
import time
from .formats import recognize, text_enabled
from .scope import AccessPolicy, build_scopes, canonical, digest, validate_output
from .navigation import section_range, symbols, references, inspect_structure


def write_json(path,value):
    path=Path(path)
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as f:
        json.dump(value,f,indent=2,ensure_ascii=False,sort_keys=True);f.write('\n');temp=f.name
    os.replace(temp,path)


def terms(query):
    ignored={'how','does','the','what','which','where','and','with','from','for','are','can','use','using','sphinxsys','sphinxsim','explain'}
    return sorted({x.lower() for x in re.findall(r'[A-Za-z_][A-Za-z0-9_]*',query) if x.lower() not in ignored and len(x)>1})


class BudgetExhausted(Exception):pass


class Session:
    @classmethod
    def create(cls,project,output,scope='curated',budget=None):
        if scope not in ('curated','expanded'):raise ValueError('Unknown scope')
        output=validate_output(project,output)
        if output.exists() or output.is_symlink():raise FileExistsError('Session output already exists')
        audit=build_scopes(project);limits=dict(audit['config']['default_budget'])
        if budget:
            if set(budget)-set(limits):raise ValueError('Unknown budget field')
            limits.update(budget)
        if any(type(v) is not int or v<0 for v in limits.values()):raise ValueError('Budget values must be nonnegative integers')
        output.mkdir(parents=True,exist_ok=False);(output/'operations').mkdir();(output/'runtime').mkdir()
        manifest=audit['manifests'][scope]
        state={'schema_version':1,'project':str(Path(project).resolve()),'scope':scope,'manifest':manifest,'budget':limits,
            'used':{'search_operations':0,'read_operations':0,'returned_characters':0},'files_explored':[],
            'evidence':{},'trace':[],'reference_depths':{},'max_source_bytes':audit['config']['max_source_bytes']}
        write_json(output/'state.json',state)
        write_json(output/'scope-audit.json',audit)
        write_json(output/'execution-metadata.json',{'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'scope_manifest_sha256':manifest['manifest_sha256'],'administrative_manifest_scan_bytes':audit['administrative_manifest_scan_bytes'],
            'manifest_scan_cost':'Trusted query-independent setup, not counted as question search/read operations; measured separately for future amortization.',
            'runtime_model':'Deterministic tools. Agent planning quality not evaluated.'})
        return cls(output)

    def __init__(self,output):
        self.output=Path(output).resolve();state=json.loads((self.output/'state.json').read_text())
        # Re-validate stored destination on every resume; never write upstreams.
        validate_output(state['project'],self.output)
        self.state=state

    @contextmanager
    def lock(self):
        lock=self.output/'.operation-lock'
        try:
            with lock.open('x') as f:f.write('single writer\n')
        except FileExistsError:raise RuntimeError('Session busy; concurrent writers are not supported')
        try:yield
        finally:lock.unlink()

    def _charge(self,key):
        if self.state['used'][key]>=self.state['budget'][key]:raise BudgetExhausted(key)
        self.state['used'][key]+=1

    def _source(self,repo,path):
        entry=self.policy.entry(repo,path);key=repo+'/'+entry['path']
        if key not in self.state['files_explored']:
            if len(self.state['files_explored'])>=self.state['budget']['files_explored']:raise BudgetExhausted('files_explored')
            self.state['files_explored'].append(key)
        entry,text=self.policy.read(repo,path)
        self.current['source_accesses'].append({'repository':repo,'path':entry['path'],'characters_scanned':len(text),'sha256':entry['sha256']})
        return entry,text

    def _emit(self,entry,text,start,end,method,extra=None,char_limit=None,location=None):
        remaining=self.state['budget']['returned_characters']-self.state['used']['returned_characters']
        maximum=min(remaining,char_limit) if char_limit is not None else remaining
        original_lines=text.splitlines(keepends=True)
        if location is None:
            if type(start) is not int or type(end) is not int or start<1 or end<start or end>len(original_lines):raise ValueError('Invalid source line range')
            selected=original_lines[start-1:end]
        else:selected=original_lines
        kept=[];size=0
        for line in selected:
            if size+len(line)>maximum:break
            kept.append(line);size+=len(line)
        truncated=len(kept)<len(selected)
        if truncated:self.current['truncations'].append('character budget or section size; complete lines only')
        if not kept:
            return {'evidence':None,'truncated':truncated,'reason':'No complete source line fits remaining limit'}
        content=''.join(kept);loc=location or {'start_line':start,'end_line':start+len(kept)-1}
        identity={'repository':entry['repository'],'path':entry['path'],'sha256':entry['sha256'],'location':loc,'text':content}
        eid=canonical(identity)
        if eid not in self.state['evidence'] and len(self.state['evidence'])>=self.state['budget']['evidence_records']:raise BudgetExhausted('evidence_records')
        self.state['used']['returned_characters']+=size
        evidence={'evidence_id':eid,**identity,'format':entry['format'],'source_category':entry['category'],'method':method,
                  'search_context':extra,'truncated':truncated,
                  'citation':f"repos/{entry['repository']}/{entry['path']}:L{loc['start_line']}-L{loc['end_line']}" if 'start_line' in loc else f"repos/{entry['repository']}/{entry['path']}:cell[{loc['cell_index']}]"}
        # Same source range/content has one retained record; repeated returns still cost.
        self.state['evidence'].setdefault(eid,evidence)
        self.current['returned_evidence_ids'].append(eid)
        return {'evidence':evidence,'truncated':truncated}

    def _read(self,repo,path,start=1,end=None,context=0):
        self._charge('read_operations');entry,text=self._source(repo,path);lines=text.splitlines(keepends=True)
        if not lines:return {'evidence':None,'truncated':False,'reason':'Empty source'}
        if type(context) is not int or context<0:raise ValueError('Invalid context')
        if type(start) is not int or start<1 or start>len(lines):raise ValueError('Invalid start line')
        end=end if end is not None else min(len(lines),start+79)
        if type(end) is not int or end<start or end>len(lines):raise ValueError('Invalid end line')
        return self._emit(entry,text,max(1,start-context),min(len(lines),end+context),'read')

    def _section(self,repo,path,line=1):
        self._charge('read_operations');entry,text=self._source(repo,path);fmt=recognize(path)
        if not fmt or not fmt.section:raise NotImplementedError('Section parser unavailable; use bounded line reading')
        start,end,title=section_range(text,fmt.section,line)
        return self._emit(entry,text,start,end,'section',{'section_title':title},self.state['budget']['max_section_characters'])

    def _symbol(self,repo,path,name):
        self._charge('read_operations');entry,text=self._source(repo,path);fmt=recognize(path)
        if not fmt.symbol:
            if fmt.category!='source':raise NotImplementedError('Symbol lookup requires a source format')
            matches=[i for i,line in enumerate(text.splitlines(),1) if re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',line)]
            if not matches:return {'evidence':None,'found':False,'reason':'Lexical name not found'}
            line=matches[0]
            result=self._emit(entry,text,max(1,line-2),min(len(text.splitlines()),line+2),'lexical-symbol',
                {'name':name,'parser':None,'not_a_symbol_boundary':True},self.state['budget']['max_section_characters'])
            result['matched_line_count']=len(matches);return result
        found=[s for s in symbols(text,fmt.symbol) if s['name']==name or s['short_name']==name]
        if not found:return {'evidence':None,'found':False,'reason':'Symbol not found'}
        selected=sorted(found,key=lambda s:(s['start_line'],s['end_line'],s['name']))[0]
        result=self._emit(entry,text,selected['start_line'],selected['end_line'],'symbol',selected,self.state['budget']['max_section_characters'])
        result['matched_symbol_count']=len(found);return result

    def _search(self,query,repo=None,category=None,limit=6,context=1):
        self._charge('search_operations')
        if repo is not None and repo not in self.state['manifest']['upstream_revisions']:raise PermissionError('Unknown repository')
        if category not in (None,'documentation','cli_source'):raise ValueError('Unknown category')
        if type(limit) is not int or not 1<=limit<=30 or type(context) is not int or not 0<=context<=10:raise ValueError('Invalid search bounds')
        ts=terms(query)
        if not ts:return {'matches':[],'reason':'No lexical search terms'}
        files=[f for f in self.policy.files.values() if (repo is None or f['repository']==repo) and (category is None or f['category']==category)]
        # Fixed lexical heuristic, not a semantic probability score.
        files.sort(key=lambda f:(-sum(t in f['path'].lower() for t in ts),f['repository'],f['path']))
        candidates=[];scan_exhausted=False
        for f in files:
            try:entry,text=self._source(f['repository'],f['path'])
            except BudgetExhausted:scan_exhausted=True;break
            lines=text.splitlines(keepends=True);path_hits=[t for t in ts if t in f['path'].lower()]
            for i,line in enumerate(lines):
                hits=[t for t in ts if t in line.lower()]
                if hits:
                    candidates.append((-(len(hits)+2*len(path_hits)),f['repository'],f['path'],i+1,hits,entry,text))
            if path_hits and lines and not any(x[1:3]==(f['repository'],f['path']) for x in candidates):
                candidates.append((-2*len(path_hits),f['repository'],f['path'],1,path_hits,entry,text))
        candidates.sort(key=lambda x:x[:4]);out=[];seen=set()
        for _,repo,path,line,hits,entry,text in candidates:
            if len(out)>=limit:break
            # Stable exact matching-line windows; overlaps retain their actual cost.
            window=(repo,path,line)
            if window in seen:continue
            seen.add(window);n=len(text.splitlines(keepends=True))
            try:result=self._emit(entry,text,max(1,line-context),min(n,line+context),'search',{'query':query,'matched_terms':hits,'matching_line':line})
            except BudgetExhausted:
                scan_exhausted=True;break
            if result['evidence']:out.append(result['evidence'])
            if result['truncated']:break
        return {'matches':out,'search_terms':ts,'scan_budget_exhausted':scan_exhausted,
            'heuristic':'descending matched line terms + 2*matched path terms; repository/path/line tie break',
            'additional_related_evidence_available':len(candidates)>len(out),'candidate_windows':len(candidates)}

    def _follow(self,repo,path,index=0,depth=1):
        self._charge('read_operations')
        if type(depth) is not int or depth<1 or depth>self.state['budget']['reference_depth']:raise BudgetExhausted('reference_depth')
        entry,text=self._source(repo,path);refs=references(text,path)
        if type(index) is not int or not 0<=index<len(refs):raise ValueError('Reference index outside available references')
        ref=refs[index];target=ref['target']
        if ref['kind']=='relative-document-link':
            if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:',target):raise PermissionError('External reference denied')
            # Resolve a genuine relative link, then apply canonical containment and manifest membership.
            candidate=(self.policy.project/'repos'/repo/PurePosixPath(path).parent/target).resolve()
            root=(self.policy.project/'repos'/repo).resolve()
            if not candidate.is_relative_to(root):raise PermissionError('Reference escapes repository')
            target=candidate.relative_to(root).as_posix()
        if (repo,target) not in self.policy.files and ref.get('alternative'):
            target=ref['alternative']
        self.policy.entry(repo,target)
        depths=self.state.setdefault('reference_depths',{})
        source_key=repo+'/'+path;target_key=repo+'/'+target
        actual_depth=max(depth,depths.get(source_key,0)+1)
        if actual_depth>self.state['budget']['reference_depth']:raise BudgetExhausted('reference_depth')
        depths[target_key]=max(actual_depth,depths.get(target_key,0))
        self.current['reference_depth']=actual_depth
        self.current['followed_reference']=ref
        if ref['kind']=='same-file-symbol':return self._symbol(repo,target,ref['symbol'])
        return self._read(repo,target,1,None,0)

    def _references(self,repo,path,limit=20):
        self._charge('read_operations');entry,text=self._source(repo,path)
        if type(limit) is not int or not 1<=limit<=100:raise ValueError('Invalid reference limit')
        refs=references(text,path)
        remaining=self.state['budget']['returned_characters']-self.state['used']['returned_characters']
        if remaining<2:raise BudgetExhausted('returned_characters')
        selected=[]
        for ref in refs[:limit]:
            if len(json.dumps(selected+[ref],ensure_ascii=False,sort_keys=True))>remaining:break
            selected.append(ref)
        truncated=len(selected)<len(refs)
        if truncated:self.current['truncations'].append('static reference limit or remaining character budget; complete references only')
        # Exact import snippets and metadata are conservatively charged in full.
        encoded=json.dumps(selected,ensure_ascii=False,sort_keys=True)
        self.state['used']['returned_characters']+=len(encoded)
        return {'references':selected,'truncated':truncated,'total_static_references':len(refs),
                'relationship':'static syntax only, never a complete/runtime call graph'}

    def _inspect(self,repo,path,cell=None):
        self._charge('read_operations');entry,text=self._source(repo,path);fmt=recognize(path)
        info,celltext=inspect_structure(text,fmt.structure,cell)
        if celltext is not None:
            return self._emit(entry,celltext,1,len(celltext.splitlines()),'notebook-cell',info,
                              self.state['budget']['max_section_characters'],location=info['location'])
        size=len(json.dumps(info,ensure_ascii=False))
        if size>self.state['budget']['returned_characters']-self.state['used']['returned_characters']:raise BudgetExhausted('returned_characters')
        self.state['used']['returned_characters']+=size
        return {'structure':info,'source_sha256':entry['sha256'],'not_schema_validation':True}

    def run(self,operation,**params):
        if operation not in {'search','read','section','symbol','references','follow','inspect'}:raise ValueError('Unknown operation')
        start=time.perf_counter()
        with self.lock():
            self.state=json.loads((self.output/'state.json').read_text());self.policy=AccessPolicy(self.state['project'],self.state['manifest'],self.state['max_source_bytes'])
            seq=len(self.state['trace'])+1
            self.current={'sequence':seq,'operation':operation,'parameters':params,'source_accesses':[],'returned_evidence_ids':[],'truncations':[]}
            try:
                result=getattr(self,'_'+operation)(**params);status='EVIDENCE_FOUND' if self.current['returned_evidence_ids'] else 'EVIDENCE_NOT_FOUND'
                if result.get('scan_budget_exhausted'):status='BUDGET_EXHAUSTED'
                elif self.current['truncations']:status='EVIDENCE_PARTIALLY_RETRIEVED'
            except BudgetExhausted as e:result={'reason':str(e),'truncated':True};status='BUDGET_EXHAUSTED'
            except (OSError,ValueError,NotImplementedError,SyntaxError) as e:result={'error':type(e).__name__,'reason':str(e)};status='ERROR'
            self.current.update(status=status,used=dict(self.state['used']),files_explored=len(self.state['files_explored']),retained_evidence=len(self.state['evidence']))
            self.state['trace'].append(self.current)
            response={'status':status,'result':result,'budget':self.state['budget'],'used':dict(self.state['used']),
                'files_explored':len(self.state['files_explored']),'retained_evidence':len(self.state['evidence']),
                'scope_manifest_sha256':self.state['manifest']['manifest_sha256'],'completeness':'NOT_ESTABLISHED','trace_sequence':seq}
            write_json(self.output/'operations'/f'{seq:04d}.json',response)
            write_json(self.output/'state.json',self.state)
            write_json(self.output/'evidence.json',list(self.state['evidence'].values()))
            write_json(self.output/'trace.json',self.state['trace'])
            write_json(self.output/'runtime'/f'{seq:04d}.json',{'duration_seconds':time.perf_counter()-start,'timestamp_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
            return response

    def explore(self,query,repo=None,category=None):
        if repo is None:
            repo=next((r for r in ('SPHinXsim','SPHinXsys') if r.lower() in query.lower()),None)
        search=self.run('search',query=query,repo=repo,category=category,limit=6,context=1)
        visited=set();responses=[search]
        for evidence in ([] if search['status']=='BUDGET_EXHAUSTED' else search['result'].get('matches',[])):
            key=evidence['repository'],evidence['path']
            if key in visited:continue
            visited.add(key);fmt=recognize(key[1]);line=evidence['search_context']['matching_line']
            if fmt.section:responses.append(self.run('section',repo=key[0],path=key[1],line=line))
            elif fmt.symbol=='python':
                # Scope-checked symbol identification; this explicit read has its own trace/cost.
                read=self.run('read',repo=key[0],path=key[1],start=max(1,line-2),end=line)
                responses.append(read)
                # Prefer an exact query identifier when present; do not guess runtime calls.
                name=next((t for t in re.findall(r'[A-Za-z_]\w*',query) if t.startswith(('cmd_','_'))),None)
                if name:responses.append(self.run('symbol',repo=key[0],path=key[1],name=name))
            else:responses.append(self.run('read',repo=key[0],path=key[1],start=line,end=line))
            if any(x['status']=='BUDGET_EXHAUSTED' for x in responses):break
        # One statically verified, already-allowlisted import/link at most by default.
        if self.state['budget']['reference_depth'] and visited and not any(x['status']=='BUDGET_EXHAUSTED' for x in responses):
            key=next(iter(sorted(visited)));refresponse=self.run('references',repo=key[0],path=key[1]);responses.append(refresponse)
            refs=refresponse['result'].get('references',[])
            approved=next((i for i,x in enumerate(refs) if (key[0],x['target']) in self.policy.files or (key[0],x.get('alternative')) in self.policy.files),None)
            if approved is not None:responses.append(self.run('follow',repo=key[0],path=key[1],index=approved,depth=1))
        summary={'query':query,'mode':'deterministic-explore','agent_decision_quality_evaluated':False,'operations':[x['trace_sequence'] for x in responses],
            'completeness':'NOT_ESTABLISHED','budget_exhausted':any(x['status']=='BUDGET_EXHAUSTED' for x in responses),
            'evidence_ids':list(self.state['evidence']),'returned_characters':self.state['used']['returned_characters']}
        write_json(self.output/'exploration-summary.json',summary)
        return summary
