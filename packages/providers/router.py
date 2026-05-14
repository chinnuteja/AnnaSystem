from __future__ import annotations

from packages.providers.adapters.mock_swiggy_adapter import (
    get_dineout_provider,
    get_food_provider,
    get_grocery_provider,
)


class ProviderRouter:
    """MVP provider router.

    For now everything routes to the mock Swiggy-shaped providers. Real Swiggy MCP,
    ONDC, and manual ops can replace these behind the same interface.
    """

    def grocery(self):
        return get_grocery_provider()

    def food(self):
        return get_food_provider()

    def dineout(self):
        return get_dineout_provider()


provider_router = ProviderRouter()
