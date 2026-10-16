## 1. Close Phase 1

- [ ] 1.1 Archive `build-fundamental-reader` with `openspec archive`, syncing its eight capability specs into `openspec/specs/`
- [ ] 1.2 Before `openspec/specs/` is first committed, correct the synced unattended-loop guard requirement to name `trading_runtime`, as `engine/store/guard.py` does, and note the edit in the PR
- [ ] 1.3 Add MODIFIED deltas to this change for drift shown by code and tests: 19 instruments including CADJPY (`instrument-universe`); per-axis half-lives and the breadth floor (`stance-reading`); release-only refreshes by default with optional periodic reads (`unattended-loop`); the article sitemap feed (`feed-ingestion`)
- [ ] 1.4 Add ADDED requirements for the offline demo, the desk chat, the read-only MCP server and archive replay, with scenarios taken from existing tests
- [ ] 1.5 Confirm that `.gitignore` keeps Phase 1's change record and its archived copy local (design decision 4), and that `git status` shows neither after archiving
- [ ] 1.6 Run `openspec validate --all --strict` and record the result

## 2. Lock and checks

- [ ] 2.1 Generate `requirements/lock.txt` with `uv pip compile --universal` for the `dev` and `mcp` extras on Python 3.11+, and document relocking in CONTRIBUTING.md
- [ ] 2.2 Add a check that every dependency declared in `pyproject.toml` is pinned in the lock, with a negative test
- [ ] 2.3 Add the constitution copy check as a test, with negative tests for an added, removed, reordered and reworded rule and for a mismatched version line
- [ ] 2.4 Add the tracked-Markdown link check and the README licence check, with negative tests
- [ ] 2.5 Add the import test asserting `engine/reading/` and `engine/universe/` import nothing from `engine.model`, `engine.stages` or `anthropic`

## 3. Gate and CI

- [ ] 3.1 Write `scripts/gate.py` running lint, format, tests and `openspec validate --all --strict`, reporting every check and failing on a missing tool; test its reporting with stubbed commands
- [ ] 3.2 Update CI to install from the lock, install a pinned OpenSpec CLI with Node, and run the gate on the existing matrix
- [ ] 3.3 Replace the interim gate command in PROJECT.md, AGENTS.md, CLAUDE.md, WORKFLOW.md, AI_OPS.md, `openspec/config.yaml` and the next-phase skill with `python scripts/gate.py`
- [ ] 3.4 Add a TL;DR section to `.github/pull_request_template.md`

## 4. Close Phase 2

- [ ] 4.1 Run the gate and `/code-review` on the PR, and resolve or answer every finding
- [ ] 4.2 Update HANDOFF.md and PROJECT.md §6, archive this change, rerun validation and the gate, and mark the PR ready
