# Fundamental Engine

Owner: prentopoulos. Built from 2026-08-31; first public release 2026-10-07; agent workflow
adopted 2026-10-15 from the new_project_agent_setup kit.

Guides: [README](README.md) (purpose, quick start, run modes) ·
[methodology](docs/methodology.md) (evidence weighting, assumptions, validation limits) ·
[architecture](docs/architecture.md) (modules and persistence) ·
[running](docs/running.md) (live setup, scheduling, MCP, troubleshooting) ·
[contributing](CONTRIBUTING.md) (conventions and checks).

## 1. Goal

Fundamental Engine is an in-development macroeconomic research reader for one operator. It
reads a headline feed and an economic calendar and shows, for 19 instruments and the 13
entities behind them, where each stands on two axes: monetary policy (hawkish or dovish) and
direction (bullish or bearish). It shows both the current read and the read the coming week's
scheduled events are expected to leave, and links every read to the situations and events
behind it.

It is not a trading system: no orders, broker, positions or sizing. It is not a price or
technical-analysis tool, not a forecast of returns, and not evidence of predictive accuracy.
Confidence labels are ranking weights, not probabilities.

## 2. Current scope boundary

Built (Phase 1): ingestion of the headline feed, the article sitemap and the weekly calendar;
model triage, scoring and situation updates; deterministic scores, states, degrees and
instrument legs; forward expectations and attention ranking; narratives written from fact
sheets with citation checks; the local dashboard, CLI, desk chat and read-only MCP server; an
offline demo with fictional scenarios; a scheduler that ships disabled.

Out of scope: execution or broker access; price data, charts and technicals; hosted or
multi-user deployment; other model providers out of the box; backtesting (there is no
historical calendar feed, and archive replay is not a leakage-controlled backtest).

TBD, each needing an owner decision before the work that depends on it:
- Spending budget, per-pass ceiling and behaviour when the budget runs out. Needed before the
  scheduler is enabled.
- Retention rule for headlines, situations, chat history and the model replay cache.
- Re-banding `READ_THRESHOLD` and `DEGREE_BANDS` after a week of passes, and the week-two
  model comparison. The commissioning record (local only) asks for both; the repo records
  neither as done.
- Bounded "tilt" scoring, event-class decay and read-confidence bands. Proposed in the
  unfinished 2026-09-10 rebuild folder `fundamental_engine_official`; not adopted here.

Data: live mode uses public feeds and the operator's own Anthropic key. `data/engine.db` and
`build/model_cache/` are local and untracked. The demo uses fictional scenarios; tests use
synthetic and recorded fixtures. One user, one machine, loopback only.

## 3. Acceptance and evidence

- Software behaviour: offline tests with injected feeds, clocks and model replies, plus lint
  and format checks. CI runs them on Windows and Linux with Python 3.11 and 3.13.
- A live pass: `scripts/inspect_pass.py` checks that situation adherence is 100%, every
  narrative citation resolves, and no entity reads neutral with zero situations. A person then
  reads the explanations. Passing these checks does not establish economic accuracy.
- Economic validity: the validation programme in `docs/methodology.md` is proposed work. No
  predictive benchmark, backtest or calibration exists. Any accuracy claim needs a
  predeclared evaluation under the macro-read review skill.

Read `AI_OPS.md` and `.agents/skills/fundamental-engine-ai-ops/SKILL.md` for
model/prompt/evaluation, retrieval, training, runtime-state or operational changes.

## 4. Stack and commands

- Python 3.11+ (CI: 3.11 and 3.13), setuptools, editable install with
  `python -m pip install -e ".[dev,mcp]"`. No lockfile yet (Phase 2).
- SQLite; FastAPI, Uvicorn and Jinja2 on loopback; APScheduler; Pydantic; defusedxml; PyYAML;
  optional MCP server (`mcp`).
- Anthropic API, routed in `config/params.yaml`: Haiku 4.5 for triage, Opus 5.5 for scoring,
  situations, expectation, narrative and chat. Access is the operator's own key in `.env`;
  spending is gated (§6).
