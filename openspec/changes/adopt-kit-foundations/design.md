## Context

The code was built under one OpenSpec change (`build-fundamental-reader`, 101 tasks) and
released publicly on 2026-10-07 as a single curated commit. The development history, the
change record, the agent tooling and the commissioning record were kept out of Git. The kit
was adopted on 2026-10-15 (setup commit on this branch).

The current state against the kit's Phase 0:
- `openspec/specs/` is empty, and the Phase 1 specs lag the code.
- There is no lockfile and no single gate.
- CI runs lint, format and tests on Windows and Linux with Python 3.11 and 3.13.
- 603 offline tests pass.
- Nothing checks that AGENTS.md and `openspec/config.yaml` still match PROJECT.md §5, or
  that links in tracked documents resolve.

## Goals / Non-Goals

**Goals:**
- Main specs that match the code, a lock, and one gate that CI and agents both run. The gate
  includes the constitution copy check.
- Every new check fails on a planted defect (a negative test), not only passes on the current
  tree.

**Non-Goals:**
- The evidence records (Phase 3) and the showcase (Phase 4).
- Any change to reads, scoring, configuration defaults, prompts, model routing or stored data.

## Decisions

Owner decisions:

1. Root process documents and `openspec/` are published. `.agents/`, `.claude/` and
   `COMMISSIONING.md` stay local (2026-10-15).
2. Phase branches are pushed and draft PRs opened on the public remote. Merges wait for the
   owner's go-ahead (2026-10-15).
3. The kit catch-up is three phases: Foundations (this one), Evidence and Showcase
   (2026-10-16).
4. Phase 1's change record stays local, including its archived copy. Only the reconciled
   main specs are published (2026-10-16).
5. Rules 4–12 are confirmed. Rule 7 is corrected in constitution v1.0.1, because model output
   does set expected states and confidence (2026-10-16).

Settled, each reopenable by number:

6. **Lock** (default). `uv pip compile --universal` with the `dev` and `mcp` extras writes
   `requirements/lock.txt`. CI installs `pip install -c requirements/lock.txt -e ".[dev,mcp]"`.
   Users keep the plain pip path. uv 0.13 is needed only to relock.
   - Alternative: `pip-tools`. It needs a per-platform lock for the Windows and Linux matrix.
   - Alternative: a `uv.lock` project. It would move contributors off pip.
7. **Gate** (default). `scripts/gate.py` is pure Python and runs the same on Windows and Linux.
   It runs every check and reports all of them, rather than a shell `&&` chain that stops at
   the first failure. CI installs an exact `@fission-ai/openspec` version with Node. A missing
   CLI fails the gate rather than skipping it.
8. **Constitution check** (recorded: PROJECT.md § Amending the constitution). Extract the
   bullet list after the version line in §5, the bullets after "Non-negotiables" in
   AGENTS.md, and the `- ` lines after "Non-negotiables" in the config context. Unwrap and
   collapse whitespace, then compare as ordered lists. Run it as a pytest test, so the
   suite and the gate both enforce it.
9. **Private paths as code** (recorded: decision 1 and the showcase link rule). Tracked
   documents refer to `.agents/` and `.claude/` paths in code spans. This lets the link check
   require every link to resolve to a tracked file.
10. **Arithmetic stays model-free** (recorded: PROJECT.md §5, "Python calculates"). An import
   test asserts that `engine/reading/` and `engine/universe/` import nothing from
   `engine.model`, `engine.stages` or `anthropic`.
11. **Main specs** (recorded: SETUP.md brownfield path). Archive Phase 1 first, then correct
   only drift shown by code or tests, as MODIFIED deltas. Add requirements for the four
   unspecified surfaces. Do not re-specify the whole system.
   - Exception: the guard requirement's directory name is corrected in the synced spec itself
     before `openspec/specs/` is first committed. A delta would leave the sibling project's
     name in the published branch until this change is archived.

### Non-negotiables check

| Rule or gate | How this change keeps it |
|---|---|
| Secrets | No secret is read or printed. The lock holds no index credentials |
| No fabricated results | Gate and CI outcomes are recorded as run. Spec corrections cite the code or test that shows the drift |
| Operator resolves gates | Publishing decisions come from the owner (decisions 1, 2 and 4) |
| `no read`, separate lanes, repeated coverage, fact sheet, research only | Untouched. The archived specs keep these requirements |
| Models interpret, Python calculates | Enforced by the new import test (decision 10) |
| Offline tests | Every new check is offline. The gate makes no live call |
| Methodology changes documented | No value changes. Spec corrections describe existing behaviour |
| No accuracy claims | Untouched |
| ⛔ Spending, unattended runs, service exposure, live records | Not touched |
| ⛔ Publishing | Draft PR only (decision 2). Phase 1's record stays local (decision 4) |

## Risks / Trade-offs

- **[Synced specs name the sibling project]** → The guard requirement is corrected before
  `openspec/specs/` is first committed (decision 11). The other Phase 1 spec text was checked
  for personal details on 2026-10-15 and has none.
- **[`openspec` in CI depends on npm]** → Pin the exact version. A missing CLI fails the gate
  loudly.
- **[A universal lock can pin a version that misbehaves on one platform]** → The CI matrix
  installs from it on both operating systems and both Python versions.
- **[Spec reconciliation could grow into re-specifying everything]** → Only requirements that
  code or tests contradict are modified. The rest archive as written.

## Migration Plan

No runtime migration. CI switches to the gate in one commit; reverting that commit restores
the previous three-step job.

## Open Questions

None. Both owner calls were answered on 2026-10-16:
- Phase 1's change record stays local (decision 4).
- Rules 4–12 are confirmed, and rule 7 is corrected in constitution v1.0.1 (decision 5).
