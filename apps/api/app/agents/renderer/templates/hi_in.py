from __future__ import annotations


class HiInTemplates:
    def cart_header(self) -> str:
        return "🛒 Aapka cart:"

    def cart_line(self, display_name: str, pack_size_label: str, qty: int, line_total: int) -> str:
        size = f" {pack_size_label}" if pack_size_label else ""
        return f"• {display_name}{size} × {qty} — ₹{line_total}"

    def cart_oos_line(self, display_name: str, pack_size_label: str, qty: int) -> str:
        size = f" {pack_size_label}" if pack_size_label else ""
        return f"• {display_name}{size} × {qty} — ❌ stock mein nahi hai"

    def substitute_line(self, display_name: str, pack_size_label: str, price: int) -> str:
        size = f" {pack_size_label}" if pack_size_label else ""
        return f"  ↳ Badle mein {display_name}{size} ₹{price} available hai"

    def substitutes_header(self) -> str:
        return "Sabse qareebi available options:"

    def cart_size_adjustment(self, requested_size_label: str, pack_size_label: str) -> str:
        return f"  ↳ {requested_size_label} pack nahi hai, {pack_size_label} available hai"

    def cart_summary(self, subtotal: int, delivery_fee: int, total: int) -> str:
        return f"Subtotal: ₹{subtotal} | Delivery: ₹{delivery_fee} | Total: ₹{total}"

    def cart_eta(self, eta_min: int, eta_max: int, address_label: str | None) -> str:
        suffix = f" ({address_label})" if address_label else ""
        return f"Delivery: ~{eta_min}-{eta_max} min{suffix}"

    def cart_confirm_prompt(self) -> str:
        return "Confirm karein? (haan / nahi)"

    def capability_line(self, grocery_example: str, food_example: str) -> str:
        return (
            f"🛒 Groceries: {grocery_example}\n"
            f"🍽 Food delivery: {food_example}\n"
            "🪑 Dineout reservations"
        )

    def welcome(self, items_line: str) -> str:
        return f"Namaste! foodleaf pe text ya voice se order kar sakte hain.\n{items_line}\nKya chahiye?"

    def offers_applied(self, offers: list[str]) -> str:
        if not offers:
            return ""
        return "\n".join(f"✅ {offer}" for offer in offers)
