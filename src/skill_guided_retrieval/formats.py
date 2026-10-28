"""Capabilities are implementation claims, never file-access authorization."""
from dataclasses import dataclass, asdict
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
import importlib.util


@dataclass(frozen=True)
class Format:
    name: str
    patterns: tuple[str, ...]
    category: str
    textual: bool = True
    status: str = 'TEXT_SUPPORTED'
    section: str | None = None
    symbol: str | None = None
    structure: str | None = None
    adapter: str = 'utf8-lines-v1'
    limitations: str = 'UTF-8 text only; no execution, rendering or schema validation.'
    optional_dependency: str | None = None


FORMATS = []

def add(name, patterns, category='documentation', **kw):
    FORMATS.append(Format(name, tuple(patterns.split()), category, **kw))

# Special filenames precede broad suffix recognition.
add('environment-template', '.env.example .env.sample .env.template', 'configuration')
add('requirements', 'requirements*.txt', 'configuration')
add('python-project', 'pyproject.toml setup.cfg', 'configuration')
add('conda-environment', 'environment.yml environment.yaml', 'configuration')
add('pipfile', 'Pipfile', 'configuration')
add('build-config', 'setup.py tox.ini pytest.ini', 'source', symbol=None)
add('cmake', 'CMakeLists.txt *.cmake', 'source')
add('make', 'Makefile GNUmakefile *.mk', 'source')
add('dockerfile', 'Dockerfile Dockerfile.* *.dockerfile', 'source')
for name,pat,section in [
    ('markdown','*.md *.markdown','markdown'),('rst','*.rst *.rest','rst'),
    ('plain-text','*.txt *.text',None),('mdx','*.mdx','markdown'),
    ('quarto','*.qmd','markdown'),('r-markdown','*.rmd','markdown'),
    ('asciidoc','*.adoc *.asciidoc *.asc','asciidoc'),('org','*.org','org'),
    ('html','*.html *.htm *.xhtml','html'),('latex','*.tex *.latex *.ltx','latex'),
    ('bibtex','*.bib',None)]:
    add(name,pat,section=section,status='STRUCTURED_SUPPORTED' if section else 'TEXT_SUPPORTED',
        limitations='Basic headings only; embedded code/JS/LaTeX not executed; not a renderer.' if section else 'Lexical text only; no bibliography parser.')
for name,pat,dep in [('pdf','*.pdf','pypdf'),('word','*.docx *.doc','docx'),
    ('odt','*.odt','odf'),('rtf','*.rtf','striprtf'),('epub','*.epub','ebooklib'),
    ('powerpoint','*.pptx *.ppt','pptx'),('odp','*.odp','odf'),
    ('excel','*.xlsx *.xls','openpyxl'),('ods','*.ods','odf')]:
    add(name,pat,textual=False,status='REGISTERED_ADAPTER_UNAVAILABLE',adapter='binary-adapter-protocol-v1',
        optional_dependency=dep,limitations='No implemented/tested binary adapter in V1, including legacy variants; never UTF-8 decoded.')
for name,pat in [('json','*.json'),('jsonc','*.jsonc'),('json5','*.json5'),
    ('jsonl','*.jsonl *.ndjson'),('yaml','*.yaml *.yml'),('toml','*.toml'),
    ('xml','*.xml'),('csv','*.csv'),('tsv','*.tsv'),('ini','*.ini'),
    ('cfg','*.cfg'),('conf','*.conf'),('properties','*.properties'),('protobuf','*.proto'),
    ('sql','*.sql'),('notebook','*.ipynb')]:
    structure = name if name in {'json','jsonl','csv','tsv','toml','ini','notebook'} else None
    add(name,pat,'configuration',structure=structure,status='STRUCTURED_SUPPORTED' if structure else 'TEXT_SUPPORTED')
for name,pat in [('python','*.py *.pyi'),('c','*.c *.h'),('cpp','*.cpp *.cc *.cxx *.hpp *.hh *.hxx *.ipp *.tpp *.inl'),
    ('cuda','*.cu *.cuh'),('shell','*.sh *.bash *.zsh *.fish'),('powershell','*.ps1 *.psm1'),
    ('windows-script','*.bat *.cmd'),('javascript','*.js *.mjs *.cjs *.jsx'),('typescript','*.ts *.tsx'),
    ('rust','*.rs'),('go','*.go'),('java','*.java'),('csharp','*.cs'),('matlab','*.m'),
    ('fortran','*.f *.f90 *.f95'),('ruby','*.rb'),('perl','*.pl *.pm')]:
    symbol='python' if name=='python' else 'tree-sitter' if name in {'c','cpp'} else None
    add(name,pat,'source',symbol=symbol,status='STRUCTURED_SUPPORTED' if symbol else 'TEXT_SUPPORTED',
        limitations='C headers use C++ grammar for optional navigation; parser errors reported.' if name=='c' else 'Generic text; Python AST or optional existing C++ Tree-sitter only.')
add('svg','*.svg','artifact',textual=True,status='METADATA_ONLY',adapter='metadata-only',limitations='Textual XML but deliberately not enabled as exploration source.')
add('image','*.png *.jpg *.jpeg *.webp *.gif *.bmp','artifact',textual=False,status='METADATA_ONLY',adapter='metadata-only')
add('archive','*.tar.gz *.tgz *.tar *.zip *.gz *.7z *.whl','artifact',textual=False,status='UNSUPPORTED',adapter='none')
add('native-library','*.so *.dll *.dylib','artifact',textual=False,status='UNSUPPORTED',adapter='none')
add('serialized-data','*.pkl *.pickle *.npy *.npz *.h5 *.hdf5','artifact',textual=False,status='UNSUPPORTED',adapter='none')


def recognize(path):
    name=PurePosixPath(path).name.lower()
    return next((f for f in FORMATS if any(fnmatchcase(name,p.lower()) for p in f.patterns)),None)


def text_enabled(fmt):
    return bool(fmt and fmt.textual and fmt.status in {'TEXT_SUPPORTED','STRUCTURED_SUPPORTED'})


class BinaryAdapter:
    """Future adapters must return exact content plus verifiable non-line locations."""
    def extract(self, source_bytes):
        raise NotImplementedError('REGISTERED_ADAPTER_UNAVAILABLE')


def capabilities(counts=None,scope_counts=None):
    result=[]
    for f in FORMATS:
        row=asdict(f);n=(counts or {}).get(f.name,0)
        cpp_available=importlib.util.find_spec('tree_sitter_language_pack') is not None
        row.update(search=text_enabled(f),read=text_enabled(f),section_capability=f.section,
            symbol_capability=f.symbol if f.symbol!='tree-sitter' or cpp_available else None,
            structure_capability=f.structure,inventory_count=n,
            presence='PRESENT' if n else 'NOT_PRESENT_IN_PINNED_REPOS',
            allowed_counts=(scope_counts or {}).get(f.name,{}),binary_adapter_implemented=False if not f.textual else None)
        if f.symbol=='tree-sitter' and not cpp_available:
            row['status']='TEXT_SUPPORTED';row['limitations']+=' Existing parser unavailable in this Python environment.'
        result.append(row)
    return result
