#!/usr/bin/env python3
"""Read-only exploration. Create once; resume the same session across tool calls."""
import argparse
import json
from pathlib import Path
import sys

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from src.skill_guided_retrieval import Session,build_scopes
from src.skill_guided_retrieval.explorer import write_json
from src.skill_guided_retrieval.scope import validate_output


def parser():
    p=argparse.ArgumentParser(description=__doc__);subs=p.add_subparsers(dest='operation',required=True)
    for name in ['create','audit','formats','search','read','section','symbol','references','follow','inspect','explore']:
        s=subs.add_parser(name)
        if name in {'create','audit','explore'}:s.add_argument('--output-dir',type=Path)
        if name not in {'create','audit','formats'}:s.add_argument('--session-dir',type=Path)
        if name in {'create','explore','formats'}:s.add_argument('--scope',choices=['curated','expanded'],default=None if name=='explore' else 'curated')
        if name in {'create','explore'}:
            fields=['search_operations','read_operations','files_explored','evidence_records','returned_characters','max_section_characters','reference_depth']
            for key in fields:
                flag=key.replace('_','-') if key.startswith('max_') else 'max-'+key.replace('_','-')
                s.add_argument('--'+flag,type=int,dest='budget_'+key)
        if name in {'search','explore'}:
            s.add_argument('--query',required=True);s.add_argument('--repository',choices=['SPHinXsys','SPHinXsim']);s.add_argument('--category',choices=['documentation','cli_source'])
        if name in {'read','section','symbol','references','follow','inspect'}:
            s.add_argument('--repository',choices=['SPHinXsys','SPHinXsim'],required=True);s.add_argument('--path',required=True)
        if name=='search':s.add_argument('--limit',type=int,default=6);s.add_argument('--context',type=int,default=1)
        if name=='read':s.add_argument('--start',type=int,default=1);s.add_argument('--end',type=int);s.add_argument('--context',type=int,default=0)
        if name=='section':s.add_argument('--line',type=int,default=1)
        if name=='symbol':s.add_argument('--name',required=True)
        if name=='references':s.add_argument('--limit',type=int,default=20)
        if name=='follow':s.add_argument('--index',type=int,default=0);s.add_argument('--depth',type=int,default=1)
        if name=='inspect':s.add_argument('--cell',type=int)
    return p


def main(argv=None):
    p=parser();a=p.parse_args(argv)
    try:
        if a.operation in {'audit','formats'}:
            audit=build_scopes(PROJECT)
            if a.operation=='audit':
                if not a.output_dir:p.error('audit requires --output-dir')
                d=validate_output(PROJECT,a.output_dir)
                if d.exists():raise FileExistsError('Output exists')
                d.mkdir(parents=True,exist_ok=False);write_json(d/'audit.json',audit)
                for name,m in audit['manifests'].items():write_json(d/(name+'-manifest.json'),m)
                result={'files':len(audit['inventory']),'curated':len(audit['manifests']['curated']['files']),'expanded':len(audit['manifests']['expanded']['files'])}
            else:result={'scope':a.scope,'formats':audit['formats']}
        elif a.operation=='create' or (a.operation=='explore' and a.output_dir):
            if not a.output_dir:p.error('create requires --output-dir')
            if getattr(a,'session_dir',None):p.error('Choose new --output-dir or existing --session-dir')
            budget={k[7:]:v for k,v in vars(a).items() if k.startswith('budget_') and v is not None}
            session=Session.create(PROJECT,a.output_dir,a.scope or 'curated',budget)
            result=session.explore(a.query,a.repository,a.category) if a.operation=='explore' else {'created':True,'scope':a.scope,'budget':session.state['budget']}
        else:
            if not a.session_dir:p.error('Operation requires --session-dir')
            if a.operation=='explore' and any(v is not None for k,v in vars(a).items() if k.startswith('budget_')):p.error('Budgets are fixed at session creation')
            session=Session(a.session_dir)
            if a.operation=='explore' and a.scope is not None and a.scope!=session.state['scope']:raise ValueError('Scope is fixed at session creation')
            if a.operation=='explore':result=session.explore(a.query,a.repository,a.category)
            else:
                params={k:v for k,v in vars(a).items() if k not in {'operation','session_dir'}}
                if 'repository' in params:params['repo']=params.pop('repository')
                result=session.run(a.operation,**params)
        print(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True))
        return 2 if result.get('status')=='ERROR' else 0
    except (OSError,ValueError,RuntimeError) as e:
        print(f'{type(e).__name__}: {e}',file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
