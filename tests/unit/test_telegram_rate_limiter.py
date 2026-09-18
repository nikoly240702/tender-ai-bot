"""Ограничитель частоты отправки в Telegram.

Регрессия: обе паузы стояли внутри `async with self._lock`, а лок один на
процесс. Ожидание per-chat паузы (1.1 с) останавливало отправку во ВСЕ
остальные чаты — реальная пропускная способность выходила около 0.9
сообщения в секунду вместо 25, и рассылка растягивалась линейно по числу
получателей.
"""
import asyncio
import time

import pytest

from tender_sniper.notifications.telegram_notifier import _TelegramRateLimiter


@pytest.mark.unit
@pytest.mark.asyncio
class TestRateLimiter:
    async def test_first_message_to_a_chat_is_immediate(self):
        rl = _TelegramRateLimiter()
        t0 = time.monotonic()
        await rl.acquire(111)
        assert time.monotonic() - t0 < 0.05

    async def test_second_message_to_same_chat_waits(self):
        rl = _TelegramRateLimiter()
        rl.PER_CHAT_INTERVAL = 0.3
        await rl.acquire(111)
        t0 = time.monotonic()
        await rl.acquire(111)
        assert time.monotonic() - t0 >= 0.25

    async def test_a_waiting_chat_does_not_block_other_chats(self):
        """Главное свойство. Пока чат A отбывает свою паузу, сообщения в
        другие чаты должны уходить, а не стоять за ним в очереди."""
        rl = _TelegramRateLimiter()
        rl.PER_CHAT_INTERVAL = 0.4

        await rl.acquire(111)          # занимаем слот чата A

        done_at = {}

        async def send(chat_id, label):
            await rl.acquire(chat_id)
            done_at[label] = time.monotonic()

        t0 = time.monotonic()
        # A ждёт свою паузу; B и C к нему отношения не имеют.
        await asyncio.gather(send(111, 'A'), send(222, 'B'), send(333, 'C'))

        assert done_at['B'] - t0 < 0.2, 'чужой чат ждал чужую паузу'
        assert done_at['C'] - t0 < 0.2, 'чужой чат ждал чужую паузу'
        assert done_at['A'] - t0 >= 0.35, 'свою паузу чат A всё же обязан отбыть'

    async def test_global_rate_is_still_enforced(self):
        """Ослаблять глобальный лимит нельзя — за это Telegram банит бота."""
        rl = _TelegramRateLimiter()
        rl.GLOBAL_RATE = 10
        rl.PER_CHAT_INTERVAL = 0.0
        rl._tokens = 0.0               # bucket пуст, нужен хотя бы один токен
        rl._last_refill = time.monotonic()

        t0 = time.monotonic()
        await rl.acquire(999)
        assert time.monotonic() - t0 >= 0.05, 'пустой bucket не притормозил отправку'

    async def test_same_chat_messages_do_not_overlap(self):
        """Слот занимается под локом: две одновременные отправки в один чат
        не должны обе решить, что можно прямо сейчас."""
        rl = _TelegramRateLimiter()
        rl.PER_CHAT_INTERVAL = 0.3
        stamps = []

        async def send():
            await rl.acquire(555)
            stamps.append(time.monotonic())

        await asyncio.gather(send(), send())
        assert abs(stamps[0] - stamps[1]) >= 0.25

    async def test_chat_history_does_not_grow_without_bound(self):
        rl = _TelegramRateLimiter()
        rl.PER_CHAT_INTERVAL = 0.0
        rl._MAX_TRACKED_CHATS = 50
        for chat_id in range(200):
            await rl.acquire(chat_id)
        assert len(rl._per_chat_last) <= 60
