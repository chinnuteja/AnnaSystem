"""Family Resolver — phone → family / member / role lookup with Redis cache.

Given a phone number (E.164), resolves:
  - which family they belong to
  - their User record (role, display_name, preferred_language, etc.)
  - the family's default payer User record
  - the family's approval threshold

Results are cached in Redis for 10 min to avoid repeated DB hits.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.db import get_session
from packages.core.models import Family, FamilyPayer, User
from packages.core.phone_utils import whatsapp_db_lookup_variants

logger = logging.getLogger("foodleaf.family_resolver")

_CACHE_TTL = 10 * 60  # 10 minutes
_CACHE_PREFIX = "fam_resolve:"


@dataclass
class FamilyContext:
    """Resolved family context for a given phone number."""

    user: User
    family: Family
    payer: User | None = None
    payer_auto_approve_threshold: int = 0

    # Convenience properties ---------------------------------------------------

    @property
    def user_id(self) -> str:
        return str(self.user.id)

    @property
    def family_id(self) -> str:
        return str(self.family.id)

    @property
    def role(self) -> str:
        return self.user.role

    @property
    def is_payer(self) -> bool:
        return self.role in ("payer", "both")

    @property
    def is_ordering_user(self) -> bool:
        return self.role in ("ordering_user", "both")

    @property
    def payer_phone(self) -> str | None:
        return self.payer.whatsapp_phone_e164 or self.payer.phone_e164 if self.payer else None

    @property
    def payer_display_name(self) -> str | None:
        return self.payer.display_name if self.payer else None

    @property
    def approval_threshold(self) -> int:
        return self.family.approval_threshold_inr

    @property
    def primary_locale(self) -> str:
        return self.family.primary_locale

    @property
    def city(self) -> str:
        return self.family.city

    def to_cache_dict(self) -> dict:
        """Serialize for Redis storage."""
        return {
            "user_id": str(self.user.id),
            "family_id": str(self.family.id),
            "role": self.role,
            "display_name": self.user.display_name,
            "preferred_language": self.user.preferred_language,
            "family_display_name": self.family.display_name,
            "primary_locale": self.family.primary_locale,
            "city": self.family.city,
            "approval_threshold_inr": self.family.approval_threshold_inr,
            "payer_id": str(self.payer.id) if self.payer else None,
            "payer_display_name": self.payer_display_name,
            "payer_phone": self.payer_phone,
            "payer_auto_approve_threshold": self.payer_auto_approve_threshold,
        }


def _cache_key(phone: str) -> str:
    return f"{_CACHE_PREFIX}{phone}"


async def resolve_family_context(
    phone_e164: str,
    redis: Redis,
    *,
    skip_cache: bool = False,
) -> FamilyContext | None:
    """Resolve phone → FamilyContext, using Redis cache when available.

    Returns None if the phone is not registered.
    """
    if not skip_cache:
        cached = await redis.get(_cache_key(phone_e164))
        if cached:
            try:
                data = json.loads(cached)
                return _rehydrate_from_cache(data, phone_e164)
            except Exception:
                logger.warning("Failed to parse cached family context for %s", phone_e164)

    # DB lookup
    variants = whatsapp_db_lookup_variants(phone_e164)
    if not variants:
        return None

    async with get_session() as session:
        user = await _lookup_user_by_phone(session, variants)
        if user is None:
            return None

        family = await session.get(Family, user.family_id)
        if family is None:
            return None

        payer, payer_threshold = await _resolve_payer(session, family, user)

    ctx = FamilyContext(user=user, family=family, payer=payer, payer_auto_approve_threshold=payer_threshold)

    # Cache the result
    try:
        await redis.set(_cache_key(phone_e164), json.dumps(ctx.to_cache_dict()), ex=_CACHE_TTL)
    except Exception:
        logger.warning("Failed to cache family context for %s", phone_e164)

    return ctx


async def invalidate_cache(phone_e164: str, redis: Redis) -> None:
    """Remove cached family context for a phone number."""
    await redis.delete(_cache_key(phone_e164))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _lookup_user_by_phone(session: AsyncSession, variants: list[str]) -> User | None:
    result = await session.execute(select(User).where(User.whatsapp_phone_e164.in_(variants)))
    return result.scalars().first()


async def _resolve_payer(
    session: AsyncSession,
    family: Family,
    ordering_user: User,
) -> tuple[User | None, int]:
    """Resolve the default payer for a family.

    Priority:
      1. FamilyPayer with is_default_payer=True
      2. Family.default_payer_user_id
      3. Any other payer in the family
    """
    # Check FamilyPayer records
    result = await session.execute(
        select(FamilyPayer)
        .where(FamilyPayer.family_id == family.id, FamilyPayer.active == True)  # noqa: E712
        .order_by(FamilyPayer.is_default_payer.desc())
    )
    payer_records = result.scalars().all()

    if payer_records:
        best = payer_records[0]
        payer_user = await session.get(User, best.user_id)
        return payer_user, best.auto_approve_threshold_inr

    # Fallback: family.default_payer_user_id
    if family.default_payer_user_id:
        payer_user = await session.get(User, family.default_payer_user_id)
        return payer_user, 0

    # Last resort: any payer-role user in the family
    result = await session.execute(
        select(User).where(
            User.family_id == family.id,
            User.role.in_(["payer", "both"]),
            User.active == True,  # noqa: E712
        )
    )
    payer_user = result.scalars().first()
    return payer_user, 0


def _rehydrate_from_cache(data: dict, phone_e164: str) -> FamilyContext:
    """Rehydrate a FamilyContext from cached dict.

    Note: The User and Family objects are minimal stubs with only the fields
    we cached. This is sufficient for pipeline routing decisions. If full
    ORM objects are needed, the caller should do a fresh DB lookup.
    """
    user = User(
        id=data["user_id"],
        phone_e164=phone_e164,
        display_name=data["display_name"],
        preferred_language=data["preferred_language"],
        role=data["role"],
        family_id=data["family_id"],
    )
    family = Family(
        id=data["family_id"],
        display_name=data["family_display_name"],
        primary_locale=data["primary_locale"],
        city=data["city"],
        approval_threshold_inr=data["approval_threshold_inr"],
    )
    payer = None
    if data.get("payer_id"):
        payer = User(
            id=data["payer_id"],
            display_name=data.get("payer_display_name", "Payer"),
            phone_e164=data.get("payer_phone", ""),
            whatsapp_phone_e164=data.get("payer_phone"),
        )
    return FamilyContext(
        user=user,
        family=family,
        payer=payer,
        payer_auto_approve_threshold=data.get("payer_auto_approve_threshold", 0),
    )
