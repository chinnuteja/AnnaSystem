# foodleaf — Project Context (Always Load First)

> **Purpose of this file:** This is the master context document. Always pin this in Cursor/Antigravity when working on any part of foodleaf. Other plan files reference concepts defined here.
>
> **Naming note:** Final working brand name is foodleaf. Any older project names in prior notes are stale and should be replaced with foodleaf.

---

## What We Are Building

foodleaf is a WhatsApp-native text-and-voice ordering layer for Indian families. Anyone in the family — typing or speaking naturally in Telugu, Hindi, Tamil, or any Indian language — can place food, grocery, or restaurant orders through one shared WhatsApp number. No app to download. No menus to scroll. Just chat or voice notes.

The product compresses 4-8 minutes of Swiggy/Zomato scrolling into one 8-second voice command, AND solves the broken discovery problem ("find me good dinner with offers nearby") that even tech-savvy 25-year-olds suffer from daily.

It runs entirely inside WhatsApp for end users. A lightweight web PWA at `app.foodleaf.in` lets any family member configure preferences, view family activity, and manage payment routing.

We use Swiggy Builders Club MCP (Food, Instamart, Dineout) as the commerce backbone — but architected behind a provider-adapter interface so we can add Zepto/BigBasket/Zomato later without touching agent logic.

### Critical positioning rule (read this twice)

**foodleaf is NOT marketed as "for elders" or "for grandparents."** Indian families are proud; products that label someone as a special demographic insult them and the family. foodleaf is positioned as "the fastest way to order on Swiggy, by voice, in your language" — for anyone aged 22-75.

Family-care features (silence detection, anomaly alerts, approval flows) exist as **opt-in capabilities**, not the product's core identity. A young couple uses foodleaf without ever turning these on. A family with members in another city quietly enables them. Same product, adaptive depth.

---

## Core Users (One product, three usage modes)

1. **Fast-ordering family member** — 22-45, ordering for self or splitting household ordering. Hates Swiggy/Zomato scroll friction. Wants speed + good discovery. Doesn't think of self as a "foodleaf target user" — just someone who wants ordering to be fast.
2. **Voice-first family member** — Anyone who prefers voice over typing or may speak only their regional language. Uses the same product, same WhatsApp number. No special flows visible to them.
3. **Family payer** — Whoever is configured to receive UPI payment requests for orders. Could be the same person ordering, or a different family member, or rotating per category.

Free tier exists forever. Premium tier monetizes families that want care/oversight features. Brand partnerships subsidize the rest at scale.

**Naming/copy rule:** In all UI, marketing, voice prompts, and internal docs, never use words like "elder," "senior," "grandparent," "parent" as user descriptors. Use "family member," "user," or refer to them by their actual name (Amma, Kiran). The word "parent" can appear in family-relationship config (e.g., "this family member is my parent") but never as a user-tier label.

---

## The Four-Rail Revenue Model (For Long-Term Architecture Decisions)

| Rail | Year 1 | Year 3 | Year 5 |
|---|---|---|---|
| Free tier (cost center) | -₹1.2 cr | -₹8 cr | -₹22 cr |
| Premium subscriptions | ₹2 cr | ₹40 cr | ₹120 cr |
| Brand partnerships | ₹0.5 cr | ₹180 cr | ₹550 cr |
| Family-care marketplace | ₹0 | ₹25 cr | ₹180 cr |
| Swiggy transaction rev-share | ₹0.3 cr | ₹15 cr | ₹60 cr |

**Architectural implication:** Every data model decision must support brand-weighted SKU ranking and per-user spend analytics from day 1, even if those rails monetize only in year 2-3.

---

## The Three Core Capabilities (in priority order)

1. **WhatsApp-native ordering by text or voice, any Indian language** — Type naturally or speak naturally → order placed in seconds. Voice is the demo wow factor; text is the reliability path and should be equally supported.
2. **Smart cross-provider discovery** — "Find me good dinner with offers" actually finds something good across Food, Instamart, Dineout, ONDC/direct-provider data, or manual fallback catalogs, with real reasoning. The retention engine. *This is the differentiator vs. Swiggy native search and any voice ordering competitor.*
3. **Family-shared, dignity-first** — Anyone in the family uses one shared WhatsApp number. Payments via UPI Requests, not wallets. Care features are opt-in, not the product's identity. The defensible moat.

