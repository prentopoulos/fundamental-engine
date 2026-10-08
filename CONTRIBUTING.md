# Contributing

The project is in development. Focus contributions on clear behaviour, inspectable calculations, and reproducible bugs.

## Setup and checks

Use Python 3.11+ and an editable checkout:

```bash
python -m pip install -e ".[dev,mcp]"
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

Tests must run without live feeds, API credentials, or paid calls. Inject a fetcher, model client, or clock when a test needs one.

## Conventions

Use snake_case for Python modules, functions, and variables; PascalCase for classes; UPPER_SNAKE_CASE for constants. Name modules after their responsibility. Keep model interpretation out of the arithmetic modules.

Comments should explain why a rule exists or what a failure would mean. Use short docstrings for public functions and avoid narrating obvious Python syntax. Keep config comments aligned with actual defaults.

A change to thresholds, half-lives, breadth, coefficients, or ranking must explain its assumption and update the methodology. Do not replace no-read with neutral, mix current and expected scores, or count repeated coverage as independent evidence.

## Issues and pull requests

Describe the trigger, observed behaviour, expected behaviour, and a minimal reproducible example. Include relevant versions and redacted logs. Do not attach API keys, local databases, or cached model prompts containing private information.

Keep pull requests focused. Explain the behavioural change and the checks you ran. Passing tests does not justify claims about prediction accuracy or trading performance.

The project uses the [PolyForm Noncommercial License 1.0.0](LICENSE.md). Contributions should be compatible with those terms and preserve required notices.
