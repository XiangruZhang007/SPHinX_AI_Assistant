# SPHinX AI Assistant

Formal Semesterarbeit prototype for repository-grounded assistance over the
SPHinXsys and SPHinXsim upstream repositories.

## Repository layout

```text
SPHinX_AI_Assistant/
├── apps/       # Local Gradio interface
├── configs/    # Project-owned configuration
├── data/       # Corpus, evaluation, and user-testing data
├── doc/        # Project documentation
├── repos/      # Read-only full upstream clones (not stored in this Git repo)
├── scripts/    # Chunking, retrieval, answer, and aggregation CLIs
├── src/        # Project-owned Python modules
├── tests/
└── tools/      # Local third-party evaluation tool checkout (not stored here)
```

`repos/SPHinXsys` and `repos/SPHinXsim` are read-only upstream source
repositories. Do not put assistant code in them or modify their source files.
They are intentionally full Git clones because later synchronization and
diff-based processing require repository history.

## Local setup (Ubuntu 24.04 ARM64)

### 1. Clone the assistant project and both full upstream repositories

```bash
git clone https://github.com/XiangruZhang007/SPHinX_AI_Assistant.git
cd SPHinX_AI_Assistant

mkdir -p repos tools
git clone https://github.com/Xiangyu-Hu/SPHinXsys.git repos/SPHinXsys
git clone https://github.com/Xiangyu-Hu/SPHinXsim.git repos/SPHinXsim
```

Do not use `--depth 1`. Confirm both clones are non-shallow:

```bash
git -C repos/SPHinXsys rev-parse --is-shallow-repository
git -C repos/SPHinXsim rev-parse --is-shallow-repository
```

Both commands must print `false`.

### 2. Create the dedicated Python environment

Install virtual-environment support if it is not present:

```bash
sudo apt-get update
sudo apt-get install -y python3.12-venv
```

Clone the evaluated Tree-sitter chunker tool and create its isolated virtual
environment:

```bash
git clone https://github.com/Consiliency/treesitter-chunker.git tools/treesitter-chunker
python3.12 -m venv tools/treesitter-chunker/.venv
tools/treesitter-chunker/.venv/bin/python -m pip install --upgrade pip
tools/treesitter-chunker/.venv/bin/python -m pip install -e tools/treesitter-chunker
tools/treesitter-chunker/.venv/bin/python -m pip install \
  openai==3.15.0 gradio==6.27.0 markdown-it-py==4.2.0
```

Verify the essential imports:

```bash
tools/treesitter-chunker/.venv/bin/python -c \
  "import chunker, gradio, openai; print('environment ready')"
```

### 3. Use the prototype locally

The committed combined corpus is:

```text
data/chunks/source_chunks_v1_combined.jsonl
```

Run lexical retrieval without an LLM:

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
tools/treesitter-chunker/.venv/bin/python \
scripts/retrieve_v1.py \
  --query "What does WeaklyCompressibleFluid do?" \
  --top-k 5
```

For grounded answering or the Gradio app, set the provider configuration in
the current terminal only. Never commit keys or `.env` files:

```bash
export LLM_API_KEY="<provider API key>"
export LLM_MODEL="<provider model ID>"
export LLM_BASE_URL="<OpenAI-compatible base URL>"
```

Then start the local interface:

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
tools/treesitter-chunker/.venv/bin/python apps/gradio_app_v1.py
```

The app binds locally to `127.0.0.1`; open the URL printed by Gradio. The
interface records successful user-testing interactions under
`data/user_testing/<tester_id>/`. Tester feedback remains unreviewed input,
not approved knowledge.

### 4. Aggregate feedback for human review

```bash
tools/treesitter-chunker/.venv/bin/python \
  scripts/aggregate_user_testing_feedback.py

tools/treesitter-chunker/.venv/bin/python \
  scripts/aggregate_user_testing_feedback.py --only-unreviewed
```

Review batches are written to `data/user_testing/review_batches/`. Aggregation
does not modify source sessions or approve corrections.

## Prototype scope

The current V1 includes structural chunk generation, lexical retrieval,
repository-grounded LLM answering, a local Gradio interface, user-testing
logging, feedback persistence, and review-batch aggregation. It does not yet
implement embeddings, a vector database, an approved-answer workflow, or
central synchronization.
