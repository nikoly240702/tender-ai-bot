from decimal import Decimal
from .models import KPData, KPItem
from .calc import compute_totals
from .renderer import render_kp_pdf


def build_kp_data(profile: dict, recipient: dict, vat_mode: str,
                  delivery_time: str, number: str, items: list) -> KPData:
    seller = {
        "name": profile.get("company_name") or profile.get("company_name_short") or "",
        "inn": profile.get("inn") or "",
        "kpp": profile.get("kpp") or "",
        "address": profile.get("legal_address") or profile.get("actual_address") or "",
        "phone": profile.get("phone") or "",
        "email": profile.get("email") or "",
    }
    return KPData(
        seller=seller, recipient=recipient, number=number, vat_mode=vat_mode,
        delivery_time=delivery_time, items=items,
        validity_days=profile.get("kp_default_validity_days") or 15,
        payment_terms=profile.get("kp_default_payment_terms") or "",
        delivery_terms=profile.get("kp_default_delivery_terms") or "",
        signer_name=profile.get("kp_signer_name") or profile.get("director_name") or "",
    )


class KPService:
    def __init__(self, adapter):
        self.adapter = adapter

    async def create(self, user_id: int, recipient: dict, vat_mode: str,
                     delivery_time: str, items: list) -> tuple:
        profile = await self.adapter.get_company_profile(user_id) or {}
        number = await self.adapter.next_kp_number(user_id)
        kp = build_kp_data(profile, recipient, vat_mode, delivery_time, number, items)
        pdf_bytes = render_kp_pdf(kp)
        totals = compute_totals(items, vat_mode)
        await self.adapter.save_commercial_proposal(user_id, {
            "number": number,
            "recipient_kuda": recipient.get("kuda") or None,
            "recipient_komu": recipient.get("komu") or None,
            "recipient_tel": recipient.get("tel") or None,
            "vat_mode": vat_mode, "delivery_time": delivery_time,
            "items": [{
                "name": it.name, "proposed_name": it.proposed_name, "unit": it.unit,
                "qty": str(it.qty), "price": str(it.price), "sum": str(it.sum),
                "delivery": it.delivery,
            } for it in items],
            "total": float(totals.total),
            "vat_amount": float(totals.vat_amount) if totals.vat_amount is not None else None,
        })
        return pdf_bytes, number
