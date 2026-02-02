"""
AI Keyword Recommender - умные рекомендации ключевых слов.

Предлагает связанные термины при создании фильтра (Premium функция).
"""

import logging
from typing import List, Dict, Any, Optional

from tender_sniper.ai_features import AIFeatureGate, has_ai_access

logger = logging.getLogger(__name__)


# Статический словарь рекомендаций (fallback без API)
KEYWORD_RECOMMENDATIONS = {
    # IT оборудование
    'сервер': ['серверное оборудование', 'blade-сервер', 'стоечный сервер', 'СХД', 'ИБП'],
    'серверы': ['серверное оборудование', 'blade-сервер', 'стоечный сервер', 'СХД', 'ИБП'],
    'компьютер': ['ноутбук', 'моноблок', 'рабочая станция', 'ПК', 'монитор'],
    'ноутбук': ['компьютер', 'ультрабук', 'лэптоп', 'трансформер'],
    'схд': ['система хранения данных', 'СХД', 'дисковый массив', 'NAS', 'SAN'],
    'ибп': ['источник бесперебойного питания', 'UPS', 'ИБП', 'бесперебойник'],

    # ПО
    'linux': ['линукс', 'astra linux', 'альт линукс', 'ubuntu', 'centos', 'ос'],
    'windows': ['виндовс', 'microsoft windows', 'операционная система', 'ОС'],
    'антивирус': ['kaspersky', 'касперский', 'dr.web', 'eset', 'защита информации'],
    '1с': ['1c', 'бухгалтерия', 'erp', 'автоматизация'],

    # Сети
    'коммутатор': ['switch', 'свитч', 'сетевое оборудование', 'маршрутизатор'],
    'маршрутизатор': ['router', 'роутер', 'сетевое оборудование', 'коммутатор'],
    'firewall': ['межсетевой экран', 'брандмауэр', 'utm', 'ngfw'],

    # Безопасность
    'видеонаблюдение': ['камера', 'CCTV', 'DVR', 'NVR', 'регистратор'],
    'скуд': ['контроль доступа', 'турникет', 'домофон', 'биометрия'],
    'сигнализация': ['охранная сигнализация', 'ОПС', 'датчики', 'пожарная'],

    # Офис
    'мебель': ['офисная мебель', 'столы', 'стулья', 'шкафы', 'кресла'],
    'канцелярия': ['канцтовары', 'бумага', 'офисные принадлежности'],
    'принтер': ['МФУ', 'сканер', 'копир', 'печатающее устройство'],

    # Строительство
    'ремонт': ['капитальный ремонт', 'текущий ремонт', 'отделка', 'реконструкция'],
    'строительство': ['СМР', 'возведение', 'монтаж', 'благоустройство'],

    # Медицина
    'медицинское оборудование': ['медтехника', 'диагностическое оборудование', 'УЗИ', 'рентген', 'томограф'],
    'лекарства': ['медикаменты', 'препараты', 'фармацевтика', 'лекарственные средства'],

    # Транспорт
    'автомобиль': ['транспорт', 'машина', 'автотранспорт', 'спецтехника'],
    'автобус': ['пассажирский транспорт', 'маршрутка', 'микроавтобус'],
}


async def get_keyword_recommendations(
    keywords: List[str],
    subscription_tier: str = 'trial',
    use_ai: bool = True
) -> Dict[str, Any]:
    """
    Получает рекомендации ключевых слов.

    Args:
        keywords: Введённые пользователем ключевые слова
        subscription_tier: Тариф пользователя
        use_ai: Использовать AI (если доступен)

    Returns:
        Dict с рекомендациями:
        {
            'recommendations': ['слово1', 'слово2', ...],
            'source': 'ai' | 'static',
            'is_premium': bool
        }
    """
    gate = AIFeatureGate(subscription_tier)

    if not gate.can_use('keyword_recommendations'):
        # Не Premium - возвращаем только статические рекомендации
        recommendations = _get_static_recommendations(keywords)
        return {
            'recommendations': recommendations[:5],  # Лимит для non-premium
            'source': 'static',
            'is_premium': False,
            'upgrade_hint': 'Получите больше рекомендаций с Premium!'
        }

    # Premium пользователь
    if use_ai:
        try:
            # Пробуем AI рекомендации
            from tender_sniper.query_expander import QueryExpander

            expander = QueryExpander()
            expanded = await expander.expand_keywords(keywords)

            recommendations = []
            recommendations.extend(expanded.get('synonyms', []))
            recommendations.extend(expanded.get('related_terms', []))

            # Убираем дубликаты и исходные слова
            keywords_lower = {k.lower() for k in keywords}
            recommendations = [
                r for r in recommendations
                if r.lower() not in keywords_lower
            ]

            if recommendations:
                return {
                    'recommendations': list(set(recommendations))[:15],
                    'source': 'ai',
                    'is_premium': True
                }

        except Exception as e:
            logger.warning(f"AI рекомендации недоступны: {e}")

    # Fallback на статические рекомендации
    recommendations = _get_static_recommendations(keywords)
    return {
        'recommendations': recommendations[:15],
        'source': 'static',
        'is_premium': True
    }


def _get_static_recommendations(keywords: List[str]) -> List[str]:
    """
    Получает статические рекомендации из словаря.

    Args:
        keywords: Ключевые слова пользователя

    Returns:
        Список рекомендуемых слов
    """
    recommendations = []
    keywords_lower = {k.lower() for k in keywords}

    for keyword in keywords:
        keyword_lower = keyword.lower()

        # Прямое совпадение
        if keyword_lower in KEYWORD_RECOMMENDATIONS:
            for rec in KEYWORD_RECOMMENDATIONS[keyword_lower]:
                if rec.lower() not in keywords_lower:
                    recommendations.append(rec)

        # Частичное совпадение (ищем в ключах словаря)
        for key, values in KEYWORD_RECOMMENDATIONS.items():
            if key in keyword_lower or keyword_lower in key:
                for rec in values:
                    if rec.lower() not in keywords_lower:
                        recommendations.append(rec)

    # Убираем дубликаты, сохраняя порядок
    seen = set()
    unique_recommendations = []
    for rec in recommendations:
        if rec.lower() not in seen:
            seen.add(rec.lower())
            unique_recommendations.append(rec)

    return unique_recommendations


def format_recommendations_message(
    recommendations: Dict[str, Any],
    original_keywords: List[str]
) -> str:
    """
    Форматирует сообщение с рекомендациями для Telegram.

    Args:
        recommendations: Результат get_keyword_recommendations
        original_keywords: Исходные ключевые слова

    Returns:
        Отформатированное сообщение
    """
    recs = recommendations.get('recommendations', [])

    if not recs:
        return ""

    message = f"\n\n💡 <b>Рекомендуем добавить:</b>\n"

    for i, rec in enumerate(recs[:8], 1):
        message += f"• {rec}\n"

    if not recommendations.get('is_premium'):
        message += f"\n<i>⭐ Premium: больше рекомендаций</i>"

    return message
