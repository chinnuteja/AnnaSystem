<p align="center">
  <img src="https://em-content.zobj.net/source/apple/391/diya-lamp_1fa94.png" width="80" />
</p>

<h1 align="center">Anna</h1>

<p align="center">
  <strong>WhatsApp AI concierge for Indian families living apart</strong><br/>
  <em>She won't ask her son. She'll tell Anna.</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue?logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Gemini-Flash-orange?logo=google&logoColor=white" />
  <img src="https://img.shields.io/badge/WhatsApp-Cloud_API-25D366?logo=whatsapp&logoColor=white" />
  <img src="https://img.shields.io/badge/Swiggy-MCP-FF5200?logoColor=white" />
  <img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/Redis-DC382D?logo=redis&logoColor=white" />
  <img src="https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql&logoColor=white" />
</p>

---

## The Problem

**180 million Indian families** have at least one member living in a different city from their parents.

Maa needs groceries. The cooking oil is low. Diwali is next week and she hasn't bought sweets.

**But she won't order.** Not because she can't — because she's stubborn. Because she thinks *"itna kharcha kyun karna?"* Because she'll eat dal-roti for three days rather than "trouble" her children.

And Beta — the son in Bangalore — calls every Sunday. *"Maa, kuch chahiye?"* And Maa says, *"Kuch nahi chahiye, sab hai."*

It's a lie born from love.

---

## What Anna Does

**Anna removes the asking.**

```
Maa sends a Hindi voice note  →  Anna builds a cart
Cart crosses ₹1500 threshold  →  Anna asks Beta to approve (in English)
Beta replies "approve"         →  Order placed via Swiggy
Both get confirmation          →  Maa in Hindi, Beta in English
```

No app downloads. No tech literacy. No guilt. Just WhatsApp.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        WhatsApp Cloud API                       │
│                    (webhook in + messages out)                   │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                    ┌──────▼──────┐
                    │   FastAPI   │
                    │  Webhook +  │
                    │   Worker    │
                    └──────┬──────┘
                           │
              ┌────────────▼────────────────┐
              │     Family Resolver         │
              │  phone → family + user +    │
              │  role (ordering_user/payer)  │
              │  Redis-cached (10 min TTL)  │
              └────────────┬────────────────┘
                           │
              ┌────────────▼────────────────┐
              │     Gemini Brain (v3_anna)   │
              │  Hindi-first, family-aware   │
              │  role-context in prompt      │
              │  occasion hints injected     │
              └────────────┬────────────────┘
                           │
              ┌────────────▼────────────────┐
              │     Action Dispatcher       │
              │  order_items │ confirm │     │
              │  approve │ reject_approval │ │
              │  greet │ chitchat │ ...      │
              └─────┬──────────────┬────────┘
                    │              │
         ┌──────────▼───┐   ┌─────▼──────────┐
         │ Family Cart  │   │ Provider Layer  │
         │ (Redis)      │   │ (Swiggy MCP)   │
         │ shared cart  │   │ mock adapter    │
         │ threshold    │   │ catalog + quote │
         └──────────────┘   └────────────────┘
                    │
         ┌──────────▼──────────────────────┐
         │  Conversation State Machine     │
         │  IDLE → PARSING →               │
         │  AWAITING_CONFIRMATION →        │
         │  AWAITING_APPROVAL →            │
         │  EXECUTING → COMPLETE           │
         │  Redis-backed, Lua atomic       │
         └─────────────────────────────────┘
