"""Full bootstrap only: existing generators plus completed-file observations.

No file change detection, old snapshot lookup, or incremental reuse is present.
The supplied pinned selection/commits are verified before and after generation;
the source root may be a byte-verified copy without private Git directories.
"""
from __future__ import annotations
import hashlib
import importlib.metadata
from pathlib import Path
import shutil
import stat
import sys
import time

from . import chunk_generation_v1 as g
from . import documentation_chunking as doc
from .indexing_scope import discover_files,load_config,normalize_relative_path
from .corpus_snapshot import (FORMAT,SCHEMA_FIELDS,SnapshotError,canonical,digest,file_digest,
    read_records,write_json,seal_snapshot,verify_snapshot,validate_graph,source_bytes)

IMPLEMENTATION_FILES = [
    'src/chunk_generation_v1.py','src/cpp_coverage.py','src/chunk_identity.py',
    'src/documentation_chunking.py','src/indexing_scope.py','src/corpus_snapshot.py',
    'src/corpus_snapshot_build.py','scripts/corpus_snapshot.py','scripts/combine_candidate_corpus.py',
    'configs/indexing_scope.json','configs/documentation_scope.json',
]


def build_contract(project,chunker_commit):
    project=Path(project)
    return {'contract_version':1,'snapshot_format':FORMAT,'identity_scheme':'portable-v1',
            'record_schema':sorted(SCHEMA_FIELDS),
            'implementation_files':{name:file_digest(project/name) for name in IMPLEMENTATION_FILES},
            'external_chunker_commit':chunker_commit,
            'runtime':{'python':sys.version.split()[0],
                'packages':{d.metadata['Name']:d.version for d in importlib.metadata.distributions()
                            if 'tree' in d.metadata['Name'].lower() or 'chunk' in d.metadata['Name'].lower()}},
            'extraction':{'source_node_types':sorted(g.PRIMARY_NODE_TYPES),
                          'python_node_types':sorted(g.PYTHON_PRIMARY_NODE_TYPES),
                          'semantic_oversized_bytes':g.OVERSIZED_BYTES,
                          'documentation_version':doc.CHUNKER_VERSION,
                          'documentation_target_bytes':doc.TARGET_BYTES,
                          'documentation_oversized_bytes':doc.OVERSIZED_BYTES},
            'normalization':{'source':'Path.read_text UTF8 universal newlines; unchanged JSON convention',
                             'documentation':'strict UTF8 original bytes'},
            'serialization':'JSONL UTF8 ensure_ascii=False sort_keys=True newline=LF; array order preserved',
            'ordering':['sys discovery order/chunk order','sim discovery order/chunk order',
                        'documentation configuration order/chunk order'],
            'compatibility_policy':'exact canonical fingerprint equality; no partial invalidation'}


def selected_files(project,source_root):
    project,source_root=Path(project),Path(source_root)
    source_config=load_config(project/'configs/indexing_scope.json')
    doc_config=load_config(project/'configs/documentation_scope.json')
    result=[];seen=set()
    for component,config,repositories in [('sys',source_config,['SPHinXsys']),
                                         ('sim',source_config,['SPHinXsim']),
                                         ('documentation',doc_config,list(doc_config['repositories']))]:
        for repository in repositories:
            root=source_root/'repos'/repository
            if component=='documentation':
                for relative in config['repositories'][repository]['include_files']:
                    if not (root/relative).is_file():raise SnapshotError('Missing mandatory document: '+relative)
            for path in discover_files(config,repository,root):
                relative=path.relative_to(root).as_posix();normalize_relative_path(relative)
                if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or not path.resolve().is_relative_to(root.resolve()):
                    raise SnapshotError('Nonregular or escaping source: '+relative)
                key=repository,relative
                if key in seen:raise SnapshotError('Duplicate selected source: '+str(key))
                seen.add(key);raw=path.read_bytes()
                role='documentation' if component=='documentation' else (
                    g._source_role(root,path) if component=='sys' else g._sphinxsim_source_role(root,path))
                result.append({'repository':repository,'path':relative,'component':component,
                    'git_mode':'100755' if path.stat().st_mode&0o111 else '100644',
                    'git_blob':hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),
                    'source_sha256':hashlib.sha256(raw).hexdigest(),'source_bytes':len(raw),
                    'source_role':role,'language':path.suffix[1:] if component=='documentation' or path.suffix=='.json'
                                                    else ('python' if path.suffix=='.py' else 'cpp')})
    return result


