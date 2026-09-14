"""Расчёт экономики сделки (cabinet/pipeline_service.calc_margin).

Раньше маржа была «наша цена − закупочная» и завышала прибыль: не
учитывались логистика, доп. расходы и налог. Здесь закрепляем новую
формулу на конкретных числах, чтобы она не «поехала» незаметно.
"""

import pytest

from cabinet.pipeline_service import calc_margin, DEFAULT_TAX_RATE


@pytest.mark.unit
class TestCalcMargin:
    def test_none_when_no_sale_price(self):
        assert calc_margin(purchase=1000, sale=None) is None
        assert calc_margin(purchase=1000, sale=0) is None

    def test_full_formula(self):
        # Цена 100 000, закупка 60 000, логистика 5 000, доп. 2 000, налог 7%
        # налог = 7 000; затраты = 67 000; чистая = 100 000 − 67 000 − 7 000 = 26 000
        m = calc_margin(purchase=60000, sale=100000, extra_costs=2000,
                        logistics_cost=5000, tax_rate=7)
        assert m['tax'] == pytest.approx(7000)
        assert m['costs_total'] == pytest.approx(67000)
        assert m['abs'] == pytest.approx(26000)
        assert m['pct'] == pytest.approx(26.0)
        assert m['color'] == 'positive'

    def test_tax_is_on_revenue_not_profit(self):
        """УСН «доходы»: налог считается с цены, а не с прибыли, поэтому
        рост затрат налог НЕ уменьшает."""
        low_costs = calc_margin(purchase=10000, sale=100000, tax_rate=7)
        high_costs = calc_margin(purchase=90000, sale=100000, tax_rate=7)
        assert low_costs['tax'] == high_costs['tax'] == pytest.approx(7000)

    def test_costs_default_to_zero(self):
        m = calc_margin(purchase=60000, sale=100000, tax_rate=7)
        assert m['extra_costs'] == 0
        assert m['logistics_cost'] == 0
        assert m['abs'] == pytest.approx(33000)  # 100k − 60k − 7k

    def test_default_tax_rate_applied_when_absent(self):
        m = calc_margin(purchase=50000, sale=100000)
        assert m['tax_rate'] == DEFAULT_TAX_RATE
        assert m['tax'] == pytest.approx(100000 * DEFAULT_TAX_RATE / 100)

    def test_negative_profit_flags_alert(self):
        # Закупка выше нашей цены — уходим в минус
        m = calc_margin(purchase=100000, sale=90000, tax_rate=7)
        assert m['abs'] < 0
        assert m['color'] == 'alert'

    def test_thin_margin_flags_warn(self):
        # Прибыль положительная, но меньше 5% — предупреждение
        m = calc_margin(purchase=90000, sale=100000, tax_rate=7)
        assert 0 <= m['pct'] < 5
        assert m['color'] == 'warn'

    def test_discount_from_nmck(self):
        """Снижение от НМЦК — на сколько уступили от начальной цены."""
        m = calc_margin(purchase=50000, sale=80000, tax_rate=7, price_max=100000)
        assert m['discount_abs'] == pytest.approx(20000)
        assert m['discount_pct'] == pytest.approx(20.0)

    def test_no_nmck_means_no_discount_fields(self):
        m = calc_margin(purchase=50000, sale=80000, tax_rate=7)
        assert m['discount_abs'] is None
        assert m['discount_pct'] is None

    def test_nmck_does_not_affect_profit(self):
        """НМЦК — справочная величина: на прибыль влиять не должна."""
        without = calc_margin(purchase=50000, sale=80000, tax_rate=7)
        with_nmck = calc_margin(purchase=50000, sale=80000, tax_rate=7, price_max=999999)
        assert without['abs'] == with_nmck['abs']
