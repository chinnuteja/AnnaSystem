"""Broad-style WhatsApp utterances: domain routing via retrieval + latency smoke.

These are integration-style checks against mock catalogs (no LLM).
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_DIR))

import pytest

from app.agents.semantic_router import route_domains
from app.schemas.message import ParsedIntent, ParsedItem
from packages.providers.interface import Location


def _loc():
    return Location(
        latitude=17.4486,
        longitude=78.3792,
        pincode="500032",
        city="Hyderabad",
    )


EVAL_CASES = [
    ("atta 10kg pampu", "grocery"),
    ("2 liter milk kavali", "grocery"),
    ("mutton dum biryani single", "food_delivery"),
    ("chicken dum biryani delivery", "food_delivery"),
    ("Absolute Barbecues Madhapur buffet booking", "dineout"),
]


@pytest.mark.parametrize("text,expected", EVAL_CASES)
def test_eval_utterance_domain_routing(text, expected):
    async def _run():
        intent = ParsedIntent(
            action="ORDER",
            raw_text=text,
            items=[ParsedItem(text=text)],
            goal="shop",
            domain_hint="unknown",
        )
        t0 = time.monotonic()
        r = await route_domains(intent, location=_loc(), language="te-IN")
        elapsed_ms = (time.monotonic() - t0) * 1000
        assert r.chosen == expected, f"{text!r} -> {r.chosen} scores={r.domain_scores}"
        assert elapsed_ms < 15_000, f"p95 budget check smoke: {elapsed_ms}ms"

    asyncio.run(_run())
