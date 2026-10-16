## ADDED Requirements

### Requirement: One gate command runs every local check

The project SHALL provide one gate command that runs lint, format checking, the offline test
suite, `openspec validate --all --strict` and the constitution copy check, in that order. It
MUST exit non-zero when any check fails or cannot run, and MUST name each failed check. CI
SHALL run the same command on every supported operating system and Python version.

#### Scenario: All checks pass

- **WHEN** the gate runs on a clean checkout whose checks all pass
- **THEN** it exits zero and lists each check as passed

#### Scenario: One check fails

- **WHEN** a test fails, or a file is not formatted
- **THEN** the gate exits non-zero and names that check
- **AND** it still reports the result of every other check

#### Scenario: A required tool is missing

- **WHEN** the OpenSpec CLI is not installed
- **THEN** the gate reports that check as failed, not skipped, and exits non-zero

### Requirement: The constitution copies cannot drift

The gate SHALL compare the non-negotiables in PROJECT.md §5 with the copies in AGENTS.md and
`openspec/config.yaml`, ignoring only line wrapping. It SHALL also check that the version line
in §5 matches the newest row of the amendment log.

#### Scenario: Copies match

- **WHEN** every copy lists the same rules in the same order as PROJECT.md §5
- **THEN** the check passes

#### Scenario: A copy drifts

- **WHEN** a rule in AGENTS.md or `openspec/config.yaml` is added, removed, reordered or
  reworded relative to PROJECT.md §5
- **THEN** the check fails and names the file and the first differing rule

#### Scenario: Version line out of step

- **WHEN** the §5 version line differs from the newest amendment-log row
- **THEN** the check fails

### Requirement: Dependencies are installed from a lock

The repository SHALL hold a lock that pins every runtime, `dev` and `mcp` dependency for
Windows and Linux on the supported Python versions. CI MUST install with the lock as a
constraint. A check SHALL fail when `pyproject.toml` declares a dependency the lock does not
pin. The editable `pip install -e .` path in the README keeps working without the lock.

#### Scenario: A dependency is added without relocking

- **WHEN** a dependency is added to `pyproject.toml` and the lock is not regenerated
- **THEN** the check fails and names the missing dependency

#### Scenario: CI uses the lock

- **WHEN** CI installs the project
- **THEN** every installed distribution's version equals the one the lock pins

### Requirement: Tracked documents link only to tracked files

Every relative link in a tracked Markdown file SHALL resolve to a tracked file or directory.
Paths that exist only on the owner's machine, such as `.agents/` and `.claude/`, MUST appear
as code, not as links. The README SHALL name the licence and link to `LICENSE.md` and
`NOTICE.md`.

#### Scenario: A broken link

- **WHEN** a tracked Markdown file links to a missing or untracked path
- **THEN** the check fails and names the file and the link

#### Scenario: Licence removed from the README

- **WHEN** the README no longer links to `LICENSE.md` or `NOTICE.md`
- **THEN** the check fails
