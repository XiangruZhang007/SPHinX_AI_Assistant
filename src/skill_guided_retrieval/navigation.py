"""Pure, conservative navigation over already-authorized source text."""
import ast
import configparser
import csv
import html
import io
import json
import re
import tomllib


def headings(text,kind):
    lines=text.splitlines(keepends=True);found=[];fence=None;rst_levels={};skip=set()
    for i,line in enumerate(lines):
        if i in skip:continue
        value=line.rstrip('\r\n')
        if kind=='markdown':
            marker=re.match(r'^ {0,3}(`{3,}|~{3,})',value)
            if marker:
                char=marker[1][0]
                if fence is None:fence=(char,len(marker[1]))
                elif char==fence[0] and len(marker[1])>=fence[1]:fence=None
                continue
            if fence:continue
            m=re.match(r'^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$',value)
            if m:found.append((i+1,len(m[1]),m[2]))
            elif value.strip() and i+1<len(lines) and re.fullmatch(r' {0,3}(=+|-+)\s*',lines[i+1].rstrip('\r\n')):
                found.append((i+1,1 if lines[i+1].lstrip().startswith('=') else 2,value.strip()));skip.add(i+1)
        elif kind=='rst':
            def adorn(x):
                x=x.rstrip('\r\n')
                return len(x)>=3 and len(set(x))==1 and x[0] in '=-~^"`:+*#_' and not x[0].isspace()
            # Exclude indented directive/code content from heading detection.
            if not value or value[0].isspace():continue
            if adorn(value) and i+2<len(lines) and lines[i+1].strip() and not lines[i+1][0].isspace() and lines[i+2].rstrip('\r\n')==value:
                key=('over',value[0]);rst_levels.setdefault(key,len(rst_levels)+1)
                found.append((i+1,rst_levels[key],lines[i+1].strip()));skip.update((i+1,i+2))
            elif not adorn(value) and i+1<len(lines) and adorn(lines[i+1]) and len(lines[i+1].rstrip('\r\n'))>=len(value):
                key=('under',lines[i+1][0]);rst_levels.setdefault(key,len(rst_levels)+1)
                found.append((i+1,rst_levels[key],value));skip.add(i+1)
        elif kind=='asciidoc':
            m=re.match(r'^(={1,6})\s+(.+)$',value)
            if m:found.append((i+1,len(m[1]),m[2]))
        elif kind=='org':
            m=re.match(r'^(\*+)\s+(.+)$',value)
            if m:found.append((i+1,len(m[1]),m[2]))
        elif kind=='latex':
            m=re.search(r'\\((?:sub)*section|chapter|part)\*?\{([^{}]+)\}',value)
            if m:found.append((i+1,{'part':1,'chapter':2,'section':3,'subsection':4,'subsubsection':5}.get(m[1],6),m[2]))
    if kind=='html':
        for m in re.finditer(r'<h([1-6])\b[^>]*>(.*?)</h\1\s*>',text,re.I|re.S):
            found.append((text[:m.start()].count('\n')+1,int(m[1]),html.unescape(re.sub('<[^>]+>','',m[2])).strip()))
    return sorted(found)


def section_range(text,kind,line):
    lines=text.splitlines(keepends=True);hs=headings(text,kind)
    if not 1<=line<=len(lines):raise ValueError('Line outside source')
    preceding=[h for h in hs if h[0]<=line]
    if not preceding:return 1,(hs[0][0]-1 if hs else len(lines)),'Document preamble'
    start,level,title=preceding[-1]
    end=next((h[0]-1 for h in hs if h[0]>start and h[1]<=level),len(lines))
    return start,end,title


