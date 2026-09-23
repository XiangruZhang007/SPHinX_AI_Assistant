# Tree-sitter Chunker Retrieval-Selection Analysis

This analysis reads existing raw evaluation JSON only; it does not rerun chunking or access upstream source files.

## Per-file overview

| File | Raw chunks | Nested chunks | Duplication factor | Chunks <100 B | Chunks >2000 B |
| --- | ---: | ---: | ---: | ---: | ---: |
| Dambreak.cpp | 3 | 1 | 1.02 | 0 | 1 |
| base_body.h | 81 | 80 | 2.73 | 64 | 2 |
| scalar_functions.cpp | 4 | 3 | 1.85 | 1 | 1 |
| sphinxsys_variable.h | 121 | 120 | 3.96 | 70 | 3 |
| test_scalar_functions.cpp | 2 | 0 | 1.00 | 0 | 0 |

## Policy definitions

- **A:** retain all raw chunks.
- **B:** retain class, struct, function, method, and template node types.
- **C:** start from B; remove a candidate strictly contained in a larger retained candidate. Strict containment gives 100% byte coverage of the removed candidate, satisfying the ≥90% high-overlap criterion.
- **D:** retain function and method chunks for retrieval; retain class, struct, and template chunks as context-only metadata.

## Policy advantages and disadvantages

- **A:** preserves every structural signal, but retains all hierarchy-driven overlap and tiny declarations.
- **B:** filters fields, namespaces, and type definitions, but retains overlap among class/template/function levels.
- **C:** reduces overlap substantially, but can remove granular functions or methods if their enclosing class/template is retained.
- **D:** retains granular functions/methods while keeping parents as context, but leaves long functions intact and needs external parent-context association.

See `per_file_analysis.json`, `policy_comparison.json`, and `overlap_examples.json` for the complete computed data.
