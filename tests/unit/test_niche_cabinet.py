"""Раздел «Аналитика ниш» в кабинете: подготовка данных и разбор запроса."""
import pytest

from cabinet.api import _niche_query
from cabinet.niche_service import BUCKET_LABELS, _humanise


class _Request:
    def __init__(self, **query):
        self.query = {k: str(v) for k, v in query.items()}


@pytest.mark.unit
class TestQueryValidation:
    def test_defaults(self):
        q = _niche_query(_Request())
        assert q == {"level": 4, "min_count": 5, "region": None, "bucket": None}

    def test_level_outside_the_view_falls_back(self):
        """Витрина посчитана только для уровней 2/4/6. Любое другое
        значение дало бы пустую выдачу без объяснения."""
        assert _niche_query(_Request(level=3))["level"] == 4
        assert _niche_query(_Request(level=99))["level"] == 4

    def test_level_is_accepted_when_valid(self):
        for level in (2, 4, 6):
            assert _niche_query(_Request(level=level))["level"] == level

    def test_garbage_level_does_not_crash(self):
        assert _niche_query(_Request(level="; DROP TABLE"))["level"] == 4

    def test_min_count_is_clamped(self):
        """Отрицательный минимум обессмысливает фильтр, слишком большой
        гарантированно даёт пустоту."""
        assert _niche_query(_Request(min_count=-5))["min_count"] == 1
        assert _niche_query(_Request(min_count=99999))["min_count"] == 500

    def test_blank_region_becomes_none(self):
        """Пустая строка в SQL означала бы «регион равен пустой строке»,
        то есть ни одной записи, вместо «любой регион»."""
        assert _niche_query(_Request(region="  "))["region"] is None
        assert _niche_query(_Request(region="77"))["region"] == "77"


@pytest.mark.unit
class TestHumanise:
    BASE = {"okpd2": "21.20", "region": "77", "price_bucket": "1m-3m",
            "procedures_count": 30, "median_drop": 0.0}

    def test_region_code_becomes_a_name(self):
        assert _humanise(self.BASE)["region_name"] == "Москва"

    def test_bucket_label(self):
        assert _humanise(self.BASE)["price_bucket_label"] == BUCKET_LABELS["1m-3m"]

    def test_zero_drop_is_marked_as_unmeasured_when_nothing_compared(self):
        """Ключевое различие для пользователя: «снижение 0%» значит
        «цену не роняют», а «не измерено» — «сравнивать было не с чем».
        Без пометки он прочитает второе как первое и пойдёт в нишу с
        неверным ожиданием."""
        row = _humanise({**self.BASE, "comparable_prices": 0})
        assert row["drop_known"] is False

    def test_drop_is_known_when_there_were_comparable_prices(self):
        row = _humanise({**self.BASE, "comparable_prices": 12})
        assert row["drop_known"] is True

    def test_confidence_reflects_missing_winners(self):
        """Победители неизвестны — «открытость» не измерена и даёт полные
        баллы, то есть индекс завышен. Это должно быть видно."""
        assert _humanise({**self.BASE, "comparable_prices": 5,
                          "known_winners": 0})["confidence"] == "без концентрации"
        assert _humanise({**self.BASE, "comparable_prices": 5,
                          "known_winners": 9})["confidence"] == "полная"
        assert _humanise({**self.BASE, "comparable_prices": 0,
                          "known_winners": 0})["confidence"] == "мало данных"

    def test_unknown_region_code_survives(self):
        row = _humanise({**self.BASE, "region": "99"})
        assert row["region_name"] == "99"

    def test_original_row_is_not_mutated(self):
        source = dict(self.BASE)
        _humanise(source)
        assert source == self.BASE


@pytest.mark.unit
class TestExpectedWinnerPrice:
    """Главное число блока «Конкуренция в нише»: сразу видно, укладывается
    ли закупочная цена, ещё до подготовки заявки."""

    def test_typical_price_uses_median_drop(self):
        from cabinet.niche_service import expected_winner_price
        e = expected_winner_price(1_000_000, 0.20, 0.45)
        assert e["typical"] == 800_000.0

    def test_tough_case_uses_p90(self):
        """Две границы, а не одна: с одной типичное снижение легко
        принять за худший случай и отказаться от проходной закупки."""
        from cabinet.niche_service import expected_winner_price
        e = expected_winner_price(1_000_000, 0.20, 0.45)
        assert e["tough"] == 550_000.0
        assert e["tough"] < e["typical"]

    def test_no_drop_means_price_stays_at_nmck(self):
        from cabinet.niche_service import expected_winner_price
        assert expected_winner_price(482496.20, 0.0, None)["typical"] == 482496.20

    def test_without_p90_only_typical(self):
        from cabinet.niche_service import expected_winner_price
        assert expected_winner_price(100_000, 0.1, None)["tough"] is None

    def test_missing_inputs_give_nothing(self):
        """Выдумывать ориентир, когда данных нет, хуже чем не показывать."""
        from cabinet.niche_service import expected_winner_price
        assert expected_winner_price(None, 0.2, 0.4) is None
        assert expected_winner_price(1000, None, 0.4) is None


@pytest.mark.unit
class TestCapturedNiche:
    """Мало участников — ещё не свободное поле. Если все победы у одного
    поставщика, это «приходить бесполезно», а не «никто не приходит»."""

    def test_single_supplier_is_flagged(self):
        from cabinet.niche_service import captured_by
        c = captured_by(["a"] * 7 + ["b"] * 4)
        assert c["inn"] == "a" and c["wins"] == 7 and c["total"] == 11

    def test_fragmented_market_is_not_flagged(self):
        from cabinet.niche_service import captured_by
        assert captured_by(["a", "b", "c", "d"]) is None

    def test_exactly_half_counts_as_captured(self):
        from cabinet.niche_service import captured_by
        assert captured_by(["a", "a", "b", "c"]) is not None

    def test_no_winners_known(self):
        from cabinet.niche_service import captured_by
        assert captured_by([]) is None
        assert captured_by(None) is None
        assert captured_by([None, None]) is None


@pytest.mark.unit
class TestFilterVerdict:
    """Короткий вывод по категории, которую ловит фильтр.

    Формулировки осторожные намеренно: это подсказка, куда смотреть, а
    не рекомендация отключать фильтр. Данных по одному региону мало, и
    ошибиться здесь дороже, чем промолчать.
    """

    def test_crowded_niche(self):
        from cabinet.niche_service import verdict_for
        assert verdict_for(5.0, 0.30, 100) == "людно"

    def test_free_niche(self):
        from cabinet.niche_service import verdict_for
        assert verdict_for(1.0, 0.0, 100) == "свободно"

    def test_price_pressure_even_with_few_bids(self):
        from cabinet.niche_service import verdict_for
        assert verdict_for(2.0, 0.35, 100) == "цену роняют"

    def test_moderate(self):
        from cabinet.niche_service import verdict_for
        assert verdict_for(2.0, 0.05, 100) == "умеренно"

    def test_no_history_is_not_a_verdict(self):
        """Отсутствие истории нельзя выдавать за «свободно»: это разные
        вещи, и вторая подтолкнёт пойти туда, где мы ничего не знаем."""
        from cabinet.niche_service import verdict_for
        assert verdict_for(1.0, 0.0, 0) == "нет истории"
        assert verdict_for(1.0, 0.0, None) == "нет истории"

    def test_unknown_results(self):
        from cabinet.niche_service import verdict_for
        assert verdict_for(None, None, 50) == "результаты неизвестны"