---

## Non-Negotiable Product Principles

These are commandments. Any code or design that violates them is wrong, regardless of technical elegance.

1. **Users never install anything for the core ordering experience.** WhatsApp is the only interface for ordering.
2. **Replies always in the user's language.** Voice + text in their language. No forced English.
3. **User autonomy is sacred.** Other family members can suggest, never override a confirmed order.
4. **Reply in the same mode the user used, with optional dual-mode fallback.** Voice in → voice + text summary. Text in → text first, voice optional if the user prefers it.
5. **No menus, no buttons, no quick-replies in the ordering flow.** Conversational only.
6. **Existing brand preference always wins over partnership weighting.** Document this in every brand partnership contract.
7. **Every order over the threshold gets soft-approval from the family payer.** Default ₹1,500.
8. **Confirmations are conversational, not transactional.** "Sare, 45 nimishala lo vasthundi" — not "Order #45821 placed for ₹420."
9. **Mistakes are owned in the user's voice.** "Sorry, naaku ardham kaledu, malli cheppandi" — never "Error" or technical jargon.
10. **Silence is okay.** No nag pings. Pressure feels rude.
11. **Never label users by age/demographic in any UI, voice prompt, or marketing.** No "for seniors," "for elders," "for grandparents."
12. **Discovery Agent always shows reasoning, not just options.** "Try Sai Punjabi — 30% off this weekend, your favourite cuisine, 8 min away" beats "Top 10 restaurants near you."

---

## Tech Stack (Locked Decisions)

### Chat, Voice & Language
- **STT:** Sarvam Saaras V3 (Indian-language ASR, code-mix native, ₹30/hour)
- **TTS:** Sarvam Bulbul V3 (natural Telugu voices)
- **Reasoning LLM:** Claude Sonnet 4.5 via Anthropic API direct (move to Bedrock when on AWS)
- **Fast classifier LLM:** Claude Haiku 4.5 for intent parsing & confirmation generation
- **Fallback STT/TTS:** Gemini 2.5 Flash if Sarvam confidence <70% or down

### Application Platform (MVP — months 1-4)
- **App hosting:** Railway.app
- **Database:** Neon.tech Postgres (Mumbai region) + pgvector extension
- **Cache + Queue:** Upstash Redis
- **Object storage:** Cloudflare R2 (voice notes, audio files)
- **WhatsApp BSP:** Gupshup (Indian, cheap, supports voice notes)
- **Family payer PWA:** Next.js 14, hosted on Vercel
- **Payments:** Razorpay UPI Collect (UPI Request flow, not wallets) for India; Stripe Connect for NRI flows where Indian UPI not available

### Migration target (months 4-6, with AWS Activate credits)
- AWS ECS Fargate, RDS Postgres (ap-south-1), ElastiCache, S3, Bedrock

### Why these choices
- **Railway over AWS:** MVP velocity. Don't fight VPCs and IAM for 50 test users.
- **Neon over Supabase:** Cleaner India regional support, no DPDP friction.
- **Gupshup over Twilio:** 5x cheaper for Indian numbers, native voice note support.
- **Sarvam over Gemini-only:** Indian-language accuracy on accented regional speech.

---

## Critical Constraints That Shape Every Decision

### Latency Budget — 5 seconds end-to-end
Text input should skip audio download/STT and usually return faster. Voice input follows the full budget below.
| Stage | Budget |
|---|---|
| Audio download from WhatsApp | 0.4s |
| STT (Sarvam Saaras streaming) | 0.8s |
| Intent extraction (Claude Haiku) | 0.6s |
| SKU search (parallelized) | 1.2s |
| Confirmation generation (Claude Haiku) | 0.5s |
| TTS (Sarvam Bulbul) | 0.7s |
| WhatsApp send | 0.4s |
| **Total** | **4.6s** |

**The Acknowledge Hack:** If processing exceeds 1.5s, fire a pre-recorded "Sare Amma, chustunnanu..." voice clip while backend continues. 8-12 variants, rotated. Threshold-gated (skip if backend returns <1.5s).

### WhatsApp Cost Model
- User-initiated sessions = 24-hour free service window
- Replies inside window = free
- Care alerts to configured family payer/caregiver outside window = ₹0.13 per utility template
- Re-engagement marketing templates = ₹0.88 (avoid)
- **Design every flow to keep the ordering user inside the 24-hour service window.**

