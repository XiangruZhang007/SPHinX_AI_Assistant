#!/usr/bin/env python3
"""Step 1.8C: isolated Codex CLI + a session-bound stdlib MCP Skill adapter.

No model SDK, direct answer API, installation, or changes to retrieval/Skill code.
The parent controller can grade; the bwrap child cannot mount any oracle/source
checkout and receives source text only through this narrowly scoped broker.
"""
from __future__ import annotations
import argparse
from collections import Counter
import concurrent.futures
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import tomllib

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from src.skill_guided_retrieval import Session
from src.comparative_evaluation import Sources,sha256,write_json,canonical_sha256,skill_evidence_stream,score

TOOLS={
 'search':({'query':str,'repository':str,'category':str,'limit':int,'context':int},['query']),
 'read':({'repository':str,'path':str,'start':int,'end':int,'context':int},['repository','path']),
 'section':({'repository':str,'path':str,'line':int},['repository','path']),
 'symbol':({'repository':str,'path':str,'name':str},['repository','path','name']),
 'references':({'repository':str,'path':str,'limit':int},['repository','path']),
 'follow':({'repository':str,'path':str,'index':int,'depth':int},['repository','path']),
 'inspect':({'repository':str,'path':str,'cell':int},['repository','path']),
 'skill_guide':({},[]),'session_status':({},[]),
}
GUIDE_FILES=['SKILL.md','references/exploration_workflow.md','references/access_policy.md','references/supported_formats.md']
PROXY='''import socket,sys,threading
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.connect('/bridge/skill.sock')
def incoming():
    with s.makefile('rb') as f:
        for line in f:sys.stdout.buffer.write(line);sys.stdout.buffer.flush()
t=threading.Thread(target=incoming,daemon=True);t.start()
for line in sys.stdin.buffer:s.sendall(line)
s.shutdown(socket.SHUT_WR);t.join(timeout=1)
'''
DESCRIPTIONS={
 'skill_guide':'Read the exact existing Skill and workflow/access/format instructions before source exploration. Policy text is not repository evidence.',
 'session_status':'Inspect this fixed session budget, stopping state and retained evidence IDs. Cannot create, reset, or switch a session.',
 'search':'Lexical keyword/filename search in approved files. Returns exact snippets and context, charged to the existing cumulative budget. Narrow repository/category where justified by the question.',
 'read':'Read exact original lines. Default at most 80 lines. Use start/end for bounded excerpts; repository-relative path only.',
 'section':'Read the section containing an observed source line. Large sections truncate at the fixed section size; consider bounded read if a later part is needed.',
 'symbol':'Read a Python AST symbol, qualified name or optional C/C++ symbol. Other languages are explicitly lexical.',
 'references':'Inspect a bounded prefix of static source references; not a runtime call graph. Returned list indices are valid for follow.',
 'follow':'Follow one verified reference by index, only within this fixed allowlist and cumulative depth limit.',
 'inspect':'Lightweight stdlib structure inspection of an approved configuration/notebook source; no execution.',
}
SCHEMA={'type':'object','properties':{
 'exploration_status':{'type':'string','enum':['EVIDENCE_COLLECTED','INSUFFICIENT_REPOSITORY_EVIDENCE','BUDGET_EXHAUSTED','TOOL_ERROR']},
 'evidence_ids':{'type':'array','items':{'type':'string'}},'summary':{'type':'string'},
 'unresolved_items':{'type':'array','items':{'type':'string'}}},
 'required':['exploration_status','evidence_ids','summary','unresolved_items'],'additionalProperties':False}


def load(path):return json.loads(Path(path).read_text())
def digest_file(path):return sha256(Path(path).read_bytes())
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()


def schemas():
    result=[]
    for name,(fields,required) in TOOLS.items():
        props={k:{'type':'string' if t is str else 'integer'} for k,t in fields.items()}
        if 'repository' in props:props['repository']['enum']=['SPHinXsys','SPHinXsim']
        if 'category' in props:props['category']['enum']=['documentation','cli_source']
        result.append({'name':name,'description':DESCRIPTIONS[name],
            'annotations':{'readOnlyHint':name in {'skill_guide','session_status'},'destructiveHint':False,'openWorldHint':False},
            'inputSchema':{'type':'object','properties':props,'required':required,'additionalProperties':False}})
    return result


