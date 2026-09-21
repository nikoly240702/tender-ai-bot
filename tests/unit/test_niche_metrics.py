"""Индекс привлекательности ниши: составляющие, HHI, ранжирование."""
import math

import pytest

from tender_sniper.niche.metrics import (
    DEFAULT_CONFIG,
    IndexConfig,
    compute_index,
    herfindahl,
    rank,
    repeat_customers_share,
    score_competition,
    score_margin,
    score_openness,
    score_volume,
    top_n_share,
)


@pytest.mark.unit
class TestCompetition:
    """Контрольные точки прямо из ТЗ: 1 заявка → 100, 3 → ~33, 6 → ~6."""

    def test_single_bid_is_the_best_case(self):
        assert score_competition(1, DEFAULT_CONFIG) == pytest.approx(100.0)

    def test_three_bids(self):
        assert score_competition(3, DEFAULT_CONFIG) == pytest.approx(33.3, abs=1.0)

    def test_six_bids(self):
        assert score_competition(6, DEFAULT_CONFIG) == pytest.approx(6.4, abs=1.0)

    def test_zero_bids_is_not_better_than_one(self):
        """Ноль заявок — это несостоявшаяся закупка, а не сверхудача.
        Без ограничения снизу экспонента дала бы 173 балла из 100."""
        assert score_competition(0, DEFAULT_CONFIG) == pytest.approx(100.0)

    def test_unknown_is_zero_not_a_free_pass(self):
        """Нет данных о заявках — ниша не может возглавить рейтинг."""
        assert score_competition(None, DEFAULT_CONFIG) == 0.0

    def test_monotonically_decreasing(self):
        scores = [score_competition(n, DEFAULT_CONFIG) for n in (1, 2, 4, 8)]
        assert scores == sorted(scores, reverse=True)


@pytest.mark.unit
class TestMargin:
    def test_no_price_drop_is_full_margin(self):
        assert score_margin(0.0, DEFAULT_CONFIG) == pytest.approx(100.0)

    def test_drop_at_limit_is_zero(self):
        assert score_margin(0.20, DEFAULT_CONFIG) == pytest.approx(0.0)

    def test_half_limit_is_half_score(self):
        assert score_margin(0.10, DEFAULT_CONFIG) == pytest.approx(50.0)

    def test_deeper_drop_does_not_go_negative(self):
        assert score_margin(0.90, DEFAULT_CONFIG) == 0.0

    def test_negative_drop_is_not_a_bonus(self):
        """Цена выросла относительно НМЦК — маржа от этого не больше
        стопроцентной, а формула без защиты дала бы больше 100."""
        assert score_margin(-0.5, DEFAULT_CONFIG) == pytest.approx(100.0)

    def test_unknown_is_zero(self):
        assert score_margin(None, DEFAULT_CONFIG) == 0.0


@pytest.mark.unit
class TestVolume:
    def test_saturation_point_reaches_full_score(self):
        assert score_volume(50, DEFAULT_CONFIG) == pytest.approx(100.0)

    def test_above_saturation_is_capped(self):
        assert score_volume(5000, DEFAULT_CONFIG) == 100.0

    def test_single_procedure_is_low_but_not_zero(self):
        score = score_volume(1, DEFAULT_CONFIG)
        assert 0 < score < 20

    def test_empty_slice(self):
        assert score_volume(0, DEFAULT_CONFIG) == 0.0
        assert score_volume(None, DEFAULT_CONFIG) == 0.0


@pytest.mark.unit
class TestOpenness:
    def test_monopoly_scores_zero(self):
        assert score_openness(1.0, DEFAULT_CONFIG) == pytest.approx(0.0)

    def test_at_limit_scores_zero(self):
        assert score_openness(0.25, DEFAULT_CONFIG) == pytest.approx(0.0)

    def test_fragmented_market_scores_high(self):
        assert score_openness(0.05, DEFAULT_CONFIG) == pytest.approx(80.0)

    def test_unknown_winners_treated_as_open(self):
        """Победители неизвестны (контракты ещё не заключены) — это не
        доказательство монополии, иначе свежие срезы штрафовались бы ни
        за что. Допущение задокументировано и видно по known_winners."""
        assert score_openness(None, DEFAULT_CONFIG) == 100.0


