"""Occasion Calendar — in-code festival dictionary and helper functions.

Provides upcoming festival detection for proactive brain hints.
Currently focused on Diwali as the MVP occasion.
"""

from __future__ import annotations

from datetime import date, timedelta
from dataclasses import dataclass


@dataclass
class Festival:
    """A festival/occasion entry."""

    name: str
    name_hi: str
    date: date
    description: str
    description_hi: str
    relevant_categories: list[str]  # e.g. ["grocery", "sweets", "gifts"]
    suggestion_hi: str  # proactive hint in Hindi
    suggestion_en: str  # proactive hint in English


# 2024-2026 festival dates (approximate for lunar calendars)
_FESTIVALS: list[Festival] = [
    Festival(
        name="Diwali",
        name_hi="\u0926\u093f\u0935\u093e\u0932\u0940",
        date=date(2026, 11, 8),
        description="Festival of lights \u2014 families celebrate with sweets, diyas, gifts, and feasting.",
        description_hi="\u0926\u0940\u092a\u093e\u0935\u0932\u0940 \u2014 \u092e\u093f\u0920\u093e\u0908, \u0926\u0940\u092f\u0947, \u0909\u092a\u0939\u093e\u0930 \u0914\u0930 \u0926\u093e\u0935\u0924 \u0915\u0947 \u0938\u093e\u0925 \u092a\u0930\u093f\u0935\u093e\u0930 \u092e\u0928\u093e\u0924\u093e \u0939\u0948\u0964",
        relevant_categories=["grocery", "sweets", "gifts", "snacks"],
        suggestion_hi="\u0926\u093f\u0935\u093e\u0932\u0940 \u0906 \u0930\u0939\u0940 \u0939\u0948! \u092e\u093f\u0920\u093e\u0908, \u0928\u092e\u0915\u0940\u0928, \u092f\u093e \u0926\u0940\u092f\u0947 \u09d1\u0930\u094d\u0921\u0930 \u0915\u0930\u0928\u093e \u091a\u093e\u0939\u0947\u0902\u0917\u0940?",
        suggestion_en="Diwali is coming! Would you like to order sweets, snacks, or diyas?",
    ),
    Festival(
        name="Holi",
        name_hi="\u0939\u094b\u0932\u0940",
        date=date(2026, 3, 10),
        description="Festival of colors \u2014 celebrated with gujiya, thandai, and colors.",
        description_hi="\u0939\u094b\u0932\u0940 \u2014 \u0917\u0941\u091d\u093f\u092f\u093e, \u0920\u0902\u0921\u093e\u0908 \u0914\u0930 \u0930\u0930\u0917\u094b\u0902 \u0915\u093e \u0924\u094d\u092f\u094b\u0939\u093e\u0930\u0964",
        relevant_categories=["grocery", "sweets", "snacks", "beverages"],
        suggestion_hi="\u0939\u094b\u0932\u0940 \u0906 \u0930\u0939\u0940 \u0939\u0948! \u0917\u0941\u091d\u093f\u092f\u093e \u092f\u093e \u0920\u0902\u0921\u093e\u0908 \u0915\u093e \u0938\u093e\u092e\u093e\u0928 \u091a\u093e\u0939\u093f\u090f?",
        suggestion_en="Holi is coming! Need ingredients for gujiya or thandai?",
    ),
    Festival(
        name="Raksha Bandhan",
        name_hi="\u0930\u0915\u094d\u0937\u093e\u092c\u0902\u0927\u0928",
        date=date(2026, 8, 13),
        description="Brother-sister festival \u2014 rakhi, sweets, and gifts exchanged.",
        description_hi="\u092d\u093e\u0908-\u092c\u0939\u0928 \u0915\u093e \u0924\u094d\u092f\u094b\u0939\u093e\u0930 \u2014 \u0930\u093e\u0916\u0940, \u092e\u093f\u0920\u093e\u0908 \u0914\u0930 \u0909\u092a\u0939\u093e\u0930\u0964",
        relevant_categories=["gifts", "sweets", "grocery"],
        suggestion_hi="\u0930\u0915\u094d\u0937\u093e\u092c\u0902\u0927\u0928 \u0906 \u0930\u0939\u093e \u0939\u0948! \u0930\u093e\u0916\u0940 \u092f\u093e \u092e\u093f\u0920\u093e\u0908 \u09d1\u0930\u094d\u0921\u0930 \u0915\u0930\u0928\u093e \u091a\u093e\u0939\u0947\u0902\u0917\u0947?",
        suggestion_en="Raksha Bandhan is coming! Want to order rakhis or sweets?",
    ),
    Festival(
        name="Navratri",
        name_hi="\u0928\u0935\u0930\u093e\u0924\u094d\u0930\u093f",
        date=date(2026, 10, 8),
        description="Nine nights of devotion \u2014 fasting foods, fruits, and puja items needed.",
        description_hi="\u0928\u094c \u0930\u093e\u0924 \u0915\u0940 \u092d\u0915\u094d\u0924\u093f \u2014 \u0935\u094d\u0930\u0924 \u0915\u093e \u0916\u093e\u0928\u093e, \u092b\u0932, \u0914\u0930 \u092a\u0942\u091c\u093e \u0915\u093e \u0938\u093e\u092e\u093e\u0928\u0964",
        relevant_categories=["grocery", "fruits", "puja_items"],
        suggestion_hi="\u0928\u0935\u0930\u093e\u0924\u094d\u0930\u093f \u0906 \u0930\u0939\u0940 \u0939\u0948! \u0935\u094d\u0930\u0924 \u0915\u093e \u0916\u093e\u0928\u093e \u092f\u093e \u092a\u0942\u091c\u093e \u0915\u093e \u0938\u093e\u092e\u093e\u0928 \u091a\u093e\u0939\u093f\u090f?",
        suggestion_en="Navratri is coming! Need fasting foods or puja items?",
    ),
    Festival(
        name="Karva Chauth",
        name_hi="\u0915\u0930\u0935\u093e \u091a\u094c\u0925",
        date=date(2026, 10, 19),
        description="Wives fast for husbands' longevity \u2014 need puja items and sargi ingredients.",
        description_hi="\u092a\u0924\u094d\u0928\u093f\u092f\u093e\u0901 \u092a\u0924\u093f \u0915\u0940 \u0932\u0902\u092c\u0940 \u0909\u092e\u094d\u0930 \u0915\u0947 \u0932\u093f\u090f \u0935\u094d\u0930\u0924 \u0930\u0916\u0924\u0940 \u0939\u0948\u0902 \u2014 \u092a\u0942\u091c\u093e \u0938\u093e\u092e\u093e\u0928 \u0914\u0930 \u0938\u0930\u0917\u0940\u0964",
        relevant_categories=["grocery", "sweets", "puja_items"],
        suggestion_hi="\u0915\u0930\u0935\u093e \u091a\u094c\u0925 \u0906 \u0930\u0939\u093e \u0939\u0948! \u0938\u0930\u0917\u0940 \u092f\u093e \u092a\u0942\u091c\u093e \u0915\u093e \u0938\u093e\u092e\u093e\u0928 \u091a\u093e\u0939\u093f\u090f?",
        suggestion_en="Karva Chauth is coming! Need sargi ingredients or puja items?",
    ),
]


