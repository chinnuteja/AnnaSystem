"""Normalize WhatsApp / E.164 phone strings for DB lookup and Graph API sends."""

from __future__ import annotations


def digits_only(phone: str | None) -> str:
    if not phone:
        return ""
    return "".join(c for c in phone if c.isdigit())


def whatsapp_db_lookup_variants(phone: str | None) -> list[str]:
    """Values to match against `users.whatsapp_phone_e164` (Meta often omits '+')."""
    raw = (phone or "").strip()
    if not raw:
        return []
    d = digits_only(raw)
    variants: set[str] = {raw}
    if d:
        variants.add(d)
        variants.add(f"+{d}")
    return list(variants)


def graph_api_recipient(to_phone: str | None) -> str | None:
    """Recipient for Cloud API `to` field: digits only, no spaces/plus. None if unusable."""
    d = digits_only(to_phone)
    if len(d) < 10:
        return None
    return d
