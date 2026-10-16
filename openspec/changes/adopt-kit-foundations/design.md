## Context

The code was built under one OpenSpec change (`build-fundamental-reader`, 101 tasks) and
released publicly on 2026-10-07 as a single curated commit. The development history, the
change record, the agent tooling and the commissioning record were kept out of Git. The kit
was adopted on 2026-10-15 (setup commit on this branch).

Current state against the kit:
- **Specs:** `openspec/specs/` is empty, and the Phase 1 specs lag the code.
- **Checks and CI:** no lockfile and no single gate. CI runs lint, format and tests on Windows
  and Linux with Python 3.11 and 3.13.
- **Diagrams:** no C4 diagrams. `docs/architecture.md` has one Mermaid flowchart.
- **Run records:** the `passes` table records tokens, cost, trigger and the failing stage.
  Expectations and narratives store their prompt, model id and effort. No pass carries one
  id for the whole system version.
- **Tests:** 603 offline tests pass. Recorded-reply fixtures cover every stage parser.
- **Never done:**
  - review records for build safety, data, spend or the domain;
  - a release record or a runbook;
  - a pre-publication scan;
  - the deck and the walkthrough video.

## Goals / Non-Goals

**Goals:**
- Leave the repository as if Phases 0 and 1 had run under the kit: main specs that match the
  code, one gate including the constitution check, and a lock. Plus AI-ops, build-safety and
  domain evidence records, C4 diagrams, pre-publication checks and the showcase pieces.
- Every new check fails on a planted defect (negative test), not only passes on the current
  tree.

**Non-Goals:**
- Any change to reads, scoring, configuration defaults, prompts or model routing.
- Live API calls, provider evaluations, re-banding or the week-two model comparison.
- Budget enforcement and retention rules (values not decided; PROJECT.md §2).
- History rewrites, enabling the scheduler, hosting.

## Decisions

Owner decisions, 2026-10-15:

1. Root process documents and `openspec/` are published. `.agents/`, `.claude/` and
   `COMMISSIONING.md` stay local.
2. Phase branches are pushed and draft PRs opened on the public remote. Merges wait for the
   owner's go-ahead.
