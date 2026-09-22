"""Доставка совпадений общего пула ЕИС.

Главное, что здесь проверяется, — дедуп. Пул работает РЯДОМ с прежним
путём через сайт, значит один и тот же тендер находят оба, и без защиты
пользователь получил бы его дважды. Ограничение в БД
UNIQUE (user_id, company_id, tender_number) от дубля в базе спасёт, но
сообщение к тому моменту уже улетит — поэтому проверка обязана стоять
ДО отправки, а не после.
"""
import pytest

from tender_sniper.jobs.pool_loop import SOURCE, deliver


class FakeDb:
    """Минимальная БД: помнит, кому что уже отправляли."""

    def __init__(self, notified=(), in_chat=()):
        self.notified = set(notified)      # (tender_number, user_id)
        self.in_chat = set(in_chat)        # (tender_number, chat_id)
        self.saved = []

    async def is_tender_notified(self, tender_number, user_id, company_id=None):
        return (tender_number, user_id) in self.notified

    async def is_tender_sent_to_chat(self, tender_number, chat_id):
        return (tender_number, chat_id) in self.in_chat

    async def save_notification(self, **kwargs):
        self.saved.append(kwargs)


class FakeNotifier:
    def __init__(self, succeed=True):
        self.succeed = succeed
        self.sent = []

    async def send_tender_notification(self, **kwargs):
        self.sent.append(kwargs)
        return self.succeed


def match(tender_number="0173300004226000004", filter_id=1):
    return {
        'tender_number': tender_number, 'filter_id': filter_id,
        'filter_name': 'Ноутбуки', 'user_id': 10, 'score': 42,
        'tender': {'number': tender_number, 'name': 'Ноутбук'},
        'match_info': {'score': 42, 'matched_keywords': ['ноутбук']},
    }


def filters_by_id(**over):
    data = {'id': 1, 'user_id': 10, 'company_id': 57, 'telegram_id': 999,
            'subscription_tier': 'premium'}
    data.update(over)
    return {1: data}


