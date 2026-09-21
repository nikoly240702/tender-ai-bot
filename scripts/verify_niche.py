"""Сверка разобранных данных ЕИС с карточкой на сайте — вручную, глазами.

Печатает то, что мы положили в базу, рядом со ссылкой на карточку
процедуры. Нужен потому, что автотесты проверяют разбор на фикстурах, а
вопрос «совпадают ли наши цифры с тем, что видит человек на сайте» ими
не закрывается: ошибка может быть не в парсере, а в понимании предметной
области.

Так уже находились две ошибки, которых тесты не видели:
  * снижение цены −10 765%, потому что при неопределённом объёме
    торгуются суммы цен за единицу, а не цена контракта;
  * у открытого конкурса в заявках нет цены вовсе — победителя выбирают
    по критериям.

Запуск:
  python -m scripts.verify_niche --purchase-number 0338300003326000140
  python -m scripts.verify_niche --okpd2 21.20 --region 77 --limit 5
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from sqlalchemy import text

from database import DatabaseSession

CARD_URL = ("https://zakupki.gov.ru/epz/order/notice/ea44/view/"
            "supplier-results.html?regNumber={number}")

PROCEDURE_SQL = """
SELECT pr.purchase_number, pr.procedure_type, pr.customer_name, pr.customer_inn,
       pr.customer_region_code, pr.nmck, pr.published_at, pr.okpd2_primary,
       pr.okpd2_codes, pr.quantity_undefined,
       p.protocol_date, p.bids_submitted, p.bids_admitted, p.winner_price,
       p.is_failed,
       c.reg_num, c.contract_price, c.supplier_inn, c.supplier_name, c.sign_date
FROM eis.procedure pr
LEFT JOIN eis.protocol p ON p.purchase_number = pr.purchase_number
LEFT JOIN eis.contract c ON c.purchase_number = pr.purchase_number
WHERE pr.purchase_number = :number
"""

BY_NICHE_SQL = """
SELECT pr.purchase_number
FROM eis.procedure pr
WHERE (CAST(:okpd2 AS text) IS NULL OR pr.okpd2_primary LIKE CAST(:okpd2 AS text) || '%')
  AND (CAST(:region AS text) IS NULL OR pr.customer_region_code = CAST(:region AS text))
ORDER BY pr.published_at DESC NULLS LAST
LIMIT :limit
"""


def _money(value):
    return f"{float(value):,.2f} ₽".replace(",", " ") if value is not None else "—"


def _drop(nmck, winner):
    """Снижение цены. Считается только когда сравнение осмысленно —
    иначе печатаем причину, а не число."""
    if nmck is None or winner is None:
        return "— (нет одной из цен)"
    if float(nmck) <= 0:
        return "— (НМЦК ноль)"
    if float(winner) > float(nmck):
        return "— (цена выше НМЦК, в медиану не идёт)"
    return f"{(float(nmck) - float(winner)) / float(nmck) * 100:.1f}%"


def show(row) -> None:
    print("=" * 74)
    print(f"ЗАКУПКА {row.purchase_number}")
    print(f"  карточка: {CARD_URL.format(number=row.purchase_number)}")
    print()
    print("  --- процедура ---")
    print(f"  тип                {row.procedure_type or '—'}")
    print(f"  заказчик           {(row.customer_name or '—')[:58]}")
    print(f"  ИНН заказчика      {row.customer_inn or '—'}")
    print(f"  регион (код)       {row.customer_region_code or '—'}")
    print(f"  НМЦК               {_money(row.nmck)}")
    print(f"  опубликовано       {row.published_at or '—'}")
    print(f"  ОКПД2 основной     {row.okpd2_primary or '—'}")
    print(f"  ОКПД2 все          {', '.join(row.okpd2_codes or []) or '—'}")
    if row.quantity_undefined:
        print("  ⚠ объём не определён — участники торгуются суммами цен за")
        print("    единицу, с НМЦК это несопоставимо, снижение не считаем")

    print()
    print("  --- протокол ---")
    if row.protocol_date is None and row.bids_submitted is None:
        print("  протокола нет (процедура ещё идёт либо не загружена)")
    else:
        print(f"  дата протокола     {row.protocol_date or '—'}")
        print(f"  подано заявок      {row.bids_submitted if row.bids_submitted is not None else '—'}")
        print(f"  допущено           {row.bids_admitted if row.bids_admitted is not None else '—'}")
        print(f"  цена победителя    {_money(row.winner_price)}")
        print(f"  снижение           {_drop(row.nmck, row.winner_price)}")
        print(f"  несостоявшаяся     {'да' if row.is_failed else 'нет'}")

    print()
    print("  --- контракт ---")
    if row.reg_num is None:
        print("  контракта нет (не заключён либо не загружен)")
        print("  ⚠ без него неизвестен ИНН победителя: в протоколе участник")
        print("    обезличен номером заявки")
    else:
        print(f"  реестровый номер   {row.reg_num}")
        print(f"  цена контракта     {_money(row.contract_price)}")
        print(f"  поставщик          {(row.supplier_name or '—')[:58]}")
        print(f"  ИНН поставщика     {row.supplier_inn or '—'}")
        print(f"  дата заключения    {row.sign_date or '—'}")

    print()
    print("  Сверьте вручную: НМЦК, число заявок и цену победителя на")
    print("  карточке выше. Расхождение — повод чинить парсер, а не UI.")


async def run(args) -> None:
    async with DatabaseSession() as session:
        numbers = [args.purchase_number] if args.purchase_number else [
            r[0] for r in (await session.execute(text(BY_NICHE_SQL), {
                "okpd2": args.okpd2, "region": args.region,
                "limit": args.limit})).all()]
        if not numbers:
            print("нечего сверять: под условия не попала ни одна процедура")
            return
        for number in numbers:
            row = (await session.execute(text(PROCEDURE_SQL),
                                         {"number": number})).first()
            if row is None:
                print(f"закупки {number} нет в eis.procedure")
                continue
            show(row)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Сверка данных ЕИС из базы с карточкой на сайте")
    parser.add_argument("--purchase-number", help="реестровый номер закупки")
    parser.add_argument("--okpd2", help="код ОКПД2 или его начало, например 21.20")
    parser.add_argument("--region", help="код региона, например 77")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    if not args.purchase_number and not args.okpd2 and not args.region:
        parser.error("укажите --purchase-number либо --okpd2/--region")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