def _node(raw):
    return {**{key:raw.get(key) for key in ['chunk_id','node_type','parent_chunk_id','symbol_id',
                                           'definition_id','file_id','byte_start','byte_end']},
            'content_sha256':hashlib.sha256(raw['content'].encode('utf-8')).hexdigest()}


class FileCollector:
    """Events prove completion, including genuine successful zero-chunk files."""
    def __init__(self,root,selection):
        self.root=Path(root);self.selection=selection;self.inventory=[];self.graph=[]
        self.cursors={'sys':0,'sim':0,'documentation':0}

    def __call__(self,path,records,native,transformed,error):
        index=len(self.inventory)
        if index>=len(self.selection):raise SnapshotError('Unexpected completed-file event')
        source=self.selection[index]
        if Path(path)!=self.root/'repos'/source['repository']/source['path']:
            raise SnapshotError('Generation/discovery file order differs from frozen inventory')
        component=source['component'];count=len(records)
        self.inventory.append({**source,'status':'FAILED' if error else ('SUCCESS_WITH_CHUNKS' if count else 'SUCCESS_ZERO_CHUNKS'),
            'error':error,'record_start':self.cursors[component],'chunk_count':count,
            'chunk_ids':[r['chunk_id'] for r in records],'record_digest':digest(records)})
        self.cursors[component]+=count
        replacements=[]
        for raw in transformed:
            recovery=raw.get('metadata',{}).get('recovery')
            if recovery:replacements.append({'replacement_id':raw['chunk_id'],'fragment_id':recovery['replaced_fragment_id']})
        self.graph.append({'repository':source['repository'],'path':source['path'],
                           'native':[_node(r) for r in native],'transformed':[_node(r) for r in transformed],
                           'replacements':replacements})


def _descriptor(root,path,records):
    return {'path':path,'records':len(records),'sha256':file_digest(root/path)}


