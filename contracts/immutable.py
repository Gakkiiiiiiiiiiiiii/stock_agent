"""Small JSON-compatible containers that reject in-place mutation."""
from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any


class FrozenDict(dict):
    def _blocked(self, *args, **kwargs):
        raise TypeError("immutable mapping")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _blocked


class FrozenList(list):
    def _blocked(self, *args, **kwargs):
        raise TypeError("immutable sequence")

    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = __iadd__ = __imul__ = _blocked


def freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenDict({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple, set, frozenset)):
        return FrozenList([freeze(item) for item in value])
    return value


def ensure_json(value: Any, path: str = "payload") -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains non-finite float")
        return value
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} contains non-string object key")
            result[key] = ensure_json(item, f"{path}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [ensure_json(item, f"{path}[]") for item in value]
    raise ValueError(f"{path} contains non-JSON value: {type(value).__name__}")
