"""SQLAlchemy ORM models — every table from 01_ARCHITECTURE.md.

Tables:
    families, users, family_payers, payment_requests, voice_sessions,
    canonical_skus, provider_sku_mappings, orders, care_signals,
    vocabulary_terms, brand_partnerships, acknowledgement_variants
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow():
    return datetime.now(timezone.utc)


def _new_uuid():
    return uuid.uuid4()


# ============================================================================
# Base
# ============================================================================

class Base(DeclarativeBase):
    """Shared declarative base for all foodleaf models."""
    pass


# ============================================================================
# Family
# ============================================================================

class Family(Base):
    __tablename__ = "families"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    default_payer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", use_alter=True), nullable=True
    )
    primary_locale: Mapped[str] = mapped_column(String(10), default="te-IN")
    city: Mapped[str] = mapped_column(String(100), default="Hyderabad")
    approval_threshold_inr: Mapped[int] = mapped_column(Integer, default=1500)
    care_features_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # Relationships
    members = relationship("User", back_populates="family", foreign_keys="User.family_id")
    payers = relationship("FamilyPayer", back_populates="family")


# ============================================================================
# Users
# ============================================================================

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("families.id"), nullable=False)
    role: Mapped[str] = mapped_column(
        Enum("ordering_user", "payer", "both", name="user_role_enum", create_constraint=True),
        default="ordering_user",
    )
    relationship_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    phone_e164: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    whatsapp_phone_e164: Mapped[str | None] = mapped_column(String(20), nullable=True)
    preferred_language: Mapped[str] = mapped_column(String(10), default="te-IN")
    voice_print_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    dietary_constraints: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    brand_preferences: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # Relationships
    family = relationship("Family", back_populates="members", foreign_keys=[family_id])


# ============================================================================
# Family Payer Configuration
# ============================================================================

class FamilyPayer(Base):
    __tablename__ = "family_payers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("families.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    upi_handle: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default_payer: Mapped[bool] = mapped_column(Boolean, default=False)
    category_routing: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    auto_approve_threshold_inr: Mapped[int] = mapped_column(Integer, default=0)
    trust_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # Relationships
    family = relationship("Family", back_populates="payers")
    user = relationship("User")


# ============================================================================
# Payment Requests
# ============================================================================

class PaymentRequest(Base):
    __tablename__ = "payment_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("families.id"), nullable=False)
    ordering_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    payer_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    related_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", use_alter=True), nullable=True
    )
    related_voice_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("voice_sessions.id", use_alter=True), nullable=True
    )
    amount_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    upi_handle_charged: Mapped[str | None] = mapped_column(Text, nullable=True)
    razorpay_request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Enum(
            "initiated", "sent_to_payer", "approved", "rejected",
            "expired", "paid", "failed",
            name="payment_status_enum", create_constraint=True,
        ),
        default="initiated",
    )
    auto_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    initiated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payer_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        # Prevent duplicate payment requests for the same voice session
        UniqueConstraint("related_voice_session_id", name="uq_payment_voice_session"),
    )


# ============================================================================
# Voice / Interaction Sessions
# ============================================================================

class VoiceSession(Base):
    __tablename__ = "voice_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("families.id"), nullable=False)
    ordering_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    whatsapp_message_id: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    input_mode: Mapped[str] = mapped_column(
        Enum("text", "voice", name="input_mode_enum", create_constraint=True),
        default="text",
    )
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_r2_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_duration_sec: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    transcription_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    language_detected: Mapped[str | None] = mapped_column(String(10), nullable=True)
    transcription_confidence: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    parsed_intent: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    resolved_cart: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    conversation_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    pipeline_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str | None] = mapped_column(
        Enum(
            "order_placed", "cancelled", "amended", "failed",
            "still_pending", "clarification_requested", "audio_unavailable",
            "payment_rejected",
            name="session_outcome_enum", create_constraint=True,
        ),
        nullable=True,
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ack_message_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


# ============================================================================
# SKU Catalog
# ============================================================================

class CanonicalSKU(Base):
    __tablename__ = "canonical_skus"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    canonical_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name_en: Mapped[str] = mapped_column(Text, nullable=False)
    display_names_local: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    subcategory: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand: Mapped[str | None] = mapped_column(Text, nullable=True)
    pack_size: Mapped[str | None] = mapped_column(Text, nullable=True)
    typical_price_band_min_inr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    typical_price_band_max_inr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # pgvector column — 1024 dimensions for future flexibility
    embedding = mapped_column(Vector(1024), nullable=True)
    brand_partnership_weight: Mapped[float | None] = mapped_column(Numeric, default=0.0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # Relationships
    provider_mappings = relationship("ProviderSKUMapping", back_populates="canonical_sku")

    __table_args__ = (
        Index("ix_canonical_skus_category", "category"),
        Index("ix_canonical_skus_brand", "brand"),
    )


class ProviderSKUMapping(Base):
    __tablename__ = "provider_sku_mappings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    canonical_sku_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("canonical_skus.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(
        Enum(
            "swiggy_instamart_mcp", "swiggy_food_mcp", "swiggy_dineout_mcp",
            "ondc", "manual_ops",
            name="provider_enum", create_constraint=True,
        ),
        nullable=False,
    )
    provider_sku_id: Mapped[str] = mapped_column(Text, nullable=False)
    provider_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    last_price_inr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    canonical_sku = relationship("CanonicalSKU", back_populates="provider_mappings")


# ============================================================================
# Orders
# ============================================================================

class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("families.id"), nullable=False)
    ordering_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    voice_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("voice_sessions.id"), nullable=True
    )
    payment_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_requests.id"), nullable=True
    )
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    cart_items: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    total_inr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        Enum(
            "pending_payment", "pending", "confirmed", "preparing",
            "out_for_delivery", "delivered", "cancelled", "failed",
            name="order_status_enum", create_constraint=True,
        ),
        default="pending_payment",
    )
    required_explicit_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    delivery_address_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


# ============================================================================
# Care Signals
# ============================================================================

class CareSignal(Base):
    __tablename__ = "care_signals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("families.id"), nullable=False)
    affected_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    signal_type: Mapped[str] = mapped_column(
        Enum(
            "silence", "duplicate_order", "cognitive_pattern",
            "upi_rejection_pattern", "payer_balance_low",
            "delivery_failed", "unusual_value", "unusual_hour",
            "provider_outage",
            name="signal_type_enum", create_constraint=True,
        ),
        nullable=False,
    )
    severity: Mapped[str] = mapped_column(
        Enum("info", "warn", "urgent", name="severity_enum", create_constraint=True),
        default="info",
    )
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sent_to_family_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ============================================================================
# Vocabulary Map
# ============================================================================

class VocabularyTerm(Base):
    __tablename__ = "vocabulary_terms"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    term: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="te-IN")
    region: Mapped[str | None] = mapped_column(Text, nullable=True)
    maps_to_category: Mapped[str] = mapped_column(Text, nullable=False)
    maps_to_brand: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_pack_size: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Numeric, default=1.0)

    __table_args__ = (
        Index("ix_vocab_term_lang", "term", "language"),
    )


# ============================================================================
# Brand Partnerships
# ============================================================================

class BrandPartnership(Base):
    __tablename__ = "brand_partnerships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    brand_name: Mapped[str] = mapped_column(Text, nullable=False)
    contract_status: Mapped[str] = mapped_column(
        Enum("active", "paused", "expired", name="contract_status_enum", create_constraint=True),
        default="active",
    )
    weight_multiplier: Mapped[float] = mapped_column(Numeric, default=1.0)
    per_order_payout_inr: Mapped[int] = mapped_column(Integer, default=0)
    category_scope: Mapped[list | None] = mapped_column(ARRAY(Text), nullable=True)
    region_scope: Mapped[list | None] = mapped_column(ARRAY(Text), nullable=True)
    contract_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    contract_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_orders_attributed: Mapped[int] = mapped_column(Integer, default=0)
    total_payout_inr: Mapped[int] = mapped_column(Integer, default=0)


# ============================================================================
# Acknowledgement Variants (for the "Sare Amma, chustunnanu..." clips)
# ============================================================================

class AcknowledgementVariant(Base):
    __tablename__ = "acknowledgement_variants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    variant_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    context_tag: Mapped[str] = mapped_column(Text, nullable=False)  # "generic", "long_order", "late_hour", "repeat"
    audio_r2_key: Mapped[str] = mapped_column(Text, nullable=False)
    transcript_telugu: Mapped[str] = mapped_column(Text, nullable=False)
    transcript_english: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
