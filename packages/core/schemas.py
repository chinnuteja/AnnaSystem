"""Pydantic schemas for all core models — used for API serialization and validation.

Each DB model has corresponding Create, Read, and (where needed) Update schemas.
These are the Pydantic equivalents required by the F2 acceptance criteria.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ============================================================================
# Family
# ============================================================================

class FamilyCreate(BaseModel):
    display_name: str
    primary_locale: str = "te-IN"
    city: str = "Hyderabad"
    approval_threshold_inr: int = 1500
    care_features_enabled: bool = False


class FamilyRead(BaseModel):
    id: str
    display_name: str
    default_payer_user_id: str | None = None
    primary_locale: str
    city: str
    approval_threshold_inr: int
    care_features_enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FamilyUpdate(BaseModel):
    display_name: str | None = None
    primary_locale: str | None = None
    city: str | None = None
    approval_threshold_inr: int | None = None
    care_features_enabled: bool | None = None


# ============================================================================
# User
# ============================================================================

class UserCreate(BaseModel):
    family_id: str
    role: str = "ordering_user"  # ordering_user | payer | both
    relationship_label: str | None = None
    display_name: str
    phone_e164: str
    whatsapp_phone_e164: str | None = None
    preferred_language: str = "te-IN"
    dietary_constraints: dict | None = None
    brand_preferences: dict | None = None


class UserRead(BaseModel):
    id: str
    family_id: str
    role: str
    relationship_label: str | None = None
    display_name: str
    phone_e164: str
    whatsapp_phone_e164: str | None = None
    preferred_language: str
    voice_print_id: str | None = None
    dietary_constraints: dict | None = None
    brand_preferences: dict | None = None
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    display_name: str | None = None
    preferred_language: str | None = None
    dietary_constraints: dict | None = None
    brand_preferences: dict | None = None


# ============================================================================
# Family Payer
# ============================================================================

class FamilyPayerCreate(BaseModel):
    family_id: str
    user_id: str
    upi_handle: str | None = None
    is_default_payer: bool = False
    category_routing: dict | None = None  # {"groceries": user_id, "food": user_id}
    auto_approve_threshold_inr: int = 0


class FamilyPayerRead(BaseModel):
    id: str
    family_id: str
    user_id: str
    upi_handle: str | None = None
    is_default_payer: bool
    category_routing: dict | None = None
    auto_approve_threshold_inr: int
    trust_started_at: datetime | None = None
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FamilyPayerUpdate(BaseModel):
    upi_handle: str | None = None
    is_default_payer: bool | None = None
    category_routing: dict | None = None
    auto_approve_threshold_inr: int | None = None


# ============================================================================
# Payment Request
# ============================================================================

class PaymentRequestCreate(BaseModel):
    family_id: str
    ordering_user_id: str
    payer_user_id: str
    amount_inr: int
    upi_handle_charged: str | None = None
    related_voice_session_id: str | None = None


class PaymentRequestRead(BaseModel):
    id: str
    family_id: str
    ordering_user_id: str
    payer_user_id: str
    related_order_id: str | None = None
    related_voice_session_id: str | None = None
    amount_inr: int
    upi_handle_charged: str | None = None
    razorpay_request_id: str | None = None
    status: str  # initiated | sent_to_payer | approved | rejected | expired | paid | failed
    auto_approved: bool
    initiated_at: datetime | None = None
    payer_responded_at: datetime | None = None
    paid_at: datetime | None = None
    expired_at: datetime | None = None
    failure_reason: str | None = None
    ts: datetime

    model_config = {"from_attributes": True}


# ============================================================================
# Voice Session
# ============================================================================

class VoiceSessionCreate(BaseModel):
    family_id: str
    ordering_user_id: str
    whatsapp_message_id: str | None = None
    input_mode: str = "text"  # text | voice
    raw_text: str | None = None
    audio_r2_key: str | None = None


class VoiceSessionRead(BaseModel):
    id: str
    family_id: str
    ordering_user_id: str
    whatsapp_message_id: str | None = None
    input_mode: str
    raw_text: str | None = None
    audio_r2_key: str | None = None
    audio_duration_sec: float | None = None
    transcription_raw: str | None = None
    normalized_text: str | None = None
    language_detected: str | None = None
    transcription_confidence: float | None = None
    parsed_intent: dict | None = None
    resolved_cart: dict | None = None
    conversation_state: str | None = None
    pipeline_latency_ms: int | None = None
    outcome: str | None = None
    failure_reason: str | None = None
    ack_message_sent: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ============================================================================
# Canonical SKU
# ============================================================================

class CanonicalSKUCreate(BaseModel):
    canonical_key: str
    display_name_en: str
    display_names_local: dict | None = None  # {"te-IN": ["godi pindi", "atta"]}
    category: str
    subcategory: str | None = None
    brand: str | None = None
    pack_size: str | None = None
    typical_price_band_min_inr: int | None = None
    typical_price_band_max_inr: int | None = None
    brand_partnership_weight: float = 0.0


class CanonicalSKURead(BaseModel):
    id: str
    canonical_key: str
    display_name_en: str
    display_names_local: dict | None = None
    category: str
    subcategory: str | None = None
    brand: str | None = None
    pack_size: str | None = None
    typical_price_band_min_inr: int | None = None
    typical_price_band_max_inr: int | None = None
    brand_partnership_weight: float | None = None
    active: bool
    last_seen_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ============================================================================
# Provider SKU Mapping
# ============================================================================

class ProviderSKUMappingCreate(BaseModel):
    canonical_sku_id: str
    provider: str  # swiggy_instamart_mcp | swiggy_food_mcp | swiggy_dineout_mcp | ondc | manual_ops
    provider_sku_id: str
    provider_metadata: dict | None = None
    city: str | None = None
    available: bool = True
    last_price_inr: int | None = None


class ProviderSKUMappingRead(BaseModel):
    id: str
    canonical_sku_id: str
    provider: str
    provider_sku_id: str
    provider_metadata: dict | None = None
    city: str | None = None
    available: bool
    last_price_inr: int | None = None
    last_seen_at: datetime | None = None

    model_config = {"from_attributes": True}


# ============================================================================
# Order
# ============================================================================

class OrderCreate(BaseModel):
    family_id: str
    ordering_user_id: str
    voice_session_id: str | None = None
    payment_request_id: str | None = None
    provider: str | None = None
    cart_items: dict | None = None
    total_inr: int | None = None
    delivery_address_snapshot: dict | None = None


class OrderRead(BaseModel):
    id: str
    family_id: str
    ordering_user_id: str
    voice_session_id: str | None = None
    payment_request_id: str | None = None
    provider: str | None = None
    provider_order_id: str | None = None
    cart_items: dict | None = None
    total_inr: int | None = None
    status: str
    required_explicit_approval: bool
    delivery_address_snapshot: dict | None = None
    placed_at: datetime | None = None
    delivered_at: datetime | None = None
    failure_reason: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ============================================================================
# Care Signal
# ============================================================================

class CareSignalCreate(BaseModel):
    family_id: str
    affected_user_id: str
    signal_type: str  # silence | duplicate_order | cognitive_pattern | ...
    severity: str = "info"  # info | warn | urgent
    payload: dict | None = None


class CareSignalRead(BaseModel):
    id: str
    family_id: str
    affected_user_id: str
    signal_type: str
    severity: str
    payload: dict | None = None
    sent_to_family_at: datetime | None = None
    acknowledged_at: datetime | None = None
    ts: datetime

    model_config = {"from_attributes": True}


# ============================================================================
# Vocabulary Term
# ============================================================================

class VocabularyTermCreate(BaseModel):
    term: str
    language: str = "te-IN"
    region: str | None = None
    maps_to_category: str
    maps_to_brand: str | None = None
    default_pack_size: str | None = None
    notes: str | None = None
    confidence: float = 1.0


class VocabularyTermRead(BaseModel):
    id: str
    term: str
    language: str
    region: str | None = None
    maps_to_category: str
    maps_to_brand: str | None = None
    default_pack_size: str | None = None
    notes: str | None = None
    confidence: float

    model_config = {"from_attributes": True}


# ============================================================================
# Brand Partnership
# ============================================================================

class BrandPartnershipCreate(BaseModel):
    brand_name: str
    contract_status: str = "active"  # active | paused | expired
    weight_multiplier: float = 1.0
    per_order_payout_inr: int = 0
    category_scope: list[str] | None = None
    region_scope: list[str] | None = None
    contract_start: datetime | None = None
    contract_end: datetime | None = None


class BrandPartnershipRead(BaseModel):
    id: str
    brand_name: str
    contract_status: str
    weight_multiplier: float
    per_order_payout_inr: int
    category_scope: list[str] | None = None
    region_scope: list[str] | None = None
    contract_start: datetime | None = None
    contract_end: datetime | None = None
    total_orders_attributed: int
    total_payout_inr: int

    model_config = {"from_attributes": True}


# ============================================================================
# Acknowledgement Variant
# ============================================================================

class AcknowledgementVariantCreate(BaseModel):
    variant_key: str
    context_tag: str  # generic | long_order | late_hour | repeat
    audio_r2_key: str
    transcript_telugu: str
    transcript_english: str | None = None


class AcknowledgementVariantRead(BaseModel):
    id: str
    variant_key: str
    context_tag: str
    audio_r2_key: str
    transcript_telugu: str
    transcript_english: str | None = None
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