def check_arguments(name,args):
    if name not in TOOLS:raise ValueError('Only fixed Skill tool names are accepted')
    if type(args) is not dict:raise ValueError('Arguments must be an object')
    fields,required=TOOLS[name]
    if set(args)-set(fields) or set(required)-set(args):raise ValueError('Unexpected or missing tool argument')
    for key,value in args.items():
        if type(value) is not fields[key]:raise ValueError('Invalid argument type: '+key)
    return dict(args)


class Broker:
    def __init__(self,session,log):
        self.session=Path(session);self.log=Path(log);self.lock=threading.Lock();self.stopped=False

    def call(self,name,args):
        args=check_arguments(name,args)
        if name=='skill_guide':
            folder=PROJECT/'.agents/skills/sphinx-doc-cli-explorer'
            return {'kind':'existing-skill-instructions','files':{f:(folder/f).read_text() for f in GUIDE_FILES}}
        if name=='session_status':
            s=load(self.session/'state.json')
            return {'scope':s['scope'],'budget':s['budget'],'used':s['used'],'files_explored':len(s['files_explored']),
              'evidence_ids':list(s['evidence']),'stopped_after_budget':self.stopped,'semantic_completeness':'NOT_ESTABLISHED'}
        if self.stopped:return {'status':'BUDGET_EXHAUSTED','reason':'A hard budget already ended this session; no further source operation permitted.'}
        if 'repository' in args:args['repo']=args.pop('repository')
        result=Session(self.session).run(name,**args)
        if result['status']=='BUDGET_EXHAUSTED':self.stopped=True
        return result

    def rpc(self,message):
        with self.lock:
            request_id=message.get('id');method=message.get('method');start=time.perf_counter()
            response=None
            try:
                if method=='initialize':
                    requested=message.get('params',{}).get('protocolVersion','2024-11-05')
                    result={'protocolVersion':requested,'capabilities':{'tools':{}},'serverInfo':{'name':'sphinx-step18c-fixed-session','version':'1'}}
                elif method=='ping':result={}
                elif method=='tools/list':result={'tools':schemas()}
                elif method=='tools/call':
                    params=message.get('params',{})
                    result={'content':[{'type':'text','text':json.dumps(self.call(params.get('name'),params.get('arguments',{})),ensure_ascii=False,sort_keys=True)}]}
                elif method and method.startswith('notifications/'):
                    result=None
                else:raise ValueError('Unknown RPC method')
                if request_id is not None:response={'jsonrpc':'2.0','id':request_id,'result':result}
            except (OSError,ValueError,TypeError,RuntimeError) as e:
                response={'jsonrpc':'2.0','id':request_id,'error':{'code':-32602,'message':str(e)}}
            event={'timestamp_utc':utc(),'method':method,'request':message,'response':response,'duration_seconds':time.perf_counter()-start}
            with self.log.open('a') as f:f.write(json.dumps(event,ensure_ascii=False,sort_keys=True)+'\n')
            return response


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            try:message=json.loads(line)
            except ValueError:continue
            reply=self.server.broker.rpc(message)
            if reply is not None:self.wfile.write(json.dumps(reply,ensure_ascii=False).encode()+b'\n');self.wfile.flush()


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads=True


