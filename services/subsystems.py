"""Remote subsystem clients used after Content/Factor cutover."""
from __future__ import annotations

import os
from clients.content_client import RemoteContentClient
from clients.factor_client import RemoteFactorClient


def content_backend() -> str:
    return "remote"


def factor_backend() -> str:
    return "remote"


def build_content_client():
    return RemoteContentClient(os.getenv("CONTENT_SERVICE_URL", "http://stock-content:8100"))


def build_factor_client():
    return RemoteFactorClient(os.getenv("FACTOR_SERVICE_URL", "http://stock-factor:8200"))


def get_factor_client():
    return build_factor_client()


def get_content_client():
    return build_content_client()
