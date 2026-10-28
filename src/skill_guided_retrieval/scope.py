"""Trusted manifest construction and one access policy for every query primitive."""
from pathlib import Path, PurePosixPath
import ast
import hashlib
import json
import os
import stat
import subprocess
from collections import Counter
from .formats import recognize, text_enabled, capabilities
from src.indexing_scope import normalize_relative_path, is_in_scope


def digest(data): return hashlib.sha256(data).hexdigest()
def canonical(value): return digest(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode())

def secret(path):
    p=PurePosixPath(path);n=p.name.lower()
    template=n in {'.env.example','.env.sample','.env.template'}
    return (not template and (n=='.env' or n.startswith('.env.'))) or n.endswith(('.key','.pem','.p12','.pfx')) or n in {'credentials','id_rsa','id_ed25519'} or any(x in p.parts for x in ('.aws','.ssh','.git'))


def checked_path(root,relative):
    relative=normalize_relative_path(relative)
    if secret(relative):raise PermissionError('Secret-bearing source denied')
    root=Path(root).resolve();p=(root/relative).resolve(strict=True)
    if not p.is_relative_to(root):raise PermissionError('Symlink escapes repository')
    if not stat.S_ISREG(p.stat().st_mode):raise PermissionError('Only regular files are allowed')
    return p


def tracked_paths(root):
    try:
        out=subprocess.check_output(['git','ls-files','-z'],cwd=root,stderr=subprocess.DEVNULL)
        return sorted(p for p in out.decode().split('\0') if p)
    except subprocess.CalledProcessError:
        # Fixture/unversioned roots: filenames only; never recurse symlink dirs.
        paths=[]
        for folder,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=sorted(d for d in dirs if d not in {'.git','.venv','__pycache__'} and not (Path(folder)/d).is_symlink())
            paths += [str((Path(folder)/n).relative_to(root)) for n in sorted(files)]
        return sorted(paths)


def revision(root):
    try:return subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,stderr=subprocess.DEVNULL,text=True).strip()
    except subprocess.CalledProcessError:return 'UNVERSIONED_FIXTURE'


def local_imports(data,relative):
    result=[]
    try:tree=ast.parse(data)
    except (SyntaxError,UnicodeError):return result
    for n in ast.walk(tree):
        if isinstance(n,ast.ImportFrom):
            module=n.module or ''
            if n.level:
                parent=PurePosixPath(relative).parent.parts
                module='.'.join((*parent[:len(parent)-n.level+1],module)).strip('.')
            if module.startswith('sphinxsim'):
                result.append((module,n.lineno))
        elif isinstance(n,ast.Import):
            result += [(a.name,n.lineno) for a in n.names if a.name.startswith('sphinxsim')]
    return sorted(set(result))