def build_full_snapshot(project,source_root,output,inputs,*,identity_scheme):
    """Build one new local full snapshot. Caller independently freezes inputs.

    Snapshot verification after relocation does not require project/source_root.
    A failed build retains diagnostics and is never finalized as a valid snapshot.
    """
    if identity_scheme!='portable-v1':raise SnapshotError('Snapshot builder requires explicit portable-v1')
    project,source_root,output=Path(project),Path(source_root),Path(output)
    if output.exists() or output.is_symlink():raise SnapshotError('Build output already exists')
    contract=build_contract(project,inputs['chunker_commit'])
    if contract!=inputs['build_contract']:raise SnapshotError('Actual builder differs from independently frozen contract')
    selection=selected_files(project,source_root)
    if selection!=inputs['selection']:raise SnapshotError('Source tree differs from pinned complete selection')
    output.mkdir(parents=True,exist_ok=False)
    start=time.perf_counter();collector=FileCollector(source_root,selection)
    try:
        components=output/'components';components.mkdir()
        g.generate(source_root,components/'sys.jsonl',components/'sys-summary.json',
                   identity_scheme=identity_scheme,file_observer=collector)
        g.generate_sphinxsim(source_root,components/'sim.jsonl',components/'sim-summary.json',
                            identity_scheme=identity_scheme,file_observer=collector)
        g.generate_combined_corpus(components/'sys.jsonl',components/'sys-summary.json',
                                  components/'sim.jsonl',components/'sim-summary.json',
                                  components/'source.jsonl',components/'source-summary.json')
        doc.generate_documentation(source_root,components/'documentation.jsonl',components/'documentation-summary.json',
                                   project/'configs/documentation_scope.json',file_observer=collector)
        from scripts.combine_candidate_corpus import combine
        combine([components/'source.jsonl',components/'documentation.jsonl'],output/'corpus.jsonl')
        if len(collector.inventory)!=len(selection):raise SnapshotError('Selected source lacks a completion event')
        if any(item['status']=='FAILED' for item in collector.inventory):raise SnapshotError('Selected source processing failed')
        if selected_files(project,source_root)!=selection:raise SnapshotError('Source tree changed during build')
        write_json(output/'build-contract.json',contract);write_json(output/'selection.json',selection)
        write_json(output/'inventory.json',collector.inventory);write_json(output/'extraction-graph.json',collector.graph)
        for source in selection:
            target=output/'sources'/source['repository']/source['path'];target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source_root/'repos'/source['repository']/source['path'],target)
            if file_digest(target)!=source['source_sha256']:raise SnapshotError('Source copy changed during packaging')
        contents={name:read_records(components/(name+'.jsonl')) for name in ['sys','sim','source','documentation']}
        corpus=read_records(output/'corpus.jsonl')
        cursors={'sys':0,'sim':0,'documentation':0}
        from .corpus_snapshot import _source_span
        for item,evidence in zip(collector.inventory,collector.graph):
            component=item['component'];a=cursors[component];b=a+item['chunk_count'];emitted=contents[component][a:b];cursors[component]=b
            if [r['chunk_id'] for r in emitted]!=item['chunk_ids']:raise SnapshotError('Observed chunks differ from emitted corpus')
            data=source_bytes((output/'sources'/item['repository']/item['path']).read_bytes(),component=='documentation')
            for record in emitted:_source_span(record,data)
            validate_graph(evidence,emitted,data)
        identity={'snapshot_type':'full-bootstrap','parent_snapshot_id':None,'identity_scheme':identity_scheme,
                  'upstreams':inputs['upstreams'],'build_fingerprint':digest(contract),
                  'inventory':{'path':'inventory.json','files':len(selection),'sha256':file_digest(output/'inventory.json')},
                  'extraction_graph':{'path':'extraction-graph.json','files':len(collector.graph),'sha256':file_digest(output/'extraction-graph.json')},
                  'components':{name:_descriptor(output,'components/'+name+'.jsonl',records) for name,records in contents.items()},
                  'corpus':_descriptor(output,'corpus.jsonl',corpus)}
        warnings={name:__import__('json').loads((components/(name+'-summary.json')).read_text()) for name in ['sys','sim','documentation']}
        write_json(output/'build-provenance.json',{'upstreams':inputs['upstreams'],
                    'chunker_commit':inputs['chunker_commit'],'build_fingerprint':digest(contract),
                    'selection_digest':digest(selection),'warnings_and_summaries':warnings})
        write_json(output/'execution.json',{'source_root':str(source_root.resolve()),'project':str(project.resolve()),
                   'output':str(output.resolve()),'duration_seconds':time.perf_counter()-start,
                   'project_checkpoint':inputs['project_head'],'observational_only':True})
        write_json(output/'validation.json',{'structural_status':'STRUCTURALLY_VALID',
                   'retrieval_quality_status':'QUALITY_NOT_EVALUATED','production_promotion':'NOT_AUTHORIZED'})
        manifest=seal_snapshot(output,identity,{'structural_status':'STRUCTURALLY_VALID',
                   'retrieval_quality_status':'QUALITY_NOT_EVALUATED','report':'validation.json'})
        return verify_snapshot(output,digest(contract))
    except Exception as error:
        write_json(output/'build-failure.json',{'error':type(error).__name__+': '+str(error),
                   'completed_file_events':len(collector.inventory),'selected_files':len(selection),
                   'inventory':collector.inventory,'structural_status':'STRUCTURALLY_INVALID'})
        raise
