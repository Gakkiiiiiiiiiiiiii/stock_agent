"""Non-authoritative analysis API.

This router intentionally has no import path to execution or broker adapters.
"""
from __future__ import annotations

from datetime import date as Date
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app import dependencies
from app.domain.decision.authority import analysis_response

router = APIRouter(prefix="/api/v2/analysis", tags=["analysis"])


class StockAnalysisRequest(BaseModel):
    symbol: str
    date: Date | None = None
    patterns: list[str] | None = None


class ThemeAnalysisRequest(BaseModel):
    theme_name: str


@router.post("/stock")
def analyze_stock(request: StockAnalysisRequest) -> dict[str, Any]:
    return analysis_response(dependencies.orchestrator.analyze_stock(request.symbol, as_of=request.date, patterns=request.patterns))


@router.post("/theme")
def analyze_theme(request: ThemeAnalysisRequest) -> dict[str, Any]:
    return analysis_response(dependencies.orchestrator.analyze_theme(request.theme_name))