def build_scopes(project):
    """Administrative, query-independent audit. Reads only rule-approved candidates.

    Path/format inventory precedes content access. Manifests freeze at session
    creation; a query never enlarges authorization by following a reference.
    """
    project=Path(project).resolve();config=json.loads((project/'configs/skill_doc_cli_scope_v1.json').read_text())
    docs=json.loads((project/config['documentation_config']).read_text());index=json.loads((project/config['indexing_config']).read_text())
    manifests={s:{'schema_version':1,'scope':s,'configuration_sha256':canonical(config),'files':[]} for s in ('curated','expanded')}
    audit=[];counts=Counter();revisions={};bootstrap_bytes=0
    for repo in sorted(docs['repositories']):
        root=project/'repos'/repo;rev=revision(root);revisions[repo]=rev;paths=tracked_paths(root)
        curated_docs=set(docs['repositories'][repo]['include_files']);cli=set(config['curated_cli_files']) if repo=='SPHinXsim' else set()
        expanded_cli={}
        if repo=='SPHinXsim' and config['expanded_direct_cli_imports']:
            # Trusted bootstrap is limited to explicitly approved CLI entry files.
            for entry in sorted(cli):
                if entry!='sphinxsim/cli.py':continue
                p=checked_path(root,entry);data=p.read_bytes();bootstrap_bytes+=len(data)
                for module,line in local_imports(data,entry):
                    base=module.replace('.','/')
                    target=next((x for x in (base+'.py',base+'/__init__.py') if x in paths),None)
                    if target:expanded_cli[target]=f'static import {entry}:L{line} ({module})'
            for path in config['expanded_cli_metadata_files']:
                if path in paths:expanded_cli[path]='explicit CLI packaging metadata rule'
        for path in paths:
            fmt=recognize(path)
            if fmt:counts[fmt.name]+=1
            parts=PurePosixPath(path).parts
            exclusion=None
            if secret(path):exclusion='secret-bearing filename'
            elif any(x in parts for x in config['excluded_directory_names']):exclusion='excluded directory'
            elif any(path==x or path.startswith(x+'/') for x in config['excluded_prefixes'].get(repo,[])):exclusion='embedded/excluded prefix'
            reasons={}
            if path in curated_docs:reasons['curated']='curated documentation configuration'
            if path in cli:
                if not is_in_scope(index,repo,path):raise ValueError('Curated CLI file is outside validated indexing scope: '+path)
                reasons['curated']='explicit CLI entry/support file in validated indexing scope'
            if 'curated' in reasons:reasons['expanded']=reasons['curated']
            expanded_doc=(fmt is not None and fmt.category=='documentation' and
                ((len(parts)==1 and config['expanded_root_documentation']) or parts[0] in config['expanded_documentation_directories']))
            if expanded_doc:
                # Plain data .txt under tests is not automatically documentation.
                if fmt.name!='plain-text' or parts[0] in ('docs','tutorials') or PurePosixPath(path).name.lower().startswith('readme'):
                    reasons.setdefault('expanded','registered documentation format under explicit documentation path rule')
            if path in expanded_cli:reasons.setdefault('expanded',expanded_cli[path])
            if not fmt:exclusion=exclusion or 'unregistered format'
            elif not text_enabled(fmt):exclusion=exclusion or 'adapter unavailable or metadata-only/unsupported format'
            if not exclusion:
                try:p=checked_path(root,path)
                except (OSError,ValueError) as e:exclusion=type(e).__name__+': '+str(e)
                else:
                    if p.stat().st_size>config['max_source_bytes']:exclusion='source size limit'
            if not reasons:exclusion=exclusion or 'outside explicit exploration scope'
            item={'repository':repo,'path':path,'format':fmt.name if fmt else None,'category':fmt.category if fmt else None,'allowed_scopes':[],'excluded_reason':exclusion}
            if not exclusion and reasons:
                data=p.read_bytes();bootstrap_bytes+=len(data)
                # Never classify binary or undecodable text as readable UTF-8.
                try:data.decode('utf-8')
                except UnicodeError:exclusion='non-UTF8 text';item['excluded_reason']=exclusion
                if b'\0' in data:exclusion='NUL-bearing text';item['excluded_reason']=exclusion
                if not exclusion:
                    for scope,reason in sorted(reasons.items()):
                        category='documentation' if path in curated_docs or expanded_doc else 'cli_source'
                        manifests[scope]['files'].append({'repository':repo,'path':path,'format':fmt.name,'category':category,'revision':rev,'sha256':digest(data),'reason':reason})
                        item['allowed_scopes'].append(scope)
            audit.append(item)
        missing=curated_docs-set(paths)
        if missing:raise FileNotFoundError('Curated documentation absent: '+str(sorted(missing)))
    for scope,m in manifests.items():
        m['files'].sort(key=lambda f:(f['repository'],f['path']));m['upstream_revisions']=revisions
        m['manifest_sha256']=canonical(m)
    scope_counts={}
    for scope,m in manifests.items():
        for fmt,n in Counter(f['format'] for f in m['files']).items():scope_counts.setdefault(fmt,{})[scope]=n
    return {'config':config,'manifests':manifests,'inventory':audit,'formats':capabilities(counts,scope_counts),
            'administrative_manifest_scan_bytes':bootstrap_bytes,'inventory_is_metadata_only_for_unapproved_sources':True}


class AccessPolicy:
    def __init__(self,project,manifest,max_bytes=2000000):
        self.project=Path(project).resolve();self.manifest=manifest;self.max_bytes=max_bytes
        check=dict(manifest);h=check.pop('manifest_sha256')
        if canonical(check)!=h:raise ValueError('Scope manifest checksum mismatch')
        self.files={(f['repository'],f['path']):f for f in manifest['files']}

    def entry(self,repo,path):
        path=normalize_relative_path(path)
        if repo not in self.manifest['upstream_revisions']:raise PermissionError('Unknown repository')
        if secret(path):raise PermissionError('Secret-bearing source denied')
        if (repo,path) not in self.files:raise PermissionError('File not in frozen scope; scope extension required')
        return self.files[repo,path]

    def read(self,repo,path):
        entry=self.entry(repo,path);p=checked_path(self.project/'repos'/repo,entry['path'])
        flags=os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)
        fd=os.open(p,flags)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):raise PermissionError('Special file denied')
            if os.fstat(fd).st_size>self.max_bytes:raise ValueError('Source size limit')
            with os.fdopen(fd,'rb',closefd=False) as f:data=f.read(self.max_bytes+1)
        finally:os.close(fd)
        if len(data)>self.max_bytes:raise ValueError('Source size limit')
        if digest(data)!=entry['sha256']:raise ValueError('Source changed since scope freeze')
        text=data.decode('utf-8')
        if '\0' in text:raise ValueError('NUL-bearing text')
        return entry,text


def output_root(project):return Path(project).resolve().parent/'SPHinX_AI_Assistant_artifacts'/'skill_prototypes'

def validate_output(project,path,base=None):
    project=Path(project).resolve();p=Path(path).resolve();base=Path(base or output_root(project)).resolve()
    for repo in ('SPHinXsys','SPHinXsim'):
        if p.is_relative_to((project/'repos'/repo).resolve()):raise PermissionError('Output inside upstream repository')
    if p==base or not p.is_relative_to(base):raise PermissionError('Output must be a child of the permitted skill_prototypes directory')
    return p
