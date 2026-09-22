"""Разбор срока подачи в save_notification.

Функция принимает срок «в каком дали» и перебирает форматы. Пока
перебор ловил только ValueError/TypeError, любой неожиданный ТИП ронял
её целиком: parsedate_to_datetime зовёт .split() у чего угодно и кидает
AttributeError. Замер 22.09.2026 — общий пул ЕИС передавал datetime из
своей таблицы, и ни один цикл рассылки не доходил до конца.
"""
from datetime import datetime

import pytest


def parse_deadline(value):
    """Повторяет блок разбора из sqlalchemy_adapter.save_notification."""
    deadline_str = value
    submission_deadline = None
    if isinstance(deadline_str, datetime):
        submission_deadline = deadline_str
        deadline_str = None
    try:
        submission_deadline = datetime.fromisoformat(deadline_str)
    except (ValueError, TypeError, AttributeError):
        try:
            from email.utils import parsedate_to_datetime
            submission_deadline = parsedate_to_datetime(deadline_str)
        except (ValueError, TypeError, AttributeError):
            for fmt in ['%d.%m.%Y', '%Y-%m-%d', '%d.%m.%Y %H:%M', '%Y-%m-%d %H:%M']:
                try:
                    submission_deadline = datetime.strptime(deadline_str, fmt)
                    break
                except (ValueError, TypeError):
                    continue
    if submission_deadline and submission_deadline.tzinfo is not None:
        submission_deadline = submission_deadline.replace(tzinfo=None)
    return submission_deadline


@pytest.mark.unit
class TestParseDeadline:
    def test_datetime_passes_through(self):
        """Общий пул хранит срок колонкой timestamp и отдаёт datetime."""
        value = datetime(2026, 9, 30, 8, 0)
        assert parse_deadline(value) == value

    def test_iso_string(self):
        assert parse_deadline('2026-09-30T08:00:00') == datetime(2026, 9, 30, 8, 0)

    def test_russian_date(self):
        assert parse_deadline('30.09.2026') == datetime(2026, 9, 30)

    def test_timezone_is_dropped(self):
        got = parse_deadline('2026-09-30T08:00:00+03:00')
        assert got.tzinfo is None and got.hour == 8

    def test_garbage_gives_none_without_raising(self):
        """Неразобранный срок — это отсутствующий срок, а не падение
        всей рассылки."""
        assert parse_deadline('когда-нибудь') is None

    def test_unexpected_type_gives_none_without_raising(self):
        for value in (12345, [2026, 9, 30], {'d': 1}, object()):
            assert parse_deadline(value) is None
