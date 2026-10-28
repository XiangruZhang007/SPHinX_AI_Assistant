"""Tests for the isolated adapter only; no model/network requests."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

FILE=Path(__file__).resolve().parents[1]/'scripts/evaluate_agent_step_1_8c.py'
spec=importlib.util.spec_from_file_location('agent_eval18c',FILE)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.broker=m.Broker(self.root,self.root/'rpc.jsonl')

    def test_no_arbitrary_operation_or_session_reset(self):
        for name,args in [('exec',{}),('create',{}),('explore',{'query':'x'}),('read',{'repository':'SPHinXsim','path':'x','session_dir':'other'}),('search',{'query':'x','budget':999})]:
            with self.subTest(name=name),self.assertRaises(ValueError):m.check_arguments(name,args)

    def test_strict_argument_types(self):
        for args in [{'repository':'SPHinXsim','path':'x','start':True},{'repository':'SPHinXsim','path':'x','start':'1'}]:
            with self.assertRaises(ValueError):m.check_arguments('read',args)
        with self.assertRaises(ValueError):m.check_arguments('read',{'path':'x'})

    def test_rpc_rejects_resources_and_unlisted_tools(self):
        for method,params in [('resources/read',{'uri':'file:///secret'}),('tools/call',{'name':'exec','arguments':{}})]:
            r=self.broker.rpc({'jsonrpc':'2.0','id':1,'method':method,'params':params})
            self.assertIn('error',r)

    def test_tools_are_explicit_and_closed(self):
        r=self.broker.rpc({'id':1,'method':'tools/list'})
        tools=r['result']['tools'];self.assertEqual({x['name'] for x in tools},set(m.TOOLS))
        self.assertTrue(all(not x['inputSchema']['additionalProperties'] for x in tools))
        self.assertNotIn('create',{x['name'] for x in tools})

    def test_one_fixed_session_and_argument_translation(self):
        class Fake:
            def __init__(self,path):self.path=path
            def run(self,op,**kw):return {'status':'EVIDENCE_FOUND','bound_session':str(self.path),'op':op,'parameters':kw}
        original={'repository':'SPHinXsim','path':'docs/x.md','start':1}
        with patch.object(m,'Session',Fake):r=self.broker.call('read',original)
        self.assertEqual(r['bound_session'],str(self.root));self.assertEqual(r['parameters']['repo'],'SPHinXsim')
        self.assertIn('repository',original)

    def test_budget_terminal_gate_prevents_further_source_calls(self):
        called=[]
        class Fake:
            def __init__(self,path):pass
            def run(self,op,**kw):called.append(op);return {'status':'BUDGET_EXHAUSTED'}
        with patch.object(m,'Session',Fake):
            self.broker.call('search',{'query':'x'})
            result=self.broker.call('read',{'repository':'SPHinXsim','path':'anything'})
        self.assertEqual(called,['search']);self.assertEqual(result['status'],'BUDGET_EXHAUSTED')

    def test_guide_is_exact_existing_skill_and_has_no_oracle_payload(self):
        r=self.broker.call('skill_guide',{})
        folder=FILE.parents[1]/'.agents/skills/sphinx-doc-cli-explorer'
        self.assertEqual(set(r['files']),set(m.GUIDE_FILES))
        for name,text in r['files'].items():self.assertEqual(text,(folder/name).read_text())

    def test_observable_log_excludes_private_reasoning(self):
        raw='\n'.join(json.dumps(x) for x in [
            {'type':'item.completed','item':{'type':'reasoning','text':'private content'}},
            {'type':'item.completed','item':{'type':'mcp_tool_call','arguments':{'query':'x'}}},
            {'type':'turn.completed','usage':{'input_tokens':4}}])
        events=m.safe_cli_events(raw)
        self.assertNotIn('private content',json.dumps(events));self.assertEqual(events[1]['item']['arguments'],{'query':'x'})
        self.assertEqual(events[-1]['usage']['input_tokens'],4)

    def test_box_does_not_mount_project_sources_or_oracles(self):
        box=m.box_files(self.root)
        command=m.base_box(Path('/usr/bin/true'),box,auth=False)
        sources=[command[i+1] for i,x in enumerate(command) if x in {'--ro-bind','--bind'}]
        self.assertNotIn(str(FILE.parents[1]),sources)
        self.assertTrue(all('comparative_evaluations' not in x for x in sources))
        self.assertIn('--unshare-pid',command);self.assertIn('--clearenv',command)
        self.assertNotIn('--bind',command)

    def test_isolated_config_disables_alternate_tools_and_memory(self):
        config=m.tomllib.loads(m.build_config())
        for key in ['shell_tool','apps','multi_agent','memories','code_mode']:
            self.assertFalse(config['features'][key])
        self.assertTrue(config['features']['code_mode_host'])
        self.assertFalse(config['memories']['use_memories']);self.assertEqual(config['web_search'],'disabled')
        self.assertEqual(list(config['mcp_servers']),['skill'])
        self.assertEqual(config['mcp_servers']['skill']['default_tools_approval_mode'],'approve')
        self.assertEqual(set(config['mcp_servers']['skill']['enabled_tools']),set(m.TOOLS))


if __name__=='__main__':unittest.main()
