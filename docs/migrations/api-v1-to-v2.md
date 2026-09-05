# API v1 to v2 migration

Use `/api/v2/analysis/stock` and `/api/v2/analysis/theme` for non-authoritative
analysis. Use `/api/v2/decisions` for formal decisions with timezone-aware
`as_of`. `POST /api/v1/decisions` is permanently `410 Gone`; v1 analysis and
agent routes remain read-only compatibility responses with deprecation and
sunset headers.
