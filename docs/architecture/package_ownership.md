# Package ownership

This is the enforced direction for incremental moves; it deliberately avoids a
big-bang `src/` migration.

| Package | Owns | Must not own |
|---|---|---|
| `agent/` | runtime, context, skill execution | HTTP routes, persistence implementation |
| `app/` | FastAPI wiring, model clients, tool providers | investment/domain calculation |
| `engines/` | deterministic domain logic and retrieval | HTTP request handling |
| `storage/` | models, migrations, repositories | agent orchestration |
| `workers/` | asynchronous job execution | business-rule implementation |
| `financial_agent/` | legacy compatibility models/config only | new domain modules |

`architect/` and `artitect/` are retained as historical source material. New
architecture documentation belongs in this directory; no new runtime imports
may target either historical directory.