def base_box(codex,box,bridge=None,auth=True):
    """No project, upstream, evaluation root or host home bind is permitted."""
    cmd=['bwrap','--unshare-user','--unshare-pid','--die-with-parent','--new-session',
      '--ro-bind','/usr','/usr','--symlink','usr/bin','/bin','--symlink','usr/lib','/lib',
      '--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/etc',
      '--ro-bind','/etc/ssl/certs','/etc/ssl/certs','--ro-bind','/etc/resolv.conf','/etc/resolv.conf',
      '--ro-bind','/etc/hosts','/etc/hosts','--dir','/home/agent','--tmpfs','/home/agent/.codex',
      '--dir','/opt','--ro-bind',str(codex),'/opt/codex',
      '--ro-bind',str(codex.parent/'codex-code-mode-host'),'/opt/codex-code-mode-host',
      '--ro-bind',str(box/'proxy.py'),'/opt/skill_proxy.py',
      '--ro-bind',str(box/'config.toml'),'/home/agent/.codex/config.toml',
      '--ro-bind',str(box/'schema.json'),'/opt/response-schema.json','--dir','/work',
      '--clearenv','--setenv','PATH','/opt:/usr/bin:/bin','--setenv','HOME','/home/agent',
      '--setenv','CODEX_HOME','/home/agent/.codex','--setenv','PYTHONDONTWRITEBYTECODE','1',
      '--setenv','LANG','C.UTF-8','--chdir','/work']
    if auth:cmd+=['--ro-bind','/home/zhang/.codex/auth.json','/home/agent/.codex/auth.json']
    if bridge:cmd+=['--ro-bind',str(bridge),'/bridge']
    return cmd


def build_config(model=None):
    values=['model_reasoning_effort = "low"','approval_policy = "never"','sandbox_mode = "read-only"','web_search = "disabled"',
      'project_doc_max_bytes = 0','[features]','shell_tool = false','shell_snapshot = false',
      'apps = false','enable_mcp_apps = false','multi_agent = false','memories = false',
      'code_mode = false','code_mode_host = true','[memories]','use_memories = false','generate_memories = false',
      '[mcp_servers.skill]','command = "/usr/bin/python3"','args = ["-B", "/opt/skill_proxy.py"]',
      'required = true','default_tools_approval_mode = "approve"','startup_timeout_sec = 15','tool_timeout_sec = 30',
      'enabled_tools = '+json.dumps(list(TOOLS))]
    if model:values.insert(0,'model = '+json.dumps(model))
    return '\n'.join(values)+'\n'


def box_files(output,model=None):
    box=output/'isolated-config';box.mkdir()
    (box/'proxy.py').write_text(PROXY);(box/'config.toml').write_text(build_config(model))
    write_json(box/'schema.json',SCHEMA)
    return box


def safe_cli_events(stdout):
    """Observable tool/messages/usage only; do not retain private reasoning events."""
    result=[]
    for line in stdout.splitlines():
        try:event=json.loads(line)
        except ValueError:continue
        item=event.get('item',{})
        if item.get('type') in {'reasoning','reasoning_summary'}:
            result.append({'type':event.get('type'),'item':{'type':item['type'],'content_omitted':True}})
        else:result.append(event)
    return result


def policy(root):
    env=load(root/'environment.json');baseline=Path(env['baseline'])
    return env,baseline,load(baseline/'frozen-experiment-config.json')


def verify(root):
    env,baseline,cfg=policy(root)
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=PROJECT,text=True).strip()==env['head']
    for rel,h in load(root/'S0-checksums.json').items():assert digest_file(PROJECT/rel)==h,rel
    assert digest_file(baseline/'frozen-benchmark.json')==env['benchmark_sha256']
    assert digest_file(PROJECT/'src/comparative_evaluation.py')==env['scoring_implementation_sha256']
    return env,baseline,cfg


