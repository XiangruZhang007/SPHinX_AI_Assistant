"""Synthetic safety/navigation tests; real upstream smoke tests are archived separately."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from src.skill_guided_retrieval import Session, build_scopes, AccessPolicy
from src.skill_guided_retrieval.formats import FORMATS, recognize, capabilities, text_enabled, BinaryAdapter
from src.skill_guided_retrieval.navigation import headings, section_range, symbols, inspect_structure
from src.skill_guided_retrieval.scope import canonical, checked_path, validate_output

PROJECT=Path(__file__).resolve().parents[1]


class ExplorerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='skill-fixtures-')
        self.addCleanup(self.tmp.cleanup)
        self.project=Path(self.tmp.name)/'project';self.project.mkdir()
        (self.project/'configs').mkdir()
        for repo in ('SPHinXsys','SPHinXsim'):(self.project/'repos'/repo).mkdir(parents=True)
        self.config=json.loads((PROJECT/'configs/skill_doc_cli_scope_v1.json').read_text())
        self.docs={'schema_version':1,'repositories':{r:{'include_files':[]} for r in ('SPHinXsys','SPHinXsim')}}
        (self.project/'configs/indexing_scope.json').write_bytes((PROJECT/'configs/indexing_scope.json').read_bytes())
        for path in self.config['curated_cli_files']:self.put(path,'# fixture\n',repo='SPHinXsim',approved=False)
        self.counter=0
        self.put('guide.md','# Guide\nalpha needle\n\n## Child\nmore\n\n# Next\nother\n')
        self.flush()

    def put(self,path,text,repo='SPHinXsys',approved=True):
        p=self.project/'repos'/repo/path;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(text.encode() if isinstance(text,str) else text)
        if approved and path not in self.docs['repositories'][repo]['include_files']:
            self.docs['repositories'][repo]['include_files'].append(path)
        return p

    def flush(self):
        (self.project/'configs/skill_doc_cli_scope_v1.json').write_text(json.dumps(self.config))
        (self.project/'configs/documentation_scope.json').write_text(json.dumps(self.docs))

    def session(self,budget=None,scope='curated'):
        self.flush();self.counter+=1
        return Session.create(self.project,Path(self.tmp.name)/'SPHinX_AI_Assistant_artifacts/skill_prototypes'/str(self.counter),scope,budget)

    def run_tool(self,s,op,path='guide.md',repo='SPHinXsys',**params):
        return s.run(op,repo=repo,path=path,**params)

    def test_registry_recognizes_every_pattern(self):
        for fmt in FORMATS:
            for pattern in fmt.patterns:
                sample=pattern.replace('*','fixture')
                self.assertIsNotNone(recognize(sample),pattern)
        self.assertEqual(recognize('README.MD').name,'markdown')
        self.assertEqual(recognize('CMakeLists.txt').name,'cmake')
        self.assertEqual(recognize('.env.example').name,'environment-template')
        self.assertEqual(recognize('requirements-dev.txt').name,'requirements')
        self.assertEqual(recognize('pyproject.toml').name,'python-project')
        self.assertIsNone(recognize('opaque.unknown'))

    def test_capability_absence_and_binary_honesty(self):
        matrix=capabilities()
        self.assertTrue(all(x['presence']=='NOT_PRESENT_IN_PINNED_REPOS' for x in matrix))
        for row in matrix:
            if row['status']=='REGISTERED_ADAPTER_UNAVAILABLE':
                self.assertFalse(row['read']);self.assertFalse(row['binary_adapter_implemented'])
        with self.assertRaises(NotImplementedError):BinaryAdapter().extract(b'%PDF')
        self.assertFalse(text_enabled(recognize('x.pickle')))
        self.assertFalse(text_enabled(recognize('x.svg')))

    def test_generic_read_search_for_every_advertised_text_format(self):
        paths=[]
        for fmt in FORMATS:
            if not text_enabled(fmt):continue
            path=fmt.patterns[0].replace('*','fixture')
            path='formats/'+path
            marker='unique_'+fmt.name.replace('-','_')
            self.put(path,marker+'\nsecond\n');paths.append((path,marker))
        s=self.session({'read_operations':200,'search_operations':100,'files_explored':200,'evidence_records':500,'returned_characters':100000})
        for path,marker in paths:
            with self.subTest(path=path):
                result=self.run_tool(s,'read',path)
                self.assertEqual(result['status'],'EVIDENCE_FOUND',result)
                self.assertEqual(result['result']['evidence']['text'],marker+'\nsecond\n')
                hit=s.run('search',query=marker,repo='SPHinXsys',limit=30,context=0)
                self.assertIn(path,[x['path'] for x in hit['result']['matches']])

    def test_manifest_scopes_and_freezing(self):
        self.put('docs/extra.md','# Additional\n',approved=False)
        self.put('unapproved.py','def bad():pass\n',approved=False)
        self.flush();a=build_scopes(self.project);b=build_scopes(self.project)
        self.assertEqual(a,b)
        curated={x['path'] for x in a['manifests']['curated']['files']}
        expanded={x['path'] for x in a['manifests']['expanded']['files']}
        self.assertNotIn('docs/extra.md',curated);self.assertIn('docs/extra.md',expanded)
        self.assertNotIn('unapproved.py',expanded)
        self.assertEqual(len(a['manifests']['curated']['files']),5)
        expected=sum((self.project/'repos'/f['repository']/f['path']).stat().st_size for f in a['manifests']['expanded']['files'])
        expected+=(self.project/'repos/SPHinXsim/sphinxsim/cli.py').stat().st_size
        self.assertEqual(a['administrative_manifest_scan_bytes'],expected)
        s=self.session();self.put('guide.md','changed\n')
        self.assertEqual(self.run_tool(s,'read')['status'],'ERROR')

    def test_independent_resume_cumulative_cost_and_dedup(self):
        s=self.session({'read_operations':2})
        a=self.run_tool(s,'read');s=Session(s.output);b=self.run_tool(s,'read');c=self.run_tool(Session(s.output),'read')
        self.assertEqual(a['result']['evidence']['evidence_id'],b['result']['evidence']['evidence_id'])
        self.assertEqual(b['used']['returned_characters'],2*len(a['result']['evidence']['text']))
        self.assertEqual(b['retained_evidence'],1);self.assertEqual(b['used']['read_operations'],2)
        self.assertEqual(c['status'],'BUDGET_EXHAUSTED');self.assertEqual(c['used']['read_operations'],2)
        self.assertEqual(len(Session(s.output).state['trace']),3)

    def test_search_snippet_context_accounting(self):
        s=self.session();a=s.run('search',query='needle',context=1)
        self.assertEqual(a['used']['returned_characters'],sum(len(e['text']) for e in a['result']['matches']))
        self.assertEqual(a['used']['search_operations'],1);self.assertEqual(a['used']['read_operations'],0)
        self.assertTrue(s.state['trace'][0]['source_accesses'])

    def test_file_and_evidence_limits(self):
        s=self.session({'files_explored':1,'evidence_records':1})
        a=s.run('search',query='needle');self.assertEqual(a['files_explored'],1)
        self.assertEqual(a['status'],'BUDGET_EXHAUSTED')
        b=self.run_tool(s,'read','guide.md');self.assertEqual(b['status'],'BUDGET_EXHAUSTED')
        s=self.session({'evidence_records':1})
        self.run_tool(s,'read',start=1,end=1)
        self.assertEqual(self.run_tool(s,'read',start=2,end=2)['status'],'BUDGET_EXHAUSTED')

    def test_complete_line_unicode_crlf_and_no_final_newline(self):
        text='标题\r\nαβ\r\nlast';self.put('exact.md',text)
        s=self.session({'returned_characters':len('标题\r\nαβ\r\n')+1})
        a=self.run_tool(s,'read','exact.md');e=a['result']['evidence']
        self.assertEqual(e['text'],'标题\r\nαβ\r\n');self.assertEqual(e['location'],{'start_line':1,'end_line':2})
        self.assertTrue(e['truncated']);self.assertEqual(a['status'],'EVIDENCE_PARTIALLY_RETRIEVED')
        s=self.session();e=self.run_tool(s,'read','exact.md',start=3,end=3)['result']['evidence']
        self.assertEqual(e['text'],'last')

    def test_no_half_line_and_section_limit(self):
        self.put('long.md','# Heading\n'+'a'*60+'\n')
        s=self.session({'max_section_characters':5})
        a=self.run_tool(s,'section','long.md');self.assertIsNone(a['result']['evidence']);self.assertTrue(a['result']['truncated'])
        s=self.session({'max_section_characters':20})
        a=self.run_tool(s,'section','long.md');self.assertEqual(a['result']['evidence']['text'],'# Heading\n')
        self.assertTrue(a['result']['truncated'])

    def test_markdown_headings_fences_setext(self):
        text='Title\n=====\nintro\n```md\n# fake\n```\n## Child\nbody\n# Next\nend\n'
        self.assertEqual(headings(text,'markdown'),[(1,1,'Title'),(7,2,'Child'),(9,1,'Next')])
        self.assertEqual(section_range(text,'markdown',8),(7,8,'Child'))
        self.assertEqual(section_range(text,'markdown',3),(1,8,'Title'))

    def test_rst_overline_and_directive(self):
        text='=====\nTitle\n=====\nintro\n\nChild\n-----\n.. code-block:: python\n\n    fake\n    ====\n\nNext\n----\nend\n'
        self.assertEqual(headings(text,'rst'),[(1,1,'Title'),(6,2,'Child'),(13,2,'Next')])
        self.put('guide.rst',text);s=self.session()
        e=self.run_tool(s,'section','guide.rst',line=10)['result']['evidence']
        self.assertEqual(e['location'],{'start_line':6,'end_line':12});self.assertIn('    fake',e['text'])

    def test_python_ast_decorators_nested_and_async(self):
        text='@deco\nclass C:\n    @wrap\n    async def run(self):\n        return 1\n'
        self.put('example.py',text);s=self.session()
        e=self.run_tool(s,'symbol','example.py',name='C.run')['result']['evidence']
        self.assertEqual(e['text'],'    @wrap\n    async def run(self):\n        return 1\n')
        self.assertEqual(e['location'],{'start_line':3,'end_line':5})
        self.assertEqual(e['search_context']['parser'],'python-ast')

    def test_optional_cpp_and_generic_language_symbol(self):
        self.put('example.rs','fn hello() {\n  println!("hello");\n}\n');s=self.session()
        a=self.run_tool(s,'symbol','example.rs',name='hello')
        self.assertTrue(a['result']['evidence']['search_context']['not_a_symbol_boundary'])
        if importlib.util.find_spec('tree_sitter_language_pack'):
            found=symbols('int twice(int x) { return x*2; }\n','tree-sitter')
            self.assertTrue(any(x['short_name']=='twice' for x in found))

    def test_reference_approved_unapproved_and_depth(self):
        self.put('a.md','[link](b.md)\n');self.put('b.md','[link](c.md)\n');self.put('c.md','final\n')
        s=self.session();a=self.run_tool(s,'follow','a.md')
        self.assertEqual(a['result']['evidence']['path'],'b.md')
        self.assertEqual(self.run_tool(Session(s.output),'follow','b.md')['status'],'BUDGET_EXHAUSTED')
        self.put('unapproved.md','unapproved\n',approved=False)
        self.put('bad.md','[link](unapproved.md)\n[external](https://example.com)\n')
        s=self.session();self.assertEqual(self.run_tool(s,'follow','bad.md')['status'],'ERROR')
        self.assertEqual(self.run_tool(s,'follow','bad.md',index=1)['status'],'ERROR')

    def test_python_static_reference_navigation(self):
        self.put('sphinxsim/cli.py','from sphinxsim.config.schemas import Config\n',repo='SPHinXsim',approved=False)
        self.put('sphinxsim/config/schemas.py','class Config:\n    pass\n',repo='SPHinXsim',approved=False)
        s=self.session();a=self.run_tool(s,'references','sphinxsim/cli.py',repo='SPHinXsim')
        self.assertFalse(a['result']['references'][0]['runtime_dependency_confirmed'])
        b=self.run_tool(s,'follow','sphinxsim/cli.py',repo='SPHinXsim')
        self.assertEqual(b['result']['evidence']['path'],'sphinxsim/config/schemas.py')

    def test_all_primitives_reject_unapproved_sources(self):
        self.put('unapproved.py','def bad():pass\n',approved=False);s=self.session({'read_operations':30})
        for op,kw in [('read',{}),('section',{}),('symbol',{'name':'bad'}),('references',{}),('follow',{}),('inspect',{})]:
            with self.subTest(operation=op):self.assertEqual(self.run_tool(s,op,'unapproved.py',**kw)['status'],'ERROR')
        hit=s.run('search',query='bad',limit=30)
        self.assertNotIn('unapproved.py',[x['path'] for x in hit['result']['matches']])

    def test_path_attacks_and_secret_policy(self):
        s=self.session({'read_operations':30})
        for path in ['../guide.md','/etc/passwd','guide.md\0','..\\guide.md','.env','.aws/credentials','unknown.md']:
            with self.subTest(path=path):self.assertEqual(self.run_tool(s,'read',path)['status'],'ERROR')
        self.assertEqual(self.run_tool(s,'read',repo='unknown')['status'],'ERROR')
        self.put('.env','secret\n');self.put('.env.example','TEMPLATE=x\n')
        s=self.session();self.assertEqual(self.run_tool(s,'read','.env')['status'],'ERROR')
        self.assertEqual(self.run_tool(s,'read','.env.example')['status'],'EVIDENCE_FOUND')

    def test_symlink_escape_and_special_file_rejected(self):
        s=self.session();p=self.project/'repos/SPHinXsys/guide.md';p.unlink()
        outside=Path(self.tmp.name)/'outside.md';outside.write_text('outside\n');p.symlink_to(outside)
        self.assertEqual(self.run_tool(s,'read')['status'],'ERROR')
        p.unlink();os.mkfifo(p)
        self.assertEqual(self.run_tool(s,'read')['status'],'ERROR')
        p.unlink();p.symlink_to(outside)
        self.flush();audit=build_scopes(self.project)
        self.assertNotIn('guide.md',[e['path'] for e in audit['manifests']['curated']['files']])

    def test_output_validation_existing_and_concurrent_rejection(self):
        s=self.session()
        for path in [self.project/'repos/SPHinXsys/out',self.project/'repos/SPHinXsim/out',self.project/'out',Path(self.tmp.name)/'SPHinX_AI_Assistant_artifacts/candidate_baselines/out']:
            with self.assertRaises(PermissionError):validate_output(self.project,path)
        with self.assertRaises(FileExistsError):Session.create(self.project,s.output)
        (s.output/'.operation-lock').write_text('busy')
        with self.assertRaises(RuntimeError):self.run_tool(s,'read')

    def test_manifest_tamper_rejected(self):
        s=self.session();m=copy.deepcopy(s.state['manifest']);m['files'][0]['sha256']='tampered'
        with self.assertRaises(ValueError):AccessPolicy(self.project,m)

    def test_unsupported_binary_never_read(self):
        for name in ['x.pdf','x.pickle','x.tar.gz','x.png','x.svg']:self.put(name,b'\xff\x00binary')
        s=self.session()
        for name in ['x.pdf','x.pickle','x.tar.gz','x.png','x.svg']:
            self.assertEqual(self.run_tool(s,'read',name)['status'],'ERROR')

    def test_structured_stdlib_adapters(self):
        cases=[('json','{"a":1}'),('jsonl','{"a":1}\n{"b":2}\n'),('csv','a,b\n1,2\n'),('tsv','a\tb\n1\t2\n'),('toml','a=1\n'),('ini','[s]\na=1\n')]
        for kind,text in cases:
            info,cell=inspect_structure(text,kind);self.assertEqual(info['kind'],kind);self.assertIsNone(cell)
        self.put('config.json','{"a":1}');s=self.session()
        self.assertEqual(self.run_tool(s,'inspect','config.json')['result']['structure']['item_count'],1)

    def test_notebook_cell_provenance_without_execution(self):
        source=['print("never executed")\n','α=1']
        self.put('example.ipynb',json.dumps({'cells':[{'id':'c1','cell_type':'code','source':source}]}))
        s=self.session();e=self.run_tool(s,'inspect','example.ipynb',cell=0)['result']['evidence']
        self.assertEqual(e['text'],''.join(source));self.assertEqual(e['location'],{'cell_index':0,'cell_id':'c1'})
        self.assertNotIn('start_line',e['location']);self.assertTrue(e['citation'].endswith('cell[0]'))

    def test_invalid_ranges_and_unavailable_section(self):
        self.put('data.yaml','a: b\n');s=self.session()
        self.assertEqual(self.run_tool(s,'section','data.yaml')['status'],'ERROR')
        for kw in [{'start':0},{'start':99},{'start':2,'end':1},{'context':-1}]:
            self.assertEqual(self.run_tool(s,'read',**kw)['status'],'ERROR')

    def test_deterministic_traces_evidence_and_source_unchanged(self):
        before={p:p.read_bytes() for p in (self.project/'repos').rglob('*') if p.is_file()}
        a=self.session();b=self.session()
        params=dict(query='needle guide',limit=3,context=1)
        self.assertEqual(a.run('search',**params),b.run('search',**params))
        self.assertEqual(self.run_tool(a,'section',line=2),self.run_tool(b,'section',line=2))
        self.assertEqual(a.state['trace'],b.state['trace']);self.assertEqual(a.state['evidence'],b.state['evidence'])
        self.assertEqual(before,{p:p.read_bytes() for p in before})

    def test_cli_help_and_argument_budget_names(self):
        cmd=[str(PROJECT/'tools/treesitter-chunker/.venv/bin/python'),'-B',str(PROJECT/'scripts/explore_doc_cli_skill.py')]
        result=subprocess.run(cmd+['create','--help'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0);self.assertIn('--max-section-characters',result.stdout)
        self.assertIn('--max-read-operations',result.stdout)

    def test_bounded_reference_prefix_accounting(self):
        self.put('links.md','[one](guide.md)\n[two](other.md)\n')
        s=self.session();a=self.run_tool(s,'references','links.md',limit=1)
        self.assertEqual(len(a['result']['references']),1)
        self.assertTrue(a['result']['truncated'])
        self.assertEqual(a['used']['returned_characters'],len(json.dumps(a['result']['references'],ensure_ascii=False,sort_keys=True)))

    def test_explore_stops_on_exhaustion(self):
        s=self.session({'search_operations':0})
        summary=s.explore('needle guide')
        self.assertTrue(summary['budget_exhausted'])
        self.assertEqual(len(s.state['trace']),1)
        self.assertEqual(s.state['used']['read_operations'],0)

    def test_cli_resume_rejects_scope_or_budget_change(self):
        import contextlib
        import io
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('skill_entrypoint',PROJECT/'scripts/explore_doc_cli_skill.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        s=self.session()
        with patch.object(module,'PROJECT',self.project),contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.main(['explore','--session-dir',str(s.output),'--scope','expanded','--query','needle']),2)
            with self.assertRaises(SystemExit):module.main(['explore','--session-dir',str(s.output),'--max-read-operations','99','--query','needle'])


if __name__=='__main__':unittest.main()
