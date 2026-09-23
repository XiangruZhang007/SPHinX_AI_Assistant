# SPHinXsim-specific Tree-sitter Chunking Validation

Scope is limited to the declared SPHinXsim-specific directories. `sphinxsim/sphinxsys/` was excluded and not read by this validation.

Inventory: 103 files across 7 requested directories.

## Representative compatibility checks

| File | Language | Parse | Raw chunks | Functions intact | Classes intact | Tiny <100 B | >5 KB |
| --- | --- | --- | ---: | --- | --- | ---: | ---: |
| `repos/SPHinXsim/sphinxsim/bindings/loader.py` | python | success | 6 | True | True | 0 | 0 |
| `repos/SPHinXsim/sphinxsim/config/schemas.py` | python | success | 104 | True | True | 5 | 5 |
| `repos/SPHinXsim/sphinxsim/sph_simulation/simulation_builder/base_simulation_builder.cpp` | cpp | success | 19 | True | True | 2 | 1 |
| `repos/SPHinXsim/sphinxsim/bindings/sphinxsys_python.cpp` | cpp | success | 1 | True | True | 0 | 0 |
| `repos/SPHinXsim/examples/test_simulation_2d.py` | python | success | 3 | True | True | 0 | 0 |
| `repos/SPHinXsim/examples/input/test_simulation_2d/config.json` | json | not checked (input/config) | — | — | — | — | — |
| `repos/SPHinXsim/tests/test_schemas.py` | python | success | 86 | True | True | 0 | 1 |
| `repos/SPHinXsim/tests/test_simulation/test_2d_simulation/simulation.cpp` | cpp | success | 8 | True | True | 1 | 0 |

## Tentative Prototype V1 policy

- **simulator_library**: use language-specific Tree-sitter structural parsing; retrieve function/method units, retaining enclosing class/struct context as metadata.
- **config/schema**: treat JSON input/config files as atomic configuration documents for this prototype; apply structural Python chunks to schema code.
- **simulation_example**: use Python function/class structural chunks; preserve module and class context.
- **test**: use Python/C++ function or test-case-level structural chunks.
- **llm_support** and **visualization**: use Python function/class structural chunks with class/module context metadata.

This is a compatibility-based demo policy only; no full SPHinXsim production chunks were generated.

Parse failures in this representative set: 0.
