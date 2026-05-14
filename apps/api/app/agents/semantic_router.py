"""Retrieval-first domain router: scores grocery / food_delivery / dineout from catalog hits.

Replaces hardcoded dish lexicons for domain choice. Parser supplies goal/action;
this module supplies domain evidence and a confidence gate.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Literal

from app.agents.catalog_search import parallel_catalog_search
from app.schemas.message import ParsedIntent
from packages.providers.interface import Location

logger = logging.getLogger("foodleaf.semantic_router")

ChosenDomain = Literal["grocery", "food_delivery", "dineout", "ambiguous", "unknown"]

# Tunables for mock catalogs (tighten when real pgvector scores land)
_MIN_EVIDENCE = 0.26
_GAP_CLEAR_WIN = 0.12
_GAP_AMBIGUOUS = 0.10


@dataclass(frozen=True)
class SemanticRouteResult:
    """Router output merged into ParsedIntent.router_trace and domain_hint."""

    domain_scores: dict[str, float]
    chosen: ChosenDomain
    confidence: float  # 0–1 how clear the winner is
    should_clarify_domain: bool
    clarification_question: str | None
    top_evidence: list[str]
    catalog_search_ms: int
    status_score: float


def _routing_query(intent: ParsedIntent) -> str:
    parts = [intent.raw_text.strip()]
    parts.extend(i.text.strip() for i in intent.items if i.text.strip())
    return " ".join(p for p in parts if p).strip()


def _should_route(intent: ParsedIntent) -> bool:
    if intent.action in ("TRACK", "CONFIRM", "CANCEL", "CHITCHAT"):
        return False
    # Brain handles corrections — no need to block them from routing
    # Parser already asked for a follow-up with no concrete tokens — avoid noisy catalog hits.
    if intent.needs_clarification and not intent.items and intent.action != "DISCOVER":
        return False
    if intent.action == "AMEND":
        return True
    q = _routing_query(intent)
    if not q:
        return False
    return True


async def route_domains(
    intent: ParsedIntent,
    *,
    location: Location,
    language: str,
) -> SemanticRouteResult:
    """Score domains from parallel catalog retrieval + parser action for status."""
    t0 = time.monotonic()

    status_score = 1.0 if intent.action == "TRACK" else 0.0
    if intent.action == "TRACK":
        ms = int((time.monotonic() - t0) * 1000)
        return SemanticRouteResult(
            domain_scores={"grocery": 0.0, "food_delivery": 0.0, "dineout": 0.0, "status": 1.0, "unclear": 0.0},
            chosen="unknown",
            confidence=1.0,
            should_clarify_domain=False,
            clarification_question=None,
            top_evidence=["order status / tracking"],
            catalog_search_ms=ms,
            status_score=status_score,
        )

    q = _routing_query(intent)
    bundles = await parallel_catalog_search(q, language, location)
    ms = int((time.monotonic() - t0) * 1000)

    def _best(domain: str) -> float:
        hits = bundles.get(domain, [])
        return max((h.score for h in hits), default=0.0)

    scores = {
        "grocery": _best("grocery"),
        "food_delivery": _best("food_delivery"),
        "dineout": _best("dineout"),
        "status": status_score,
        "unclear": 0.0,
    }

    # Evidence strings: top hit per domain
    evidence: list[str] = []
    for dom in ("grocery", "food_delivery", "dineout"):
        hs = bundles.get(dom, [])
        if hs:
            top = hs[0]
            evidence.append(f"{dom}:{top.label}({top.score:.2f})")

    ranked = sorted(
        (("grocery", scores["grocery"]), ("food_delivery", scores["food_delivery"]), ("dineout", scores["dineout"])),
        key=lambda x: x[1],
        reverse=True,
    )
    first_n, first_s = ranked[0]
    second_s = ranked[1][1]
    gap = first_s - second_s

    clarify = False
    chosen: ChosenDomain = "unknown"
    conf = 0.0
    clarify_q: str | None = None

    if first_s < _MIN_EVIDENCE:
        chosen = "unknown"
        conf = max(first_s, 0.0)
        clarify = intent.action in ("ORDER", "UNCLEAR", "DISCOVER")
        if clarify:
            clarify_q = (
                "Groceries (atta, milk) na food delivery na dineout? "
                "Okka line lo cheppandi."
            )
    elif gap < _GAP_AMBIGUOUS and second_s >= _MIN_EVIDENCE - 0.04:
        chosen = "ambiguous"
        conf = gap
        clarify = True
        clarify_q = (
            "Idi grocery item na restaurant food na dineout? "
            "Okka choice cheppandi — groceries / delivery / dineout."
        )
    elif gap >= _GAP_CLEAR_WIN or first_s >= 0.82:
        chosen = first_n  # type: ignore[assignment]
        conf = min(1.0, 0.55 + gap + first_s * 0.25)
        clarify = False
    else:
        chosen = "unknown"
        conf = gap
        clarify = intent.action in ("ORDER", "DISCOVER", "UNCLEAR")
        if clarify:
            clarify_q = (
                "Konchem specific ga cheppandi — item peru (atta, ice cream) "
                "leda dish peru (biryani) leda dineout?"
            )

    logger.info(
        "semantic_router scores=%s chosen=%s conf=%.2f clarify=%s (%dms)",
        scores,
        chosen,
        conf,
        clarify,
        ms,
    )

    return SemanticRouteResult(
        domain_scores=scores,
        chosen=chosen,
        confidence=conf,
        should_clarify_domain=clarify,
        clarification_question=clarify_q,
        top_evidence=evidence[:6],
        catalog_search_ms=ms,
        status_score=status_score,
    )


def apply_route_to_intent(
    intent: ParsedIntent,
    route: SemanticRouteResult,
    *,
    client_timings: dict[str, Any] | None = None,
) -> ParsedIntent:
    """Attach router trace; set domain_hint / clarification from retrieval evidence."""
    trace: dict[str, Any] = {
        "domain_scores": route.domain_scores,
        "chosen": route.chosen,
        "confidence": route.confidence,
        "should_clarify_domain": route.should_clarify_domain,
        "top_evidence": route.top_evidence,
        "catalog_search_ms": route.catalog_search_ms,
    }
    if client_timings:
        trace["client_timings_ms"] = client_timings

    updates: dict[str, Any] = {"router_trace": trace}

    if intent.action == "TRACK":
        return intent.model_copy(update=updates)

    # If the pipeline already locked in a concrete domain (e.g. user already answered
    # "Groceries" to a clarification question), keep that domain and do NOT re-clarify.
    # The router trace is still attached for observability.
    if intent.domain_hint not in ("any", "unknown"):
        logger.info(
            "Router respecting locked domain_hint=%s (router would have chosen=%s)",
            intent.domain_hint,
            route.chosen,
        )
        return intent.model_copy(update=updates)

    if route.chosen == "ambiguous":
        updates["domain_hint"] = "any"
        updates["needs_clarification"] = True
        if route.clarification_question:
            updates["clarification_question"] = route.clarification_question
    elif route.chosen in ("grocery", "food_delivery", "dineout"):
        updates["domain_hint"] = route.chosen

    if route.should_clarify_domain and route.chosen != "ambiguous" and route.clarification_question:
        updates["needs_clarification"] = True
        updates["clarification_question"] = route.clarification_question

    return intent.model_copy(update=updates)


async def maybe_route_intent(
    intent: ParsedIntent,
    *,
    location: Location | None,
    default_location: Location,
    language: str,
    client_timings: dict[str, Any] | None = None,
) -> ParsedIntent:
    """Run catalog router when appropriate; otherwise return intent unchanged."""
    if not _should_route(intent):
        return intent

    loc = location or default_location
    route = await route_domains(intent, location=loc, language=language)
    return apply_route_to_intent(intent, route, client_timings=client_timings)
