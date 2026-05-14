"""Payer Notification Renderer — formats approval requests for the payer.

Generates English-language summaries for the payer (Beta) when the
care recipient's (Maa's) cart exceeds the family approval threshold.
"""

from __future__ import annotations

from packages.core.family_cart import FamilyCart


def render_payer_approval_notification(
    cart: FamilyCart,
    *,
    payer_name: str = "Rahul",
    ordering_name: str = "Sunita ji",
    family_name: str = "Sharma Family",
    recipient_locale: str = "en-IN",
) -> str:
    """Render an approval notification message for the payer.

    The payer gets an English summary with item details and total.
    They can reply "approve" / "haan" / "yes" or "reject" / "no" / "nahi".
    """
    items_lines = []
    for i, item in enumerate(cart.items, 1):
        brand_str = f" ({item.brand})" if item.brand else ""
        qty_str = f" x{item.quantity}" if item.quantity > 1 else ""
        price_str = f" — Rs.{item.price_inr * item.quantity:.0f}" if item.price_inr else ""
        items_lines.append(f"  {i}. {item.name}{brand_str}{qty_str}{price_str}")

    items_text = "\n".join(items_lines)
    total = f"Rs.{cart.total_inr:.0f}"

    return (
        f"Hi {payer_name}, {ordering_name} has placed an order for your family ({family_name}):\n"
        f"\n"
        f"{items_text}\n"
        f"\n"
        f"Total: {total}\n"
        f"\n"
        f"Reply APPROVE to confirm or REJECT to cancel."
    )


def render_approval_confirmed_to_ordering_user(
    cart: FamilyCart,
    *,
    payer_name: str = "Rahul",
    locale: str = "hi-IN",
) -> str:
    """Render confirmation to the ordering user (Maa) that payer approved."""
    if locale.startswith("hi"):
        return (
            f"{payer_name} ne aapka order approve kar diya! "
            f"Ab order place ho raha hai. "
            f"Jaldi milenge aapke saamaan! 🙏"
        )
    return (
        f"{payer_name} has approved your order! "
        f"Your order is being placed now. "
        f"You'll receive your items soon! 🙏"
    )


def render_approval_rejected_to_ordering_user(
    cart: FamilyCart,
    *,
    payer_name: str = "Rahul",
    locale: str = "hi-IN",
) -> str:
    """Render rejection notice to the ordering user (Maa)."""
    if locale.startswith("hi"):
        return (
            f"{payer_name} ne ye order reject kar diya. "
            f"Aap chahein toh kam items ke saath dobara try karein, "
            f"ya unse baat karein. Kya main help karoon?"
        )
    return (
        f"{payer_name} has declined this order. "
        f"You can try again with fewer items, or talk to them. "
        f"Would you like help with that?"
    )


def render_approval_confirmed_to_payer(
    cart: FamilyCart,
    *,
    payer_name: str = "Rahul",
    locale: str = "en-IN",
) -> str:
    """Render confirmation to the payer that their approval was processed."""
    if locale.startswith("hi"):
        return (
            f"Order approve ho gaya! Total: Rs.{cart.total_inr:.0f}. "
            f"Order place ho raha hai — delivery jaldi hogi. 🙏"
        )
    return (
        f"Order approved! Total: Rs.{cart.total_inr:.0f}. "
        f"Order is being placed — delivery will be soon. 🙏"
    )


def render_approval_rejected_to_payer(
    cart: FamilyCart,
    *,
    payer_name: str = "Rahul",
    locale: str = "en-IN",
) -> str:
    """Render rejection confirmation to the payer."""
    if locale.startswith("hi"):
        return "Order reject ho gaya. Koi baat nahi!"
    return "Order rejected. No problem!"
