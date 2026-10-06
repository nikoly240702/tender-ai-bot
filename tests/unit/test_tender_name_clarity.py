"""Название тендера должно быть понятным всегда.

Замер боевого потока за неделю (1821 уведомление, 28.09.2026) показал три
источника невнятных имён: имя, оборванное на полуслове (5%), совпадение
только по обрезанному корню слова (13%) и зонтичное имя (8%).

Здесь закрыты первые два.
"""
import pytest

from tender_sniper.tender_name_resolver import (
    looks_truncated,
    resolve_tender_name,
    safe_truncate,
    subject_from_summary,
)


@pytest.mark.unit
class TestLooksTruncated:
    """Имя, обрезанное на полуслове, показывать нельзя."""

    def test_cut_before_the_keyword(self):
        """Реальный случай: ИИ отдал первые четыре слова оригинала."""
        assert looks_truncated(
            "Выполнение работ по капитальному",
            original="Выполнение работ по капитальному ремонту помещений школы") is True

    def test_cut_on_preposition(self):
        assert looks_truncated("Поставка оборудования для") is True

    def test_cut_on_open_bracket(self):
        assert looks_truncated("Поставка сантехнических товаров (") is True

    def test_sensible_shortening_is_kept(self):
        """Отрезать «для нужд больницы» — это нормальное сокращение."""
        assert looks_truncated(
            "Поставка перчаток",
            original="Поставка перчаток для нужд больницы №3") is False

    def test_adjective_at_the_end_of_a_full_name_is_fine(self):
        """«Поставка перчаток хирургических» — обычный обратный порядок слов."""
        assert looks_truncated("Поставка перчаток хирургических") is False

    def test_full_name_is_never_truncated(self):
        assert looks_truncated("Ремонт мягкой кровли",
                               original="Ремонт мягкой кровли") is False

    def test_dangling_adjective_without_its_noun(self):
        """Из потока 28.09: рядом пришли «Поставка водонепроницаемых» и
        «Поставка бахил водонепроницаемых» — первое обрезано, во втором
        прилагательное согласовано с существительным."""
        assert looks_truncated("Поставка водонепроницаемых") is True
        assert looks_truncated("Поставка бахил водонепроницаемых") is False

    def test_agreed_adjective_after_noun_is_fine(self):
        for name in ("Поставка перчаток хирургических",
                     "Морозильник низкотемпературный",
                     "Поставка изделий медицинских"):
            assert looks_truncated(name) is False, name

    def test_preposition_before_substantivised_adjective_is_not_truncation(self):
        """Из потока 06.10: «Поставка весов для новорожденных» отбраковывалась
        как обрыв — последнее слово кончается на «-ых», как «капитальному»,
        и перед ним предлог «для». Но «новорождённые» здесь существительное
        («для кого?», а не «для чего?» с пропущенным хвостом), и фраза
        полная. Раньше это ломало резолвер целиком: хорошее сырое имя
        отбрасывалось, и в карточку уходило название из справочника ОКПД2
        («Оборудование для облучения, электрическое диагностическое...»)."""
        assert looks_truncated("Поставка весов для новорожденных") is False

    def test_preposition_before_pronoun_is_not_truncation(self):
        """«к ним» — местоимение, не прилагательное без существительного;
        тот же класс ложного срабатывания, что и выше."""
        assert looks_truncated(
            "Поставка дорожных знаков и комплектующих к ним") is False

    def test_dangling_preposition_heuristic_only_for_ai_text(self):
        """Грубая проверка «предлог + слово на „-ому/-ему/...“» неотличима
        от «для новорождённых» без знания источника — поэтому включается
        только для текста модели (is_ai_text=True), где цена ложного
        срабатывания низкая: у AI-текста есть куда откатиться."""
        text = "Выполнение работ по текущему"
        assert looks_truncated(text) is False
        assert looks_truncated(text, is_ai_text=True) is True

    def test_genuine_cut_before_adjective_still_caught_via_original(self):
        """Без оригинала эвристика теперь мягче, но с ним обрыв перед
        прилагательным всё так же ловится — именно это было первым фиксом."""
        assert looks_truncated(
            "Выполнение работ по капитальному",
            original="Выполнение работ по капитальному ремонту помещений школы") is True


@pytest.mark.unit
class TestSafeTruncate:
    """Резать текст по границе слова, а не посимвольно.

    Замер 06.10 на боевых уведомлениях за 14 дней: 176 из 4310 (4,1%)
    получали имя, обрезанное РОВНО по max_length — наивный срез
    `name[:max_length]` применялся уже ПОСЛЕ проверки на обрыв, так что
    проверялся целый оригинал, а резалась готовая строка без оглядки на
    то, где кончается слово. «...бесконвертных почтовых отправле» —
    реальный случай из потока."""

    def test_short_text_is_untouched(self):
        assert safe_truncate("Поставка перчаток", 200) == "Поставка перчаток"

    def test_cuts_at_word_boundary_not_mid_word(self):
        text = ("Изготовление (печать) уведомлений гражданам, включенным в "
                "списки кандидатов в присяжные заседатели на территории "
                "Городского округа Серпухов, формирование (мейлирование) "
                "бесконвертных почтовых отправлений из напечатанных "
                "уведомлений и доставка их до адресатов")
        result = safe_truncate(text, 200)
        assert len(result) <= 201  # с учётом добавленного «…»
        assert "отправле…" not in result  # слово не должно быть разрублено
        assert not result.rstrip('…').endswith(("отправле",))

    def test_marks_the_cut_with_ellipsis(self):
        result = safe_truncate("а" * 10 + " " + "б" * 300, 50)
        assert result.endswith('…')

    def test_never_ends_on_a_dangling_word(self):
        """Срез по пробелу мог сам попасть точно на предлог/союз — его
        дополнительно откатываем назад."""
        text = "Поставка оборудования для нужд больницы и клиники региона"
        # Обрезаем ровно так, чтобы граница пришлась на «и»
        cut_at = text.index(" и ") + 2
        result = safe_truncate(text, cut_at)
        words = result.rstrip('…').split()
        assert words[-1].lower() not in ("и", "для", "по", "в", "на")

    def test_exact_fit_keeps_no_ellipsis(self):
        text = "Поставка перчаток"
        assert safe_truncate(text, len(text)) == text


