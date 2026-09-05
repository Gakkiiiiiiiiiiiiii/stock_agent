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

Historical `architect/` and `artitect/` documents are archived under
`docs/architecture/legacy/`. New architecture documentation belongs in this
directory; no runtime imports may target the legacy archive.
