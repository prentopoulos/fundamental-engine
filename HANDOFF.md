# Current handoff

Read PROJECT.md and current user instructions. Inspect actual Git/OpenSpec state before resuming.

## Current checkpoint

Agent skills and OpenSpec were set up on 2026-10-15 from the new_project_agent_setup kit, on
`main`, after the code was already built. The project was not started from the kit.

- Phase 1 (`build-fundamental-reader`): all 101 tasks are ticked and the code is on `main`,
  released publicly on 2026-10-07. The change is not archived, so `openspec/specs/` is empty.
  Its record stays untracked until the owner decides how to publish it (PROJECT.md §6).
- Phase 2 (`adopt-kit-foundations`): next. It delivers what Phases 0–1 would have produced
  under this workflow.
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

Next action: propose Phase 2, `adopt-kit-foundations`, on its own branch with a spec-only
commit and a draft PR.
