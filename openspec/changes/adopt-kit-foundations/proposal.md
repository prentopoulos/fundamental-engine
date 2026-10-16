## Why

Fundamental Engine was built and released before it adopted the project kit on 2026-10-15. It
never ran the kit's Phase 0: there is no lock, no single gate, no check that the constitution's
copies agree, and Phase 1 was never archived, so `openspec/specs/` is empty. This change,
**PROJECT.md Phase 2**, lays those foundations first. Phases 3 and 4 then build the evidence
records and the showcase under the gate this phase adds. It does not change what the engine
reads or how it scores.

## What Changes

- **Close Phase 1.** Archive `build-fundamental-reader` so its eight capability specs become
  the main specs. Then correct the requirements the code has moved past: 19 instruments
  rather than 18, a half-life per axis, the breadth floor, release-only refreshes by default,
  the neutral guard directory name, and the article sitemap feed. Add specs for the
  unspecified demo, desk chat, MCP server and archive replay.
- **Dependency lock.** A pinned lock for the `dev` and `mcp` extras on Windows and Linux,
  used by CI, with a check that it covers every declared dependency.
- **One gate.** `scripts/gate.py` runs lint, format, tests and `openspec validate --all
  --strict`, plus a new constitution copy check. CI runs the gate on the existing matrix.
- **Document checks.** Every local link in tracked Markdown resolves to a tracked file, and
  the README names the licence. An import test keeps model code out of the arithmetic.
- **PR template.** The template gains the TL;DR the workflow requires.

## Capabilities

### New Capabilities

- `quality-gate`: the single gate command, the constitution copy check, the dependency lock,
  and the link and licence checks over tracked Markdown.

### Modified Capabilities

None at proposal time: `openspec/specs/` is empty until task 1 archives Phase 1. The
corrections listed above then become MODIFIED deltas on `instrument-universe`,
`stance-reading`, `unattended-loop` and `feed-ingestion`, plus ADDED requirements for the
demo, desk chat, MCP server and archive replay. They are added to this change in task 1.

## Non-goals

- **Phase 3 (Evidence)** covers:
  - the per-pass system bundle, the frozen evaluation set and plan;
  - the release record and runbook;
  - the build-safety and macro-read reviews;
  - tested published figures and the pre-publication scan.
- **Phase 4 (Showcase)** covers the C4 and schema diagrams, the deck, the walkthrough video
  and the README links to them.
- No change to scoring, thresholds, half-lives, breadth, coefficients, ranking, prompts or
  models. The bounded "tilt" score and the other 2026-09-10 rebuild proposals stay unadopted.
- No live API call, budget enforcement, retention rule, scheduler change, hosting or history
  rewrite.

## Impact

- New files:
  - `scripts/gate.py` and `requirements/lock.txt`
  - tests for the constitution copies, links, the licence, the lock and arithmetic imports
  - `openspec/specs/`
- Changed: `.github/workflows/ci.yml` installs from the lock, installs a pinned OpenSpec CLI
  and runs the gate. `.github/pull_request_template.md` and `CONTRIBUTING.md` change, and
  the gate command in the root documents.
- No runtime behaviour, stored data or schema changes.
- Gate touched: publishing, because the main specs and Phase 1's change record become public
  (open question 1). No §5 rule or ⛔ gate is amended.