@pytest.mark.unit
class TestSubjectFromSummary:
    """Когда имя негодное, предмет закупки берётся из сводки — она есть
    в том же ответе модели и описывает суть."""

    def test_strips_lead_in_and_price_tail(self):
        assert subject_from_summary(
            "Тендер на выполнение капитального ремонта помещений "
            "с начальной ценой до 3 миллионов рублей."
        ) == "Выполнение капитального ремонта помещений"

    def test_strips_procurement_lead_in(self):
        assert subject_from_summary(
            "Закупка на поставку бумаги для офисной техники."
        ) == "Поставка бумаги для офисной техники"

    def test_keeps_plain_sentence(self):
        assert subject_from_summary(
            "Поставка снегоуборщика для нужд администрации."
        ) == "Поставка снегоуборщика для нужд администрации"

    def test_empty_gives_nothing(self):
        assert subject_from_summary("") is None
        assert subject_from_summary("   ") is None


@pytest.mark.unit
class TestResolverPrefersClearName:
    def test_truncated_ai_name_is_rejected_in_favour_of_summary(self):
        """Тендер 0137200001226007578 из боевого потока: имя процедуры
        негодное, ai_simple_name обрезан, а сводка описывает суть."""
        tender = {"name": "Электронный аукцион", "number": "0137200001226007578"}
        match_info = {
            "ai_simple_name": "Выполнение работ по текущему",
            "ai_summary": "Тендер на выполнение работ по ремонту актового зала.",
        }
        assert resolve_tender_name(tender, match_info) == \
            "Выполнение работ по ремонту актового зала"

    def test_good_raw_name_wins(self):
        tender = {"name": "Поставка перчаток хирургических", "number": "1"}
        assert resolve_tender_name(tender, {"ai_simple_name": "Перчатки"}) == \
            "Поставка перчаток хирургических"

    def test_good_raw_name_not_rejected_for_category_list_in_description(self):
        """Тендер 0348500004626000296 из потока 06.10: хорошее сырое имя
        «Поставка весов для новорожденных» отбраковывалось ложным срабатыванием
        на «для» + «-ых», и в карточку уходил список категорий ОКПД2 из
        description — «Оборудование для облучения...; Весы для
        новорожденных...; Средства измерений массы...» (обрезан на 200-м
        символе, посередине слова)."""
        tender = {
            "name": "Поставка весов для новорожденных",
            "number": "0348500004626000296",
            "description": ("Оборудование для облучения, электрическое "
                            "диагностическое и терапевтическое, применяемые в "
                            "медицинских целях; Весы для новорожденных, "
                            "электронные; Средства измерений массы, силы, "
                            "энергии, линейных и угловых величин, температуры"),
        }
        assert resolve_tender_name(tender, {}) == "Поставка весов для новорожденных"

    def test_good_raw_name_not_rejected_for_pronoun_tail(self):
        """Тендер 0848300069526000362: «...к ним» ломал резолвер тем же
        способом, и в карточку уходил «Знак дорожный; Изделия прочие из
        недрагоценных металлов, не включенные в другие группировки; ...»."""
        tender = {
            "name": "Поставка дорожных знаков и комплектующих к ним",
            "number": "0848300069526000362",
            "description": ("Знак дорожный; Изделия прочие из недрагоценных "
                            "металлов, не включенные в другие группировки; "
                            "Опора знака дорожного; Изделия крепежные и винты "
                            "крепежные"),
        }
        assert resolve_tender_name(tender, {}) == \
            "Поставка дорожных знаков и комплектующих к ним"

    def test_pool_raw_names_do_not_leak(self):
        """Пул присылал сырые имена вида «Поставка №2» — теперь они
        резолвятся при сохранении, а не только при показе карточки."""
        tender = {"name": "Поставка №2", "number": "0348100009126000212"}
        match_info = {"ai_summary": "Тендер на поставку бумаги для офисной техники."}
        assert resolve_tender_name(tender, match_info) == \
            "Поставка бумаги для офисной техники"

    def test_works_without_match_info(self):
        """save_notification зовёт резолвер и там, где разбора ИИ нет."""
        assert resolve_tender_name({"name": "Поставка №2", "number": "77"}, None) == \
            "Тендер №77"

    def test_never_returns_truncated_tail(self):
        """Если годного источника нет совсем — честный номер, а не огрызок."""
        tender = {"name": "Запрос котировок в электронной форме", "number": "123"}
        match_info = {"ai_simple_name": "Выполнение работ по"}
        assert resolve_tender_name(tender, match_info) == "Тендер №123"
