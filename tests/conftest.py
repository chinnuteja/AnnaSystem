"""Shared pytest fixtures."""

from __future__ import annotations

import asyncio
import pytest


@pytest.fixture(autouse=True)
def disable_mock_provider_random_failures(monkeypatch):
    """Deterministic tests: mock adapters inject ~3% failures by default."""
    from packages.providers.adapters import mock_swiggy_adapter as m

    monkeypatch.setattr(m, "_maybe_fail", lambda *a, **k: None)


def make_mock_family_ctx(
    user_id="bbbbbbbb-0002-0002-0002-000000000002",
    family_id="aaaaaaaa-0001-0001-0001-000000000001",
    role="ordering_user",
    display_name="Test User",
    preferred_language="te-IN",
    payer_id=None,
    payer_name=None,
    payer_phone=None,
    approval_threshold=1500,
    primary_locale="te-IN",
    city="Hyderabad",
):
    """Create a mock FamilyContext for pipeline tests.

    Usage in tests:
        from tests.conftest import make_mock_family_ctx
        fam_ctx = make_mock_family_ctx(...)
        monkeypatch.setattr(pipeline_mod, "resolve_family_context",
                            lambda *a, **k: asyncio.sleep(0, result=fam_ctx))
    """
    from packages.core.family_resolver import FamilyContext
    from packages.core.models import Family, User

    user = User(
        id=user_id, family_id=family_id, role=role,
        display_name=display_name, phone_e164="+919999999999",
        whatsapp_phone_e164="+919999999999", preferred_language=preferred_language,
    )
    family = Family(
        id=family_id, display_name="Test Family",
        default_payer_user_id=payer_id, primary_locale=primary_locale,
        city=city, approval_threshold_inr=approval_threshold,
    )
    payer = None
    if payer_id:
        payer = User(
            id=payer_id, family_id=family_id, role="payer",
            display_name=payer_name or "Payer", phone_e164=payer_phone or "+919999999998",
            whatsapp_phone_e164=payer_phone or "+919999999998", preferred_language="en-IN",
        )
    return FamilyContext(user=user, family=family, payer=payer)


def mock_pipeline_user_lookup(monkeypatch, fam_ctx=None, **kwargs):
    """Monkey-patch resolve_family_context and build_occasion_hint for pipeline tests.

    Call this in any test that exercises process_text_order.
    """
    from packages.core import pipeline as pipeline_mod

    if fam_ctx is None:
        fam_ctx = make_mock_family_ctx(**kwargs)
    monkeypatch.setattr(pipeline_mod, "resolve_family_context",
                        lambda *a, **k: asyncio.sleep(0, result=fam_ctx))
    monkeypatch.setattr(pipeline_mod, "build_occasion_hint", lambda: None)
    # Avoid DB calls during rehydration check
    monkeypatch.setattr(pipeline_mod, "_rehydrate_recent_pending_session",
                        lambda *a, **k: asyncio.sleep(0, result=None))
    return fam_ctx
