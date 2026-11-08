# Contributing

`main` is protected: nothing is committed to it directly. Every change goes through a pull request that passes CI.

## Workflow

1. Branch from an up-to-date `main`: `feature/<topic>`, `fix/<topic>` or `docs/<topic>`.
2. Make small commits, one logical step each, with an imperative subject ("Add sleep chart", not "added stuff") and a body that says why.
3. Run the checks locally (below) before pushing.
4. Open a pull request using the template. State what you checked and what you did not.
5. CI must pass (`test (3.11)` and `test (3.13)`), and open review conversations must be resolved.
6. The maintainer merges. Merge commits and rebase merges are allowed, squash is not, so the step-by-step history stays visible. The branch is deleted after merge.

## Checks

```
uv venv && uv pip install -e '.[dev]'
ruff check . && ruff format --check . && pytest
```

## Data and privacy

Never commit real health or fitness data (Apple exports, Fitbod CSVs, GPX routes). The synthetic generator (`wearables synth`) produces safe demo and test data. Keep personal data in a separate private location and pass its path on the command line.
