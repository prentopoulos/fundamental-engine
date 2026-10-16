## ADDED Requirements

### Requirement: Every pass records its system bundle

Each pass SHALL record the complete system version it ran with as a bundle: the SHA-256 of
each stage prompt file, the model routing, the SHA-256 of `config/params.yaml` and
`config/instruments.yaml`, the package version, and the Git revision when one is available.
The bundle id MUST be a deterministic hash of those fields. Computing it SHALL make no network
call and need no credential.

#### Scenario: Same inputs give the same id

- **WHEN** the bundle is computed twice with unchanged prompts, routing, config and code
- **THEN** both ids are equal

#### Scenario: A prompt changes

- **WHEN** one stage prompt file changes by one byte
- **THEN** the bundle id changes and the stored bundle shows which prompt hash moved

#### Scenario: No Git checkout

- **WHEN** the package runs outside a Git checkout
- **THEN** the revision is recorded as unknown, not empty, and the bundle is still recorded

### Requirement: The offline evaluation set is frozen

The recorded model replies used to check stage parsers and integrity rules SHALL be listed in a
manifest with each file's SHA-256 and its case label: normal, edge or deliberately failing.
The gate MUST fail when a listed file changes without a manifest update. Changing the
manifest is a reviewed change that states why.

#### Scenario: A frozen case is edited

- **WHEN** a listed fixture changes and the manifest does not
- **THEN** the check fails and names the fixture

#### Scenario: Every stage is covered

- **WHEN** the manifest is read
- **THEN** each model stage (triage, scoring, situations, expectation, narrative) has at least
  one normal and one deliberately failing case