def days_until_festival(festival: Festival, today: date | None = None) -> int:
    """Calculate days until a festival. Negative = already passed."""
    today = today or date.today()
    return (festival.date - today).days


def get_upcoming_festivals(
    within_days: int = 30,
    today: date | None = None,
) -> list[tuple[Festival, int]]:
    """Get festivals occurring within the next N days.

    Returns list of (Festival, days_until) sorted by proximity.
    """
    today = today or date.today()
    upcoming = []
    for f in _FESTIVALS:
        days = days_until_festival(f, today)
        if 0 <= days <= within_days:
            upcoming.append((f, days))
    upcoming.sort(key=lambda x: x[1])
    return upcoming


def get_nearest_festival(today: date | None = None) -> tuple[Festival, int] | None:
    """Get the nearest upcoming festival, or None if none within 30 days."""
    upcoming = get_upcoming_festivals(within_days=30, today=today)
    return upcoming[0] if upcoming else None


def build_occasion_hint(today: date | None = None) -> str | None:
    """Build a proactive occasion hint string for the brain prompt.

    Returns None if no festival is within 14 days.
    """
    nearest = get_nearest_festival(today)
    if nearest is None:
        return None
    festival, days = nearest
    if days > 14:
        return None

    if days == 0:
        return f"Today is {festival.name} ({festival.name_hi})! {festival.suggestion_en}"
    if days == 1:
        return f"Tomorrow is {festival.name} ({festival.name_hi})! {festival.suggestion_en}"
    return f"{festival.name} ({festival.name_hi}) is in {days} days. {festival.suggestion_en}"
