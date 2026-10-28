# Step 1.6 benchmark versions

Active evaluated source-inspected benchmark: `combined_step_1_6_v2.json` (30 new questions).

`combined_step_1_6_v1.json` is the retained, unevaluated pre-ranking design draft. Before ranking, source inspection corrected C-22's time-step interval to Dambreak.cpp lines 105–106. The active complete evaluation specification also adds explicit 2D geometry evidence to a broad legacy query. No oracle changes were made after ranking.

The complete frozen evaluation specification (40 historical documentation questions, 14 reused source/CLI questions with explicitly documented source bindings, 30 new questions, and 4 P1 diagnostics), hashes, source excerpts, ranks, metrics and analysis are stored at:

`/home/zhang/Projects/SPHinX_AI_Assistant_artifacts/retrieval_evaluation_results/step_1_6_combined_20261027T211957Z/`

`documentation_40_step_1_3g_recovered.json` remains unchanged. `documentation_40_semantic_supplement_v1.json` contains separately frozen source-inspected semantic criteria for known historical limitations. It never replaces historical strict-oracle metrics.

No retrieval ranking, indexing or chunking implementation is included in these benchmark files. Existing historical ranks are observations, not required future ranks. Preserve old results and create new benchmark versions for any future oracle revision.
