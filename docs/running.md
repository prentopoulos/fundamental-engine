# Running the engine

Use a source checkout and an editable install. Commands assume you are in the repository folder and `python` points to the virtual environment. Without activation, use `.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on macOS/Linux.

## Live configuration

Copy `.env.example` to `.env`, then set your own `ANTHROPIC_API_KEY`. An environment variable takes precedence over the file. Do not paste your key into issues, screenshots, or configuration YAML.

Edit `config/params.yaml` to select models and adjust the reading rules. The defaults use Haiku 4.5 for triage and Opus 5.5 for interpretation and explanations. Model access depends on your API account. Check the provider's [model catalogue](https://platform.claude.com/docs/en/models/overview) before changing identifiers, and [pricing](https://platform.claude.com/docs/en/about-claude/pricing) before updating token rates.

Edit `config/instruments.yaml` to change the instrument universe and transmission coefficients. Currency pairs resolve automatically from their base and quote codes; non-FX instruments require explicit mappings.

Data and cache paths are resolved from the checkout rather than your shell's working directory. The default database is `data/engine.db` and the cache is `build/model_cache`. Do not point the reader at an execution database. The startup guard rejects reserved execution directories and databases containing a `verdicts` table.

## One pass

```bash
python -m engine
python -m engine --json
python scripts/inspect_pass.py
```

| Option | Behaviour |
| --- | --- |
| `--poll` | Ingest, triage, score, and update situations; skip the calendar, entity reads, expectations, and prose. |
| `--forced` | Rerun all per-entity stages and bypass replay caching; previously processed headlines are not reclassified. |
| `--json` | Print a machine-readable summary. |

A failed stage records the error. Ingestion and triage can remain committed so a later pass can resume. Headline completion markers are committed with situation updates, avoiding lost evidence after an intervening failure. Previously completed results remain available.

Pass inspection checks identifiers and citations, missing-evidence integrity, and score distribution. It cannot certify factual accuracy. Review warnings and explanations yourself.

## Local dashboard and chat

```bash
python -m engine.web
```

Open http://127.0.0.1:8000. The server binds to loopback and rejects non-loopback clients. It is intended for local use, not public hosting.

Browsing saved results makes no model calls. **Refresh now** requests a forced pass; **Ask the desk** calls the configured chat model. Both can incur charges. Displayed costs are estimates from configured rates, including five-minute prompt-cache reads and writes where the provider reports them. A replayed disk-cache response costs nothing. No monthly spending cap is implemented.

## Automatic refreshes

Run and inspect one live pass first. Set `loop.ENABLED: true`, then:

```bash
python scripts/run_loop.py
```

This serves the dashboard and runs the scheduler in one process. Ctrl+C stops both.

The default `HOURLY_MINUTES: 0` disables periodic reads. Watched High-impact releases trigger a pass five minutes after their scheduled time. Nearby releases are grouped within the configured debounce window. Set a positive interval, such as 60, to add periodic reads.

Recent missed triggers are considered on startup. Failed triggers are recorded rather than retried indefinitely. Read age makes stale results visible. Run one writer process per database: the scheduler and web refresh share an in-process lock, not a distributed lock.

## Optional MCP: bring your own assistant

```bash
python -m pip install -e ".[mcp]"
python mcp_server.py
```

The local server follows the [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk). Configure a compatible MCP client with absolute paths:

```json
{
  "mcpServers": {
    "fundamental-engine": {
      "command": "/absolute/path/fundamental-engine/.venv/bin/python",
      "args": ["/absolute/path/fundamental-engine/mcp_server.py"]
    }
  }
}
```

On Windows, use the `.venv\\Scripts\\python.exe` path and escape backslashes in JSON, or use forward slashes. The host may start in another directory; data paths still resolve from the checkout.

Tools include status, board, instrument, entity, upcoming events, situation lookup, situation search, and all-entity reads. They open existing results read-only and cannot run a refresh, call the analysis model, or create a new database. Complete a live pass before using them.

Your external assistant may have its own billing and data-handling settings. The engine's API key is not needed for these read-only tools.

## Research utilities

| Utility | Purpose |
| --- | --- |
| `python scripts/inspect_pass.py --all-passes` | Inspect stored score distributions and integrity. |
| `python scripts/replay.py --from YYYY-MM-DD --to YYYY-MM-DD --dry-run` | Count available archive records without storing or calling models. |
| `python scripts/replay.py --from YYYY-MM-DD --to YYYY-MM-DD` | Build situations chronologically from archive URL-derived titles; incurs API usage. |
| `python scripts/compare_model.py --model MODEL_ID --stage expectation` | Compare a model against stored prompts; may incur API usage. |

Archive titles lose detail, and archive modification dates need not equal the original publication time. Replay does not establish historical prediction accuracy. Model comparison reports wording differences, not statistical equivalence or economic correctness.

## Troubleshooting

| Symptom | First check |
| --- | --- |
| Missing API key | Set `ANTHROPIC_API_KEY` in the environment or checkout's `.env`. |
| Model unavailable | Confirm account access and IDs in `config/params.yaml`. |
| API credit / rate-limit error | Check your provider account; saved reads remain browsable. |
| Empty board | Complete a full live pass, or start the offline demo. A poll does not publish a board. |
| Many no-read cells | Inspect source coverage, situation grouping, decay, and material breadth before retuning thresholds. |
| Calendar / feed failure | Inspect the recorded failed stage; endpoint availability and formats can change. |
| Port already used | For the demo, add `--port 8001`. Stop the other local server before running live mode. |
| Automatic reads do not run | Check `ENABLED`, watched events, and the process log. |
| MCP missing database | Complete a live pass and confirm the configured path. |
