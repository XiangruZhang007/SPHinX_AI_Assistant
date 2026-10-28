"""Portable generation contract: real source copies, whole records and processes."""
from dataclasses import asdict
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from chunker import chunk_file
from src import chunk_generation_v1 as g
from src.chunk_identity import canonical_source_key, source_identity_path
from src.cpp_coverage import data_definitions, recover_delegating_constructors
from src.documentation_chunking import chunk_document
from src.retrieval_v1 import RetrievalV1, _searchable_text

P = Path(__file__).resolve().parents[1]
SAMPLES = {
    'SPHinXsys': ['src/shared/materials/elastic_solid.cpp',
                 'tests/2d_examples/test_2d_dambreak/Dambreak.cpp'],
    'SPHinXsim': ['sphinxsim/sph_simulation/dynamics_builder/fluid_dynamics/fluid_dynamics_builder.h',
                 'examples/input/test_simulation_2d/config.json',
                 'sphinxsim/cli.py', 'sphinxsim/json_format.py'],
}
DOCUMENTS = [('SPHinXsim', 'docs/cli-usage.md'),
             ('SPHinXsys', 'tutorials/sphinx/examples/example1_2D_dambreak.rst')]


def selected_records(root, output, scheme=None):
    output.mkdir(parents=True)
    rows = []
    options = {} if scheme is None else {'identity_scheme': scheme}
    for repository, function, discovery in [('SPHinXsys', g.generate, '_source_files'),
                                            ('SPHinXsim', g.generate_sphinxsim, '_sphinxsim_files')]:
        paths = [root/'repos'/repository/p for p in SAMPLES[repository]]
        target = output/(repository+'.jsonl')
        with patch.object(g, discovery, return_value=iter(paths)):
            report = function(root, target, output/(repository+'.json'), **options)
        if report['skipped_files']:
            raise AssertionError(report['skipped_files'])
        rows += [json.loads(line) for line in target.read_text().splitlines()]
    for repository, path in DOCUMENTS:
        rows += chunk_document((root/'repos'/repository/path).read_bytes(), repository, path)[0]
    return rows


class CanonicalIdentityTests(unittest.TestCase):
    def test_repository_qualification_and_posix_normalization(self):
        self.assertEqual(canonical_source_key('SPHinXsim', './src//a.cpp'), 'SPHinXsim/src/a.cpp')
        self.assertNotEqual(canonical_source_key('SPHinXsim', 'src/a.cpp'),
                            canonical_source_key('SPHinXsys', 'src/a.cpp'))

    def test_invalid_canonical_paths_and_repositories(self):
        for path in ['/home/host/a.cpp', '../a.cpp', 'src/../a.cpp', 'C:\\a.cpp', '', '.', 'a\x00.cpp']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                canonical_source_key('SPHinXsim', path)
        with self.assertRaises(ValueError):canonical_source_key('unknown', 'a.cpp')

    def test_explicit_root_required_and_symlink_escape_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);root=base/'repo';root.mkdir();outside=base/'a.cpp';outside.write_text('int x;')
            link=root/'a.cpp';link.symlink_to(outside)
            with self.assertRaises(ValueError):source_identity_path(link, 'portable-v1')
            with self.assertRaises(ValueError):source_identity_path(link, 'portable-v1', repository='SPHinXsim', repo_root=root)

    def test_unknown_schemes_fail_before_generation_or_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for function in [g.generate, g.generate_sphinxsim]:
                with self.assertRaises(ValueError):function(root, root/'out', root/'summary', identity_scheme='unknown')
            with self.assertRaises(ValueError):list(data_definitions(root/'missing', identity_scheme='unknown'))
            with self.assertRaises(ValueError):recover_delegating_constructors(root/'missing', [], identity_scheme='unknown')
            self.assertFalse((root/'out').exists())


class RealSourcePortabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.base=Path(cls.temp.name)
        cls.roots=[cls.base/'local', cls.base/'runner']
        for root in cls.roots:
            for repo, paths in SAMPLES.items():
                for path in paths:
                    target=root/'repos'/repo/path;target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(P/'repos'/repo/path, target)
            for repo, path in DOCUMENTS:
                target=root/'repos'/repo/path;target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(P/'repos'/repo/path, target)
        cls.portable=[selected_records(root, cls.base/('portable'+str(i)), 'portable-v1') for i, root in enumerate(cls.roots)]

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_entire_ordered_records_and_serialization_equal_across_roots(self):
        self.assertEqual(self.portable[0], self.portable[1])
        self.assertEqual(json.dumps(self.portable[0], sort_keys=True), json.dumps(self.portable[1], sort_keys=True))
        self.assertEqual(len({r['chunk_id'] for r in self.portable[0]}), len(self.portable[0]))
        for root in self.roots:self.assertNotIn(str(root), json.dumps(self.portable))

    def test_default_and_explicit_legacy_and_native_primary_ids(self):
        default=selected_records(self.roots[0], self.base/'default')
        explicit=selected_records(self.roots[0], self.base/'legacy', 'legacy')
        self.assertEqual(default, explicit)
        path=self.roots[0]/'repos/SPHinXsim/sphinxsim/json_format.py'
        native={c.chunk_id for c in chunk_file(str(path), 'python') if c.node_type in g.PYTHON_PRIMARY_NODE_TYPES}
        emitted={r['chunk_id'] for r in default if r['source_path']=='repos/SPHinXsim/sphinxsim/json_format.py'}
        self.assertEqual(native, emitted)

    def test_json_and_documentation_records_unchanged(self):
        legacy=selected_records(self.roots[0], self.base/'json-legacy')
        stable=lambda rows:[r for r in rows if r['chunk_type'] in {'config_document','documentation_section'}]
        self.assertTrue(stable(legacy));self.assertEqual(stable(legacy), stable(self.portable[0]))

    def test_semantic_parent_ids_match_original_function(self):
        root=self.roots[0];relative='tests/2d_examples/test_2d_dambreak/Dambreak.cpp'
        repo=root/'repos/SPHinXsys';raw=g._raw_source_chunks(repo/relative,'cpp',repo,'SPHinXsys','portable-v1')
        functions={r['chunk_id']:r for r in raw if r['node_type'] in g.PRIMARY_NODE_TYPES}
        semantic=[r for r in self.portable[0] if r['node_type']=='semantic_section']
        self.assertTrue(semantic)
        for section in semantic:
            parent=functions[section['parent_context']['original_function_chunk_id']]
            self.assertGreaterEqual(section['byte_start'], parent['byte_start'])
            self.assertLessEqual(section['byte_end'], parent['byte_end'])

    def test_recovered_constructor_fragment_and_original_source(self):
        root=self.roots[0];repo=root/'repos/SPHinXsys';path=repo/'src/shared/materials/elastic_solid.cpp'
        raw=g._raw_source_chunks(path,'cpp',repo,'SPHinXsys','portable-v1')
        fixed=recover_delegating_constructors(path,raw,identity_scheme='portable-v1',repository='SPHinXsys',repo_root=repo)
        recovered=[r for r in fixed if r.get('metadata',{}).get('recovery')]
        self.assertEqual(len(recovered),1)
        record=recovered[0];oldid=record['metadata']['recovery']['replaced_fragment_id']
        self.assertIn(oldid,{r['chunk_id'] for r in raw});self.assertNotIn(oldid,{r['chunk_id'] for r in fixed})
        self.assertEqual(record['content'].encode(),path.read_bytes()[record['byte_start']:record['byte_end']])
        self.assertEqual(len(raw),len(fixed))

    def test_native_parent_links_and_supplemental_definitions(self):
        root=self.roots[0];repo=root/'repos/SPHinXsim';path=repo/'sphinxsim/json_format.py'
        raw=g._raw_source_chunks(path,'python',repo,'SPHinXsim','portable-v1');ids={r['chunk_id'] for r in raw}
        parents=[r['parent_chunk_id'] for r in raw if r.get('parent_chunk_id')]
        self.assertTrue(parents);self.assertTrue(set(parents)<=ids)
        data=[r for r in self.portable[0] if r['node_type']=='struct_specifier']
        self.assertTrue(any(r['symbol']=='FluidSolverConfig' for r in data))

    def test_all_output_ranges_match_source_contract(self):
        for record in self.portable[0]:
            path=self.roots[0]/record['source_path']
            source=path.read_bytes() if record['source_role']=='documentation' else path.read_text().encode()
            self.assertEqual(source[record['byte_start']:record['byte_end']],record['content'].encode())
            self.assertEqual(record['start_line'],source[:record['byte_start']].count(b'\n')+1)

    def test_independent_processes_with_different_working_directories(self):
        # Load this file by path without a dependency on a tests package.
        code="""import importlib.util,json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
s=importlib.util.spec_from_file_location('portable_tests',Path(sys.argv[1])/'tests/test_portable_chunk_identity.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
rows=m.selected_records(Path(sys.argv[2]),Path(sys.argv[3]),'portable-v1')
Path(sys.argv[4]).write_text(json.dumps(rows,sort_keys=True,ensure_ascii=False))
"""
        outputs=[]
        for i,root in enumerate(self.roots):
            target=self.base/('process-result'+str(i)+'.json')
            run=subprocess.run([sys.executable,'-B','-c',code,str(P),str(root),str(self.base/('process'+str(i))),str(target)],
                               cwd=root if i==0 else self.base,capture_output=True,text=True,
                               env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONHASHSEED':'0'})
            self.assertEqual(run.returncode,0,run.stderr);outputs.append(target.read_bytes())
        self.assertEqual(*outputs)

    def test_portable_bm25_inputs_scores_and_order(self):
        retrievers=[]
        for rows in self.portable:
            with patch.object(RetrievalV1,'_load_records',return_value=rows):retrievers.append(RetrievalV1(Path('not-read')))
        self.assertEqual([_searchable_text(r) for r in self.portable[0]],[_searchable_text(r) for r in self.portable[1]])
        for query in ['time step','How does the CLI validate simulation configuration?','RawFloat','FluidSolverConfig']:
            outcomes=[[(x.record['chunk_id'],x.score.hex()) for x in r.search(query,10)] for r in retrievers]
            self.assertEqual(*outcomes)


class IdentityMutationAndInterfaceTests(unittest.TestCase):
    def test_source_cli_modes_propagate_without_changing_legacy_calls(self):
        spec=importlib.util.spec_from_file_location('source_cli',P/'scripts/generate_source_chunks_v1.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for mode in [None,'legacy','portable-v1']:
            argv=['generate']+([] if mode is None else ['--identity-scheme',mode])
            with patch.object(sys,'argv',argv),patch.object(module,'generate',return_value={'total_chunk_count':1}) as build:
                module.main()
            self.assertEqual(build.call_args.kwargs,{} if mode!='portable-v1' else {'identity_scheme':mode})

    def test_sim_cli_modes_and_portable_input_mismatch(self):
        spec=importlib.util.spec_from_file_location('sim_cli',P/'scripts/generate_sphinxsim_source_chunks_v1.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            summary=Path(directory)/'sys-summary';summary.write_text('{"identity_scheme":"portable-v1"}')
            for mode in [None,'legacy','portable-v1']:
                argv=['generate','--sys-summary-input',str(summary)]+([] if mode is None else ['--identity-scheme',mode])
                with patch.object(sys,'argv',argv),patch.object(module,'generate_sphinxsim',return_value={'total_chunk_count':1}) as build,patch.object(module,'generate_combined_corpus',return_value={'total_chunk_count':1}):
                    module.main()
                self.assertEqual(build.call_args.kwargs,{} if mode!='portable-v1' else {'identity_scheme':mode})
            summary.write_text('{}')
            with patch.object(sys,'argv',['generate','--sys-summary-input',str(summary),'--identity-scheme','portable-v1']),patch.object(module,'generate_sphinxsim') as build:
                with self.assertRaises(ValueError):module.main()
                build.assert_not_called()

    def test_candidate_entrypoint_modes_propagate_to_both_repositories(self):
        spec=importlib.util.spec_from_file_location('candidate_cli',P/'scripts/build_remediation_candidate.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            for i,mode in enumerate([None,'legacy','portable-v1']):
                output=Path(directory)/str(i)
                argv=['build','--output-dir',str(output)]+([] if mode is None else ['--identity-scheme',mode])
                with patch.object(sys,'argv',argv),patch.object(module,'generate') as sys_build,patch.object(module,'generate_sphinxsim') as sim_build,patch.object(module,'generate_combined_corpus'),patch.object(module,'generate_documentation') as docs,patch.object(module,'combine',side_effect=lambda inputs,path:path.write_text('')),patch.object(module,'read_records',return_value=[]):
                    module.main()
                for build in [sys_build,sim_build]:
                    self.assertEqual(build.call_args.kwargs,{} if mode!='portable-v1' else {'identity_scheme':mode})
                self.assertEqual(docs.call_args.kwargs,{})

    def test_body_and_position_changes_follow_native_identity_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'a.py'
            def extract(text):
                path.write_text(text)
                return g._raw_source_chunks(path,'python',root,'SPHinXsim','portable-v1')[0]
            before=extract('def f():\n    return 1\n');changed=extract('def f():\n    return 2\n');moved=extract('\ndef f():\n    return 1\n')
            self.assertNotEqual(before['chunk_id'],changed['chunk_id']);self.assertNotEqual(before['chunk_id'],moved['chunk_id'])
            self.assertEqual(before['definition_id'],changed['definition_id']);self.assertEqual(before['definition_id'],moved['definition_id'])
            a=source_identity_path(path,'portable-v1',repository='SPHinXsim',repo_root=root)
            b=source_identity_path(path,'portable-v1',repository='SPHinXsys',repo_root=root)
            self.assertNotEqual(chunk_file(path,'python',identity_path=a)[0].chunk_id,chunk_file(path,'python',identity_path=b)[0].chunk_id)

    def test_combining_mixed_schemes_rejected_before_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);sys_summary=root/'sys.json';sim_summary=root/'sim.json'
            sys_summary.write_text('{}');sim_summary.write_text('{"identity_scheme":"portable-v1"}')
            with self.assertRaises(ValueError):g.generate_combined_corpus(root/'missing',sys_summary,root/'missing2',sim_summary,root/'output',root/'summary')
            self.assertFalse((root/'output').exists())

    def test_cli_rejects_unknown_modes(self):
        for name in ['generate_source_chunks_v1.py','generate_sphinxsim_source_chunks_v1.py','build_remediation_candidate.py']:
            run=subprocess.run([sys.executable,'-B',str(P/'scripts'/name),'--identity-scheme','unknown'],capture_output=True,text=True,
                               env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
            self.assertEqual(run.returncode,2,run.stderr);self.assertIn('invalid choice',run.stderr)


if __name__=='__main__':unittest.main()
