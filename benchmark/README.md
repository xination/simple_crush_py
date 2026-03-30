# Small-Model Benchmark

This benchmark is for comparing runtime strategies for local 3B / 4B models.

## What it gives you

- A fixed prompt set in `benchmark/small_model_cases.json`
- A simple runner that executes each prompt in a fresh session
- Automatic extraction of:
  - tool sequence
  - first tool
  - whether `view` was used
  - how many locator tools were used
  - final assistant answer

## Run it

```bash
python scripts/run_small_model_benchmark.py --config config.json
```

Optional:

```bash
python scripts/run_small_model_benchmark.py \
  --config config.json \
  --backend lm_studio \
  --output benchmark/results/gemma_branch_a.json
```

Run only one or a few cases:

```bash
python scripts/run_small_model_benchmark.py \
  --config config.json \
  --case-id readme_quick_start \
  --case-id session_store_locate
```

## Compare two runs

```bash
python scripts/compare_benchmark_results.py \
  benchmark/results/branch_a.json \
  benchmark/results/branch_b.json
```

The comparator reports:

- which cases changed `first_tool`
- which cases changed `used_view`
- tool call count deltas
- which cases need manual review

## Suggested experiment flow

1. Run the same benchmark on two branches.
2. Use the same backend and model for both runs.
3. Compare:
   - `first_tool`
   - `tool_call_count`
   - `used_view`
   - `locator_tool_count`
   - final answer quality by manual review

## What to review manually

- Did the model answer only after reading the file?
- Did it guess file paths or implementation details?
- Did it over-search with repeated `glob` / `grep`?
- Did the final answer actually reflect the file contents?
