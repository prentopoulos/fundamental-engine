# Fundamental Engine

Fundamental Engine turns macroeconomic headlines and an economic calendar into an explainable
research board: separate current and expected policy and directional reads for 19 instruments
and 13 entities, each linked to its evidence.

Read PROJECT.md for scope, acceptance criteria and gates.
Follow WORKFLOW.md and .agents/skills/next-phase/SKILL.md for phase/checkpoint work.
Read HANDOFF.md for recovery state. Use .agents/skills/fundamental-engine-build-safety/SKILL.md for
architecture, dependencies, tool contracts, data, telemetry, spend or readiness.
Read `AI_OPS.md` and `.agents/skills/fundamental-engine-ai-ops/SKILL.md` for
model/prompt/evaluation, retrieval, training, runtime-state or operational changes.
Use .agents/skills/fundamental-engine-macro-read-review/SKILL.md for axis reads, instrument
reads, expected stances, attention ranking, narratives and any claim about their accuracy.

The `.agents/` and `.claude/` folders are local to the owner's machine and are not in the
public repository.

## Boundaries

- Phase 1 is built: feeds, model stages, situations, deterministic reads, expectations,
  narratives, the local board, desk chat, read-only MCP and an offline demo.
- Out: orders, broker access, prices and charts, hosting, multiple users, other model
  providers, backtesting.
- Live data is public feeds plus the operator's own paid Anthropic key; `data/` and `build/`
  stay local. The demo uses fictional scenarios; tests use synthetic and recorded fixtures.
- The scheduler ships disabled. Enabling it waits on a spending budget (PROJECT.md §2, §6).

## Non-negotiables

Verbatim from PROJECT.md §5, the project's constitution, which governs. Only the owner amends
it (PROJECT.md § Amending the constitution); a gate check fails if this copy drifts.

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

Surface and stop at the relevant ⛔ gate; local work can proceed before deferred gates.
Every change to code gets a code review before it merges (WORKFLOW.md).

## Development

Python 3.11+ in a `.venv`, installed with `python -m pip install -e ".[dev,mcp]"`. The package
manager is pip with setuptools; there is no lockfile yet (Phase 2 adds one). Copy
`.env.example` to `.env` for live runs only.
Architecture diagrams: C4 as code (Structurizr DSL in `docs/architecture/`, Mermaid in
READMEs), drawn in Phase 4; see the showcase skill. Figma is for slides and mocks.

Windows working notes:
- write multi-line scripts to a file and run it; long bash heredocs mangle backslashes;
- clone into a short path, or set `git config core.longpaths true`; deep paths fail with
  "Filename too long";
- `python -I` ignores `PYTHONIOENCODING`; add `-X utf8` for UTF-8 output;
- after starting a local server from a new folder or launcher, confirm it listens on
  127.0.0.1 only (`netstat -ano`); config files are often read beside the script;
- in a sandboxed agent session pytest may be denied its default temp folder; pass
  `--basetemp` pointing at a writable scratch folder.
Gate: `python -m ruff check . && python -m ruff format --check . && python -m pytest -q`.
Remote: https://github.com/prentopoulos/fundamental-engine (public). Push phase branches and
open PRs at the points WORKFLOW.md sets; nothing goes straight to main, and a PR merges only
after its review and the user's go-ahead.
