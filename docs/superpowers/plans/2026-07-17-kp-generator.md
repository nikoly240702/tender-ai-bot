# КП Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Telegram-команда `/kp`, которая пошагово собирает позиции КП, считает суммы/итог и отдаёт готовый PDF в фирменном шаблоне.

**Architecture:** Отдельный пакет `tender_sniper/kp_generator/` (чистые данные + расчёт + рендер), сервис-слой для сборки из `CompanyProfile` и записи в новую модель `CommercialProposal`, aiogram FSM-хендлер `bot/handlers/kp_generator.py`. PDF рендерится из Jinja2-HTML через WeasyPrint.

**Tech Stack:** Python 3.11, aiogram 3.x, SQLAlchemy (async), Alembic, Jinja2, WeasyPrint, pytest.

## Global Constraints

- Реквизиты продавца берём ТОЛЬКО из существующей модели `CompanyProfile` (не дублируем).
- Деньги считаем через `decimal.Decimal`, квантование до `0.01`, округление `ROUND_HALF_UP`.
- Формат денег строго русский: `3 968 150,00` (пробел-разделитель тысяч, запятая-дроби, всегда 2 знака).
- Парсинг ввода — детерминированный (без AI).
- Два режима НДС: `none` (без НДС, УСН — дефолт) и `vat20` (цена вводится как финальная, с НДС; НДС = total × 20/120).
- Колонки таблицы строго в порядке: `№ · Наименование · Предлагаемое наименование · Ед. изм. · Кол-во · Цена за ед. · Сумма · Срок поставки`.
- PDF не сохраняем на диск (Railway эфемерна) — отдаём в чат как `BufferedInputFile`, метаданные пишем в БД.
- **Setup для WeasyPrint** (нужен до Task 2): macOS dev — `brew install pango gdk-pixbuf libffi`; Railway — системные пакеты в `Dockerfile` (Task 2, шаг про Dockerfile). Если установка WeasyPrint окажется болезненной — документированный fallback на `reportlab` (та же сигнатура `render_kp_pdf`).

---

### Task 1: Расчёт и модель данных КП

**Files:**
- Create: `tender_sniper/kp_generator/__init__.py` (пустой)
- Create: `tender_sniper/kp_generator/models.py`
- Create: `tender_sniper/kp_generator/calc.py`
- Test: `tests/unit/test_kp_calc.py`

**Interfaces:**
- Produces:
  - `KPItem(name: str, proposed_name: str, unit: str, qty: Decimal, price: Decimal, delivery: str)` с property `sum -> Decimal` (= qty×price, квантование 0.01)
  - `KPData(seller: dict, recipient: dict, number: str, vat_mode: str, delivery_time: str, items: list[KPItem], validity_days: int, payment_terms: str, delivery_terms: str, signer_name: str)`
  - `Totals(total: Decimal, vat_amount: Decimal | None)`
  - `format_money(value: Decimal) -> str`
  - `parse_price(s: str) -> Decimal` (кидает `ValueError` при мусоре)
  - `parse_qty(s: str) -> Decimal` (кидает `ValueError`; > 0)
  - `compute_totals(items: list[KPItem], vat_mode: str) -> Totals`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_kp_calc.py
import pytest
from decimal import Decimal
from tender_sniper.kp_generator.models import KPItem
from tender_sniper.kp_generator.calc import format_money, parse_price, parse_qty, compute_totals


def _item(qty, price):
    return KPItem(name="Бумага А4", proposed_name="Бумага А4 SvetoCopy",
                  unit="шт.", qty=Decimal(qty), price=Decimal(price), delivery="15 к.д.")


def test_item_sum():
    assert _item(1000, "350").sum == Decimal("350000.00")


def test_format_money_russian():
    assert format_money(Decimal("3968150")) == "3 968 150,00"
    assert format_money(Decimal("350.5")) == "350,50"
    assert format_money(Decimal("0")) == "0,00"


def test_parse_price_variants():
    assert parse_price("350") == Decimal("350")
    assert parse_price("350,00") == Decimal("350.00")
    assert parse_price("1 000.50") == Decimal("1000.50")
    with pytest.raises(ValueError):
        parse_price("abc")


def test_parse_qty_positive():
    assert parse_qty("1000") == Decimal("1000")
    with pytest.raises(ValueError):
        parse_qty("0")
    with pytest.raises(ValueError):
        parse_qty("-5")


def test_compute_totals_none():
    t = compute_totals([_item(11600, "340"), _item(35, "690")], "none")
    assert t.total == Decimal("3968150.00")
    assert t.vat_amount is None


def test_compute_totals_vat20():
    t = compute_totals([_item(1000, "360")], "vat20")
    assert t.total == Decimal("360000.00")
    assert t.vat_amount == Decimal("60000.00")  # 360000 * 20 / 120
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_kp_calc.py -v`
Expected: FAIL (`ModuleNotFoundError: tender_sniper.kp_generator`)

- [ ] **Step 3: Write minimal implementation**

```python
# tender_sniper/kp_generator/__init__.py
```

```python
# tender_sniper/kp_generator/models.py
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional


