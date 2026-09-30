# Elektro Vienna Knowledge System

Foundation for an operational and technical learning system: inquiry → understanding → pricing → execution → outcome → reusable knowledge.

This repository currently contains architecture documentation and an empty Python package scaffold. No application functionality or integrations are implemented.

Start with [AGENTS.md](AGENTS.md), then read:

- [North Star](docs/NORTH_STAR.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Conceptual data model](docs/DATA_MODEL.md)
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)

The standard `src/` layout contains `elektro_vienna`. Project metadata is in `pyproject.toml`; Python 3.11 or newer is the initial baseline. No runtime dependencies, command-line entry point, or test framework are selected. `tests/` is reserved for meaningful tests as functionality is added.

Git stores code and technical documentation. Runtime knowledge and customer material belong outside this repository, in the authorized storage described in the architecture. The next implementation milestone is read-only historical Gmail ingestion; Outlook Calendar remains a separate future integration.
