from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


# Factor research is owned by stock_factor.  stock_agent intentionally has no
# factor-production HTTP surface; decision tools consume read-only evidence.
