"""HTTP route layer split by domain (设计文档 P0-05 / §28)。

Routers only parse params, do pre-checks, call services, and shape HTTP
responses; business logic lives in app services / engines / mcp_servers.
"""
