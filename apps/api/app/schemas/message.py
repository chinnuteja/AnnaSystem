from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TextMessageRequest(BaseModel):
    text: str = Field(..., min_length=1)
    family_id: str = "demo-family"
    ordering_user_id: str = "demo-user"
    language: str = "te-IN"
    city: str = "Hyderabad"
    pincode: str = "500032"
    latitude: float = 17.4486
    longitude: float = 78.3792


class LocationPayload(BaseModel):
    latitude: float
    longitude: float
    city: str = "Hyderabad"
    pincode: str = "500032"
    address_line: str | None = None
    landmark: str | None = None


class MvpMessageRequest(BaseModel):
    """WhatsApp-like local test payload for the mock-data MVP."""

    text: str | None = None
    audio_transcript: str | None = None
    input_mode: Literal["text", "voice"] = "text"
    location: LocationPayload | None = None
    language: str = "te-IN"


class ParsedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    quantity: int | None = None
    unit: str | None = None
    brand_hint: str | None = None


class DiscoveryContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occasion: str | None = None
    budget_hint: str | None = None
    dietary_preference: str | None = None
    cuisine_hint: str | None = None
    urgency: str | None = None


IntentGoal = Literal["shop", "discover", "track", "confirm", "cancel", "chat", "unknown"]
IntentDomainHint = Literal["grocery", "food_delivery", "dineout", "any", "unknown"]


class ParsedIntentCore(BaseModel):
    """LLM structured-parse shape (Azure requires strict JSON schema; no open dict fields)."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["ORDER", "DISCOVER", "CANCEL", "CONFIRM", "TRACK", "AMEND", "CHITCHAT", "UNCLEAR"]
    input_mode: Literal["text", "voice"] = "text"
    query_type: Literal["specific_items", "open_discovery"] = "specific_items"
    discovery_context: DiscoveryContext | None = None
    items: list[ParsedItem] = Field(default_factory=list)
    language_detected: str = "te-IN"
    confidence: float = 0.8
    raw_text: str
    goal: IntentGoal = "unknown"
    domain_hint: IntentDomainHint = "unknown"
    needs_clarification: bool = False
    clarification_question: str | None = None


class ParsedIntent(ParsedIntentCore):
    """Full intent including server-side router trace (not sent to the LLM parse schema)."""

    router_trace: dict[str, Any] | None = None


# Alias for OpenAI `response_format=` — same schema as ParsedIntentCore.
ParsedIntentForLLM = ParsedIntentCore


class DiscoveryOption(BaseModel):
    option_id: str
    rank: int
    source: Literal["instamart", "food", "dineout"]
    title: str
    subtitle: str
    provider_id: str
    estimated_total_inr: int
    eta_min: int | None = None
    eta_max: int | None = None
    rating: float | None = None
    offer_text: str | None = None
    reasoning: list[str]
    action_payload: dict = Field(default_factory=dict)


class DiscoveryResult(BaseModel):
    query: str
    options: list[DiscoveryOption]
    offset: int = 0
    has_more: bool = False


class CandidateItem(BaseModel):
    canonical_key: str
    display_name: str
    brand: str
    price_inr: int
    provider_specific_id: str
    in_stock: bool


class QuoteSummary(BaseModel):
    subtotal_inr: int
    delivery_fee_inr: int
    handling_fee_inr: int
    taxes_inr: int
    discount_inr: int
    total_inr: int
    estimated_delivery_min: int
    estimated_delivery_max: int
    applied_offers: list[str]


class TextMessageResponse(BaseModel):
    parsed_intent: ParsedIntent
    candidates: list[CandidateItem]
    quote: QuoteSummary | None
    confirmation_text: str
    discovery_result: DiscoveryResult | None = None


class MvpMessageResponse(BaseModel):
    parsed_intent: ParsedIntent
    reply_text: str
    candidates: list[CandidateItem] = Field(default_factory=list)
    quote: QuoteSummary | None = None
    discovery_result: DiscoveryResult | None = None
    needs_location: bool = False