def symbols(text,language):
    if language=='python':
        tree=ast.parse(text);result=[]
        def walk(node,route):
            for child in ast.iter_child_nodes(node):
                if isinstance(child,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
                    name='.'.join((*route,child.name));start=min([child.lineno,*[d.lineno for d in child.decorator_list]])
                    result.append({'name':name,'short_name':child.name,'start_line':start,'end_line':child.end_lineno,'parser':'python-ast'})
                    walk(child,(*route,child.name))
                else:walk(child,route)
        walk(tree,());return result
    if language=='tree-sitter':
        try:from tree_sitter_language_pack import get_parser
        except ImportError:raise NotImplementedError('Structured C/C++ symbols unavailable; generic search/read remain available')
        data=text.encode();tree=get_parser('cpp').parse(data);result=[]
        def walk(n):
            if n.type in {'function_definition','declaration','field_declaration','class_specifier','struct_specifier','enum_specifier'}:
                dec=n.child_by_field_name('declarator') or n.child_by_field_name('name')
                while dec is not None and dec.child_by_field_name('declarator') is not None:dec=dec.child_by_field_name('declarator')
                if dec is not None:
                    name=data[dec.start_byte:dec.end_byte].decode()
                    if len(name)<200:result.append({'name':name,'short_name':name.split('::')[-1],'start_line':n.start_point[0]+1,'end_line':n.end_point[0]+1,'parser':'existing-tree-sitter-cpp','parse_has_errors':tree.root_node.has_error})
            for c in n.named_children:walk(c)
        walk(tree.root_node);return result
    raise NotImplementedError('No structured symbol parser; use lexical search')


def references(text,path):
    result=[]
    if path.endswith(('.py','.pyi')):
        try:tree=ast.parse(text)
        except SyntaxError:return []
        for n in ast.walk(tree):
            if isinstance(n,ast.ImportFrom) and n.module:
                # Resolve only explicit static package imports; no dynamic evaluation.
                module=n.module
                if n.level:
                    from pathlib import PurePosixPath
                    parent=PurePosixPath(path).parent.parts
                    module='.'.join((*parent[:len(parent)-n.level+1],module)).strip('.')
                result.append({'kind':'python-import','target':module.replace('.','/')+'.py','alternative':module.replace('.','/')+'/__init__.py','line':n.lineno,'evidence':ast.get_source_segment(text,n),'runtime_dependency_confirmed':False})
            elif isinstance(n,ast.Call) and isinstance(n.func,ast.Name):
                result.append({'kind':'same-file-symbol','symbol':n.func.id,'target':path,'line':n.lineno,'runtime_dependency_confirmed':False})
    else:
        for i,line in enumerate(text.splitlines(),1):
            for m in re.finditer(r'\[[^\]]*\]\(([^)\s]+)\)',line):
                target=m[1].split('#')[0]
                if target:result.append({'kind':'relative-document-link','target':target,'line':i,'runtime_dependency_confirmed':False})
    return sorted(result,key=lambda x:(x['line'],x.get('target',''),x.get('symbol','')))


def inspect_structure(text,kind,cell=None):
    if kind in {'json','notebook'}:data=json.loads(text)
    elif kind=='jsonl':data=[json.loads(l) for l in text.splitlines() if l.strip()]
    elif kind in {'csv','tsv'}:data=list(csv.reader(io.StringIO(text,newline=''),delimiter=',' if kind=='csv' else '\t'))
    elif kind=='toml':data=tomllib.loads(text)
    elif kind=='ini':
        data=configparser.ConfigParser(interpolation=None);data.read_string(text)
        return {'kind':kind,'section_count':len(data.sections())},None
    else:raise NotImplementedError('No structure adapter for this format; exact lines remain available')
    if kind=='notebook':
        cells=data.get('cells',[])
        if cell is not None:
            if not 0<=cell<len(cells):raise ValueError('Cell index outside notebook')
            c=cells[cell];source=c.get('source','');source=''.join(source) if isinstance(source,list) else source
            return {'kind':kind,'location':{'cell_index':cell,'cell_id':c.get('id')},'cell_type':c.get('cell_type')},source
        return {'kind':kind,'cell_count':len(cells),'cells':[{'index':i,'id':c.get('id'),'type':c.get('cell_type')} for i,c in enumerate(cells)]},None
    return {'kind':kind,'container':type(data).__name__,'item_count':len(data) if isinstance(data,(dict,list)) else None},None
