"""Local snapshot integrity/compatibility and safe packaging; stdlib only.

Identity is SHA256 of canonical manifest.identity, not of diagnostics or the
archive. A detached checksums.json additionally seals manifest and all payload.
This proves package consistency, not authenticity of an externally supplied
manifest. No incremental reuse, network access or automatic repair occurs here.
"""
from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path
import tarfile

from .indexing_scope import normalize_relative_path

FORMAT = 'ci-v2-snapshot-v1'
CORE_FIELDS = {'snapshot_type','parent_snapshot_id','identity_scheme','upstreams',
               'build_fingerprint','inventory','components','corpus','extraction_graph'}
SCHEMA_FIELDS = {'chunk_id','repository','source_role','source_path','chunk_type','node_type',
                 'symbol','start_line','end_line','byte_start','byte_end','parent_context','content'}


class SnapshotError(ValueError):
    pass


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def write_json(path,value):
    with Path(path).open('x',encoding='utf-8',newline='\n') as stream:
        stream.write(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n')


def _unique(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise SnapshotError('Duplicate JSON key: '+key)
        value[key]=item
    return value


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'),object_pairs_hook=_unique)
    except (OSError,UnicodeError,ValueError) as error:
        raise SnapshotError(f'Invalid JSON {path}: {error}') from error


def checked_file(root,relative):
    try:
        relative=normalize_relative_path(relative)
        root=Path(root).resolve();path=root/relative
        if not path.resolve().is_relative_to(root) or not path.is_file() or path.is_symlink():
            raise SnapshotError('Missing/nonregular/escaping snapshot file: '+relative)
        # Even internal directory symlinks are not part of the package format.
        if any(parent.is_symlink() for parent in path.parents if parent!=root and parent.is_relative_to(root)):
            raise SnapshotError('Symlink directory in snapshot: '+relative)
        return path
    except (OSError,ValueError,TypeError) as error:
        raise SnapshotError(str(error)) from error


def read_records(path):
    try:
        raw=Path(path).read_bytes()
        if not raw or not raw.endswith(b'\n'):raise SnapshotError('Empty or unterminated corpus JSONL')
        records=[json.loads(line,object_pairs_hook=_unique) for line in raw.decode('utf-8').splitlines()]
        ids=set()
        for record in records:
            if not isinstance(record,dict) or not SCHEMA_FIELDS<=record.keys():raise SnapshotError('Missing record fields')
            if not isinstance(record['chunk_id'],str) or not record['chunk_id'] or record['chunk_id'] in ids:
                raise SnapshotError('Empty or duplicate chunk ID')
            ids.add(record['chunk_id'])
            repo=record['repository'];path=record['source_path']
            if repo not in {'SPHinXsys','SPHinXsim'} or not isinstance(path,str) or not path.startswith('repos/'+repo+'/'):
                raise SnapshotError('Invalid record provenance')
            normalize_relative_path(path)
            if any(type(record[k]) is not int for k in ['start_line','end_line','byte_start','byte_end']):raise SnapshotError('Invalid range types')
            if not 1<=record['start_line']<=record['end_line'] or not 0<=record['byte_start']<record['byte_end']:
                raise SnapshotError('Invalid record ranges')
            if not isinstance(record['content'],str) or len(record['content'].encode())!=record['byte_end']-record['byte_start']:
                raise SnapshotError('Invalid source content length')
        return records
    except (OSError,UnicodeError,ValueError,TypeError,KeyError) as error:
        raise SnapshotError(f'Invalid corpus {path}: {error}') from error


def snapshot_id(identity):
    if set(identity)!=CORE_FIELDS:raise SnapshotError('Unexpected/missing identity core fields')
    return 'sha256-'+digest(identity)


def source_bytes(raw,documentation=False):
    if documentation:return raw
    # Precisely the existing Path.read_text universal-newline convention.
    return io.TextIOWrapper(io.BytesIO(raw),encoding='utf-8',newline=None).read().encode('utf-8')


def _source_span(record,data):
    a,b=record['byte_start'],record['byte_end']
    if not 0<=a<b<=len(data) or data[a:b]!=record['content'].encode():raise SnapshotError('Record/source slice mismatch')
    if record['start_line']!=data[:a].count(b'\n')+1:raise SnapshotError('Record start line mismatch')
    # Legacy Tree-sitter end_line includes the exclusive boundary's physical line;
    # documentation/semantic sections use the last included line. Preserve both.
    allowed={data[:b].count(b'\n')+1,data[:b-1].count(b'\n')+1}
    if record['end_line'] not in allowed:raise SnapshotError('Record end line mismatch')


def _graph_pool(nodes,data):
    pool={}
    for node in nodes:
        identifier=node['chunk_id'];a,b=node['byte_start'],node['byte_end']
        if not isinstance(identifier,str) or not identifier or not 0<=a<b<=len(data):raise SnapshotError('Invalid extraction node')
        if hashlib.sha256(data[a:b]).hexdigest()!=node['content_sha256']:raise SnapshotError('Extraction node/source mismatch')
        if identifier in pool and pool[identifier]!=node:raise SnapshotError('Conflicting extraction identities')
        pool[identifier]=node
    for node in nodes:
        parent=node.get('parent_chunk_id')
        if parent:
            if parent not in pool:raise SnapshotError('Unresolved native parent reference')
            p=pool[parent]
            if not p['byte_start']<=node['byte_start']<=node['byte_end']<=p['byte_end']:
                raise SnapshotError('Native parent range mismatch')
    return pool


def validate_graph(graph,records,data):
    """Check actual captured native targets, replacements and final references.

    Does not reparse source or authenticate the external manifest. The builder
    records real extraction events; package integrity protects that evidence.
    """
    native=_graph_pool(graph['native'],data)
    transformed={node['chunk_id']:node for node in graph['transformed']}
    for node in transformed.values():
        a,b=node['byte_start'],node['byte_end']
        if not 0<=a<b<=len(data) or hashlib.sha256(data[a:b]).hexdigest()!=node['content_sha256']:
            raise SnapshotError('Transformed node/source mismatch')
    all_nodes={**native,**transformed};replacements={}
    for item in graph['replacements']:
        old,new=item['fragment_id'],item['replacement_id']
        if old not in native or new not in transformed:raise SnapshotError('Invalid replaced-fragment target')
        a,b=native[old],transformed[new]
        if not b['byte_start']<=a['byte_start']<a['byte_end']<=b['byte_end']:
            raise SnapshotError('Invalid constructor replacement range')
        replacements[new]=old
    stats={'final_targets':0,'native_targets':0,'transformed_targets':0,'replaced_fragments':0}
    final_ids={r['chunk_id'] for r in records}
    for r in records:
        parent=r.get('structural_metadata',{}).get('parent_chunk_id')
        if parent:
            if parent not in all_nodes:raise SnapshotError('Unknown structural parent target')
            target=all_nodes[parent]
            if not target['byte_start']<=r['byte_start']<=r['byte_end']<=target['byte_end']:
                raise SnapshotError('Structural parent/source mismatch')
            stats['final_targets' if parent in final_ids else 'native_targets']+=1
        original=r['parent_context'].get('original_function_chunk_id')
        if original:
            if original not in all_nodes:raise SnapshotError('Unknown semantic function target')
            target=all_nodes[original];context=r['parent_context']
            if target['node_type'] not in {'function_definition','method_declaration'}:
                raise SnapshotError('Semantic target is not a function')
            if (context['original_function_byte_start'],context['original_function_byte_end'])!=(target['byte_start'],target['byte_end']):
                raise SnapshotError('Semantic original function range mismatch')
            if not target['byte_start']<=r['byte_start']<r['byte_end']<=target['byte_end']:
                raise SnapshotError('Semantic section outside original function')
            stats['transformed_targets' if original in transformed and original not in native else 'native_targets']+=1
        recovery=r.get('structural_metadata',{}).get('metadata',{}).get('recovery')
        if recovery:
            if replacements.get(r['chunk_id'])!=recovery['replaced_fragment_id']:
                raise SnapshotError('Recovery does not match actual extraction replacement')
            if recovery['replaced_fragment_id'] in final_ids:raise SnapshotError('Replaced fragment retained alongside recovery')
            stats['replaced_fragments']+=1
        elif not original and graph['native'] and r['chunk_id'] not in all_nodes:
            metadata=r.get('structural_metadata',{}).get('metadata',{})
            if r['node_type'] not in {'struct_specifier','enum_specifier'} or metadata.get('extraction')!='field-only-struct-or-named-enum-v1':
                raise SnapshotError('Final record lacks captured extraction provenance')
    return stats


def _verify(root,expected_fingerprint):
    root=Path(root);manifest=read_json(checked_file(root,'manifest.json'))
    if manifest.get('format')!=FORMAT or type(manifest.get('schema_version')) is not int or manifest['schema_version']!=1:
        raise SnapshotError('Unsupported snapshot schema')
    identity=manifest['identity']
    if identity['identity_scheme']!='portable-v1':raise SnapshotError('Unsupported snapshot identity scheme')
    if identity['snapshot_type']!='full-bootstrap' or identity['parent_snapshot_id'] is not None:
        raise SnapshotError('P2 requires a full bootstrap without invented parent')
    upstreams=identity['upstreams']
    if not isinstance(upstreams,dict) or set(upstreams)!={'SPHinXsys','SPHinXsim'} or any(
        not isinstance(commit,str) or len(commit)!=40 or any(c not in '0123456789abcdef' for c in commit)
        for commit in upstreams.values()):
        raise SnapshotError('Invalid pinned upstream identities')
    if manifest['snapshot_id']!=snapshot_id(identity):raise SnapshotError('Snapshot identity checksum mismatch')
    if not isinstance(manifest['files'],dict):raise SnapshotError('Invalid payload manifest')
    checks=read_json(checked_file(root,'checksums.json'))
    if checks!={**manifest['files'],'manifest.json':file_digest(root/'manifest.json')}:
        raise SnapshotError('Manifest/detached checksum inventory differs')
    for relative,h in checks.items():
        if file_digest(checked_file(root,relative))!=h:raise SnapshotError('File checksum mismatch: '+relative)
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if actual!=set(checks)|{'checksums.json'}:raise SnapshotError('Undeclared/missing package files')
    contract=read_json(checked_file(root,'build-contract.json'))
    if digest(contract)!=identity['build_fingerprint']:raise SnapshotError('Malformed stored build fingerprint')
    if contract['identity_scheme']!=identity['identity_scheme'] or contract['snapshot_format']!=FORMAT:
        raise SnapshotError('Build contract/identity scheme mismatch')
    inventory=read_json(checked_file(root,identity['inventory']['path']))
    graph=read_json(checked_file(root,identity['extraction_graph']['path']))
    if digest_file_descriptor(root,identity['inventory'])!=len(inventory) or len(graph)!=len(inventory) or identity['extraction_graph']['files']!=len(graph):
        raise SnapshotError('Inventory or graph file count mismatch')
    if file_digest(root/identity['extraction_graph']['path'])!=identity['extraction_graph']['sha256']:
        raise SnapshotError('Graph descriptor checksum mismatch')
    corpus_desc=identity['corpus'];corpus=read_records(checked_file(root,corpus_desc['path']))
    if len(corpus)!=corpus_desc['records'] or file_digest(root/corpus_desc['path'])!=corpus_desc['sha256']:
        raise SnapshotError('Corpus descriptor mismatch')
    components={}
    for name,desc in identity['components'].items():
        records=read_records(checked_file(root,desc['path']))
        if len(records)!=desc['records'] or file_digest(root/desc['path'])!=desc['sha256']:
            raise SnapshotError('Component descriptor mismatch: '+name)
        components[name]=records
    if list(identity['components'])!=sorted(identity['components']):
        raise SnapshotError('Component descriptors require canonical key order')
    if set(components)!={'sys','sim','source','documentation'}:raise SnapshotError('Missing component')
    if components['source']!=components['sys']+components['sim'] or corpus!=components['source']+components['documentation']:
        raise SnapshotError('Component inclusion/order mismatch')
    selected=read_json(checked_file(root,'selection.json'))
    if len(selected)!=len(inventory):raise SnapshotError('Selected file missing from inventory')
    cursors={'sys':0,'sim':0,'documentation':0};seen=set();zero=0;reference_counts={}
    for selected_file,item,evidence in zip(selected,inventory,graph):
        if {k:item[k] for k in selected_file}!=selected_file:raise SnapshotError('Selected/inventory provenance mismatch')
        key=(item['repository'],item['path']);component=item['component']
        if key in seen:raise SnapshotError('Duplicate selected source')
        seen.add(key)
        if component not in cursors:raise SnapshotError('Invalid file component')
        if evidence['repository']!=key[0] or evidence['path']!=key[1]:raise SnapshotError('Graph/source association mismatch')
        count=item['chunk_count'];start=cursors[component];emitted=components[component][start:start+count]
        if type(count) is not int or count<0 or len(emitted)!=count:raise SnapshotError('Invalid inventory count')
        if item['status']!=('SUCCESS_WITH_CHUNKS' if count else 'SUCCESS_ZERO_CHUNKS'):
            raise SnapshotError('Failed/incomplete source cannot be reused')
        if [r['chunk_id'] for r in emitted]!=item['chunk_ids'] or digest(emitted)!=item['record_digest']:
            raise SnapshotError('Inventory-to-corpus mapping mismatch')
        if item['record_start']!=start:raise SnapshotError('Inventory record order mismatch')
        cursors[component]+=count;zero+=not count
        prefix='repos/'+key[0]+'/'+key[1]
        if any(r['source_path']!=prefix or r['source_role']!=item['source_role'] for r in emitted):
            raise SnapshotError('Cross-file/role record association')
        source=checked_file(root,'sources/'+key[0]+'/'+key[1]).read_bytes()
        if len(source)!=item['source_bytes'] or hashlib.sha256(source).hexdigest()!=item['source_sha256']:
            raise SnapshotError('Packaged source differs from inventory')
        data=source_bytes(source,component=='documentation')
        for r in emitted:_source_span(r,data)
        stats=validate_graph(evidence,emitted,data)
        for k,v in stats.items():reference_counts[k]=reference_counts.get(k,0)+v
    if any(cursors[k]!=len(components[k]) for k in cursors):raise SnapshotError('Uninventoried corpus records')
    report=read_json(checked_file(root,manifest['validation']['report']))
    if report['structural_status']!='STRUCTURALLY_VALID' or manifest['validation']['structural_status']!='STRUCTURALLY_VALID':
        raise SnapshotError('Snapshot not marked structurally valid')
    quality=report['retrieval_quality_status']
    if quality not in {'QUALITY_ACCEPTED','QUALITY_REVIEW_REQUIRED','QUALITY_REJECTED','QUALITY_NOT_EVALUATED'}:
        raise SnapshotError('Unsupported retrieval quality state')
    if manifest['validation']['retrieval_quality_status']!=quality:raise SnapshotError('Validation state mismatch')
    compatibility='NOT_CHECKED' if expected_fingerprint is None else ('COMPATIBLE' if expected_fingerprint==identity['build_fingerprint'] else 'INCOMPATIBLE')
    return {'structural_status':'STRUCTURALLY_VALID','build_compatibility':compatibility,
            'retrieval_quality_status':quality,'snapshot_id':manifest['snapshot_id'],
            'build_fingerprint':identity['build_fingerprint'],'records':len(corpus),'selected_files':len(inventory),
            'zero_chunk_files':zero,'reference_checks':reference_counts,
            'incremental_reuse_eligible':compatibility=='COMPATIBLE' and quality=='QUALITY_ACCEPTED',
            'production_promotion':'NOT_AUTHORIZED','source_authenticity':'Controlled local build; external manifest trust not established by SHA256 alone'}


def digest_file_descriptor(root,descriptor):
    if file_digest(checked_file(root,descriptor['path']))!=descriptor['sha256']:raise SnapshotError('Descriptor checksum mismatch')
    return descriptor['files']


def verify_snapshot(root,expected_fingerprint=None):
    try:return _verify(root,expected_fingerprint)
    except (KeyError,TypeError,IndexError,OSError,UnicodeError,ValueError) as error:
        if isinstance(error,SnapshotError):raise
        raise SnapshotError(f'Malformed snapshot: {error}') from error


def seal_snapshot(root,identity,validation):
    """One-time finalization; reports are package-protected but outside identity."""
    root=Path(root)
    files={p.relative_to(root).as_posix():file_digest(p) for p in sorted(root.rglob('*')) if p.is_file()}
    if {'manifest.json','checksums.json'}&files.keys():raise SnapshotError('Snapshot already sealed')
    manifest={'format':FORMAT,'schema_version':1,'snapshot_id':snapshot_id(identity),
              'identity':identity,'files':files,'validation':validation}
    write_json(root/'manifest.json',manifest)
    write_json(root/'checksums.json',{**files,'manifest.json':file_digest(root/'manifest.json')})
    return manifest


def package_snapshot(root,archive,expected_fingerprint=None):
    verification=verify_snapshot(root,expected_fingerprint)
    if verification['build_compatibility']=='INCOMPATIBLE':
        raise SnapshotError('Cannot package for an incompatible expected builder')
    with tarfile.open(archive,'x:gz') as tar:
        for path in sorted(Path(root).rglob('*')):
            if path.is_symlink():raise SnapshotError('Snapshot symlink is not allowed')
            if path.is_file():tar.add(path,arcname=path.relative_to(root).as_posix(),recursive=False)
    return {'archive_sha256':file_digest(archive),'snapshot_id':read_json(Path(root)/'manifest.json')['snapshot_id']}


def restore_snapshot(archive,destination,expected_fingerprint=None):
    destination=Path(destination)
    if destination.exists() or destination.is_symlink():raise SnapshotError('Restore destination already exists')
    # Validate every member before creating the destination; reject all links.
    with tarfile.open(archive,'r:*') as tar:
        seen=set()
        for member in tar:
            name=normalize_relative_path(member.name)
            if name!=member.name or name in seen or not member.isfile():raise SnapshotError('Unsafe or duplicate archive member')
            seen.add(name)
        destination.mkdir(parents=True,exist_ok=False)
        for member in tar.getmembers():
            target=destination/member.name;target.parent.mkdir(parents=True,exist_ok=True)
            with tar.extractfile(member) as source,target.open('xb') as output:
                import shutil
                shutil.copyfileobj(source,output)
    return verify_snapshot(destination,expected_fingerprint)
