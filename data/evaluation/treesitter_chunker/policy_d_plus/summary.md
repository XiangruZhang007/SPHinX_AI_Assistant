# Policy D+ Retrieval Simulation

This analysis consumes existing raw JSON only. It does not rerun treesitter-chunker or read upstream source files.

- D1: function/method chunks only.
- D2: replace functions over 5,000 bytes with fixed 50-line windows.
- D3: replace functions over 5,000 bytes with contiguous segments whose internal boundaries align to top-level non-comment Tree-sitter statement/block nodes where possible.

## Aggregate results

| Policy | Chunks | Min B | Max B | Mean B | Median B | Overlap factor | Oversized >5000 B |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| D1 | 94 | 22 | 11148 | 247.34 | 71.5 | 1.00 | 1 |
| D2 | 98 | 15 | 3248 | 237.24 | 74.5 | 1.00 | 0 |
| D3 | 96 | 22 | 4658 | 242.19 | 74.5 | 1.00 | 0 |

## Dambreak main()

See `dambreak_main_examples.json` for exact spans, parent metadata, boundary labels, and unmodified substring content for D2 and D3.

D2 boundaries are fixed line-count boundaries. D3 boundaries are Tree-sitter top-level statement/block boundaries; every D3 subchunk retains the original function's ID and full span as parent metadata.
