"""Narrow C++ evidence additions for field-only structs and named enums.

Use existing Tree-sitter; preserve original source slices and existing records.
"""
from hashlib import sha1
from pathlib import Path
from tree_sitter_language_pack import get_parser


def _walk(node):
    yield node
    for child in node.named_children:
        yield from _walk(child)


def data_definitions(source_path: Path):
    """Yield named enums and field-only structs; never duplicate method classes.

    Called only for already-discovered SPHinXsim simulator-library files.
    The newline convention matches chunk_file's existing text reader.
    """
    source = source_path.read_text(encoding='utf-8').encode('utf-8')
    tree = get_parser('cpp').parse(source)
    for node in _walk(tree.root_node):
        if node.type not in {'struct_specifier', 'enum_specifier'}:
            continue
        body = node.child_by_field_name('body'); name = node.child_by_field_name('name')
        if body is None or name is None or node.has_error:
            continue
        if node.type == 'struct_specifier':
            meaningful = [n for n in body.named_children if n.type != 'comment']
            if not meaningful or any(n.type != 'field_declaration' for n in meaningful):
                continue
            if any(n.type in {'function_declarator', 'function_definition'} for n in _walk(body)):
                continue
        start, end = node.start_byte, node.end_byte
        if source[end:end+1] == b';':
            end += 1
        content = source[start:end].decode('utf-8'); symbol = source[name.start_byte:name.end_byte].decode('utf-8')
        identity = f'cpp-data-v1:{source_path}:{start}:{end}:{content}'
        yield {'chunk_id': sha1(identity.encode()).hexdigest(), 'file_path': str(source_path),
            'node_type': node.type, 'start_line': source[:start].count(b'\n')+1,
            'end_line': source[:end].count(b'\n')+1, 'byte_start': start, 'byte_end': end,
            'content': content, 'parent_context': node.parent.type if node.parent else '',
            'parent_route': [], 'qualified_route': [f'{node.type}:{symbol}'],
            'metadata': {'signature': {'name': symbol}, 'extraction': 'field-only-struct-or-named-enum-v1'}}


def recover_delegating_constructors(source_path: Path, chunks: list[dict]) -> list[dict]:
    """Repair one specific grammar boundary pattern, retaining original bytes.

    Some nested `>>` template closers parse as an ERROR signature followed by
    a fake function for the initializer. Accept only adjacent nodes that become
    one complete qualified constructor when template closers are spaced in a
    validation-only copy. Emit the untouched original source, not that copy.
    """
    import copy
    import re
    from dataclasses import asdict
    from chunker import chunk_text

    source = source_path.read_text(encoding='utf-8').encode('utf-8')
    parser = get_parser('cpp'); tree = parser.parse(source)
    if not tree.root_node.has_error:
        return chunks
    replacements = {}
    for parent in _walk(tree.root_node):
        nodes = parent.named_children
        for error, function in zip(nodes, nodes[1:]):
            if error.type != 'ERROR' or function.type != 'function_definition':
                continue
            prefix = source[error.start_byte:function.start_byte]
            head = re.match(rb'(?P<owner>[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)::(?P<name>[A-Za-z_]\w*)\s*\(', prefix)
            if (not head or head['owner'].split(b'::')[-1] != head['name']
                    or b'>>' not in prefix or not prefix.rstrip().endswith(b':')):
                continue
            declarator = function.child_by_field_name('declarator')
            name = declarator.child_by_field_name('declarator') if declarator else None
            if name is None or source[name.start_byte:name.end_byte] != head['name']:
                continue
            start, end = error.start_byte, function.end_byte
            candidate = source[start:end]; validation = candidate.replace(b'>>', b'> >')
            fixed = parser.parse(validation)
            children = fixed.root_node.named_children
            if (fixed.root_node.has_error or len(children) != 1 or children[0].type != 'function_definition'
                    or children[0].start_byte != 0 or children[0].end_byte != len(validation)):
                continue
            raw = next((r for r in chunks if r['node_type'] == 'function_definition'
                        and r['byte_start'] == function.start_byte and r['byte_end'] == end), None)
            if raw is None:
                continue
            # Metadata comes from the validated complete syntax; original bytes
            # remain the sole retrieval content and provenance authority.
            parsed_chunks = [asdict(c) for c in chunk_text(validation.decode(), 'cpp', str(source_path))
                             if c.node_type in {'function_definition', 'method_declaration'}]
            if len(parsed_chunks) != 1:
                continue
            complete = parsed_chunks[0]
            repaired = copy.deepcopy(raw)
            repaired.update(byte_start=start, byte_end=end, start_line=source[:start].count(b'\n')+1,
                            end_line=source[:end].count(b'\n')+1, content=candidate.decode())
            repaired['chunk_id'] = sha1(f'cpp-recovered-constructor-v1:{source_path}:{start}:{end}:{candidate!r}'.encode()).hexdigest()
            repaired['metadata'] = complete.get('metadata', {})
            repaired['metadata']['source_signature'] = prefix.rstrip().rstrip(b':').rstrip().decode()
            repaired['metadata']['recovery'] = {'method': 'adjacent-error-template-closer-validation-v1',
                'replaced_fragment_id': raw['chunk_id'], 'validation_only_spacing': True}
            repaired['qualified_route'] = [*raw.get('qualified_route', [])[:-1],
                                           'function_definition:' + head['name'].decode()]
            # Fragment-derived identities must not describe the complete syntax.
            for key in ('node_id', 'symbol_id', 'definition_id'):
                repaired[key] = sha1((key + ':' + repaired['chunk_id']).encode()).hexdigest()
            replacements[raw['chunk_id']] = repaired
    return [replacements.get(r['chunk_id'], r) for r in chunks]