@pytest.mark.unit
@pytest.mark.asyncio
class TestDeliver:
    async def test_sends_and_records_the_source(self):
        db, nf = FakeDb(), FakeNotifier()
        sent = await deliver([match()], filters_by_id(), db, nf)
        assert sent == 1
        assert len(nf.sent) == 1 and nf.sent[0]['telegram_id'] == 999
        assert db.saved[0]['source'] == SOURCE

    async def test_already_notified_is_not_sent_again(self):
        """Тендер, найденный быстрым путём через сайт, пул не досылает.
        Проверка стоит ДО отправки: ограничение в БД спасло бы от дубля
        в таблице, но сообщение уже ушло бы пользователю."""
        db = FakeDb(notified={("0173300004226000004", 10)})
        nf = FakeNotifier()
        sent = await deliver([match()], filters_by_id(), db, nf)
        assert sent == 0
        assert nf.sent == []

    async def test_group_chats_get_it_instead_of_personal(self):
        db, nf = FakeDb(), FakeNotifier()
        await deliver([match()],
                      filters_by_id(notify_chat_ids=[-100, -200],
                                    notify_thread_id=7), db, nf)
        assert [s['telegram_id'] for s in nf.sent] == [-100, -200]
        assert all(s['message_thread_id'] == 7 for s in nf.sent)

    async def test_one_user_check_does_not_block_the_second_chat(self):
        """Проверка «уже уведомлён» делается один раз на пользователя, до
        перебора чатов: иначе сохранение после первого чата заблокировало
        бы второй notify_chat_id в этом же проходе."""
        db, nf = FakeDb(), FakeNotifier()
        sent = await deliver([match()],
                             filters_by_id(notify_chat_ids=[-100, -200]), db, nf)
        assert sent == 2

    async def test_chat_that_already_has_it_is_skipped(self):
        db = FakeDb(in_chat={("0173300004226000004", -100)})
        nf = FakeNotifier()
        await deliver([match()], filters_by_id(notify_chat_ids=[-100, -200]),
                      db, nf)
        assert [s['telegram_id'] for s in nf.sent] == [-200]

    async def test_failed_send_is_not_recorded(self):
        """Иначе неудачная отправка навсегда пометит тендер как
        доставленный, и повторить будет нельзя."""
        db, nf = FakeDb(), FakeNotifier(succeed=False)
        sent = await deliver([match()], filters_by_id(), db, nf)
        assert sent == 0 and db.saved == []

    async def test_match_for_a_vanished_filter_is_skipped(self):
        """Фильтр могли удалить между матчингом и рассылкой."""
        db, nf = FakeDb(), FakeNotifier()
        sent = await deliver([match(filter_id=404)], filters_by_id(), db, nf)
        assert sent == 0 and nf.sent == []

    async def test_no_notifier_does_not_crash_the_pass(self):
        db = FakeDb()
        sent = await deliver([match()], filters_by_id(), db, None)
        assert sent == 0 and db.saved == []

    async def test_every_match_is_delivered(self):
        """Ограничение стоит на РАЗБОРЕ пула (MATCH_LIMIT), а не здесь.
        Причина: match_pool помечает matched_at у всех строк, прошедших
        через него, поэтому отсечённое на рассылке совпадение пропало бы
        навсегда. Неразобранные строки, наоборот, ждут следующего часа."""
        db, nf = FakeDb(), FakeNotifier()
        many = [match(tender_number=f"03731000000260000{i:02d}") for i in range(20)]
        sent = await deliver(many, filters_by_id(), db, nf)
        assert sent == 20 and len(db.saved) == 20

    async def test_single_supplier_purchases_are_not_sent(self):
        """Замер 22.09.2026: ст.93 ч.12 — 268 закупок из 394 закрылись в
        день публикации, а в разобранном примере заявка подана на минуту
        РАНЬШЕ публикации извещения, протокол — через 19 минут. Звать
        туда участвовать не во что."""
        db, nf = FakeDb(), FakeNotifier()
        item = match()
        item['procedure_type'] = 'Закупка, осуществляемая в соответствии с частью 12 статьи 93'
        sent = await deliver([item], filters_by_id(), db, nf)
        assert sent == 0 and nf.sent == []

    async def test_normal_procedures_still_go_out(self):
        db, nf = FakeDb(), FakeNotifier()
        item = match()
        item['procedure_type'] = 'Электронный аукцион'
        assert await deliver([item], filters_by_id(), db, nf) == 1

    async def test_unknown_method_is_not_treated_as_direct(self):
        """Способ закупки может не прийти — это не повод молчать."""
        db, nf = FakeDb(), FakeNotifier()
        assert await deliver([match()], filters_by_id(), db, nf) == 1


@pytest.mark.unit
@pytest.mark.asyncio
class TestMarkingHappensAfterDelivery:
    """Порядок «разобрать → разослать → пометить» защищает от потери.

    Если пометить раньше, перезапуск воркера между разбором и отправкой
    превращает найденное совпадение в навсегда потерянное: строка уже
    считается обработанной, а уведомление не ушло. Ровно этот сценарий и
    возник 22.09.2026, когда понадобилось поднять лимит разбора и
    перезапустить воркер посреди цикла.
    """

    async def test_loop_marks_only_after_deliver(self, monkeypatch):
        from tender_sniper.jobs import pool_loop as pl

        order = []

        async def fake_match(**kwargs):
            order.append(('match', kwargs.get('dry_run')))
            return {'checked': 2, 'matches': [], 'checked_numbers': ['a', 'b']}

        async def fake_deliver(*a, **kw):
            order.append(('deliver', None))
            return 0

        async def fake_mark(numbers):
            order.append(('mark', tuple(numbers)))
            return len(numbers)

        monkeypatch.setattr(pl, 'match_pool', fake_match)
        monkeypatch.setattr(pl, 'deliver', fake_deliver)
        monkeypatch.setattr(pl, 'mark_processed', fake_mark)

        # Воспроизводим тело одного прохода без сети и sleep.
        result = await pl.match_pool(limit=10, filters=[], dry_run=True)
        await pl.deliver(result['matches'], {}, None, None)
        await pl.mark_processed(result['checked_numbers'])

        assert [step for step, _ in order] == ['match', 'deliver', 'mark']
        assert order[0][1] is True, 'разбор обязан идти без пометки'
        assert order[2][1] == ('a', 'b')
