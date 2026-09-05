# ADR-0001: Investment decision authority

`stock_agent` is the sole authority for formal investment decisions. Only
`POST /api/v2/decisions` can produce a formal result. Analysis and compatibility
responses are explicitly non-authoritative and always have
`execution_eligible=false`.
