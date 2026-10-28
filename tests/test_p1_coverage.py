"""Actual P1 source evidence checks; no upstream CLI or simulation execution."""
import ast
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from src import chunk_generation_v1 as g
from src.indexing_scope import load_config, discover_files

P=Path(__file__).resolve().parents[1]


class JsonFormatCoverageTests(unittest.TestCase):
    def test_scope_grows_by_exactly_one_file_with_old_order(self):
        root=P/'repos/SPHinXsim';config=load_config()
        prior=json.loads(json.dumps(config));prior['repositories']['SPHinXsim']['include_files'].remove('sphinxsim/json_format.py')
        before=discover_files(prior,'SPHinXsim',root);after=discover_files(config,'SPHinXsim',root)
        self.assertEqual(after,[*before,root/'sphinxsim/json_format.py'])
        self.assertEqual(len(after),len(set(after)))

    def test_actual_json_formatter_functions_have_exact_source(self):
        path=P/'repos/SPHinXsim/sphinxsim/json_format.py'
        expected={n.name for n in ast.parse(path.read_text()).body if isinstance(n,(ast.ClassDef,ast.FunctionDef))}
        with tempfile.TemporaryDirectory() as d, patch.object(g,'_sphinxsim_files',return_value=iter([path])):
            d=Path(d);summary=g.generate_sphinxsim(P,d/'chunks',d/'summary');self.assertEqual(summary['skipped_files'],[])
            records=[json.loads(l) for l in (d/'chunks').read_text().splitlines()]
        self.assertTrue(expected <= {r['symbol'] for r in records})
        self.assertTrue({'RawFloat','load_json_preserving_floats','format_json'} <= expected)
        for r in records:
            self.assertEqual(r['source_role'],'cli')
            self.assertEqual(r['content'].encode(),path.read_bytes()[r['byte_start']:r['byte_end']])


class CppDataCoverageTests(unittest.TestCase):
    def test_actual_struct_defaults_and_hook_enums(self):
        from src.cpp_coverage import data_definitions
        base=P/'repos/SPHinXsim/sphinxsim/sph_simulation'
        cases=[('dynamics_builder/continuum_dynamics/continuum_dynamics_builder.h','ContinuumSolverParameters','acoustic_cfl_{0.4}'),
               ('dynamics_builder/fluid_dynamics/fluid_dynamics_builder.h','FluidSolverConfig','advection_cfl_{0.25}'),
               ('common_builder/material_builder.h','ThermalBoundaryConfig','std::string boundary_type'),
               ('simulation_builder/base_simulation_builder.h','SimulationHookPoint','BeforeMainPhysicalTimeStep'),
               ('simulation_builder/base_simulation_builder.h','InitializationHookPoint','PreSimulationSanityCheck')]
        for path,symbol,text in cases:
            path=base/path
            raw=next(r for r in data_definitions(path) if r['metadata']['signature']['name']==symbol)
            self.assertIn(text,raw['content'])
            source=path.read_bytes()
            self.assertEqual(raw['content'].encode(),source[raw['byte_start']:raw['byte_end']])
            self.assertEqual(raw['start_line'],source[:raw['byte_start']].count(b'\n')+1)
            self.assertEqual(raw['end_line'],source[:raw['byte_end']].count(b'\n')+1)

    def test_method_containing_structs_not_duplicated(self):
        from src.cpp_coverage import data_definitions
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'a.cpp';path.write_text('struct Plain { int x{3}; };\nstruct Methods { void f() {} };\nenum class Hook { Before, After };\n')
            records=list(data_definitions(path))
        self.assertEqual([r['metadata']['signature']['name'] for r in records],['Plain','Hook'])


class ConstructorCoverageTests(unittest.TestCase):
    def test_actual_nested_template_constructor_complete_original_bytes(self):
        from dataclasses import asdict
        from chunker import chunk_file
        from src.cpp_coverage import recover_delegating_constructors
        path=P/'repos/SPHinXsys/src/shared/materials/elastic_solid.cpp'
        old=[asdict(c) for c in chunk_file(str(path),language='cpp')]
        new=recover_delegating_constructors(path,old)
        target=next(r for r in new if r['start_line']==269 and r['end_line']==272)
        self.assertIn('ConstructArgs<Real, Real, Vecd, Vecd, std::array<Real, 4>, std::array<Real, 4>> args',target['content'])
        self.assertIn('std::get<5>(args)',target['content'])
        self.assertEqual(target['content'].encode(),path.read_bytes()[target['byte_start']:target['byte_end']])
        self.assertNotIn('> >',target['content'])
        oldid=target['metadata']['recovery']['replaced_fragment_id']
        self.assertEqual([r for r in old if r['chunk_id']!=oldid],
                         [r for r in new if r['chunk_id']!=target['chunk_id']])
        self.assertEqual(len(new),len(old))

    def test_ordinary_constructors_unchanged(self):
        from dataclasses import asdict
        from chunker import chunk_file
        from src.cpp_coverage import recover_delegating_constructors
        path=P/'repos/SPHinXsys/src/shared/materials/weakly_compressible_fluid.cpp'
        old=[asdict(c) for c in chunk_file(str(path),language='cpp')]
        self.assertEqual(recover_delegating_constructors(path,old),old)

    def test_unrelated_parse_error_does_not_rewrite_functions(self):
        from dataclasses import asdict
        from chunker import chunk_file
        from src.cpp_coverage import recover_delegating_constructors
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'bad.cpp';path.write_text('???;\nvoid f() {}\n')
            old=[asdict(c) for c in chunk_file(str(path),language='cpp')]
            self.assertEqual(recover_delegating_constructors(path,old),old)


if __name__=='__main__':unittest.main()