def security_audit(root):
    root=Path(root);env,baseline,cfg=verify(root)
    if (root/'isolation-audit.json').exists():
        previous=1
        while (root/('isolation-audit-v'+str(previous)+'.json')).exists():previous+=1
        shutil.copy2(root/'isolation-audit.json',root/('isolation-audit-v'+str(previous)+'.json'))
    attempt=1
    while (root/('isolation-probes-'+str(attempt))).exists():attempt+=1
    out=root/('isolation-probes-'+str(attempt));out.mkdir()
    (out/'harness-source.py').write_bytes(Path(__file__).read_bytes())
    codex=Path(shutil.which('codex')).resolve();box=box_files(out)
    # Source/auth file contents are never copied into the runtime directory or archive.
    forbidden=[str(baseline/'frozen-benchmark.json'),str(baseline/'frozen-reference-answers.json'),
      str(PROJECT/'tests/retrieval_benchmarks/step_1_8b_comparative_v1.json'),str(PROJECT/'repos/SPHinXsim/sphinxsim/cli.py'),
      str(root/'environment.json'),str(Path('/home/zhang/.codex/sessions')),
      '/proc/1/root'+str(baseline/'frozen-benchmark.json')]
    source='import json,pathlib;paths='+repr(forbidden)+';print(json.dumps({p:pathlib.Path(p).exists() for p in paths}))'
    probe=subprocess.run(base_box(codex,box,auth=False)+['/usr/bin/python3','-B','-c',source],capture_output=True,text=True,timeout=20)
    write_json(out/'filesystem-negative-probe.json',{'command':base_box(codex,box,auth=False)+['/usr/bin/python3','-B','-c',source],
      'exit_code':probe.returncode,'stdout':probe.stdout,'stderr':probe.stderr})
    assert probe.returncode==0 and all(v is False for v in json.loads(probe.stdout).values()),probe.stderr
    # Verify native config support without an inference request or any credential read.
    config_probe=subprocess.run(base_box(codex,box,auth=False)+['/opt/codex','features','list'],capture_output=True,text=True,timeout=20)
    write_json(out/'config-feature-probe.json',{'exit_code':config_probe.returncode,'stdout':config_probe.stdout,'stderr':config_probe.stderr})
    assert config_probe.returncode==0,config_probe.stderr
    for key in ['shell_tool','apps','multi_agent','memories','code_mode']:
        line=next(x for x in config_probe.stdout.splitlines() if x.split()[0]==key)
        assert line.split()[-1]=='false',(key,line)
    assert next(x for x in config_probe.stdout.splitlines() if x.split()[0]=='code_mode_host').split()[-1]=='true'
    # Separate non-question session proves the unchanged central boundary and fixed budget.
    sessions=PROJECT.parent/'SPHinX_AI_Assistant_artifacts/skill_prototypes'/('step_1_8C_'+env['run_id'])
    session=Session.create(PROJECT,sessions/('audit-access-budget-'+str(attempt)),'curated')
    assert session.state['budget']==cfg['budget']
    denied=[]
    for repo,path in [('SPHinXsim','../../tests/retrieval_benchmarks/step_1_8b_comparative_v1.json'),('SPHinXsim','/etc/passwd'),('SPHinXsim','.env')]:
        r=session.run('read',repo=repo,path=path);assert r['status']=='ERROR';denied.append(r)
    approved=next(f for f in session.state['manifest']['files'] if f['repository']=='SPHinXsim' and f['format']=='markdown')
    reads=[]
    for i in range(6):reads.append(Session(session.output).run('read',repo=approved['repository'],path=approved['path'],start=1,end=1))
    assert reads[-1]['status']=='BUDGET_EXHAUSTED' and reads[-1]['used']['read_operations']==8
    write_json(out/'source-policy-budget-probe.json',{'denied':denied,'reads':reads,'session':str(session.output),
        'trace_sha256':digest_file(session.output/'trace.json'),'evidence_sha256':digest_file(session.output/'evidence.json')})
    result={'filesystem_boundary':'PASS','forbidden_paths_not_mounted':forbidden,'native_command_tools_disabled':True,
      'apps_and_child_agents_disabled':True,'memory_disabled':True,'network_web_search_disabled':True,
      'broker_only_source_access':'Fixed-session RPC whitelist; no create/reset/explore/exec/arbitrary-file method.',
      'Skill_policy_and_cumulative_budget':'PASS','nested_session_directories':'Allowed: validate_output requires a strict descendant, not a direct child.',
      'auth':'Existing ChatGPT login used only by Codex transport, read-only auth mount; never copied, printed or exposed by tools.',
      'harness_sha256':digest_file(Path(__file__)),
      'code_mode_host_sha256':digest_file(codex.parent/'codex-code-mode-host'),
      'codex':str(codex),'codex_sha256':digest_file(codex),'pilot_pass_required':True,'formal_execution_allowed':False}
    write_json(root/'isolation-audit.json',result)
    return result


