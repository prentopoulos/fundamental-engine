# Fundamental Engine AI operations

PROJECT.md owns scope, decisions and operator gates. OpenSpec owns requirements and tasks.
This record maps AI capabilities to their current evidence; link existing artifacts rather
than maintaining duplicate specifications. Read the AI-ops skill
(`.agents/skills/fundamental-engine-ai-ops/SKILL.md`, local to the owner's machine) for the
affected procedure.

## Capability map

| Capability | Status | Reason / evidence or task | Recheck when |
|---|---|---|---|
| Hosted or local model inference | enabled | Anthropic API through `engine/model/client.py`; routing in `config/params.yaml` (Haiku 4.5 triage, Opus 5.5 for the rest) | Model, provider or routing change |
| Prompts and structured output | enabled | One prompt per stage in `engine/prompts/`; `engine/stages.py` parses and validates each answer; expectations and narratives store the verbatim prompt, model id and effort | Prompt, schema or model change |
| RAG / document ingestion | deferred (absent) | No index or retrieval. Code builds each fact sheet from stored records; feeds are ingested into SQLite, not retrieved | First embedding, index or retrieval step |
| Semantic or response caching | enabled | Disk replay cache keyed by request hash (`engine/model/cache.py`, `build/model_cache/`); the provider's five-minute prompt cache is priced into cost estimates | Cache key, scope or bypass rule changes |
| Model training / feature pipelines | deferred (absent) | No training or fine-tuning | First training or feature-serving task |
| Tool actions / external adapters | enabled (read-only) | Fixed feed adapters in `engine/feeds/` read public endpoints; no model chooses or calls a tool; no write action | New adapter, destination or any write action |
| Persistent state / replay | enabled | SQLite holds situations, reads, expectations, narratives, passes and chat history; `scripts/replay.py` walks archived headlines | Schema change or resumable-pass change |
| Multiple users / tenants | deferred (absent) | One operator; the web UI refuses non-loopback requests | Second user, shared listener or hosting |
| Generated code execution | deferred (absent) | None | Any code-execution tool |
| MCP / external tool servers | enabled (read-only) | `mcp_server.py` opens the database with `mode=ro` and makes no model call; tested in `tests/test_public_release.py` | Server or tool contract changes |

The model writes text that refers to computed figures (narratives, chat answers). The AI-ops
reference on prose over figures applies to any change there.

## Evidence locations

| Record | Location / current status |
|---|---|
| System bundle and last known-good version | Partial. Each expectation and narrative stores its prompt, model id and effort, but no pass carries one bundle id covering prompts, routing, config and code revision. Last known-good: commissioning pass 5, 2026-08-31, in the local commissioning record. Phase 3 adds the bundle record |
| Evaluation plan, held-out cases and evaluator | TBD (Phase 3). Recorded-reply fixtures in `tests/fixtures/` exercise every parser, including malformed and non-adherent replies |
| Offline deterministic check command | `python -m ruff check . && python -m ruff format --check . && python -m pytest -q` |
| Authorized provider-evaluation command and budget | `scripts/compare_model.py` replays stored prompts against a named model. Budget TBD; behind the spending ⛔ gate. Not run in any recorded evidence |
| Run records / metrics / alerts | `passes` table: trigger, per-stage input and output tokens, billed and replayed calls, estimated cost, failed stage, entities skipped. `scripts/inspect_pass.py` checks adherence, citations and no-read integrity. No alerts |
| Release evidence and stop/recovery runbook | TBD (Phase 3). `docs/running.md` covers troubleshooting |
| Operational owner and maintenance/recheck cadence | prentopoulos. Cadence TBD |

## Current operational limits

- Models: as routed in `config/params.yaml`. Each call caps output at 4,096 tokens unless a
  stage sets otherwise (`engine/model/client.py`).
- Retries: a failed scheduled refresh logs and waits for the next trigger; there is no retry
  inside a trigger.
- Concurrency: one refresh lock shared by the scheduler and the dashboard's forced refresh.
- Cost: estimated per pass from the configured prices; provider invoices are authoritative.
  No budget, per-pass ceiling or behaviour at exhaustion exists (PROJECT.md §2 TBD). The
  scheduler stays disabled behind its ⛔ gate until they do.
- Retention: no deletion rule for headlines, situations, chat history or the replay cache
  (PROJECT.md §2 TBD). These stay in local, untracked storage.

## Latest scoped evidence

No AI runtime evaluation has been established by installing the kit. Existing evidence: the
offline suite (603 tests passed on 2026-10-15, local Windows, Python in `.venv`) and the
2026-08-31 commissioning pass recorded locally. Interpretation stability, annotation agreement
and predictive validity are not_run. A deferred capability needs fresh evidence before it is
enabled.
