## ADDED Requirements

### Requirement: The architecture is one C4 model kept as code

The repository SHALL keep one Structurizr DSL model in `docs/architecture/workspace.dsl` with
a system context view and a container view. Following the C4 notation guide, each element
MUST carry a name, a type, a one-line description and, for containers, a technology. Each
relationship MUST go one way and carry a specific verb and a technology. Exported SVG views
SHALL be committed beside the model, and `docs/architecture.md` SHALL show the container view
as Mermaid.

#### Scenario: A relationship without a verb

- **WHEN** the model contains a relationship labelled only "Uses" or with no technology
- **THEN** the architecture check fails and names it

#### Scenario: Containers match what runs

- **WHEN** the container view is read
- **THEN** it contains the pipeline CLI, the scheduler, the web interface, the MCP server, the
  SQLite database and the model replay cache, and nothing that does not run or store data

### Requirement: The schema diagram names every table

`docs/architecture/` SHALL hold an entity-relationship diagram of the SQLite schema. A test
MUST compare the tables the schema creates with the entities in the diagram.

#### Scenario: A table is added

- **WHEN** the schema gains a table that the diagram does not show
- **THEN** the test fails and names the table

#### Scenario: A table is dropped

- **WHEN** the diagram shows a table the schema no longer creates
- **THEN** the test fails and names it
