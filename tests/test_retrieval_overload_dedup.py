"""Distinct physical definitions survive name dedup; real-source cases anchored."""
import json
from pathlib import Path
import tempfile
import unittest
from src.retrieval_v1 import RetrievalV1

P = Path(__file__).resolve().parents[1]


def fixture(id, line, content='void f() {}'):
    return dict(chunk_id=id,repository='SPHinXsys',source_path='repos/SPHinXsys/example.cpp',
                source_role='core_library',node_type='function_definition',chunk_type='function_definition',
                symbol='f',start_line=line,end_line=line,content=content)


class OverloadDedupTests(unittest.TestCase):
    def search(self, records, query='function f'):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'corpus';p.write_text(''.join(json.dumps(r)+'\n' for r in records))
            r=RetrievalV1(p)
            first=r.search(query,10);second=r.search(query,10)
            self.assertEqual([(x.record['chunk_id'],x.score) for x in first],[(x.record['chunk_id'],x.score) for x in second])
            return first

    def test_distinct_overloads(self):
        result=self.search([fixture('a',1,'void f() {}'),fixture('b',2,'void f(int x) {}')])
        self.assertEqual({r.record['chunk_id'] for r in result},{'a','b'})

    def test_distinct_class_methods_at_different_ranges(self):
        result=self.search([fixture('a',1),fixture('b',8)])
        self.assertEqual(len(result),2)

    def test_true_duplicate_with_different_id_collapses(self):
        result=self.search([fixture('a',1),fixture('b',1)])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0].record['chunk_id'],'a')

    def test_unrelated_function_still_retrieves(self):
        self.assertEqual(self.search([fixture('a',1)])[0].record['chunk_id'],'a')

    def test_documentation_existing_dedup_unchanged(self):
        rows=[{**fixture('a',1),'node_type':'documentation_section'},
              {**fixture('b',8),'node_type':'documentation_section'}]
        self.assertEqual(len(self.search(rows)),1)

    def test_actual_constructor_overloads_preserve_source_ranges(self):
        path=P/'repos/SPHinXsys/src/shared/materials/weakly_compressible_fluid.cpp'
        if not path.is_file():self.skipTest('upstream unavailable')
        lines=path.read_text().splitlines()
        rows=[]
        for start,end in [(8,12),(14,15)]:
            rows.append({**fixture(str(start),start,'\n'.join(lines[start-1:end])),
                'source_path':str(path.relative_to(P)), 'symbol':'WeaklyCompressibleFluid','end_line':end})
        self.assertEqual({r.record['start_line'] for r in self.search(rows,'WeaklyCompressibleFluid')},{8,14})


if __name__=='__main__':unittest.main()
