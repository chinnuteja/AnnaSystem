from __future__ import annotations

from redis.asyncio import Redis

ACK_DELAY_SECONDS = 1.5
HARD_TIMEOUT_SECONDS = 12.0

ACK_VARIANTS = [
    "Sare, chustunnanu...",
    "Ok, best option chusthunna.",
    "Konchem wait cheyandi, compare chesthunna.",
    "Sare, price and delivery time check chesthunna.",
    "Mee kosam options verify chesthunna.",
]


async def select_ack_text(redis: Redis, context_tag: str = "generic") -> str:
    """Rotate acknowledgement text variants across sessions."""
    key = f"ack:rotation:{context_tag}"
    count = await redis.incr(key)
    return ACK_VARIANTS[(count - 1) % len(ACK_VARIANTS)]
