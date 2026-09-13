"""Превращает DTO котировочной сессии (КС) Портала поставщиков Москвы в
тот же формат tender-словаря, который tender_sniper.matching.SmartMatcher
уже умеет матчить (см. docs/superpowers/specs/2026-09-13-mos-portal-integration-design.md, разд. 4.2).
"""
from typing import Any, Dict, Optional

SOURCE_LABEL = "Портал поставщиков (Москва)"


def _card_url(ks_id: Any) -> str:
    return f"https://zakupki.mos.ru/auction/{ks_id}"


def ks_dto_to_tender(dto: Dict[str, Any]) -> Dict[str, Optional[Any]]:
    ks_id = dto["id"]
    return {
        "number": f"MOS-{ks_id}",
        "name": dto.get("name") or "",
        "description": "",  # список КС не отдаёт описание — см. спеку, разд. 2 п.6
        "price": dto.get("startPrice"),
        "region": "Москва",
        "customer_name": dto.get("company") or "",
        "published_date": dto.get("beginDate"),
        "submission_deadline": dto.get("endDate"),
        "url": _card_url(ks_id),
        "source_label": SOURCE_LABEL,
    }