NEUTRAL='''You are the retrieval agent in a repository-evidence collection task.
Read the existing Skill using skill_guide first. Then autonomously choose the available Skill source tools.
The tools call the unchanged approved implementation; use these interfaces instead of the CLI examples.
Use programming knowledge to plan, but repository facts require actual source text returned in this session.
Do not use web search, shell, external files, previous tasks, or guessed source text as evidence.
Use the one fixed session. Do not attempt another session or budget reset. Stop after any hard budget exhaustion.
Search results include exact source snippets; choose subsequent reads/sections/symbols based on observed results.
A section may start far before the matching line and be truncated; bounded line reading is available when useful.
Do not assert runtime success from static code. Return evidence IDs actually received, a concise grounded summary,
and any still-unresolved items. This is retrieval, not an unrestricted final answer. No grading feedback is available.
'''


def run_agent(root,question,condition,phase,model=None,timeout=210):
    root=Path(root);env,baseline,cfg=verify(root)
    assert (phase=='official' or re.fullmatch(r'pilot(?:-attempt[1-9][0-9]*)?',phase)) and condition in {'B-G','C-G'}
    output=root/phase/condition/question['id'];output.mkdir(parents=True,exist_ok=False)
    session_path=PROJECT.parent/'SPHinX_AI_Assistant_artifacts/skill_prototypes'/('step_1_8C_'+env['run_id'])/phase/condition/question['id']
    scope='curated' if condition=='B-G' else 'expanded'
    started=time.perf_counter();session=Session.create(PROJECT,session_path,scope)
    setup=time.perf_counter()-started
    assert session.state['budget']==cfg['budget']
    assert session.state['manifest']['manifest_sha256']==cfg['scope_manifest_ids'][scope]
    codex=Path(shutil.which('codex')).resolve();box=box_files(output,model)
    # Each agent gets a new empty home/work/tmp/PID space and only its own broker socket.
    with tempfile.TemporaryDirectory(prefix='s18c-bridge-') as bridge:
        broker=Broker(session_path,output/'mcp-observable.jsonl')
        server=Server(str(Path(bridge)/'skill.sock'),Handler);server.broker=broker
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            cmd=base_box(codex,box,bridge)+['/opt/codex','--no-daemon','exec','--strict-config','--ignore-rules',
                '--skip-git-repo-check','--ephemeral','--json','--color','never','--output-schema','/opt/response-schema.json','-']
            prompt=NEUTRAL+'\nQuestion ID: '+question['id']+'\nQuestion: '+question['question']+'\nScope: '+scope+'\nBudget: '+json.dumps(cfg['budget'],sort_keys=True)+'\n'
            write_json(output/'agent-input.json',{'question':question,'condition':condition,'scope':scope,'budget':cfg['budget'],'prompt':prompt,
                'no_oracle_or_other_result_input':True})
            start=time.perf_counter()
            try:
                proc=subprocess.run(cmd,input=prompt,capture_output=True,text=True,timeout=timeout)
                stdout,stderr,exitcode=proc.stdout,proc.stderr,proc.returncode;timed_out=False
            except subprocess.TimeoutExpired as e:
                stdout=e.stdout or '';stderr=e.stderr or ''
                if isinstance(stdout,bytes):stdout=stdout.decode(errors='replace')
                if isinstance(stderr,bytes):stderr=stderr.decode(errors='replace')
                exitcode=None;timed_out=True
            elapsed=time.perf_counter()-start
        finally:server.shutdown();server.server_close();thread.join(timeout=2)
    events=safe_cli_events(stdout)
    write_json(output/'agent-observable-events.json',events)
    # CLI stderr normally has no credentials; proactively redact token-looking values.
    stderr=re.sub(r'(?:sk-[A-Za-z0-9_-]{12,}|eyJ[A-Za-z0-9_-]{25,}(?:\.[A-Za-z0-9_-]+){1,2})','[REDACTED]',stderr)
    (output/'agent-stderr.log').write_text(stderr)
    state=load(session_path/'state.json')
    if state['trace']:retained,stream=skill_evidence_stream(session_path)
    else:retained,stream=[],[]
    final=None;usage=[];thread_id=None
    for event in events:
        if event.get('type')=='thread.started':thread_id=event.get('thread_id')
        if event.get('usage'):usage.append(event['usage'])
        item=event.get('item',{})
        if item.get('type')=='agent_message':
            try:final=json.loads(item.get('text',''))
            except ValueError:pass
    write_json(output/'final-evidence-report.json',{'question_id':question['id'],'condition':condition,'session_id':str(session_path),
      'agent_report':final,'actual_evidence':retained,'returned_source_stream':stream,'used':state['used'],
      'files_explored':state['files_explored'],'trace':state['trace'],'thread_id':thread_id,
      'unknown_agent_evidence_ids':sorted(set(final.get('evidence_ids',[]) if final else [])-{e['evidence_id'] for e in retained})})
    metadata={'question_id':question['id'],'condition':condition,'phase':phase,'exit_code':exitcode,'timeout':timed_out,
      'session_setup_seconds':setup,'agent_wall_seconds':elapsed,'tool_seconds':sum(load(p)['duration_seconds'] for p in (session_path/'runtime').glob('*.json')),
      'usage':usage,'thread_id':thread_id,'final_report_received':final is not None,
      'recorded_source_operations':len(state['trace']),'used':state['used'],'source_characters_scanned':sum(a['characters_scanned'] for t in state['trace'] for a in t['source_accesses']),
      'session':str(session_path),'command':cmd,'codex_model_requested':model or 'installed CLI default',
      'scope_manifest_sha256':state['manifest']['manifest_sha256']}
    write_json(output/'execution.json',metadata)
    print(json.dumps({'id':question['id'],'condition':condition,'phase':phase,'exit':exitcode,'final':final is not None,'operations':len(state['trace'])}),flush=True)
    return output,metadata


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['audit','pilot','official'])
    p.add_argument('--experiment',required=True,type=Path);p.add_argument('--model');p.add_argument('--pilot-attempt',type=int,default=1);a=p.parse_args()
    if a.command=='audit':security_audit(a.experiment);return
    if a.command=='pilot':
        assert load(a.experiment/'isolation-audit.json')['harness_sha256']==digest_file(Path(__file__))
        assert a.pilot_attempt>=1
        phase='pilot' if a.pilot_attempt==1 else 'pilot-attempt'+str(a.pilot_attempt)
        pilots=[{'id':'P01','question':'Which shell-completion formats can the SPHinXsim CLI generate, and how does the user select one?','condition':'B-G'},
          {'id':'P02','question':'Which SPHinXsim visualization flags tell a caller whether VTP geometry or cached shape bounds were used?','condition':'C-G'},
          {'id':'P03','question':'How should a SPHinXsys regression test set its CMake add_test working directory?','condition':'C-G'}]
        questions_file=a.experiment/'pilot-questions.json'
        if questions_file.exists():assert load(questions_file)==pilots
        else:write_json(questions_file,pilots)
        for q in pilots:
            _,metadata=run_agent(a.experiment,{'id':q['id'],'question':q['question']},q['condition'],phase,a.model)
            if metadata['recorded_source_operations']==0:break
        return
    assert load(a.experiment/'pilot-verification.json')['all_passed'],'Pilot has not passed'
    assert load(a.experiment/'isolation-audit.json')['harness_sha256']==digest_file(Path(__file__))
    frozen=load(a.experiment/'agent-freeze.json')
    assert frozen['harness_sha256']==digest_file(Path(__file__)) and frozen['model']==a.model
    questions=load(a.experiment/'baseline-reference/public-questions.json')
    assert len(questions)==20
    # Two workers at most, fresh process/context/socket per question-condition pair.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures=[executor.submit(run_agent,a.experiment,q,c,'official',a.model) for q in questions for c in ['B-G','C-G']]
        for future in concurrent.futures.as_completed(futures):future.result()


if __name__=='__main__':main()
