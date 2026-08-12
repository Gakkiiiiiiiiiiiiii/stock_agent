"""Backend selectors used during the content/factor strangler migration."""
from __future__ import annotations

import os
from typing import Any

from clients.content_client import LocalContentClient, RemoteContentClient
from clients.factor_client import LocalFactorClient, RemoteFactorClient


def content_backend() -> str:
    return os.getenv("CONTENT_BACKEND", "local").strip().lower()


def factor_backend() -> str:
    return os.getenv("FACTOR_BACKEND", "local").strip().lower()


def build_content_client(local_service: Any):
    if content_backend() == "remote":
        return RemoteContentClient(os.getenv("CONTENT_SERVICE_URL", "http://stock-content:8100"))
    return LocalContentClient(local_service)


def build_factor_client(local_server: Any):
    if factor_backend() == "remote":
        return RemoteFactorClient(os.getenv("FACTOR_SERVICE_URL", "http://stock-factor:8200"))
    return LocalFactorClient(local_server)


def get_factor_client():
    """Resolve the Factor boundary lazily to keep legacy imports out of MCP."""
    if factor_backend() == "remote":
        return RemoteFactorClient(os.getenv("FACTOR_SERVICE_URL", "http://stock-factor:8200"))
    from mcp_servers import legacy_factor_mining_server

    return LocalFactorClient(legacy_factor_mining_server)
