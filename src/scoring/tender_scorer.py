"""
Система скоринга тендеров для принятия решения об участии.
"""

from typing import Dict, Any, List


class TenderScorer:
    """Класс для оценки тендера по множественным критериям."""

    def __init__(self, scoring_weights: Dict[str, float], thresholds: Dict[str, float]):
        """
        Инициализация скоринг-системы.

        Args:
            scoring_weights: Веса критериев из конфигурации
            thresholds: Пороговые значения для принятия решений
        """
        self.weights = scoring_weights
        self.thresholds = thresholds

    def calculate_technical_fit(
        self,
        requirements: Dict[str, List[str]],
        capabilities: Dict[str, List[str]]
    ) -> float:
        """
        Оценка технического соответствия (0-100).

        Args:
            requirements: Требования тендера
            capabilities: Возможности компании

        Returns:
            Балл технического соответствия
        """
        # Упрощенная оценка: проверяем пересечения категорий
        company_categories = set(cap.lower() for cap in capabilities.get('categories', []))
        required_categories = set(req.lower() for req in requirements.get('technical', []))

        if not required_categories:
            return 80  # Если требования не указаны, считаем нормальным

        # Считаем совпадения
        matches = len(company_categories.intersection(required_categories))
        total_required = len(required_categories)

        if total_required == 0:
            return 80

        match_percent = (matches / total_required) * 100
        return min(match_percent, 100)

    def calculate_financial_attractiveness(
        self,
        financial_analysis: Dict[str, Any]
    ) -> float:
        """
        Оценка финансовой привлекательности (0-100).
        Использует результаты FinancialCalculator.

        Args:
            financial_analysis: Результат полного финансового анализа

        Returns:
            Балл финансовой привлекательности
        """
        return financial_analysis.get('financial_attractiveness_score', 50)

    def calculate_information_completeness(
        self,
        gaps: List[Dict[str, Any]]
    ) -> float:
        """
        Оценка полноты информации (0-100).

        Args:
            gaps: Список выявленных пробелов

        Returns:
            Балл полноты информации
        """
        if not gaps:
            return 100

        # Подсчитываем пробелы по критичности
        critical_count = sum(1 for g in gaps if g.get('criticality') == 'CRITICAL')
        high_count = sum(1 for g in gaps if g.get('criticality') == 'HIGH')
        medium_count = sum(1 for g in gaps if g.get('criticality') == 'MEDIUM')
        low_count = sum(1 for g in gaps if g.get('criticality') == 'LOW')

        # Вычисляем штрафы
        penalty = (
            critical_count * 20 +  # Критичный пробел = -20 баллов
            high_count * 10 +      # Важный пробел = -10 баллов
            medium_count * 5 +     # Средний пробел = -5 баллов
            low_count * 2          # Низкий пробел = -2 балла
        )

        score = max(100 - penalty, 0)
        return score

    def calculate_competence_match(
        self,
        tender_info: Dict[str, Any],
        capabilities: Dict[str, Any]
    ) -> float:
        """
        Оценка соответствия компетенциям (0-100).

        Args:
            tender_info: Информация о тендере
            capabilities: Возможности компании

        Returns:
            Балл соответствия компетенциям
        """
        score = 70  # Базовая оценка

        # Проверяем наличие сертификатов
        if 'certificates' in capabilities:
            score += 15

        # Проверяем региональное соответствие
        if 'regions' in capabilities:
            score += 15

        return min(score, 100)

    def calculate_total_score(
        self,
        technical_fit: float,
        financial_attractiveness: float,
        information_completeness: float,
        competence_match: float
    ) -> float:
        """
        Вычисляет взвешенный итоговый балл.

        Returns:
            Итоговый балл (0-100)
        """
        total = (
            technical_fit * self.weights.get('technical_fit', 0.3) +
            financial_attractiveness * self.weights.get('financial_attractiveness', 0.3) +
            information_completeness * self.weights.get('information_completeness', 0.25) +
            competence_match * self.weights.get('competence_match', 0.15)
        )

        return round(total, 2)

    def calculate_readiness(
        self,
        gaps: List[Dict[str, Any]]
    ) -> float:
        """
        Вычисляет процент готовности к участию.

        Args:
            gaps: Список пробелов

        Returns:
            Процент готовности (0-100)
        """
        if not gaps:
            return 100

        critical_count = sum(1 for g in gaps if g.get('criticality') == 'CRITICAL')
        high_count = sum(1 for g in gaps if g.get('criticality') == 'HIGH')

        # Если есть критичные пробелы, готовность низкая
        if critical_count > 0:
            readiness = max(100 - (critical_count * 30 + high_count * 10), 20)
        else:
            readiness = max(100 - (high_count * 15), 50)

        return round(readiness, 1)

    def get_recommendation(
        self,
        total_score: float,
        readiness: float,
        critical_gaps_count: int
    ) -> str:
        """
        Дает рекомендацию по участию в тендере.

        Returns:
            Рекомендация: 'УЧАСТВОВАТЬ', 'УТОЧНИТЬ', 'ОТКАЗАТЬСЯ'
        """
        min_score = self.thresholds.get('min_total_score', 60)
        max_critical = self.thresholds.get('max_critical_gaps', 2)
        min_readiness = self.thresholds.get('min_readiness_percent', 70)

        if critical_gaps_count > max_critical:
            return 'ОТКАЗАТЬСЯ'

        if total_score >= min_score and readiness >= min_readiness:
            return 'УЧАСТВОВАТЬ'
        elif readiness >= 50:
            return 'УТОЧНИТЬ'
        else:
            return 'ОТКАЗАТЬСЯ'

    def generate_full_score(
        self,
        tender_info: Dict[str, Any],
        requirements: Dict[str, List[str]],
        capabilities: Dict[str, Any],
        financial_analysis: Dict[str, Any],
        gaps: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Генерирует полную оценку тендера.

        Returns:
            Полный словарь с оценками и рекомендацией
        """
        # Рассчитываем все компоненты
        technical = self.calculate_technical_fit(requirements, capabilities)
        financial = self.calculate_financial_attractiveness(financial_analysis)
        completeness = self.calculate_information_completeness(gaps)
        competence = self.calculate_competence_match(tender_info, capabilities)

        # Итоговый балл
        total_score = self.calculate_total_score(
            technical, financial, completeness, competence
        )

        # Готовность к участию
        readiness = self.calculate_readiness(gaps)

        # Подсчитываем пробелы по критичности
        gaps_by_criticality = {
            'critical': sum(1 for g in gaps if g.get('criticality') == 'CRITICAL'),
            'high': sum(1 for g in gaps if g.get('criticality') == 'HIGH'),
            'medium': sum(1 for g in gaps if g.get('criticality') == 'MEDIUM'),
            'low': sum(1 for g in gaps if g.get('criticality') == 'LOW')
        }

        # Рекомендация
        recommendation = self.get_recommendation(
            total_score,
            readiness,
            gaps_by_criticality['critical']
        )

        return {
            'scores': {
                'technical_fit': technical,
                'financial_attractiveness': financial,
                'information_completeness': completeness,
                'competence_match': competence,
                'total_score': total_score
            },
            'readiness_percent': readiness,
            'gaps_count': gaps_by_criticality,
            'total_gaps': len(gaps),
            'recommendation': recommendation,
            'recommendation_details': self._get_recommendation_details(
                recommendation, total_score, readiness, gaps_by_criticality
            )
        }

    def _get_recommendation_details(
        self,
        recommendation: str,
        total_score: float,
        readiness: float,
        gaps_count: Dict[str, int]
    ) -> str:
        """Генерирует детальное описание рекомендации."""
        if recommendation == 'УЧАСТВОВАТЬ':
            return (
                f"Тендер соответствует критериям компании. "
                f"Общая оценка: {total_score:.0f}/100, готовность: {readiness:.0f}%. "
                f"Рекомендуется подготовка заявки."
            )
        elif recommendation == 'УТОЧНИТЬ':
            return (
                f"Требуется уточнение информации у заказчика. "
                f"Выявлено пробелов: {gaps_count['critical']} критичных, "
                f"{gaps_count['high']} важных. "
                f"После получения ответов повторно оцените возможность участия."
            )
        else:
            return (
                f"Участие не рекомендуется. "
                f"Низкая оценка ({total_score:.0f}/100) или слишком много "
                f"критичных пробелов ({gaps_count['critical']}). "
                f"Риски превышают потенциальную выгоду."
            )


if __name__ == "__main__":
    # Пример использования
    weights = {
        'technical_fit': 0.30,
        'financial_attractiveness': 0.30,
        'information_completeness': 0.25,
        'competence_match': 0.15
    }

    thresholds = {
        'min_total_score': 60,
        'max_critical_gaps': 2,
        'min_readiness_percent': 70
    }

    scorer = TenderScorer(weights, thresholds)

    # Тестовые данные
    tender_info = {'name': 'Тестовый тендер'}
    requirements = {'technical': ['IT-оборудование', 'Разработка ПО']}
    capabilities = {
        'categories': ['IT-оборудование', 'Консалтинг'],
        'certificates': ['ISO 9001']
    }
    financial_analysis = {'financial_attractiveness_score': 75}
    gaps = [
        {'criticality': 'CRITICAL'},
        {'criticality': 'HIGH'},
        {'criticality': 'MEDIUM'}
    ]

    result = scorer.generate_full_score(
        tender_info, requirements, capabilities, financial_analysis, gaps
    )

    print("=== Результаты скоринга ===")
    print(f"Итоговый балл: {result['scores']['total_score']}")
    print(f"Готовность: {result['readiness_percent']}%")
    print(f"Рекомендация: {result['recommendation']}")
    print(f"Детали: {result['recommendation_details']}")