3. The scope is every gap between this project and a kit-started one ("make a phase that
   covers all the gaps", owner, 2026-10-15). Deck and video are therefore in scope; publishing
   them is not.

Settled, each reopenable by number:

4. **AI-ops applies** (fact). The product makes model calls, keeps a response cache and
   persistent state, and serves MCP. See `AI_OPS.md`.
5. **Domain review applies** (fact). Reads and rankings are decision-driving research outputs.
   See the `fundamental-engine-macro-read-review` skill.
6. **Phase numbering** (default). One Phase 2 delivers the Phase 0 and Phase 1 retrofits
   rather than reopening closed phase numbers. PROJECT.md §6 records this.
7. **Lock** (default). `uv pip compile --universal` with the `dev` and `mcp` extras writes
   `requirements/lock.txt`. CI installs `pip install -c requirements/lock.txt -e ".[dev,mcp]"`.
   Users keep the plain pip path. uv 0.13 is needed only to relock.
   - Alternative: `pip-tools`. It needs a per-platform lock for the Windows and Linux matrix.
   - Alternative: a `uv.lock` project. It would move contributors off pip.
8. **Gate** (default). `scripts/gate.py` is pure Python and runs the same on Windows and
   Linux. It runs every check and reports all of them, rather than a shell `&&` chain that
   stops at the first failure. CI installs a pinned `@fission-ai/openspec` with Node.
9. **Constitution check** (recorded: PROJECT.md § Amending the constitution). Extract the
   bullet list after the version line in §5, the bullets after "Non-negotiables" in
   AGENTS.md, and the `- ` lines after "Non-negotiables" in the config context. Unwrap and
   collapse whitespace, then compare as ordered lists. Run it as a pytest test, so the
   suite and the gate both enforce it.
10. **Private paths as code** (recorded: decision 1 and the showcase link rule). Tracked
    documents refer to `.agents/` and `.claude/` paths in code spans. This lets the link check
    require every link to resolve to a tracked file.
11. **Bundle** (default for the computation; storage is open question 3). `engine/bundle.py`
    hashes the prompt files, routing, both config files, the package version and
    `git rev-parse HEAD`, if a checkout exists. It returns a dict plus a SHA-256 id over its
    canonical JSON.
12. **Frozen evaluation set** (default). `tests/fixtures/manifest.json` lists every
    recorded-reply fixture with its SHA-256, stage and case label. A test enforces it.
    `docs/evaluation/plan.md` adapts the AI-ops EVAL_PLAN form:
    - the offline lane is this set, run by the gate;
    - the provider lane is `scripts/compare_model.py` over stored prompts, with its budget TBD
      and behind the spending gate;
    - both lanes are bound to a bundle id.
13. **Diagrams** (recorded: showcase skill, PROJECT.md §4). The C4 model lives in Structurizr
    DSL with SVG exports. The README shows Mermaid. The ERD is a Mermaid `erDiagram` in
    `docs/architecture/schema.md`, checked against the tables `engine/store/schema.py` creates.
    A small test parses the DSL for relationship labels and technologies.
14. **Main specs** (recorded: SETUP.md brownfield path). Archive Phase 1 first, then correct
    only the drift found in code and tests, as MODIFIED deltas. Add requirements for the four
    unspecified surfaces. Do not re-specify the whole system.
15. **Reviews** (recorded: build-safety skill). One comprehensive record,
    `docs/reviews/2026-10-build-safety.md`, accounts for all 17 workflows: applicability,
    findings, evidence, owner, next action and recheck trigger. Separate records cover the
    domain review and pre-publication.
16. **Pre-publication** (recorded: showcase `pre-publication.md`). detect-secrets runs through
    `uv run --no-project --with detect-secrets`, from inside a scratch export of every blob,
    with a planted fake key and a hash file that must both be found. Nothing is installed
    system-wide.
17. **Deck and video** (recorded: showcase skill).
    - Deck: Figma Slides with the six-slide story.
    - Video: about two minutes, captions instead of voice, demo data only, scripted first in
      `docs/walkthrough.md`.
    - The video goes to the owner before it is committed anywhere.
    - The README links to them only after the owner approves both, under the publishing gate.

### Non-negotiables check

| Rule or gate | How this change keeps it |
|---|---|
| Secrets | The secret scan reads blobs into an ignored scratch folder and prints counts and paths, never values |
| No fabricated results | Every review records commands and outcomes. Commissioning follow-ups and provider evaluations stay not_run |
| Operator resolves gates | Publishing, migration and any spend wait for the owner (open questions) |
| `no read` never `neutral` | Untouched. The domain review adds independent known-answer cases |
| Current and expected separate | Untouched. The bundle and reviews add no blended field |
| Repeated coverage | Untouched |
| Models interpret, Python calculates | A new import test asserts `engine/reading/` and `engine/universe/` import no model code |
| Fact sheet, resolving citations | Untouched. The frozen set keeps the bad-citation cases |
| Offline tests | Every new check is offline. The gate runs no live call |
| Research only, loopback, read-only MCP | Untouched. The outside check confirms the server listens on 127.0.0.1 only |
| Methodology changes documented | Only markers are added. No value changes |
| No accuracy claims | The deck, video and README keep the existing limits language |
| ⛔ Spending | No API call in this phase |
| ⛔ Unattended runs | The scheduler stays disabled |
| ⛔ Publishing | Draft PR only (decision 2). Deck, video, README links and Phase 1's record wait for approval |
| ⛔ Service exposure | None |
| ⛔ Live records | The bundle migration waits for open question 3. A backup comes first |

## Risks / Trade-offs

- **[Main specs carry Phase 1 wording that names the sibling project]** → The unattended-loop
  spec's guard requirement is corrected to `trading_runtime` before `openspec/specs/` is first
  committed.
- **[`openspec` in CI depends on npm]** → Pin the exact version. A missing CLI fails the gate
  loudly rather than skipping.
- **[A universal lock can pin a version that misbehaves on one platform]** → The CI matrix
  installs from it on both operating systems and both Python versions.
- **[Spec reconciliation could drift into re-specifying everything]** → Only requirements
  contradicted by code or tests are modified. The rest archive as written.
- **[The secret scan finds something in public history]** → Rotate the credential at once.
  A history rewrite is the owner's call, and the repo is already public.
- **[Showcase pieces take owner review time]** → They are the last task group, so the rest can
  merge first if the owner prefers to split them out.

## Migration Plan

Code changes are additive. The bundle field needs a new nullable column (or table) in an
existing local `data/engine.db`. The operator backs up the file, and the engine applies an
additive `ALTER TABLE` on first connection. Older passes keep an unknown bundle. Rollback:
restore the backup. A database without the column still opens, and the field reads unknown.

## Open Questions

Owner calls. Each blocks only the task named.

1. **Phase 1's change record** (Gate: publishing; One-way door). It names the sibling trading
   project and where the copied feed and model code came from, which the public code
   neutralised. Options:
   - keep it local and publish only the reconciled main specs (recommended);
   - publish a neutralised copy;
   - publish it as is.

   Blocks task 1.5.
2. **Structurizr CLI** (Authority). Exporting SVG and Mermaid needs the Structurizr CLI.
   Java 17 is installed; Docker is not. Recommended: download a pinned release into ignored
   `.local/tools/`, with no system install. Alternative: hand-written Mermaid only, with no
   SVG. Blocks task 6.3.
3. **Bundle storage** (One-way door: stored schema; Gate: live records). Options:
   - an additive nullable `bundle_id` column on `passes`, plus a `bundles` table, migrated
     after a backup (recommended);
   - JSON bundle files under `build/bundles/`, matched to passes by time.

   Blocks task 3.2.
4. **Constitution rules 4–12** (Scope, Methodology). They were taken from existing documents
   and tests at setup. Ratify, edit or drop them in this PR's review. Any change is an
   amendment commit.