- Public headline, sitemap and calendar feeds: unauthenticated reads.
- OpenSpec CLI 1.6 for requirements, design and tasks.
- Architecture diagrams: C4 as code, drawn to the c4model.com notation guide (context and
  container views, plus an ERD of the SQLite schema). One Structurizr DSL model in
  `docs/architecture/`, exported to SVG, with Mermaid where a README shows a diagram
  (showcase skill). None exist yet; Phase 4 draws them.
- Code review: every change to code is reviewed before merge (WORKFLOW.md).

Gate: `python -m ruff check . && python -m ruff format --check . && python -m pytest -q`.
These are today's checks. Phase 2 replaces them with one gate command that also runs
`openspec validate --all --strict` and the constitution copy check.
Remote: https://github.com/prentopoulos/fundamental-engine (public). Push phase branches and
open PRs at the points WORKFLOW.md sets; nothing goes straight to main, and a PR merges only
after its review and the user's go-ahead. `.agents/`, `.claude/`, `.codex/`, `COMMISSIONING.md`,
`data/` and `build/` stay local and untracked (owner decision, 2026-10-15). Follow WORKFLOW.md
for phase work.

## 5. Non-negotiables

These rules and the ⛔ gates in §6 are this project's constitution. They override AGENTS.md,
WORKFLOW.md, the skills, OpenSpec specs and HANDOFF.md; only the owner's current instructions
and recorded gate decisions come before them.

**Version 1.0.1** · Ratified 2026-10-15 · Last amended 2026-10-16

- Secrets use the runtime secret mechanism (gitignored .env for local development); never
  print, commit or include them in reports.
- Never fabricate results or external-service output. Label mocked, local and live results
  accurately. Preserve failed runs.
- Operator resolves all ⛔ gates. The agent never satisfies or waives one itself.
- `no read` is never stored, rendered, combined or reported as `neutral`; an unreadable axis
  shows its reason.
- Current and expected reads stay separate in storage and arithmetic; nothing blends them into
  one score.
- Repeated coverage of one development is not independent evidence: headlines update a
  situation, and breadth counts situations.