@pytest.mark.unit
class TestHerfindahl:
    def test_single_supplier_is_one(self):
        assert herfindahl(["1", "1", "1"]) == pytest.approx(1.0)

    def test_four_equal_suppliers(self):
        assert herfindahl(["1", "2", "3", "4"]) == pytest.approx(0.25)

    def test_dominant_supplier_raises_index(self):
        assert herfindahl(["1", "1", "1", "2"]) > herfindahl(["1", "1", "2", "2"])

    def test_empty_is_none_not_zero(self):
        """Ноль означал бы идеально раздробленный рынок — это не то же
        самое, что отсутствие данных."""
        assert herfindahl([]) is None
        assert herfindahl([None, None]) is None


@pytest.mark.unit
class TestTopShareAndRepeat:
    def test_top3_share(self):
        inns = ["a"] * 5 + ["b"] * 3 + ["c"] * 2 + ["d"] * 1 + ["e"] * 1
        assert top_n_share(inns, 3) == pytest.approx(10 / 12)

    def test_top3_of_two_suppliers_is_everything(self):
        assert top_n_share(["a", "b"], 3) == pytest.approx(1.0)

    def test_repeat_customers(self):
        assert repeat_customers_share(["a", "a", "b", "c"]) == pytest.approx(0.5)

    def test_all_one_off_customers(self):
        assert repeat_customers_share(["a", "b", "c"]) == pytest.approx(0.0)

    def test_empty(self):
        assert top_n_share([]) is None and repeat_customers_share([]) is None


@pytest.mark.unit
class TestComputeIndex:
    IDEAL = {"median_bids": 1, "median_drop": 0.0, "procedures_count": 50,
             "winner_inns": [str(i) for i in range(50)]}

    def test_ideal_niche_approaches_hundred(self):
        assert compute_index(self.IDEAL)["index"] > 95

    def test_crowded_niche_scores_low(self):
        row = {"median_bids": 9, "median_drop": 0.35, "procedures_count": 40,
               "winner_inns": ["a"] * 40}
        assert compute_index(row)["index"] < 20

    def test_captured_niche_is_penalised_despite_low_competition(self):
        """Одна заявка и ноль снижения выглядят идеально, но если все
        победы у одного поставщика — это не свободное поле, а занятое."""
        free = compute_index({"median_bids": 1, "median_drop": 0.0,
                              "procedures_count": 30,
                              "winner_inns": [str(i) for i in range(30)]})
        captured = compute_index({"median_bids": 1, "median_drop": 0.0,
                                  "procedures_count": 30,
                                  "winner_inns": ["one"] * 30})
        assert captured["index"] < free["index"]
        assert captured["winner_hhi"] == pytest.approx(1.0)

    def test_components_are_exposed(self):
        enriched = compute_index(self.IDEAL)
        for key in ("score_competition", "score_margin", "score_volume",
                    "score_openness", "top3_winner_share", "repeat_customers"):
            assert key in enriched

    def test_weights_sum_to_one(self):
        cfg = DEFAULT_CONFIG
        total = (cfg.weight_competition + cfg.weight_margin
                 + cfg.weight_volume + cfg.weight_openness)
        assert total == pytest.approx(1.0)

    def test_index_never_exceeds_hundred(self):
        row = {"median_bids": 0, "median_drop": -1.0, "procedures_count": 10_000,
               "winner_inns": [str(i) for i in range(1000)]}
        assert compute_index(row)["index"] <= 100.0

    def test_empty_slice_scores_zero(self):
        assert compute_index({"procedures_count": 0})["index"] < 20


@pytest.mark.unit
class TestRank:
    ROWS = [
        {"okpd2": "21.20", "procedures_count": 30, "median_bids": 1,
         "median_drop": 0.01, "winner_inns": ["a", "b", "c"]},
        {"okpd2": "26.20", "procedures_count": 30, "median_bids": 8,
         "median_drop": 0.30, "winner_inns": ["a"] * 30},
        {"okpd2": "32.50", "procedures_count": 2, "median_bids": 1,
         "median_drop": 0.0, "winner_inns": ["z"]},
    ]

    def test_sorted_by_index_descending(self):
        ranked, _ = rank(self.ROWS)
        assert [r["okpd2"] for r in ranked] == ["21.20", "26.20"]

    def test_small_slices_are_parked_not_dropped(self):
        """Срез из двух процедур в рейтинге создал бы ложную вершину, но
        выбрасывать его совсем нельзя — ниша может быть интересной."""
        ranked, sparse = rank(self.ROWS)
        assert [r["okpd2"] for r in sparse] == ["32.50"]
        assert len(ranked) + len(sparse) == len(self.ROWS)

    def test_threshold_is_configurable(self):
        ranked, sparse = rank(self.ROWS, IndexConfig(min_procedures=1))
        assert len(ranked) == 3 and sparse == []
