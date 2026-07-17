from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML
from .calc import compute_totals, format_money
from .models import KPData

_TPL_DIR = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(str(_TPL_DIR)),
                   autoescape=select_autoescape(["html"]))


def _qty_str(q):
    q = q.normalize()
    return str(q.to_integral_value()) if q == q.to_integral_value() else format_money(q)


def render_kp_pdf(kp: KPData) -> bytes:
    totals = compute_totals(kp.items, kp.vat_mode)
    vat_suffix = "с НДС" if kp.vat_mode == "vat20" else "без НДС"
    items_ctx = [{
        "name": it.name, "proposed_name": it.proposed_name, "unit": it.unit,
        "qty_str": _qty_str(it.qty), "price_str": format_money(it.price),
        "sum_str": format_money(it.sum), "delivery": it.delivery,
    } for it in kp.items]

    css = (_TPL_DIR / "kp.css").read_text(encoding="utf-8")
    html = _env.get_template("kp.html").render(
        css=css, seller=kp.seller, recipient=kp.recipient, number=kp.number,
        vat_mode=kp.vat_mode, vat_suffix=vat_suffix, delivery_time=kp.delivery_time,
        items=items_ctx, total_str=format_money(totals.total),
        vat_str=format_money(totals.vat_amount) if totals.vat_amount is not None else "",
        validity_days=kp.validity_days, payment_terms=kp.payment_terms,
        delivery_terms=kp.delivery_terms, signer_name=kp.signer_name,
    )
    return HTML(string=html).write_pdf()
