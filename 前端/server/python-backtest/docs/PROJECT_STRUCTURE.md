# Project Structure

This project is a Python trading/backtest workspace. The maintained runnable
program is `main02.py`, which contains the full single-file implementation.

## Main folders

- `tests/`: automated tests.
- `docs/`: project notes and usage documents.
- `logs/`: generated runtime logs. These are ignored by git.
- `reports/`: generated backtest reports, grouped by stock code. These are ignored by git.

## Important files

- `main02.py`: stable single-file command-line program.
- `main.py`: small compatibility wrapper that calls `main02.py`.
- `backtest_config.json`: profile settings for MA, RSI, and MACD strategy runs.
- `run_batch.py`: runs all profiles listed in `batch_profiles`.
- `pyproject.toml`: Python project metadata and dependencies.
- `uv.lock`: locked dependency versions for `uv`.
- `.env`: local secrets and runtime settings. Do not commit this file.
- `.env.example`: safe template for local settings.

## Suggested commands

```powershell
uv sync
uv run python main02.py --profile 2313_ma
uv run python run_batch.py
uv run python -m unittest discover -s tests -v
```

## Cleanup notes

Generated files such as `__pycache__/`, `logs/`, `reports/`, and `*.log` can be
removed when you no longer need local output. They are excluded by `.gitignore`.

`main.py` is retained only as a compatibility wrapper. The normal workflow
should run `main02.py`.
