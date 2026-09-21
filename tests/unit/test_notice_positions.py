"""Позиции закупщика из извещения ЕИС: что доезжает до поиска."""
import pytest

from cabinet.notice_positions import (
    MAX_POSITION_LEN,
    positions_from_notice,
    render_position,
)
from tender_sniper.sources.eis_integration import Characteristic, PurchaseObject


def ch(name, text, unit=None, low=None, high=None, high_inclusive=True):
    return Characteristic(name=name, text=text, unit=unit, low=low, high=high,
                          high_inclusive=high_inclusive)

LAMP = PurchaseObject(
    name='Светильник светодиодный внутреннего освещения',
    quantity=50.0, unit='шт',
    characteristics=[
        ch('Вид светильника', 'Настенно-потолочный'),
        ch('Длина светильника', 'не менее 500 и менее 600 мм', 'мм', 500, 600,
           high_inclusive=False),
        ch('Индекс цветопередачи', 'не менее 80 и менее 90', None, 80, 90),
        ch('Материал рассеивателя', 'Поликарбонат'),
        ch('Мощность', 'более 35 и не более 40 Вт', 'Вт', 35, 40),
    ])

NOTICE_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<export xmlns="http://zakupki.gov.ru/oos/export/1"
        xmlns:c="http://zakupki.gov.ru/oos/common/1"
        xmlns:b="http://zakupki.gov.ru/oos/base/1">
 <epNotificationEF2020><notificationInfo><purchaseObjectsInfo>
  <c:purchaseObject>
   <c:name>Светильник светодиодный</c:name>
   <c:quantity><c:value>50</c:value></c:quantity>
   <c:OKEI><b:nationalCode>шт</b:nationalCode></c:OKEI>
   <c:KTRU><c:characteristics>
    <c:characteristicsUsingReferenceInfo>
     <c:name>Мощность</c:name>
     <c:values><c:value>
      <c:OKEI><b:nationalCode>Вт</b:nationalCode></c:OKEI>
      <c:rangeSet><c:valueRange>
       <c:minMathNotation>greater</c:minMathNotation><c:min>35</c:min>
       <c:maxMathNotation>lessOrEqual</c:maxMathNotation><c:max>40</c:max>
      </c:valueRange></c:rangeSet>
     </c:value></c:values>
    </c:characteristicsUsingReferenceInfo>
    <c:characteristicsUsingReferenceInfo>
     <c:name>Вторая характеристическая цифра обозначения степени защиты</c:name>
     <c:values><c:value><c:rangeSet><c:valueRange>
      <c:minMathNotation>greaterOrEqual</c:minMathNotation><c:min>0</c:min>
     </c:valueRange></c:rangeSet></c:value></c:values>
    </c:characteristicsUsingReferenceInfo>
    <c:characteristicsUsingReferenceInfo>
     <c:name>Материал рассеивателя</c:name>
     <c:values><c:value>
      <c:qualityDescription>Поликарбонат</c:qualityDescription>
     </c:value></c:values>
    </c:characteristicsUsingReferenceInfo>
   </c:characteristics></c:KTRU>
  </c:purchaseObject>
 </purchaseObjectsInfo></notificationInfo></epNotificationEF2020></export>'''.encode('utf-8')


@pytest.mark.unit
class TestRenderPosition:
    def test_name_and_characteristics_are_joined(self):
        text = render_position(LAMP)
        assert text.startswith('Светильник светодиодный внутреннего освещения')
        assert 'поликарбонат' in text.lower()

    def test_numbers_with_units_reach_the_position(self):
        """Ровно они и делают разницу: замер 21.09.2026 по закупке
        0373100128326000093 — без чисел подбор давал не тот товар, с
        числами нашёл нужный за 1 179 и 1 344 ₽."""
        text = render_position(LAMP)
        assert 'до 40 Вт' in text
        assert 'менее 600 мм' in text

    def test_narrowest_requirement_comes_first(self):
        """У светильника из закупки 0373100128326000093 мощность
        35-40 Вт — вилка 12%, длина 500-600 мм — 20%. По мощности его и
        ищут, поэтому она должна идти первой."""
        text = render_position(LAMP)
        assert text.index('до 40 Вт') < text.index('менее 600 мм')

    def test_unitless_numbers_lose_to_units(self):
        """«Индекс цветопередачи 80-90» вилку имеет узкую, но в запросе
        бесполезен: числа без единицы товар не опознают, а место
        занимают."""
        text = render_position(LAMP)
        assert text.index('до 40 Вт') < text.index('80 и менее 90')

    def test_two_sided_ranges_come_before_floors(self):
        """Двусторонний диапазон задаёт товар, односторонний порог
        выполняет и любая модель лучше — для поиска он почти ничего не
        сужает, поэтому идёт после."""
        obj = PurchaseObject(name='Ноутбук', characteristics=[
            ch('Объем оперативной памяти', 'не менее 16 Гбайт', 'Гбайт', 16, None),
            ch('Мощность', 'более 35 и не более 40 Вт', 'Вт', 35, 40),
        ])
        text = render_position(obj)
        assert text.index('до 40 Вт') < text.index('16 Гбайт')

    def test_absence_requirements_are_dropped(self):
        """«Нет» нельзя ни искать, ни подтвердить со страницы
        поставщика: каталог не пишет, чего в товаре нет."""
        obj = PurchaseObject(name='Ноутбук', characteristics=[
            ch('Наличие стилуса в комплекте', 'Нет'),
            ch('Тип накопителя', 'SSD'),
        ])
        text = render_position(obj)
        assert 'стилус' not in text.lower()
        assert 'SSD' in text

    def test_position_is_capped(self):
        obj = PurchaseObject(name='Ноутбук', characteristics=[
            ch(f'Характеристика номер {i}', f'значение {i}') for i in range(40)])
        assert len(render_position(obj)) <= MAX_POSITION_LEN

    def test_object_without_characteristics_is_just_its_name(self):
        assert render_position(PurchaseObject(name='Бумага А4')) == 'Бумага А4'


@pytest.mark.unit
class TestParseNotice:
    def test_reads_position_with_characteristics(self):
        positions = positions_from_notice(NOTICE_XML)
        assert len(positions) == 1
        assert 'до 40 Вт' in positions[0]
        assert 'поликарбонат' in positions[0].lower()

    def test_ip_digit_characteristics_are_skipped(self):
        """Степень защиты приходит двумя «характеристическими цифрами».
        По отдельности это «не менее 2» и «не менее 0» — как требование
        бессмысленно, а в запрос такие числа лезут вперёд мощности."""
        assert 'цифра' not in positions_from_notice(NOTICE_XML)[0].lower()

    def test_two_sided_range_collapses_to_its_upper_bound(self):
        """Вилка давала в запрос два числа — «мощность 35 40 вт», чего
        не пишет ни один магазин. Замер 21.09.2026: с таким запросом по
        закупке 0373100128326000093 выдача схлопывалась в ноль."""
        text = render_position(LAMP)
        assert 'до 40 Вт' in text
        assert '35' not in text

    def test_one_sided_floor_keeps_its_wording(self):
        """Односторонний порог сворачивать не во что — верхней границы
        у него нет."""
        obj = PurchaseObject(name='Ноутбук', characteristics=[
            ch('Объем памяти', 'не менее 16 Гбайт', 'Гбайт', 16, None)])
        assert 'не менее 16 Гбайт' in render_position(obj)

    def test_unreachable_upper_bound_is_not_a_nominal(self):
        """«Менее 600 мм» — это товар 595 мм, числа 600 нет ни в одном
        каталоге. Замер 21.09.2026: запрос с «600 мм» не нашёл ничего,
        тогда как с «40 Вт» нашёл нужный светильник."""
        text = render_position(LAMP)
        assert 'до 600 мм' not in text
        assert text.index('до 40 Вт') < text.index('менее 600 мм')

    def test_search_words_come_before_requirement_labels(self):
        """Каталог пишет «светильник 40 Вт поликарбонат», а не
        «мощность до 40 Вт». Запрос строится из начала строки, поэтому
        значения там идут раньше ярлыков."""
        text = render_position(LAMP)
        head = text.split(',')[0]
        assert 'до 40 Вт' in head
        assert 'мощность' not in head.lower()

    def test_labels_are_still_present_for_the_checks(self):
        """Сверка читает строку целиком: без ярлыка требование «40 Вт»
        непонятно чего."""
        text = render_position(LAMP)
        assert 'мощность до 40 Вт' in text

    def test_bare_yes_stays_out_of_the_search_head(self):
        """«Да» без своего ярлыка не значит ничего: в запрос уходило
        буквально «ноутбук pcie да». В хвосте, рядом с «наличие
        сканера отпечатка», оно осмысленно и остаётся."""
        obj = PurchaseObject(name='Ноутбук', characteristics=[
            ch('Наличие сканера отпечатка пальцев', 'Да'),
            ch('Тип накопителя', 'SSD'),
        ])
        text = render_position(obj)
        head = text.split(',')[0]
        assert 'Да' not in head
        assert 'SSD' in head
        assert 'наличие сканера отпечатка пальцев Да' in text
