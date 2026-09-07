"""HTTP adapters for independently deployed stock subsystems.

Keep package exports lazy: a knowledge-only process may construct the content
client without importing the formal Quant/Factor client modules.
"""
from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clients.content_client import ContentClient, RemoteContentClient  # noqa: F401
    from clients.factor_client import FactorClient, RemoteFactorClient  # noqa: F401
    from clients.quant_client import QuantClient, RemoteQuantClient  # noqa: F401


_EXPORTS = {
    "ContentClient": "clients.content_client",
    "RemoteContentClient": "clients.content_client",
    "FactorClient": "clients.factor_client",
    "RemoteFactorClient": "clients.factor_client",
    "QuantClient": "clients.quant_client",
    "RemoteQuantClient": "clients.quant_client",
}


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    return getattr(import_module(module_name), name)


__all__ = list(_EXPORTS)