```

---

## The Demo Flow (90 seconds)

| Step | Who | What Happens |
|------|-----|-------------|
| 1 | **Maa** 🙏 | Sends Hindi voice note: *"Anna, atta khatm ho gaya, 5kg manga do. Aur doodh bhi."* |
| 2 | **Anna** 🪔 | Transcribes (Sarvam AI), resolves SKUs (atta 5kg, milk 1L), quotes via Swiggy |
| 3 | **Anna → Maa** | Hindi confirmation: *"Maa ji, aapka order: Aashirvaad Atta 5kg ₹320, Amul Milk 1L ₹68. Total ₹1,888. Beta ko bhej rahi hoon approve ke liye."* |
| 4 | **Anna → Beta** | English notification: *"Hi Rahul, Sunita ji has placed an order: Atta 5kg ₹320, Milk 1L ₹68... Total: ₹1,888. Reply APPROVE to confirm or REJECT to cancel."* |
| 5 | **Beta** 📱 | Replies: "approve" |
| 6 | **Anna → Maa** | *"Rahul ne approve kar diya! Order place ho raha hai. 🙏"* |
| 7 | **Anna → Beta** | *"Order approved! Delivery will be soon. 🙏"* |

**Edge case — below threshold:** If Maa's cart is under ₹1,500, Anna auto-approves. No ping to Beta for small orders.

**Edge case — rejection:** Beta replies "reject" → Maa gets: *"Rahul ne ye order reject kar diya. Kam items ke saath dobara try karein?"*

**Edge case — festival:** If Diwali is within 14 days, Anna suggests: *"Diwali aa rahi hai! Mithai ya diye order karna chahenge?"*

---

## Project Structure

```
AnnaSystem/
├── apps/
│   └── api/
│       └── app/
│           ├── agents/              # AI agents
│           │   ├── brain.py         # Gemini LLM orchestrator
│           │   ├── brain_prompts.py # v1/v2/v3_anna prompt versions
│           │   ├── sku_mapper.py    # text → canonical SKU resolver
│           │   ├── executor.py      # order placement
│           │   ├── discovery.py     # restaurant/food discovery
│           │   ├── transcriber.py   # Sarvam AI voice → text
│           │   └── renderer/        # response formatters (en/hi/te)
│           ├── api/
│           │   ├── routes.py        # health, debug endpoints
│           │   └── webhook.py       # WhatsApp webhook handler
│           ├── integrations/
│           │   └── whatsapp.py      # Meta Cloud API client
│           ├── worker.py            # async job processor
│           └── main.py              # FastAPI app entry
│
├── packages/
│   ├── core/                        # domain logic (no framework deps)
│   │   ├── pipeline.py              # main brain → action dispatcher
│   │   ├── conversation.py          # Redis state machine (CSM)
│   │   ├── family_resolver.py       # phone → family context lookup
│   │   ├── family_cart.py           # shared family cart (Redis)
│   │   ├── occasion_calendar.py     # festival detection + hints
│   │   ├── payer_notification.py    # approval request renderer
│   │   ├── models.py               # SQLAlchemy models
│   │   └── db.py                    # async DB session
│   │
│   └── providers/                   # external service adapters
│       ├── interface.py             # abstract provider contracts
│       ├── router.py               # provider routing
│       ├── catalog_helpers.py       # substitutes, alternatives
│       ├── adapters/
│       │   └── mock_swiggy_adapter.py  # Swiggy-shaped mock provider
│       └── data/
│           ├── grocery_catalog.json
│           ├── food_catalog.json
│           └── dineout_catalog.json
│
├── scripts/
│   ├── seed_anna_demo.py            # seed Sharma family demo data
│   └── seed.py                      # full seed (SKUs, users, etc.)
│
├── tests/
│   ├── test_anna_approval_flow.py   # approval E2E (6 tests)
│   ├── test_anna_demo_flow.py       # demo flow assertions
│   ├── test_conversation.py         # CSM state transitions
│   ├── test_conversation_flow_mvp.py
│   └── ...                          # 15+ test files
│
├── infra/
│   └── migrations/                  # Alembic DB migrations
│
├── docker-compose.yml               # Postgres + Redis
├── requirements.txt
├── .env.example                     # template (no secrets)
└── .gitignore
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- Docker (for Postgres + Redis)
- A Gemini API key ([Google AI Studio](https://aistudio.google.com/))
- WhatsApp Business API credentials (for production)

### 1. Clone & setup

```bash
git clone https://github.com/chinnuteja/AnnaSystem.git
cd AnnaSystem

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Infrastructure

```bash
docker compose up -d   # starts Postgres (pgvector) + Redis
```

### 3. Environment

```bash
cp .env.example .env
# Edit .env — add your GEMINI_API_KEY at minimum
```

### 4. Database

```bash
# Run migrations
alembic upgrade head

# Seed demo data (Sharma family: Maa + Beta)
python -m scripts.seed_anna_demo
```

### 5. Run

```bash
uvicorn app.main:app --reload --port 8000
```

The API is now live at `http://localhost:8000`. WhatsApp webhook at `/webhook`.

### 6. Tests

```bash
pytest -x -v
```

All tests run against mocked providers and in-memory Redis — no external services needed.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `REDIS_URL` | Yes | Redis connection string |
| `GEMINI_API_KEY` | Yes | Google Gemini API key |
| `GEMINI_MODEL` | No | Default: `gemini-2.0-flash` |
| `BRAIN_PROMPT_VERSION` | No | Default: `v3_anna` |
| `WHATSAPP_ACCESS_TOKEN` | Prod | Meta Cloud API token |
| `WHATSAPP_PHONE_NUMBER_ID` | Prod | WhatsApp Business phone ID |
| `WHATSAPP_VERIFY_TOKEN` | Prod | Webhook verification token |
| `SARVAM_API_KEY` | Prod | Hindi voice transcription |
| `AZURE_OPENAI_API_KEY` | No | Optional fallback LLM |

---

## Key Design Decisions

### Why WhatsApp, not an app?

Indian parents aged 55-75 have WhatsApp. They don't download new apps. They don't read English UIs. They do send voice notes to their kids. Anna lives where they already are.

### Why family-scoped cart, not per-user?

The family is the economic unit. Maa adds items across multiple conversations over days. The cart persists at the family level so nothing gets lost. When it's time to order, the whole list goes to Beta in one approval.

### Why a threshold, not approve-everything?

Small orders (₹200 for milk) shouldn't ping Beta at midnight. The family sets a threshold (default ₹1,500). Below it — auto-approved. Above it — Beta decides. This mirrors how Indian families actually work: small daily expenses are trusted, big orders need a conversation.

### Why Hindi voice in, English text out?

Maa thinks in Hindi. Beta works in English. Anna bridges the language gap. Maa sends a voice note in Hindi → Anna transcribes → processes → replies to Maa in Hindi, to Beta in English. Each person communicates in their comfort language.

### Why mock Swiggy, not real?

The provider layer uses an abstract interface (`ProviderAdapter`). The mock adapter returns realistic catalog data, pricing, and delivery estimates. When real Swiggy MCP access is available, it's a drop-in replacement — same interface, real API calls.

---

## Conversation State Machine

```
         ┌─────┐
         │IDLE │ ◄──────────────────────────────┐
         └──┬──┘                                │
            │ user sends message                │
         ┌──▼────┐                              │
         │PARSING│                              │
         └──┬────┘                              │
            │ items resolved + quoted           │
  ┌─────────▼──────────────┐                    │
  │AWAITING_CONFIRMATION   │                    │
  └─────────┬──────────────┘                    │
            │ user confirms                     │
            ├─── cart < threshold ──► EXECUTING ─┤
            │                          │        │
            │                        order      │
            │                        placed     │
            │                          │        │
            │                       COMPLETE ───┘
            │
            └─── cart ≥ threshold
                    │
          ┌─────────▼──────────┐
          │AWAITING_APPROVAL   │
          └─────────┬──────────┘
                    │
           ┌────────┴────────┐
        approved          rejected
           │                 │
        EXECUTING          IDLE
           │
        COMPLETE
```

- **Redis-backed** with Lua scripts for atomic transitions
- **TTL auto-expiry**: stale `PARSING` sessions reset to `IDLE` after 2 minutes
- **Per-state TTLs**: `AWAITING_APPROVAL` expires after 60 minutes

---

## Occasion Calendar (Proactive Hints)

Anna doesn't just respond — she anticipates. A built-in festival calendar detects upcoming occasions and injects hints into the brain prompt:

| Festival | Hint Window | Example Suggestion |
|----------|-------------|-------------------|
| Diwali 🪔 | 14 days before | *"Diwali aa rahi hai! Mithai, namkeen, ya diye order karna chahenge?"* |
| Holi 🎨 | 14 days before | *"Holi aa rahi hai! Gujiya ya thandai ka saamaan chahiye?"* |
| Raksha Bandhan 🧵 | 14 days before | *"Raksha Bandhan aa raha hai! Rakhi ya mithai order karenge?"* |
| Navratri 🙏 | 14 days before | *"Navratri aa rahi hai! Vrat ka khaana ya puja ka saamaan chahiye?"* |
| Karva Chauth 🌙 | 14 days before | *"Karva Chauth aa raha hai! Sargi ya puja ka saamaan chahiye?"* |

The brain weaves these naturally into conversation — no forced upselling.

---

## Data Model

### Family

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `display_name` | string | "Sharma Family" |
| `primary_locale` | string | `hi-IN`, `te-IN`, `en-IN` |
| `city` | string | Delivery city |
| `approval_threshold_inr` | int | Cart amount needing payer approval |

### User

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `family_id` | UUID | FK → Family |
| `role` | enum | `ordering_user`, `payer`, `both` |
| `display_name` | string | "Sunita Sharma" |
| `phone_e164` | string | `+919876500001` |
| `preferred_language` | string | `hi-IN` |
| `dietary_constraints` | JSONB | `{"vegetarian": true}` |
| `brand_preferences` | JSONB | `{"atta": "Aashirvaad"}` |

### FamilyCart (Redis)

| Field | Type | Description |
|-------|------|-------------|
| `family_id` | string | Partition key |
| `items` | list | `[{name, brand, quantity, price_inr}]` |
| `total_inr` | float | Running total |
| `approval_status` | string | `none`, `pending_approval`, `approved`, `rejected` |
| `ordering_user_id` | string | Who added items |
| `ordering_user_phone` | string | For notifications |
| `payer_user_id` | string | Who approves |

---

## Testing

```bash
# Run all tests
pytest -x -v

# Run only Anna approval flow tests
pytest tests/test_anna_approval_flow.py -v

# Run with output
pytest -x -v -s
```

**Test coverage includes:**

- Maa places high-value order → state reaches `AWAITING_APPROVAL` → payer notified
- Maa places low-value order → auto-approved → state reaches `COMPLETE`
- Beta approves → order placed → both notified with correct phones
- Beta rejects → Maa notified → state resets to `IDLE`
- Full two-step flow: `order_items` → `confirm` → threshold check
- Proactive occasion hints injected when festival is within 14 days
- CSM state transitions (all valid + invalid transitions)
- Session recovery from stale states
- Brain action dispatch for all action types

All tests use **mocked providers and in-memory Redis** — zero external dependencies.

---

## Brain Prompt Versions

| Version | Persona | Language | Family-Aware |
|---------|---------|----------|-------------|
| `v1` | FoodLeaf (grocery bot) | Telugu-first | No |
| `v2` | FoodLeaf (improved) | Telugu-first | No |
| **`v3_anna`** | **Anna (family concierge)** | **Hindi-first** | **Yes** |

Set via `BRAIN_PROMPT_VERSION=v3_anna` in `.env` (default).

The `v3_anna` prompt injects:
- Family context (who's talking, their role, family name)
- Payer context (if talking to payer, enable approve/reject actions)
- Occasion hints (if a festival is near)
- Role-appropriate language (respectful "ji" for elders, casual for younger)

---

## Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| **API** | FastAPI | Async-native, type-safe |
| **Brain** | Gemini 2.0 Flash | Fast, multilingual, structured JSON output |
| **Transcription** | Sarvam AI | Best Hindi voice-to-text for Indian accents |
| **Database** | PostgreSQL + pgvector | Relational + future embedding search |
| **Cache/State** | Redis | Sub-ms state machine transitions |
| **Provider** | Swiggy MCP (mock) | Drop-in replaceable adapter pattern |
| **Messaging** | WhatsApp Cloud API | 500M+ Indian users, voice notes native |
| **Migrations** | Alembic | Schema version control |

---

## Roadmap

| Phase | What | Status |
|-------|------|--------|
| **V1 — MVP** | Voice/text → cart → threshold → approve → place | ✅ Complete |
| **V1.1** | Recurring orders ("weekly groceries every Monday") | 🔜 Next |
| **V1.2** | Purchase pattern memory (Anna reminds when atta is due) | 📋 Planned |
| **V2** | Real Swiggy MCP integration | 📋 Planned |
| **V2.1** | UPI Autopay via Razorpay (schema ready) | 📋 Planned |
| **V3** | Multi-family, dietary tracking, health-aware suggestions | 📋 Planned |

---

## License

MIT

---

<p align="center">
  <em>"वो नहीं बोलेगी। Anna बोल देगी।"</em><br/>
  <sub>She won't say it. Anna will.</sub>
</p>
