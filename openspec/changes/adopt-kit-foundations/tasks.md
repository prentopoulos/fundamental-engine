## 1. Close Phase 1

- [ ] 1.1 Archive `build-fundamental-reader` with `openspec archive`, syncing its eight capability specs into `openspec/specs/`
- [ ] 1.2 Before `openspec/specs/` is first committed, correct the synced unattended-loop guard requirement to name `trading_runtime`, as `engine/store/guard.py` does, and record the edit in the spec-drift notes
- [ ] 1.3 Add MODIFIED deltas to this change for drift shown by code and tests: 19 instruments including CADJPY (`instrument-universe`); per-axis half-lives and the breadth floor (`stance-reading`); release-only refreshes by default with optional periodic reads (`unattended-loop`); the article sitemap feed (`feed-ingestion`)
- [ ] 1.4 Add ADDED requirements for the offline demo, the desk chat, the read-only MCP server and archive replay, with scenarios taken from existing tests
- [ ] 1.5 Apply the owner's decision on Phase 1's change record (design open question 1) and update `.gitignore` to match
- [ ] 1.6 Run `openspec validate --all --strict` and record the result

## 2. Quality gate and lock

- [ ] 2.1 Generate `requirements/lock.txt` with `uv pip compile --universal` for the `dev` and `mcp` extras on Python 3.11+, and document relocking in CONTRIBUTING.md
- [ ] 2.2 Add a check that every dependency declared in `pyproject.toml` is pinned in the lock, with a negative test
- [ ] 2.3 Add the constitution copy check as a test, with negative tests for an added, removed, reordered and reworded rule and for a mismatched version line
- [ ] 2.4 Add the tracked-Markdown link check and the README licence check, with negative tests
- [ ] 2.5 Add an import test asserting `engine/reading/` and `engine/universe/` import no model, stage or client code
- [ ] 2.6 Write `scripts/gate.py` running lint, format, tests and `openspec validate --all --strict`, reporting every check and failing on a missing tool; test its reporting with stubbed commands
- [ ] 2.7 Update CI to install from the lock, install a pinned OpenSpec CLI with Node, and run the gate on the existing matrix
- [ ] 2.8 Replace the interim gate command in PROJECT.md, AGENTS.md, CLAUDE.md, WORKFLOW.md, AI_OPS.md, `openspec/config.yaml` and the next-phase skill with `python scripts/gate.py`

## 3. AI operations records

- [ ] 3.1 Implement `engine/bundle.py` computing the system bundle and its id, with tests for determinism, a one-byte prompt change and a missing Git checkout
- [ ] 3.2 Store the bundle with each pass in the form the owner chooses (design open question 3), with a migration test on a copy of an old-schema database; the operator backs up `data/engine.db` before first use
- [ ] 3.3 Show the bundle id in `scripts/inspect_pass.py` output
- [ ] 3.4 Write `tests/fixtures/manifest.json` labelling every recorded-reply fixture by stage and case type, with a test enforcing digests and per-stage coverage; add a deliberately failing case for any stage that lacks one
- [ ] 3.5 Write `docs/evaluation/plan.md` from the AI-ops EVAL_PLAN form: the offline lane, the provider lane (not run, budget TBD, behind the spending gate), the checks `inspect_pass.py` already enforces, and what stays proposed
- [ ] 3.6 Write `docs/releases/0.1.0.md` from the RELEASE_RECORD form: what shipped on 2026-10-07, the evidence that existed then, and every not_run check
- [ ] 3.7 Write `docs/runbook.md` from the RUNBOOK form: failed pass, feed outage, model API errors or refusals, a cost surprise, a bad prompt or model change, stopping the scheduler, and restoring the database
- [ ] 3.8 Update the evidence locations and latest evidence in AI_OPS.md

## 4. Build-safety review

- [ ] 4.1 Write `docs/reviews/2026-10-build-safety.md` accounting for all 17 Agent Builder workflows: applicable, deferred with a trigger, or not applicable with a reason
- [ ] 4.2 Review threats from feed text reaching the models, chat input, `.env` handling, the loopback middleware and read-only MCP; add a test for any control found without one
- [ ] 4.3 Record what each table and the replay cache hold, chat history included, and the open retention decision
- [ ] 4.4 Record spend: observed per-pass cost, where cost is estimated, and the missing budget, ceiling and exhaustion behaviour that keep the scheduler gate closed
- [ ] 4.5 Record release gates and CI: what must pass before a tag, and what local checks do not prove

## 5. Domain review and published figures

- [ ] 5.1 Write `docs/reviews/2026-10-macro-read-review.md`: each invariant in the domain skill, the test that checks it independently, and the gaps
- [ ] 5.2 Add known-answer tests with hand-computed values, not engine helpers, for decay, breadth, the shock exception, degree bands, pair legs and the attention rank
- [ ] 5.3 Put the README and methodology worked-example figures between hidden markers, and add the test that recomputes them and fails on drift or missing markers
- [ ] 5.4 Record the validation programme, re-banding and the week-two model comparison as not_run, with the evidence each would need

## 6. Architecture diagrams

- [ ] 6.1 Write `docs/architecture/workspace.dsl` with context and container views following the C4 notation rules
- [ ] 6.2 Write the schema ERD in `docs/architecture/schema.md` and the test comparing it with the tables the schema creates
- [ ] 6.3 Export SVG and Mermaid views with the Structurizr CLI (design open question 2), commit the SVGs, and replace the flowchart in `docs/architecture.md` with the container view
- [ ] 6.4 Add the check that every relationship in the model has a specific verb and a technology

## 7. Pre-publication checks

- [ ] 7.1 Export every blob in history and scan it with detect-secrets; prove the scan finds a planted fake key and hash file; search for known key shapes; confirm `.env` was never committed
- [ ] 7.2 Survey tracked files and history for private data (stored database values, cached prompts) and personal details, masking any value printed
- [ ] 7.3 Write the publishing list (what is public, the data statement, what stays private) for the owner's approval, and record the decision in PROJECT.md §6
- [ ] 7.4 Clone the public branch into a short path, run the README quick start as written, and confirm the demo listens on 127.0.0.1 only
- [ ] 7.5 Record 7.1–7.4 in `docs/reviews/2026-10-pre-publication.md`

## 8. Showcase

- [ ] 8.1 Add a TL;DR section to `.github/pull_request_template.md`
- [ ] 8.2 Write the timed walkthrough script `docs/walkthrough.md` from the offline demo
- [ ] 8.3 Draft the six-slide deck in Figma Slides, check every line against the showcase rules, and send it to the owner
- [ ] 8.4 Record the roughly two-minute captioned walkthrough on demo data, preview each step first, and send the MP4 to the owner before committing it anywhere
- [ ] 8.5 After the owner approves both, add the deck ("Start here") and video ("Watch") links to the README opening, under the publishing gate

## 9. Close Phase 2

- [ ] 9.1 Run the gate and `/code-review` on the PR, and resolve or answer every finding
- [ ] 9.2 Update HANDOFF.md and PROJECT.md §6, archive this change, rerun validation and the gate, and mark the PR ready
