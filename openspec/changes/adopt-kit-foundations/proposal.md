## Why

Fundamental Engine was built and released before it adopted the project kit on 2026-10-15. So
it skipped the kit's Phase 0, it has no main specs, no single gate, no constitution check, no
AI-ops or domain evidence records and no architecture diagrams, and it was never checked
before going public. This change, **PROJECT.md Phase 2**, delivers what Phases 0 and 1 would
have produced had the project started from the kit. It does not change what the engine reads
or how it scores.

## What Changes

- **Close Phase 1.** Archive `build-fundamental-reader` so its eight capability specs become
  the main specs. Then correct the requirements the code has moved past: 19 instruments
  rather than 18, a half-life per axis, the breadth floor, release-only refreshes by default,
  the neutral guard directory name, and the article sitemap feed. Add specs for the
  unspecified demo, desk chat, MCP server and archive replay.
- **Phase 0 foundations.** A pinned dependency lock used by CI. One gate command that runs
  lint, format, tests, `openspec validate --all --strict` and a new constitution copy check.
  CI runs that gate. Tests check that every local link in tracked Markdown resolves and that
  the licence is named.
- **AI operations records.** Each pass records the system bundle it ran with: prompt hashes,
  model routing, config hash, package version and code revision. Recorded-reply fixtures
  become a frozen offline evaluation set with a written plan. The release of 0.1.0 gets a
  release record, and failures get a stop-and-recover runbook.
- **Build-safety review.** One review record accounts for all 17 Agent Builder workflows:
  threats from feed text, secrets, data and retention, spend, telemetry, release gates and
  CI. Each gap is recorded with an owner and a recheck trigger.
- **Domain review.** Independent known-answer tests for the arithmetic invariants and the
  README's worked examples. The figures in the README and methodology sit between markers
  and are tested, so a drifted number fails.
- **Architecture diagrams.** A Structurizr DSL C4 model with context and container views,
  exported to SVG and Mermaid. An ERD of the SQLite schema, with a test that it names every
  table.
- **Pre-publication checks** on the already-public repository:
  - a whole-history secret scan, proved with a planted key;
  - a survey for private data and personal details;
  - a publishing list;
  - a check from a fresh clone.
- **Showcase.** A six-slide deck and a two-minute captioned walkthrough built on demo data.
  The README links to both once the owner approves them. The PR template gains a TL;DR.

## Capabilities

### New Capabilities

- `quality-gate`: the single gate command, the constitution copy check, the dependency lock,
  and the link and licence checks over tracked Markdown.
- `run-records`: the system bundle recorded per pass and the frozen offline evaluation set.
- `architecture-docs`: the C4 model as code, its exported views and the schema ERD kept in
  step with the schema.
- `published-figures`: figures quoted in the README and methodology, checked against the
  engine's own arithmetic.

### Modified Capabilities

None at proposal time: `openspec/specs/` is empty until task 1 archives Phase 1. The
corrections listed above then become MODIFIED deltas on `instrument-universe`,
`stance-reading`, `unattended-loop` and `feed-ingestion`, plus ADDED requirements for the
demo, desk chat, MCP server and archive replay. They are added to this change in task 1.

## Non-goals

- No change to scoring, thresholds, half-lives, breadth, coefficients, ranking, prompts or
  models. The bounded "tilt" score and the other proposals in the 2026-09-10 rebuild folder
  stay unadopted.
- No live API call, provider evaluation, commissioning follow-up, re-banding or week-two
  model comparison: they spend money and are owner decisions.
- No budget enforcement or retention rule. Their values are owner decisions (PROJECT.md §2);
  this phase records the gaps and leaves the scheduler gate closed.
- No enabling the scheduler, hosting, or publishing beyond phase branches and draft PRs.
- No Git history rewrite.

## Impact

- New files: `scripts/gate.py`, a lock file, new tests, `docs/architecture/`,
  `docs/reviews/`, `docs/evaluation/`, `docs/runbook.md`, `docs/releases/0.1.0.md`,
  `docs/walkthrough.md`, and `openspec/specs/`.
- Changed: `.github/workflows/ci.yml` runs the gate and installs from the lock; the
  `.github/pull_request_template.md` TL;DR; the README and methodology figure markers; and
  `docs/architecture.md`.
- Pass recording gains a bundle field. Storing it in the existing local database needs an
  additive migration, which is behind the live-records ⛔ gate (design, open question 3).
- The gates touched are publishing (deck, video, README links, Phase 1's change record) and
  live records (the migration). No §5 rule or ⛔ gate is amended.
