"""Выжимка AI-анализа карточки.

Кнопка «Запустить AI-анализ» вызывала несуществующую функцию
(`check_relevance` вместо `check_tender_relevance`), ошибка глоталась,
карточка помечалась «анализ готов» с пустым содержимым, а квота при этом
списывалась. Здесь закрепляется поведение уже рабочего разбора.
"""
import inspect

import pytest

from cabinet.pipeline_service import _do_ai_enrich, _format_ai_summary


@pytest.mark.unit
class TestFormatAiSummary:
    def test_renders_extracted_conditions(self):
        out = _format_ai_summary({
            'items_description': 'Бумага А4, 200 пачек',
            'execution_deadline': '30 дней',
            'application_security': '1% (1 800 руб)',
        })
        assert 'Предмет: Бумага А4, 200 пачек' in out
        assert 'Срок исполнения: 30 дней' in out
        assert 'Обеспечение заявки: 1% (1 800 руб)' in out

    def test_omits_fields_the_extractor_could_not_fill(self):
        """«Требования: не указаны» читается как ответ, хотя означает лишь,
        что данных не нашли. Такие строки не выводим вовсе — именно из-за
        них анализ выглядел бесполезным."""
        out = _format_ai_summary({
            'items_description': 'Бумага А4',
            'licenses_required': 'Не указано',
            'advance_percent': '',
            'payment_deadline': None,
        })
        assert 'Предмет: Бумага А4' in out
        assert 'Лицензии' not in out
        assert 'Аванс' not in out
        assert 'Срок оплаты' not in out

    def test_nothing_extracted_gives_empty_string(self):
        assert _format_ai_summary({}) == ''
        assert _format_ai_summary({'items_description': 'Не указано'}) == ''

    def test_line_per_field(self):
        out = _format_ai_summary({'items_description': 'A', 'execution_deadline': 'B'})
        assert out.count('\n') == 1


@pytest.mark.unit
class TestEnrichWiring:
    def test_no_longer_calls_the_missing_function(self):
        """Регрессия: `check_relevance` в ai_relevance_checker не существует —
        есть только `check_tender_relevance`. Вызов падал в except.

        Ищем именно вызовы и импорты, а не упоминания: в докстроке функции
        эти имена названы намеренно, чтобы объяснить, что было сломано.
        """
        src = inspect.getsource(_do_ai_enrich)
        assert 'await check_relevance(' not in src
        assert 'await summarize_tender(' not in src
        assert 'from tender_sniper.ai_relevance_checker import check_relevance' not in src
        assert 'from tender_sniper.ai_summarizer import' not in src

    def test_uses_the_documentation_pipeline(self):
        """Анализ должен идти от текста документации, а не от названия."""
        src = inspect.getsource(_do_ai_enrich)
        assert 'get_full_tz_text' in src
        assert 'TenderDocumentExtractor' in src

    def test_refunds_quota_when_there_is_nothing_to_analyse(self):
        src = inspect.getsource(_do_ai_enrich)
        assert '_refund_ai_quota' in src

    def test_says_so_when_documentation_is_unavailable(self):
        """Если достать документацию не удалось, надо сказать прямо, а не
        выдавать пересказ названия за разбор."""
        src = inspect.getsource(_do_ai_enrich)
        assert 'name_only' in src
        assert 'недоступна' in src
