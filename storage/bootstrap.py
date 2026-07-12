from __future__ import annotations

from storage.models import content  # noqa: F401
from storage.db import Base, get_engine
from storage.models import vector  # noqa: F401


def create_all() -> None:
    Base.metadata.create_all(bind=get_engine())