- Language models interpret; Python calculates. Models supply inputs (magnitude, polarity,
  situations, each entity's expected state and confidence) and prose. Current scores and states,
  degrees, instrument reads and rankings are computed by deterministic code from those stored
  inputs.
- The narrative stage receives a fact sheet, never raw feed text, and every record a narrative
  cites must resolve.
- Tests run offline: no live feeds, API credentials or paid calls. Feeds, model clients and
  clocks are injected.
- Research only: no orders, broker or account access. The web interface serves loopback only
  and the MCP server opens the database read-only.
- A change to thresholds, half-lives, breadth, coefficients or ranking states its assumption and
  updates docs/methodology.md in the same change.
- No claim of predictive accuracy, trading performance or calibrated probability without a
  predeclared evaluation; confidence weights are ranking multipliers.

### Amending the constitution

- Only the owner amends it: a change to a rule above, or adding, removing or loosening a ⛔
  gate. Recording an operator decision on a gate (§6) is not an amendment.
- An amendment states its reason and goes in its own `docs:` commit. That commit updates the
  verbatim copies in AGENTS.md and `openspec/config.yaml` and adds a row below. A check in the
  Phase 0 gate fails if a copy or the version line drifts.
- Version: major when a rule is removed or redefined, minor when one is added or materially
  widened, patch for wording only.

| Version | Date | Change | Decided by |
|---|---|---|---|
| 1.0.0 | 2026-10-15 | Ratified at kit adoption. Rules 4–12 are taken from the README, CONTRIBUTING, SECURITY, the methodology and existing tests; open to the owner's review in the Phase 2 PR | prentopoulos (setup) |
| 1.0.1 | 2026-10-16 | Rule 7 corrected: the expectation stage's model answer sets each entity's expected state and confidence (`engine/stages.py`), so the rule no longer says states never come from model output. No behaviour or check changes. Rules 4–12 confirmed | prentopoulos |

## 6. Milestones and gates

Milestones are ordered by dependency.

| Phase | Deliverable | Status |
|---|---|---|
| 0 | Toolchain, lockfile, quality gate with the constitution copy check, smoke checks; offline AI evaluation scaffold | Never run as its own phase; delivered by Phase 2 |
| 1 | Fundamental reader (OpenSpec `build-fundamental-reader`, 101 tasks): feeds, situations, reads, expectations, narratives, local board, disabled loop, commissioning pass; C4 architecture diagrams as code | Implemented before kit adoption and released 2026-10-07; archive delivered by Phase 2, diagrams by Phase 4 |
| 2 | Foundations: Phase 1 archived into main specs and corrected, dependency lock, one gate with the constitution copy check, CI on the gate, link and licence checks, PR template TL;DR (OpenSpec `adopt-kit-foundations`) | Spec ready, in owner review (draft PR) |
| 3 | Evidence: per-pass system bundle, frozen offline evaluation set and plan, 0.1.0 release record, runbook, build-safety and macro-read reviews, tested published figures, pre-publication scan | Planned |
| 4 | Showcase: C4 and schema diagrams as code, six-slide deck, captioned walkthrough video, README links to both | Planned |

The kit catch-up was split into Phases 2–4 by the owner on 2026-10-16, so each is one
reviewable PR. Questions for the later phases, kept here so they are not lost:
- Phase 3: store the system bundle as an additive `bundle_id` column on `passes` plus a
  `bundles` table, migrated after a backup (recommended), or as JSON files under
  `build/bundles/`. Behind the live-records ⛔ gate.
- Phase 4: export diagrams with a pinned Structurizr CLI downloaded into ignored
  `.local/tools/` (recommended; Java 17 is installed, Docker is not), or use hand-written
  Mermaid only.

A first draft of Phase 3 and 4 specs is in commit `b1ce6b6` (the first Phase 2 proposal).

### ⛔ Before spending Anthropic API credit

Any command that calls the API: a live pass (`python -m engine`), a forced refresh, the desk
chat, `scripts/replay.py` without `--dry-run`, `scripts/compare_model.py`, or a provider
evaluation.

- [ ] Operator confirms the run and its budget.

No standing decision is recorded. The operator runs live passes personally.

### ⛔ Before enabling unattended runs

Setting `loop.ENABLED: true`, running `scripts/run_loop.py` with it enabled, or setting
`HOURLY_MINUTES` above zero.

- [ ] Operator has read one live pass by hand and set the spending budget, per-pass ceiling
  and behaviour at exhaustion (§2).

### ⛔ Before publishing

Merging to or pushing main, tagging or releasing, uploading to a package index, changing
repository visibility or settings, publishing a deck or video, tracking a path that is
currently private (`.agents/`, `.claude/`, `.codex/`, `COMMISSIONING.md`, `data/`, `build/`,
`.env`), or rewriting published history.

- [ ] Operator approves the specific publication.

Recorded decisions (owner, 2026-10-15):
- Phase branches may be pushed and draft PRs opened on the public remote. Merging waits for
  the owner's go-ahead.
- The root process documents (AGENTS, CLAUDE, WORKFLOW, HANDOFF, AI_OPS, PROJECT) and
  `openspec/` are published. `.agents/`, `.claude/` and `COMMISSIONING.md` stay local.
- Pending: the `build-fundamental-reader` change record names the sibling trading project and
  where the copied feed and model code came from, which the public code neutralised. It stays
  untracked until the owner decides whether to publish it as is, publish it neutralised, or
  keep it local.

### ⛔ Before exposing a service or adding a write action

Serving the web UI or MCP server beyond 127.0.0.1, hosting it, adding a user, or adding any
order, broker, account or other write action toward an external system.

- [ ] Operator approves the exposure, with a threat review (build-safety) for it.

### ⛔ Before deleting or rewriting live records

Deleting, truncating or migrating `data/engine.db` or `build/model_cache/`.

- [ ] Operator approves after a backup exists.

Record explicit operator decisions already given, with scope and date; do not request them
again. Gates without such a decision remain unresolved. Local work proceeds independently.