def _q2(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class KPItem:
    name: str
    proposed_name: str
    unit: str
    qty: Decimal
    price: Decimal
    delivery: str = ""

    @property
    def sum(self) -> Decimal:
        return _q2(self.qty * self.price)


@dataclass
class Totals:
    total: Decimal
    vat_amount: Optional[Decimal] = None


@dataclass
class KPData:
    seller: dict
    recipient: dict
    number: str
    vat_mode: str            # 'none' | 'vat20'
    delivery_time: str
    items: list
    validity_days: int = 15
    payment_terms: str = ""
    delivery_terms: str = ""
    signer_name: str = ""
```

```python
# tender_sniper/kp_generator/calc.py
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from .models import KPItem, Totals


def _q2(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_money(value: Decimal) -> str:
    """Русский формат: '3 968 150,00' (пробел-тысячи, запятая-дроби, 2 знака)."""
    v = _q2(Decimal(value))
    sign = "-" if v < 0 else ""
    v = abs(v)
    int_part, frac_part = f"{v:.2f}".split(".")
    groups = []
    while len(int_part) > 3:
        groups.insert(0, int_part[-3:])
        int_part = int_part[:-3]
    groups.insert(0, int_part)
    return f"{sign}{' '.join(groups)},{frac_part}"


def parse_price(s: str) -> Decimal:
    cleaned = (s or "").strip().replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        v = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        raise ValueError(f"Не число: {s!r}")
    if v < 0:
        raise ValueError("Цена не может быть отрицательной")
    return v


def parse_qty(s: str) -> Decimal:
    cleaned = (s or "").strip().replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        v = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        raise ValueError(f"Не число: {s!r}")
    if v <= 0:
        raise ValueError("Кол-во должно быть больше нуля")
    return v


def compute_totals(items, vat_mode: str) -> Totals:
    total = _q2(sum((it.sum for it in items), Decimal("0")))
    if vat_mode == "vat20":
        vat = _q2(total * Decimal("20") / Decimal("120"))
        return Totals(total=total, vat_amount=vat)
    return Totals(total=total, vat_amount=None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_kp_calc.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add tender_sniper/kp_generator/__init__.py tender_sniper/kp_generator/models.py tender_sniper/kp_generator/calc.py tests/unit/test_kp_calc.py
git commit -m "feat(kp): расчёт сумм/НДС и парсинг для генератора КП"
```

---

### Task 2: PDF-рендер по шаблону

**Files:**
- Create: `tender_sniper/kp_generator/renderer.py`
- Create: `tender_sniper/kp_generator/templates/kp.html`
- Create: `tender_sniper/kp_generator/templates/kp.css`
- Modify: `requirements.txt` (добавить `weasyprint`)
- Modify: `Dockerfile` (системные библиотеки WeasyPrint)
- Test: `tests/unit/test_kp_renderer.py`

**Interfaces:**
- Consumes: `KPData`, `KPItem`, `compute_totals`, `format_money` (Task 1)
- Produces: `render_kp_pdf(kp: KPData) -> bytes`

- [ ] **Step 1: Установить WeasyPrint локально (macOS dev)**

Run: `brew install pango gdk-pixbuf libffi && pip install weasyprint`
Expected: `python -c "import weasyprint; print(weasyprint.__version__)"` печатает версию без ошибок.
(Если падает на системных либах — переключиться на fallback reportlab, см. Global Constraints.)

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_kp_renderer.py
from decimal import Decimal
from io import BytesIO
from PyPDF2 import PdfReader
from tender_sniper.kp_generator.models import KPData, KPItem
from tender_sniper.kp_generator.renderer import render_kp_pdf


def _kp():
    return KPData(
        seller={"name": "ИП Хисамеева Ирина Радиевна", "inn": "7710140679",
                "kpp": "771301001", "address": "Респ. Татарстан, г. Набережные Челны",
                "phone": "+7 (915) 211-30-10", "email": "zackupkigov@yandex.ru"},
        recipient={"kuda": "", "tel": "", "komu": ""},
        number="ИПХИС-26063", vat_mode="none", delivery_time="15 к.д.",
        items=[KPItem("Бумага А4", "Бумага А4 SvetoCopy класс С", "шт.",
                      Decimal("1000"), Decimal("350"), "15 к.д.")],
        validity_days=15,
        payment_terms="100% постоплата в течение 30 рабочих дней после поставки",
        delivery_terms="DDP - доставка до склада Покупателя.",
        signer_name="Хисамеева Ирина Радиевна",
    )


def test_render_returns_pdf_bytes():
    data = render_kp_pdf(_kp())
    assert isinstance(data, bytes)
    assert data[:4] == b"%PDF"


def test_pdf_contains_number_and_total():
    data = render_kp_pdf(_kp())
    text = "".join(page.extract_text() for page in PdfReader(BytesIO(data)).pages)
    assert "ИПХИС-26063" in text
    assert "350 000,00" in text
    assert "Всего без НДС" in text
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_kp_renderer.py -v`
Expected: FAIL (`ImportError: cannot import name 'render_kp_pdf'`)

- [ ] **Step 4: Создать HTML-шаблон**

```html
<!-- tender_sniper/kp_generator/templates/kp.html -->
<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><style>{{ css }}</style></head>
<body>
  <table class="head"><tr>
    <td class="seller">
      <div>{{ seller.name }}</div>
      <div>ИНН {{ seller.inn }}{% if seller.kpp %}, КПП {{ seller.kpp }}{% endif %}</div>
      <div>{{ seller.address }}</div>
      <div>Тел. {{ seller.phone }}</div>
      <div>{{ seller.email }} |</div>
    </td>
    <td class="recipient">
      <div>Куда: {{ recipient.kuda }}</div>
      <div>Тел.: {{ recipient.tel }}</div>
      <div>Кому: {{ recipient.komu }}</div>
    </td>
  </tr></table>

  <h1>КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ № {{ number }}</h1>

  <table class="items">
    <thead><tr>
      <th>№</th><th>Наименование</th><th>Предлагаемое наименование</th>
      <th>Ед.<br>изм.</th><th>Кол-во</th>
      <th>Цена за ед.,<br>RUB {{ vat_suffix }}</th>
      <th>Сумма,<br>RUB {{ vat_suffix }}</th><th>Срок<br>поставки</th>
    </tr></thead>
    <tbody>
    {% for it in items %}
      <tr>
        <td class="c">{{ loop.index }}</td>
        <td>{{ it.name }}</td><td>{{ it.proposed_name }}</td>
        <td class="c">{{ it.unit }}</td><td class="c">{{ it.qty_str }}</td>
        <td class="r">{{ it.price_str }}</td><td class="r">{{ it.sum_str }}</td>
        <td class="c">{{ it.delivery or delivery_time }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>

  <table class="totals"><tr><td>
    {% if vat_mode == 'vat20' %}
      Итого: {{ total_str }}<br>в т.ч. НДС 20%: {{ vat_str }}
    {% else %}
      Всего без НДС: {{ total_str }}
    {% endif %}
  </td></tr></table>

  <div class="terms">
    <div>Срок действия данного предложения {{ validity_days }} календарных дней.</div>
    <div>Условия оплаты: {{ payment_terms }}</div>
    <div>Условия доставки: {{ delivery_terms }}</div>
    <div>Цены действительны при полном объеме закупки. Срок поставки указан без учета праздничных выходных.</div>
  </div>
  <hr>
  <div class="sign">С наилучшими пожеланиями,<br>{{ signer_name }}</div>
</body></html>
```

- [ ] **Step 5: Создать CSS**

```css
/* tender_sniper/kp_generator/templates/kp.css */
@page { size: A4 landscape; margin: 15mm; }
body { font-family: 'DejaVu Sans', Arial, sans-serif; font-size: 9pt; color: #000; }
.head { width: 100%; border: 0; margin-bottom: 8mm; }
.head td { border: 0; vertical-align: top; }
.head .seller div { line-height: 1.35; }
.head .recipient { width: 30%; }
.head .recipient div { border-bottom: 1px solid #000; margin-bottom: 4mm; padding: 1mm 0; }
h1 { text-align: center; font-size: 10pt; font-weight: bold; margin: 4mm 0; }
table.items { width: 100%; border-collapse: collapse; }
table.items th, table.items td { border: 1px solid #000; padding: 2px 5px; vertical-align: middle; }
table.items th { text-align: center; font-weight: normal; }
td.c { text-align: center; } td.r { text-align: right; }
table.totals { width: 100%; border-collapse: collapse; margin-top: -1px; }
table.totals td { border: 1px solid #000; text-align: center; padding: 2px 5px; }
.terms { margin-top: 4mm; line-height: 1.4; }
hr { border: 0; border-top: 1px solid #000; margin: 6mm 0 2mm; }
.sign { line-height: 1.4; }
```

- [ ] **Step 6: Реализовать рендерер**

```python
# tender_sniper/kp_generator/renderer.py
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
```

- [ ] **Step 7: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_kp_renderer.py -v`
Expected: PASS (2 passed)

- [ ] **Step 8: Добавить зависимость и Docker-либы**

В `requirements.txt` добавить строку:
```
weasyprint==62.3
```
В `Dockerfile` в блок установки системных пакетов (`apt-get install`) добавить:
```
libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi-dev fonts-dejavu
```

- [ ] **Step 9: Commit**

```bash
git add tender_sniper/kp_generator/renderer.py tender_sniper/kp_generator/templates/ tests/unit/test_kp_renderer.py requirements.txt Dockerfile
git commit -m "feat(kp): PDF-рендер КП по фирменному шаблону (WeasyPrint)"
```

---

### Task 3: Схема БД — поля КП в профиле и модель CommercialProposal

**Files:**
- Modify: `database.py` (класс `CompanyProfile` — добавить поля; новый класс `CommercialProposal`)
- Create: `alembic/versions/20260717_kp_generator.py`
- Test: `tests/unit/test_kp_models.py`

**Interfaces:**
- Produces: SQLAlchemy-модель `CommercialProposal` (`commercial_proposals`) и новые колонки `CompanyProfile.kp_*`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_kp_models.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base, CommercialProposal, CompanyProfile


def test_commercial_proposal_and_kp_fields_create():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    cp = CommercialProposal(user_id=1, number="ИПХИС-26064", vat_mode="none",
                            delivery_time="15 к.д.", items=[], total=0)
    s.add(cp); s.commit()
    assert cp.id is not None
    # новые поля профиля существуют
    prof = CompanyProfile(user_id=1, kp_number_prefix="ИПХИС", kp_counter=26063)
    s.add(prof); s.commit()
    assert prof.kp_counter == 26063
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_kp_models.py -v`
Expected: FAIL (`ImportError: cannot import name 'CommercialProposal'`)

- [ ] **Step 3: Расширить CompanyProfile**

В `database.py`, в класс `CompanyProfile` (после блока `# Дополнительно`, перед `is_complete`) добавить:

```python
    # Настройки генератора КП
    kp_number_prefix = Column(String(20), nullable=True)      # напр. ИПХИС
    kp_counter = Column(Integer, default=0, nullable=False)   # последний выданный номер
    kp_default_validity_days = Column(Integer, default=15, nullable=False)
    kp_default_payment_terms = Column(Text, nullable=True)
    kp_default_delivery_terms = Column(Text, nullable=True)
    kp_signer_name = Column(String(255), nullable=True)
```

- [ ] **Step 4: Добавить модель CommercialProposal**

В `database.py` (после класса `GeneratedDocument`) добавить:

```python
class CommercialProposal(Base):
    """Коммерческое предложение (КП), сгенерированное через /kp."""
    __tablename__ = 'commercial_proposals'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False, index=True)
    number = Column(String(100), nullable=False)
    recipient_kuda = Column(String(500), nullable=True)
    recipient_komu = Column(String(500), nullable=True)
    recipient_tel = Column(String(100), nullable=True)
    vat_mode = Column(String(10), default='none', nullable=False)  # none | vat20
    delivery_time = Column(String(255), nullable=True)
    items = Column(JSON, default=list)   # [{name, proposed_name, unit, qty, price, sum, delivery}]
    total = Column(Float, nullable=False, default=0)
    vat_amount = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("SniperUser")
```

- [ ] **Step 5: Создать alembic-миграцию**

```python
# alembic/versions/20260717_kp_generator.py
"""kp generator: proposals + profile kp fields

Revision ID: 20260717_kp
Revises: 20260504_own_products
Create Date: 2026-07-17
"""
from alembic import op
import sqlalchemy as sa

revision = '20260717_kp'
down_revision = '20260504_own_products'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('company_profiles', sa.Column('kp_number_prefix', sa.String(length=20), nullable=True))
    op.add_column('company_profiles', sa.Column('kp_counter', sa.Integer(), server_default='0', nullable=False))
    op.add_column('company_profiles', sa.Column('kp_default_validity_days', sa.Integer(), server_default='15', nullable=False))
    op.add_column('company_profiles', sa.Column('kp_default_payment_terms', sa.Text(), nullable=True))
    op.add_column('company_profiles', sa.Column('kp_default_delivery_terms', sa.Text(), nullable=True))
    op.add_column('company_profiles', sa.Column('kp_signer_name', sa.String(length=255), nullable=True))
    op.create_table('commercial_proposals',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('number', sa.String(length=100), nullable=False),
        sa.Column('recipient_kuda', sa.String(length=500), nullable=True),
        sa.Column('recipient_komu', sa.String(length=500), nullable=True),
        sa.Column('recipient_tel', sa.String(length=100), nullable=True),
        sa.Column('vat_mode', sa.String(length=10), server_default='none', nullable=False),
        sa.Column('delivery_time', sa.String(length=255), nullable=True),
        sa.Column('items', sa.JSON(), nullable=True),
        sa.Column('total', sa.Float(), nullable=False, server_default='0'),
        sa.Column('vat_amount', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table('commercial_proposals')
    for col in ('kp_signer_name', 'kp_default_delivery_terms', 'kp_default_payment_terms',
                'kp_default_validity_days', 'kp_counter', 'kp_number_prefix'):
        op.drop_column('company_profiles', col)
```
Примечание: если `down_revision` `20260504_own_products` не является последней головой (`alembic heads`), подставить фактическую голову.

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_kp_models.py -v`
Expected: PASS (1 passed)

- [ ] **Step 7: Commit**

```bash
git add database.py alembic/versions/20260717_kp_generator.py tests/unit/test_kp_models.py
git commit -m "feat(kp): модель CommercialProposal + поля КП в профиле + миграция"
```

---

### Task 4: Методы адаптера БД

**Files:**
- Modify: `tender_sniper/database/sqlalchemy_adapter.py`
- Test: `tests/unit/test_kp_adapter.py`

**Interfaces:**
- Consumes: `CommercialProposal`, `CompanyProfile` модели (Task 3)
- Produces (методы адаптера, async):
  - `async next_kp_number(user_id: int) -> str` — атомарно инкрементит `kp_counter`, возвращает `{prefix}-{counter}` (префикс по умолчанию `КП` если пуст)
  - `async save_commercial_proposal(user_id: int, data: dict) -> int` — пишет строку, возвращает id

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_kp_adapter.py
import pytest
from tender_sniper.database.sqlalchemy_adapter import SQLAlchemyAdapter  # см. фактическое имя класса


@pytest.mark.asyncio
async def test_next_kp_number_increments(kp_adapter_with_profile):
    adapter, user_id = kp_adapter_with_profile  # фикстура: профиль с prefix='ИПХИС', counter=26063
    assert await adapter.next_kp_number(user_id) == "ИПХИС-26064"
    assert await adapter.next_kp_number(user_id) == "ИПХИС-26065"
```

Примечание для реализатора: фикстуру `kp_adapter_with_profile` собрать по образцу существующих async-DB тестов в `tests/` (тот же движок/сессия, что использует адаптер). Если async-DB тестовой обвязки в проекте нет — заменить на прямой тест метода с in-memory sqlite async-сессией, повторяя паттерн `DatabaseSession` из адаптера.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_kp_adapter.py -v`
Expected: FAIL (`AttributeError: 'SQLAlchemyAdapter' object has no attribute 'next_kp_number'`)

- [ ] **Step 3: Реализовать методы**

В `tender_sniper/database/sqlalchemy_adapter.py` рядом с `upsert_company_profile` добавить (импортировать `CommercialProposal` из моделей вверху файла):

```python
    async def next_kp_number(self, user_id: int) -> str:
        async with DatabaseSession() as session:
            result = await session.execute(
                select(CompanyProfileModel).where(CompanyProfileModel.user_id == user_id)
            )
            profile = result.scalar_one_or_none()
            if profile is None:
                profile = CompanyProfileModel(user_id=user_id, kp_counter=0)
                session.add(profile)
                await session.flush()
            profile.kp_counter = (profile.kp_counter or 0) + 1
            prefix = profile.kp_number_prefix or "КП"
            number = f"{prefix}-{profile.kp_counter}"
            await session.commit()
            return number

    async def save_commercial_proposal(self, user_id: int, data: dict) -> int:
        async with DatabaseSession() as session:
            proposal = CommercialProposalModel(user_id=user_id, **data)
            session.add(proposal)
            await session.flush()
            pid = proposal.id
            await session.commit()
            return pid
```
Добавить в блок импортов моделей (рядом с `CompanyProfile as CompanyProfileModel`):
```python
    CommercialProposal as CommercialProposalModel,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_kp_adapter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tender_sniper/database/sqlalchemy_adapter.py tests/unit/test_kp_adapter.py
git commit -m "feat(kp): адаптер БД — выдача номера КП и сохранение предложения"
```

---

### Task 5: Сервис сборки КП

**Files:**
- Create: `tender_sniper/kp_generator/service.py`
- Test: `tests/unit/test_kp_service.py`

**Interfaces:**
- Consumes: `KPData`, `KPItem` (Task 1); `render_kp_pdf` (Task 2); адаптер `next_kp_number`, `save_commercial_proposal`, `get_company_profile` (Task 4)
- Produces:
  - `build_kp_data(profile: dict, recipient: dict, vat_mode: str, delivery_time: str, number: str, items: list[KPItem]) -> KPData` — чистая сборка из профиля (без БД)
  - `async KPService(adapter).create(user_id, recipient, vat_mode, delivery_time, items) -> tuple[bytes, str]` — выдаёт номер, строит `KPData`, рендерит PDF, сохраняет запись; возвращает `(pdf_bytes, number)`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_kp_service.py
from decimal import Decimal
from tender_sniper.kp_generator.models import KPItem
from tender_sniper.kp_generator.service import build_kp_data


def test_build_kp_data_maps_profile():
    profile = {
        "company_name": "ИП Хисамеева Ирина Радиевна", "inn": "7710140679",
        "kpp": "771301001", "legal_address": "Респ. Татарстан",
        "phone": "+7 (915) 211-30-10", "email": "z@ya.ru",
        "kp_default_validity_days": 15,
        "kp_default_payment_terms": "100% постоплата",
        "kp_default_delivery_terms": "DDP", "kp_signer_name": "Хисамеева И.Р.",
    }
    kp = build_kp_data(profile, {"kuda": "", "tel": "", "komu": ""},
                       "none", "15 к.д.", "ИПХИС-26064",
                       [KPItem("Бумага", "Бумага А4", "шт.", Decimal("10"), Decimal("350"), "15 к.д.")])
    assert kp.seller["name"] == "ИП Хисамеева Ирина Радиевна"
    assert kp.seller["inn"] == "7710140679"
    assert kp.number == "ИПХИС-26064"
    assert kp.validity_days == 15
    assert kp.signer_name == "Хисамеева И.Р."
    assert len(kp.items) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_kp_service.py -v`
Expected: FAIL (`ImportError: cannot import name 'build_kp_data'`)

- [ ] **Step 3: Реализовать сервис**

```python
# tender_sniper/kp_generator/service.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_kp_service.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add tender_sniper/kp_generator/service.py tests/unit/test_kp_service.py
git commit -m "feat(kp): сервис сборки КП из профиля + сохранение"
```

---

### Task 6: FSM-состояния и хендлер /kp

**Files:**
- Modify: `bot/states/__init__.py` (добавить `KPStates`)
- Create: `bot/handlers/kp_generator.py`
- Modify: `bot/main.py` (регистрация роутера — строка ~405)
- Test: `tests/unit/test_kp_handler_helpers.py`

**Interfaces:**
- Consumes: `parse_price`, `parse_qty`, `format_money` (Task 1); `KPItem` (Task 1); `KPService` (Task 5); `get_sniper_db` (существующий)
- Produces: `router` (aiogram Router, name='kp_generator'); хелпер `build_preview_text(items: list[KPItem], vat_mode: str) -> str`

- [ ] **Step 1: Добавить состояния**

В `bot/states/__init__.py` добавить класс:

```python
class KPStates(StatesGroup):
    """Состояния мастера генерации КП."""
    waiting_recipient_kuda = State()
    waiting_recipient_komu = State()
    waiting_recipient_tel = State()
    waiting_vat_mode = State()
    waiting_delivery_time = State()
    waiting_item_name = State()
    waiting_item_proposed = State()
    waiting_item_unit = State()
    waiting_item_qty = State()
    waiting_item_price = State()
    confirming = State()
```

- [ ] **Step 2: Write the failing test (чистый хелпер предпросмотра)**

```python
# tests/unit/test_kp_handler_helpers.py
from decimal import Decimal
from tender_sniper.kp_generator.models import KPItem
from bot.handlers.kp_generator import build_preview_text


def test_preview_lists_items_and_total():
    items = [
        KPItem("Бумага А4", "Бумага А4 SvetoCopy", "шт.", Decimal("1000"), Decimal("350"), "15 к.д."),
        KPItem("Бумага А3", "Бумага А3 марафон", "шт.", Decimal("35"), Decimal("690"), "15 к.д."),
    ]
    text = build_preview_text(items, "none")
    assert "1." in text and "Бумага А4 SvetoCopy" in text
    assert "350 000,00" in text
    assert "Всего без НДС" in text
    assert "374 150,00" in text  # 350000 + 24150
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_kp_handler_helpers.py -v`
Expected: FAIL (`ModuleNotFoundError` / cannot import `build_preview_text`)

- [ ] **Step 4: Реализовать хендлер**

```python
# bot/handlers/kp_generator.py
"""Мастер генерации коммерческого предложения (КП) — команда /kp."""
import logging
from decimal import Decimal
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (Message, CallbackQuery, BufferedInputFile,
                           InlineKeyboardMarkup, InlineKeyboardButton)
from aiogram.fsm.context import FSMContext

from bot.states import KPStates
from tender_sniper.database import get_sniper_db
from tender_sniper.kp_generator.calc import parse_price, parse_qty, format_money, compute_totals
from tender_sniper.kp_generator.models import KPItem
from tender_sniper.kp_generator.service import KPService

logger = logging.getLogger(__name__)
router = Router(name='kp_generator')

_SKIP = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏭ Пропустить", callback_data="kp_skip")]])
_VAT = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Без НДС (УСН)", callback_data="kp_vat_none")],
    [InlineKeyboardButton(text="С НДС 20%", callback_data="kp_vat_vat20")],
])
_UNITS = InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(text="шт.", callback_data="kp_unit_шт."),
    InlineKeyboardButton(text="упак.", callback_data="kp_unit_упак."),
    InlineKeyboardButton(text="кг", callback_data="kp_unit_кг"),
    InlineKeyboardButton(text="услуга", callback_data="kp_unit_услуга"),
]])
_AFTER_ITEM = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="➕ Добавить ещё", callback_data="kp_more")],
    [InlineKeyboardButton(text="✅ Готово", callback_data="kp_done")],
])
_CONFIRM = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="✅ Сгенерировать PDF", callback_data="kp_generate")],
    [InlineKeyboardButton(text="✏️ Начать заново", callback_data="kp_restart")],
])


def build_preview_text(items, vat_mode: str) -> str:
    lines = ["<b>Предпросмотр КП:</b>", ""]
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {it.proposed_name} — {format_money(it.qty)} {it.unit} × "
                     f"{format_money(it.price)} = {format_money(it.sum)}")
    totals = compute_totals(items, vat_mode)
    lines.append("")
    if vat_mode == "vat20":
        lines.append(f"<b>Итого: {format_money(totals.total)}</b>")
        lines.append(f"в т.ч. НДС 20%: {format_money(totals.vat_amount)}")
    else:
        lines.append(f"<b>Всего без НДС: {format_money(totals.total)}</b>")
    return "\n".join(lines)


def _items_from_state(data: list) -> list:
    return [KPItem(d["name"], d["proposed_name"], d["unit"],
                   Decimal(d["qty"]), Decimal(d["price"]), d["delivery"]) for d in data]


@router.message(Command("kp"))
async def kp_start(message: Message, state: FSMContext):
    db = get_sniper_db()
    profile = await db.get_company_profile_by_telegram_id(message.from_user.id)
    if not profile or not (profile.get("company_name") or profile.get("company_name_short")):
        await message.answer("Сначала заполните реквизиты компании: /profile")
        return
    await state.clear()
    await state.update_data(items=[])
    await state.set_state(KPStates.waiting_recipient_kuda)
    await message.answer("Кому адресуем КП?\n<b>Куда</b> (организация):", reply_markup=_SKIP, parse_mode="HTML")


@router.callback_query(F.data == "kp_skip", KPStates.waiting_recipient_kuda)
async def skip_kuda(cb: CallbackQuery, state: FSMContext):
    await state.update_data(kuda="")
    await state.set_state(KPStates.waiting_recipient_komu)
    await cb.message.answer("<b>Кому</b> (ФИО контактного лица):", reply_markup=_SKIP, parse_mode="HTML")
    await cb.answer()


@router.message(KPStates.waiting_recipient_kuda)
async def set_kuda(message: Message, state: FSMContext):
    await state.update_data(kuda=message.text.strip())
    await state.set_state(KPStates.waiting_recipient_komu)
    await message.answer("<b>Кому</b> (ФИО контактного лица):", reply_markup=_SKIP, parse_mode="HTML")


@router.callback_query(F.data == "kp_skip", KPStates.waiting_recipient_komu)
async def skip_komu(cb: CallbackQuery, state: FSMContext):
    await state.update_data(komu="")
    await state.set_state(KPStates.waiting_recipient_tel)
    await cb.message.answer("<b>Телефон</b> получателя:", reply_markup=_SKIP, parse_mode="HTML")
    await cb.answer()


@router.message(KPStates.waiting_recipient_komu)
async def set_komu(message: Message, state: FSMContext):
    await state.update_data(komu=message.text.strip())
    await state.set_state(KPStates.waiting_recipient_tel)
    await message.answer("<b>Телефон</b> получателя:", reply_markup=_SKIP, parse_mode="HTML")


@router.callback_query(F.data == "kp_skip", KPStates.waiting_recipient_tel)
async def skip_tel(cb: CallbackQuery, state: FSMContext):
    await state.update_data(tel="")
    await state.set_state(KPStates.waiting_vat_mode)
    await cb.message.answer("Режим НДС:", reply_markup=_VAT)
    await cb.answer()


@router.message(KPStates.waiting_recipient_tel)
async def set_tel(message: Message, state: FSMContext):
    await state.update_data(tel=message.text.strip())
    await state.set_state(KPStates.waiting_vat_mode)
    await message.answer("Режим НДС:", reply_markup=_VAT)


@router.callback_query(F.data.startswith("kp_vat_"), KPStates.waiting_vat_mode)
async def set_vat(cb: CallbackQuery, state: FSMContext):
    await state.update_data(vat_mode=cb.data.replace("kp_vat_", ""))
    await state.set_state(KPStates.waiting_delivery_time)
    await cb.message.answer("Срок поставки (общий, напр. «15 к.д.» или «По заявкам»):")
    await cb.answer()


@router.message(KPStates.waiting_delivery_time)
async def set_delivery(message: Message, state: FSMContext):
    await state.update_data(delivery_time=message.text.strip())
    await state.set_state(KPStates.waiting_item_name)
    await message.answer("Позиция 1.\n<b>Наименование</b> (как у заказчика):", parse_mode="HTML")


@router.message(KPStates.waiting_item_name)
async def item_name(message: Message, state: FSMContext):
    await state.update_data(cur_name=message.text.strip())
    await state.set_state(KPStates.waiting_item_proposed)
    await message.answer("<b>Предлагаемое наименование</b> (ваш товар):", parse_mode="HTML")


@router.message(KPStates.waiting_item_proposed)
async def item_proposed(message: Message, state: FSMContext):
    await state.update_data(cur_proposed=message.text.strip())
    await state.set_state(KPStates.waiting_item_unit)
    await message.answer("<b>Ед. изм.</b>:", reply_markup=_UNITS, parse_mode="HTML")


@router.callback_query(F.data.startswith("kp_unit_"), KPStates.waiting_item_unit)
async def item_unit_cb(cb: CallbackQuery, state: FSMContext):
    await state.update_data(cur_unit=cb.data.replace("kp_unit_", ""))
    await state.set_state(KPStates.waiting_item_qty)
    await cb.message.answer("<b>Кол-во</b>:", parse_mode="HTML")
    await cb.answer()


@router.message(KPStates.waiting_item_unit)
async def item_unit_text(message: Message, state: FSMContext):
    await state.update_data(cur_unit=message.text.strip())
    await state.set_state(KPStates.waiting_item_qty)
    await message.answer("<b>Кол-во</b>:", parse_mode="HTML")


@router.message(KPStates.waiting_item_qty)
async def item_qty(message: Message, state: FSMContext):
    try:
        qty = parse_qty(message.text)
    except ValueError as e:
        await message.answer(f"⚠️ {e}. Введите число больше нуля:")
        return
    await state.update_data(cur_qty=str(qty))
    await state.set_state(KPStates.waiting_item_price)
    await message.answer("<b>Цена за ед.</b> (RUB):", parse_mode="HTML")


@router.message(KPStates.waiting_item_price)
async def item_price(message: Message, state: FSMContext):
    try:
        price = parse_price(message.text)
    except ValueError as e:
        await message.answer(f"⚠️ {e}. Введите цену числом:")
        return
    data = await state.get_data()
    items = data.get("items", [])
    items.append({
        "name": data["cur_name"], "proposed_name": data["cur_proposed"],
        "unit": data["cur_unit"], "qty": data["cur_qty"], "price": str(price),
        "delivery": data.get("delivery_time", ""),
    })
    await state.update_data(items=items)
    await state.set_state(KPStates.confirming)
    await message.answer(f"Позиция добавлена ({len(items)}).", reply_markup=_AFTER_ITEM)


@router.callback_query(F.data == "kp_more", KPStates.confirming)
async def add_more(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.set_state(KPStates.waiting_item_name)
    await cb.message.answer(f"Позиция {len(data.get('items', [])) + 1}.\n<b>Наименование</b>:", parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "kp_done", KPStates.confirming)
async def done_items(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    items = _items_from_state(data.get("items", []))
    if not items:
        await cb.answer("Нет позиций", show_alert=True)
        return
    await cb.message.answer(build_preview_text(items, data.get("vat_mode", "none")),
                            reply_markup=_CONFIRM, parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "kp_restart", KPStates.confirming)
async def restart(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.answer("Начнём заново: /kp")
    await cb.answer()


@router.callback_query(F.data == "kp_generate", KPStates.confirming)
async def generate(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    items = _items_from_state(data.get("items", []))
    db = get_sniper_db()
    profile = await db.get_company_profile_by_telegram_id(cb.from_user.id)
    user_id = profile["user_id"] if profile and "user_id" in profile else None
    if user_id is None:
        user = await db.get_or_create_user(cb.from_user.id)  # существующий метод получения sniper user
        user_id = user["id"] if isinstance(user, dict) else user.id
    recipient = {"kuda": data.get("kuda", ""), "komu": data.get("komu", ""), "tel": data.get("tel", "")}
    try:
        pdf_bytes, number = await KPService(db).create(
            user_id, recipient, data.get("vat_mode", "none"),
            data.get("delivery_time", ""), items)
    except Exception as e:
        logger.error("KP generation failed: %s", e, exc_info=True)
        await cb.message.answer("⚠️ Не удалось собрать PDF. Попробуйте ещё раз: /kp")
        await cb.answer()
        return
    await cb.message.answer_document(
        BufferedInputFile(pdf_bytes, filename=f"КП_{number}.pdf"),
        caption=f"Готово: КП № {number}")
    await state.clear()
    await cb.answer()
```
Примечание для реализатора: `db.get_company_profile_by_telegram_id` возвращает dict профиля; в нём поле связи с `sniper_users.id`. Свериться с фактическим ключом (`user_id`) в реализации адаптера (Task 4/существующий код) и, при необходимости, получить `user_id` через существующий метод получения sniper-пользователя по `telegram_id` (см. как это делает `company_profile.py`). Использовать реальные имена методов из адаптера.

- [ ] **Step 5: Зарегистрировать роутер**

В `bot/main.py` после строки `dp.include_router(company_profile.router)` (~405) добавить:
```python
    from bot.handlers import kp_generator
    dp.include_router(kp_generator.router)  # Генератор КП (/kp)
```
(и/или добавить `kp_generator` в существующий импорт `from bot.handlers import ...` вверху файла, по образцу `company_profile`.)

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_kp_handler_helpers.py -v`
Expected: PASS (1 passed)

- [ ] **Step 7: Commit**

```bash
git add bot/states/__init__.py bot/handlers/kp_generator.py bot/main.py tests/unit/test_kp_handler_helpers.py
git commit -m "feat(kp): Telegram-мастер /kp (FSM) + регистрация роутера"
```

---

### Task 7: Ручной smoke-тест и настройка профиля владельца

**Files:**
- (без изменений кода — верификация)

- [ ] **Step 1: Прогнать все юнит-тесты КП**

Run: `python -m pytest tests/unit/test_kp_calc.py tests/unit/test_kp_renderer.py tests/unit/test_kp_models.py tests/unit/test_kp_service.py tests/unit/test_kp_handler_helpers.py -v`
Expected: все PASS.

- [ ] **Step 2: Применить миграцию на dev/боевой БД**

Run: `alembic upgrade head`
Expected: миграция `20260717_kp` применяется без ошибок (`alembic current` показывает её головой).

- [ ] **Step 3: Заполнить КП-поля профиля владельца**

Одноразово выставить в `company_profiles` для пользователя-владельца: `kp_number_prefix='ИПХИС'`, `kp_counter` (последний использованный номер), `kp_default_payment_terms`, `kp_default_delivery_terms`, `kp_signer_name`. Проверить, что основные реквизиты (`company_name`, `inn`, `legal_address`, `phone`, `email`) заполнены (иначе через `/profile`).

- [ ] **Step 4: Smoke-тест в Telegram**

Отправить боту `/kp`, пройти мастер (пропустить получателя → без НДС → «15 к.д.» → 2 позиции по образцу Пенза/Ламода → предпросмотр → сгенерировать). Сверить полученный PDF с образцом `Коммерческое_предложение_бумага Пенза.pdf`: шапка, номер, таблица, «Всего без НДС», подпись.

- [ ] **Step 5: Финальный commit (если правились дефолты/доки)**

```bash
git add -A
git commit -m "chore(kp): smoke-тест и настройка КП-полей профиля"
```

---

## Self-Review

- **Spec coverage:** §3 шаблон → Task 2 (html/css); §4 FSM-поток → Task 6; §5.1 поля профиля → Task 3; §5.2 модель → Task 3; §6 рендер → Task 2 + Task 5; §7 ошибки (профиль/рендер) → Task 6 (проверка профиля, try/except); §8 тесты → Tasks 1,2,3,4,5,6 + Task 7 smoke. Всё покрыто.
- **НДС оба режима:** calc (Task 1), шаблон (Task 2), предпросмотр (Task 6). ОК.
- **Открытый вопрос из спеки (номер КП):** решено — автосчётчик `next_kp_number` (Task 4). Ручное редактирование номера — вне MVP (кандидат v2).
- **Placeholder scan:** явных TBD/TODO нет; в Task 4/6 есть заметки «свериться с фактическим именем метода адаптера» — это намеренные точки сверки с существующим кодом, не заглушки логики.
- **Type consistency:** `render_kp_pdf(KPData)->bytes`, `compute_totals(items, vat_mode)->Totals`, `KPService.create(...)->(bytes,str)`, `build_preview_text(items, vat_mode)->str` — согласованы между задачами.
