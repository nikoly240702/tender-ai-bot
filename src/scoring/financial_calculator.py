"""
Финансовый калькулятор для оценки тендеров.
"""

from typing import Dict, Any, Optional


class FinancialCalculator:
    """Класс для финансовых расчетов по тендеру."""

    def __init__(self, company_financial_config: Dict[str, Any]):
        """
        Инициализация калькулятора.

        Args:
            company_financial_config: Финансовая конфигурация из company_profile
        """
        self.config = company_financial_config

    def calculate_cost_estimate(
        self,
        nmck: float,
        labor_hours: Optional[int] = None
    ) -> Dict[str, float]:
        """
        Расчет предварительной себестоимости.

        Args:
            nmck: Начальная максимальная цена контракта
            labor_hours: Ожидаемые трудозатраты в часах (опционально)

        Returns:
            Словарь с расчетами
        """
        # Если трудозатраты не указаны, оцениваем как % от НМЦК
        if labor_hours is None:
            # Примерная оценка: 60% НМЦК идет на труд
            labor_cost = nmck * 0.6
        else:
            labor_cost_per_hour = self.config.get('cost_structure', {}).get('labor_cost_per_hour', 2000)
            labor_cost = labor_hours * labor_cost_per_hour

        # Накладные расходы
        overhead_coef = self.config.get('cost_structure', {}).get('overhead_coefficient', 1.3)
        total_cost = labor_cost * overhead_coef

        return {
            'labor_cost': labor_cost,
            'overhead_cost': labor_cost * (overhead_coef - 1),
            'total_cost': total_cost
        }

    def calculate_margin(self, nmck: float, estimated_cost: float) -> Dict[str, float]:
        """
        Расчет маржи и рентабельности.

        Args:
            nmck: НМЦК
            estimated_cost: Расчетная себестоимость

        Returns:
            Словарь с показателями маржи
        """
        margin_amount = nmck - estimated_cost
        margin_percent = (margin_amount / nmck * 100) if nmck > 0 else 0
        roi = (margin_amount / estimated_cost * 100) if estimated_cost > 0 else 0

        target_margin = self.config.get('cost_structure', {}).get('profit_margin_target', 0.25)
        target_margin_amount = nmck * target_margin

        return {
            'margin_amount': margin_amount,
            'margin_percent': margin_percent,
            'roi': roi,
            'target_margin_percent': target_margin * 100,
            'meets_target': margin_percent >= (target_margin * 100)
        }

    def calculate_guarantees(self, nmck: float) -> Dict[str, float]:
        """
        Расчет обеспечения заявки и контракта.

        Args:
            nmck: НМЦК

        Returns:
            Словарь с суммами обеспечения
        """
        guarantee_app_rate = self.config.get('guarantee_application', 0.02)
        guarantee_contract_rate = self.config.get('guarantee_contract', 0.05)

        return {
            'application_guarantee': nmck * guarantee_app_rate,
            'contract_guarantee': nmck * guarantee_contract_rate,
            'total_guarantee_needed': nmck * (guarantee_app_rate + guarantee_contract_rate)
        }

    def check_financial_limits(self, nmck: float) -> Dict[str, Any]:
        """
        Проверка соответствия финансовым лимитам компании.

        Args:
            nmck: НМЦК

        Returns:
            Результаты проверки
        """
        min_amount = self.config.get('min_contract_amount', 0)
        max_amount = self.config.get('max_contract_amount', float('inf'))

        within_limits = min_amount <= nmck <= max_amount

        return {
            'within_limits': within_limits,
            'nmck': nmck,
            'min_limit': min_amount,
            'max_limit': max_amount,
            'reason': self._get_limit_reason(nmck, min_amount, max_amount)
        }

    def _get_limit_reason(self, nmck: float, min_limit: float, max_limit: float) -> str:
        """Возвращает причину несоответствия лимитам."""
        if nmck < min_limit:
            return f"Сумма контракта ниже минимального лимита ({min_limit:,.0f} руб.)"
        elif nmck > max_limit:
            return f"Сумма контракта выше максимального лимита ({max_limit:,.0f} руб.)"
        return "Соответствует лимитам"

    def calculate_full_financial_analysis(
        self,
        nmck: float,
        labor_hours: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Полный финансовый анализ тендера.

        Returns:
            Полный словарь с финансовым анализом
        """
        cost_estimate = self.calculate_cost_estimate(nmck, labor_hours)
        margin = self.calculate_margin(nmck, cost_estimate['total_cost'])
        guarantees = self.calculate_guarantees(nmck)
        limits_check = self.check_financial_limits(nmck)

        # Общая оценка финансовой привлекательности (0-100)
        attractiveness_score = 0

        # Маржа (40 баллов)
        if margin['margin_percent'] >= 30:
            attractiveness_score += 40
        elif margin['margin_percent'] >= 20:
            attractiveness_score += 30
        elif margin['margin_percent'] >= 15:
            attractiveness_score += 20
        elif margin['margin_percent'] >= 10:
            attractiveness_score += 10

        # Соответствие лимитам (30 баллов)
        if limits_check['within_limits']:
            attractiveness_score += 30

        # ROI (30 баллов)
        if margin['roi'] >= 50:
            attractiveness_score += 30
        elif margin['roi'] >= 30:
            attractiveness_score += 20
        elif margin['roi'] >= 15:
            attractiveness_score += 10

        return {
            'nmck': nmck,
            'cost_estimate': cost_estimate,
            'margin': margin,
            'guarantees': guarantees,
            'limits_check': limits_check,
            'financial_attractiveness_score': attractiveness_score,
            'is_profitable': margin['margin_percent'] >= self.config.get('cost_structure', {}).get('profit_margin_target', 0.15) * 100
        }


if __name__ == "__main__":
    # Пример использования
    config = {
        'cost_structure': {
            'labor_cost_per_hour': 2000,
            'overhead_coefficient': 1.3,
            'profit_margin_target': 0.25
        },
        'min_contract_amount': 100000,
        'max_contract_amount': 50000000,
        'guarantee_application': 0.02,
        'guarantee_contract': 0.05
    }

    calc = FinancialCalculator(config)
    analysis = calc.calculate_full_financial_analysis(nmck=5000000, labor_hours=1000)

    print("=== Финансовый анализ ===")
    print(f"НМЦК: {analysis['nmck']:,.0f} руб.")
    print(f"Себестоимость: {analysis['cost_estimate']['total_cost']:,.0f} руб.")
    print(f"Маржа: {analysis['margin']['margin_amount']:,.0f} руб. ({analysis['margin']['margin_percent']:.1f}%)")
    print(f"ROI: {analysis['margin']['roi']:.1f}%")
    print(f"Обеспечение заявки: {analysis['guarantees']['application_guarantee']:,.0f} руб.")
    print(f"Оценка привлекательности: {analysis['financial_attractiveness_score']}/100")
