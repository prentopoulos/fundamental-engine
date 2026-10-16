# Current handoff

Read PROJECT.md and current user instructions. Inspect actual Git/OpenSpec state before resuming.

## Current checkpoint

Agent skills and OpenSpec were set up on 2026-10-15 from the new_project_agent_setup kit, after
the code was already built. The project was not started from the kit. The setup is the first
commit on the Phase 2 branch, so `main` still matches the public release.

- Phase 1 (`build-fundamental-reader`): all 101 tasks are ticked and the code is on `main`,
  released publicly on 2026-10-07. The change is not archived, so `openspec/specs/` is empty.
  Its record stays local, including once archived (owner, 2026-10-16; PROJECT.md §6).
- Constitution v1.0.1 (2026-10-16): the owner confirmed rules 4–12 and corrected rule 7.
- Phase 2 (`adopt-kit-foundations`, Foundations): proposed on branch
  `phase-2/adopt-kit-foundations` (a setup commit plus spec-only commits), with draft PR #1
  for owner review. No task has started. The owner split the kit catch-up into Phases 2–4
  on 2026-10-16. Phase 3 (Evidence) and Phase 4 (Showcase) are planned in PROJECT.md §6,
  with their open questions recorded there.
- `engine/prompts/narrative.md` shows as modified in `git status` but has no content diff. It
  was like that before setup and is not part of it.

## Implemented behavior

Phase 1 as described in PROJECT.md §2: feeds, model stages, situations, deterministic reads,
expectations, narratives, the local board, desk chat, a read-only MCP server and an offline
demo. The scheduler ships disabled. The tracked docs (README, methodology, architecture,
running) describe the actual behaviour.

## Verification and next action

Setup verification on 2026-10-15, local Windows:
- Kit validator: all checks pass.
- Offline suite: 603 passed (`python -m pytest -q --basetemp <scratch>`; the sandbox denied
  pytest's default temp folder, which caused 226 setup errors without `--basetemp`).
- `openspec validate --all --strict`: 1 passed (`build-fundamental-reader`).
- No AI runtime evaluation exists beyond the 2026-08-31 commissioning pass (local record).

Next action: Phase 2 has no open questions. In a fresh session, the owner runs `/next-phase`
to implement `adopt-kit-foundations` from task 1.1 on branch `phase-2/adopt-kit-foundations`
(draft PR #1).
