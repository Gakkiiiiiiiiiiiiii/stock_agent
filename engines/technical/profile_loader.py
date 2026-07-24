from __future__ import annotations

from pathlib import Path

import yaml

from engines.technical.models import IndicatorSpec, TechnicalProfile
from financial_agent.utils import project_root


def load_technical_profile(name: str = "core_daily_v1", path: str | Path | None = None) -> TechnicalProfile:
    cfg_path = Path(path) if path else project_root() / "config" / "technical_profiles.yaml"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    raw = (data.get("profiles") or {}).get(name)
    if raw is None:
        raise KeyError(f"technical profile not found: {name}")
    return TechnicalProfile(
        name=name,
        version=str(raw["version"]),
        frequency=str(raw.get("frequency", "1d")),
        minimum_bars=int(raw.get("minimum_bars", 0)),
        price_adjustment=str(raw.get("price_adjustment", "front")),
        indicators=[
            IndicatorSpec(name=str(item["name"]), alias=str(item["alias"]), params=dict(item.get("params") or {}))
            for item in raw.get("indicators") or []
        ],
        output_precision=int(raw.get("output_precision", 8)),
    )