### Idempotency Mandate
WhatsApp webhooks are at-least-once. Every text or voice message has a Gupshup message ID. Before processing, atomic check in Redis: `SETNX processed:msg:{id} 1 EX 86400`. If false, skip. **No exceptions to this rule.**

### Conversation State Machine
Per family, track conversation state: `IDLE → PARSING → AWAITING_CONFIRMATION → EXECUTING → COMPLETE`. While in `AWAITING_CONFIRMATION`, any new WhatsApp message from the same ordering user is treated as an amendment, not a new request. Cancel in-flight, re-parse with combined context, re-confirm.

---

## The Six-Agent System

Each agent is a specialized worker. Avoid one-monolithic-agent. Each has a clear input/output contract.

1. **Message Parser Agent** — Handles WhatsApp text directly and voice via STT, then performs intent extraction. Input: text body or audio URL. Output: `ParsedIntent` (action, items[], quantities, brand prefs, **query type: ORDER vs DISCOVER**, input mode).
2. **Discovery Agent** *(new)* — Activated when intent is open-ended ("find me good dinner"). Calls available commerce sources through provider adapters (Swiggy MCP if available, ONDC/direct APIs/manual catalog fallback if not), applies offer-awareness + personalization + context (time, weather, location), returns 2-3 reasoned options. This is our differentiator.
3. **SKU Mapper Agent** — Resolve specific items to real SKUs (when intent is ORDER, not DISCOVER). Input: ParsedIntent. Output: `ResolvedCart` (top 1-3 candidates per item, brand-weighted).
4. **Confirmation Agent** — Generate Telugu/regional voice reply. Input: ResolvedCart or DiscoveryResults. Output: Audio URL + transcript.
5. **Executor Agent** — On confirm, transact via commerce provider + trigger UPI Request. Input: ResolvedCart + payment routing. Output: OrderResult.
6. **Care Monitor Agent** — Background anomaly detection (opt-in per family). Runs every 4 hours. No LLM.

---

## The ICommerceProvider Adapter Pattern

Do **NOT** hardcode Swiggy MCP into the Executor Agent. Build a semantic interface and Swiggy MCP is just one adapter. foodleaf must still work if MCP access disappears.

```
ICommerceProvider:
  search_skus(query) → list[CanonicalSKU]
  check_availability(sku_ids, location) → list[AvailabilityResult]
  assemble_cart(items) → CartHandle
  quote_cart(cart) → QuoteResult
  execute_checkout(cart, payment) → OrderResult
  track_order(order_id) → OrderStatus
  cancel_order(order_id) → CancellationResult
```

CanonicalSKU lives in our DB and maps internal IDs → provider IDs across MCP, ONDC, direct APIs, and manual ops:
```
canonical_sku "aashirvaad_select_atta_5kg"
  ↳ providers: { instamart_mcp: "12345", ondc: "seller/sku", manual_ops: "fallback_item_789" }
  ↳ embeddings: [vectors for "atta", "godi pindi", "wheat flour"]
  ↳ category: "staples_flour"
  ↳ price_band: ₹280-₹420
```

**Day 1: build SwiggyAdapter plus a ManualOpsAdapter stub.** The manual adapter can create a human-assisted order task when live provider checkout is unavailable. **Day 30: add ONDCAdapter spike for non-Swiggy or MCP-unavailable coverage.** Never let agent logic depend on a specific provider.

---

## Edge Cases We MUST Handle (Reference List)

These are documented in detail in `02_AGENTS_AND_EDGE_CASES.md`. Quick reference:

1. Audio quality terrible (fan, TV, mumbling)
2. Defunct brand names ("Mother Dairy" for any milk)
3. Missing quantity ("Atta teesuko" without amount)
4. SKU doesn't exist on Instamart
5. Confused over-ordering / duplicates
6. UPI Request rejected, expired, or payer balance low
7. Delivery rider can't find the address
8. Wrong item delivered
9. Multi-member household (Amma + Nanna, siblings, spouses)
10. Repeat-order pattern / potential cognitive concern
11. Cash-on-delivery insistence
12. User shares number with friends (viral signal)
13. Multilingual mid-sentence
14. Festival long-list ordering
15. Family payer override attempts
16. Phone dead/lost/stolen
17. Brand partnership conflict with existing preference
18. Commerce provider down/rate-limited (including Swiggy MCP)
19. User in non-Swiggy city
20. Trust attribution ("kid pays, not me")
21. Webhook duplication (idempotency)
22. Mid-flow correction (interruption support)
23. Acknowledge hack failure masking (12s hard timeout)

