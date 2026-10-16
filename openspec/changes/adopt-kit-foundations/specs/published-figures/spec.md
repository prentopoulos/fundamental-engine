## ADDED Requirements

### Requirement: Published worked examples are computed, not typed

Every figure in a worked example in `README.md` or `docs/methodology.md` SHALL sit between
named hidden markers. Examples include contributions after decay, example scores and their
states, the breadth floor, the degree bands and the index coefficients. A test MUST recompute
each figure with the engine's own arithmetic and configuration, and compare it with the
published text.

#### Scenario: Example matches the engine

- **WHEN** the README says a fresh +6 and a fresh −4 situation score +2 and read neutral
- **THEN** the test computes the same score and state from the default configuration and passes

#### Scenario: A figure drifts

- **WHEN** a configured default changes, or a published figure is edited, so the two disagree
- **THEN** the test fails and names the marker and both values

#### Scenario: Markers are removed

- **WHEN** a marked block disappears from the document
- **THEN** the test fails rather than passing with nothing to check