Every one of these has a designed response in the agent specs.

---

## What Is NOT in MVP (Explicit Out-of-Scope)

- Non-Telugu languages (Tamil, Hindi, Malayalam come in months 3-6)
- Native iOS/Android apps for family payers/caregivers (PWA only)
- Embedded fintech / lending features
- Brand partnership campaigns (year 2)
- Family-care marketplace integrations (year 2-3)
- Fully automated Dineout MCP integrations (manual usage only in MVP)
- Sophisticated multi-provider optimization (MVP only needs provider fallback routing)
- IVR/landline fallback (year 2)
- Full ONDC automation for non-Swiggy cities (MVP should still have ONDC/manual fallback design and adapter stub)

---

## Locked Configuration Decisions

| Decision | Value |
|---|---|
| Payment model | UPI Request (Razorpay UPI Collect) per order, NOT pre-funded wallets |
| Input modes | WhatsApp text and WhatsApp voice are both first-class; same ParsedIntent contract |
| Provider dependency | No hard dependency on MCP; commerce always goes through `ICommerceProvider` adapters |
| Auto-approve threshold (silent UPI charge) | Configurable per family; default off, recommended ₹500 after trust period |
| Soft-approval threshold (explicit UPI request prompt) | ₹1,500 default, configurable per family |
| Cooldown for duplicate orders | 4 hours |
| Order anomaly trigger | >2x normal cart value OR exact duplicate of yesterday |
| Day-1 language | Telugu only |
| First city | Hyderabad (Gachibowli/Kondapur/Madhapur clusters) |
| Hard timeout for full pipeline | 12 seconds |
| Care silence trigger | No orders in 5 days for users normally ordering twice/week |
| Voice confidence threshold | 70% for direct execution; 50-70% triggers re-confirm; <50% asks "malli cheppandi" |
| Auto-cancel mid-flow | If new WhatsApp message arrives while in `AWAITING_CONFIRMATION` |
| UPI Request timeout | 90 seconds — if payer doesn't approve, order cancelled, user informed gracefully |

---

## Compliance & Trust Requirements

- **DPDP Act 2023** — explicit consent at onboarding for every user; data residency in India
- **Voice recordings** — stored 90 days for retraining, then auto-deleted unless user opts into longer retention
- **Payment money is never custodied by us.** UPI Request flow means the family payer's UPI app charges them at order time; funds go directly via Razorpay to Swiggy. We never touch the money. **Avoids RBI PPI license requirement entirely.**
- **WhatsApp BSP** is Gupshup; we cannot use unofficial WhatsApp tools (account ban risk)
- **Family payer/caregiver must verify they are family** before getting access — soft KYC via Razorpay payment + ordering user's phone number on call

---

## Apply For (Day 1, Before Coding)

- [ ] Swiggy Builders Club Developer Track — `https://forms.gle/4vkeKyqm15Qb6fnJA`
- [ ] Sarvam Startup Programme (₹10 cr free credits) — apply at sarvam.ai
- [ ] AWS Activate Founders ($1,000 credits, redeem in month 4-6)
- [ ] Gupshup WhatsApp Business API account
- [ ] Anthropic API account ($5 free credit, then pay-as-you-go)
- [ ] Razorpay merchant account (Indian payments)
- [ ] Stripe account (NRI payments)
- [ ] Domain: foodleaf.in (or alternative — verify availability)
- [ ] Cloudflare account for R2 + DNS
- [ ] Neon.tech, Railway.app, Upstash, Vercel accounts (all free tier)

---

## Files in This Plan

- `00_PROJECT_CONTEXT.md` (this file) — Always pin in Cursor
- `01_ARCHITECTURE.md` — Data models, API contracts, system topology
- `02_AGENTS_AND_EDGE_CASES.md` — Per-agent specs and full edge case handlers
- `03_FEATURE_BUILD_ORDER.md` — Feature-by-feature build sequence with acceptance criteria

When asking Cursor/Antigravity to write code for a specific area, load `00_PROJECT_CONTEXT.md` + the relevant module file. Don't load all four at once unless doing cross-cutting refactor.
