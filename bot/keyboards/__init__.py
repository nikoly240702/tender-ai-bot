"""
Клавиатуры для Telegram бота.
"""

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder


# Федеральные округа и их регионы
FEDERAL_DISTRICTS = {
    'ЦФО': {
        'name': 'Центральный ФО',
        'regions': [
            'Москва', 'Московская область', 'Белгородская область',
            'Брянская область', 'Владимирская область', 'Воронежская область',
            'Ивановская область', 'Калужская область', 'Костромская область',
            'Курская область', 'Липецкая область', 'Орловская область',
            'Рязанская область', 'Смоленская область', 'Тамбовская область',
            'Тверская область', 'Тульская область', 'Ярославская область'
        ]
    },
    'СЗФО': {
        'name': 'Северо-Западный ФО',
        'regions': [
            'Санкт-Петербург', 'Ленинградская область', 'Республика Карелия',
            'Республика Коми', 'Архангельская область', 'Вологодская область',
            'Калининградская область', 'Мурманская область', 'Новгородская область',
            'Псковская область', 'Ненецкий автономный округ'
        ]
    },
    'ЮФО': {
        'name': 'Южный ФО',
        'regions': [
            'Республика Адыгея', 'Республика Калмыкия', 'Республика Крым',
            'Краснодарский край', 'Астраханская область', 'Волгоградская область',
            'Ростовская область', 'Севастополь'
        ]
    },
    'СКФО': {
        'name': 'Северо-Кавказский ФО',
        'regions': [
            'Республика Дагестан', 'Республика Ингушетия', 'Кабардино-Балкарская Республика',
            'Карачаево-Черкесская Республика', 'Республика Северная Осетия-Алания',
            'Чеченская Республика', 'Ставропольский край'
        ]
    },
    'ПФО': {
        'name': 'Приволжский ФО',
        'regions': [
            'Республика Башкортостан', 'Республика Марий Эл', 'Республика Мордовия',
            'Республика Татарстан', 'Удмуртская Республика', 'Чувашская Республика',
            'Пермский край', 'Кировская область', 'Нижегородская область',
            'Оренбургская область', 'Пензенская область', 'Самарская область',
            'Саратовская область', 'Ульяновская область'
        ]
    },
    'УФО': {
        'name': 'Уральский ФО',
        'regions': [
            'Курганская область', 'Свердловская область', 'Тюменская область',
            'Челябинская область', 'Ханты-Мансийский автономный округ',
            'Ямало-Ненецкий автономный округ'
        ]
    },
    'СФО': {
        'name': 'Сибирский ФО',
        'regions': [
            'Республика Алтай', 'Республика Тыва', 'Республика Хакасия',
            'Алтайский край', 'Красноярский край', 'Иркутская область',
            'Кемеровская область', 'Новосибирская область', 'Омская область',
            'Томская область'
        ]
    },
    'ДФО': {
        'name': 'Дальневосточный ФО',
        'regions': [
            'Республика Бурятия', 'Республика Саха (Якутия)', 'Забайкальский край',
            'Камчатский край', 'Приморский край', 'Хабаровский край',
            'Амурская область', 'Магаданская область', 'Сахалинская область',
            'Еврейская автономная область', 'Чукотский автономный округ'
        ]
    }
}


def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Главное меню бота."""
    builder = ReplyKeyboardBuilder()

    builder.row(
        KeyboardButton(text="🔍 Новый поиск"),
        KeyboardButton(text="📂 Мои поиски")
    )
    builder.row(
        KeyboardButton(text="ℹ️ Помощь")
    )

    return builder.as_markup(resize_keyboard=True)


def get_price_range_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора ценового диапазона."""
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(text="до 500 тыс. ₽", callback_data="price_до_500к")
    )
    builder.row(
        InlineKeyboardButton(text="500 тыс. - 1 млн ₽", callback_data="price_500к_1млн")
    )
    builder.row(
        InlineKeyboardButton(text="1 млн - 3 млн ₽", callback_data="price_1млн_3млн")
    )
    builder.row(
        InlineKeyboardButton(text="от 3 млн ₽", callback_data="price_3млн_плюс")
    )
    builder.row(
        InlineKeyboardButton(text="💰 Свой вариант", callback_data="price_custom")
    )
    builder.row(
        InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_query")
    )

    return builder.as_markup()


def get_tender_count_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора количества тендеров."""
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(text="3", callback_data="count_3"),
        InlineKeyboardButton(text="5", callback_data="count_5"),
        InlineKeyboardButton(text="10", callback_data="count_10")
    )
    builder.row(
        InlineKeyboardButton(text="💯 Свой вариант", callback_data="count_custom")
    )
    builder.row(
        InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_region")
    )

    return builder.as_markup()


def get_tender_actions_keyboard(tender_index: int, tender_url: str = None, has_analysis: bool = False, html_report_path: str = None) -> InlineKeyboardMarkup:
    """
    Клавиатура действий для конкретного тендера.

    Args:
        tender_index: Индекс тендера в списке результатов
        tender_url: URL тендера на zakupki.gov.ru
        has_analysis: Есть ли уже анализ для этого тендера
        html_report_path: Путь к HTML отчету (если есть)
    """
    builder = InlineKeyboardBuilder()

    if not has_analysis:
        builder.row(
            InlineKeyboardButton(text="🤖 Анализировать", callback_data=f"analyze_{tender_index}")
        )
    else:
        # Если анализ уже выполнен, показываем кнопку открытия отчета
        if html_report_path:
            builder.row(
                InlineKeyboardButton(text="📊 Открыть отчет в браузере", callback_data=f"open_report_{tender_index}")
            )

    if tender_url:
        builder.row(
            InlineKeyboardButton(text="🔗 Открыть на zakupki.gov.ru", url=tender_url)
        )

    builder.row(
        InlineKeyboardButton(text="« Назад к списку", callback_data="back_to_results")
    )

    return builder.as_markup()


def get_results_navigation_keyboard(
    current_page: int,
    total_pages: int,
    results_per_page: int = 5
) -> InlineKeyboardMarkup:
    """
    Клавиатура навигации по результатам поиска.

    Args:
        current_page: Текущая страница (начиная с 0)
        total_pages: Общее количество страниц
        results_per_page: Результатов на странице
    """
    builder = InlineKeyboardBuilder()

    # Кнопки навигации
    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton(text="« Предыдущая", callback_data=f"page_{current_page-1}"))
    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="Следующая »", callback_data=f"page_{current_page+1}"))

    if nav_buttons:
        builder.row(*nav_buttons)

    # Кнопка "В главное меню"
    builder.row(
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")
    )

    return builder.as_markup()


def get_tender_item_keyboard(tender_index: int) -> InlineKeyboardMarkup:
    """Кнопка для просмотра деталей тендера."""
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(text="📄 Подробнее", callback_data=f"details_{tender_index}")
    )

    return builder.as_markup()


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура с кнопкой отмены."""
    builder = ReplyKeyboardBuilder()

    builder.row(KeyboardButton(text="❌ Отменить"))

    return builder.as_markup(resize_keyboard=True)


def get_inline_cancel_keyboard() -> InlineKeyboardMarkup:
    """Inline клавиатура с кнопкой отмены."""
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(text="❌ Отменить", callback_data="cancel")
    )

    return builder.as_markup()


def get_region_keyboard(selected_regions: list = None) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора регионов с поддержкой множественного выбора.

    Args:
        selected_regions: Список уже выбранных регионов
    """
    builder = InlineKeyboardBuilder()

    if selected_regions is None:
        selected_regions = []

    # Популярные регионы
    regions = [
        "Москва",
        "Санкт-Петербург",
        "Московская область",
        "Краснодарский край",
        "Свердловская область",
        "Новосибирская область",
        "Республика Татарстан",
        "Нижегородская область",
    ]

    # Добавляем кнопки по 2 в ряд с индикацией выбора
    for i in range(0, len(regions), 2):
        row = []

        # Первый регион в ряду
        region1 = regions[i]
        checkmark1 = "✓ " if region1 in selected_regions else ""
        row.append(InlineKeyboardButton(
            text=f"{checkmark1}{region1}",
            callback_data=f"region_toggle_{region1}"
        ))

        # Второй регион в ряду (если есть)
        if i + 1 < len(regions):
            region2 = regions[i + 1]
            checkmark2 = "✓ " if region2 in selected_regions else ""
            row.append(InlineKeyboardButton(
                text=f"{checkmark2}{region2}",
                callback_data=f"region_toggle_{region2}"
            ))

        builder.row(*row)

    # Разделитель
    builder.row()

    # Кнопки действий
    if selected_regions:
        # Если есть выбранные регионы, показываем кнопку подтверждения
        builder.row(
            InlineKeyboardButton(
                text=f"✅ Продолжить ({len(selected_regions)} регионов)",
                callback_data="region_confirm"
            )
        )
        builder.row(
            InlineKeyboardButton(
                text="❌ Сбросить выбор",
                callback_data="region_clear"
            )
        )
    else:
        # Если ничего не выбрано, предлагаем "Все регионы"
        builder.row(
            InlineKeyboardButton(
                text="🌍 Все регионы",
                callback_data="region_all"
            )
        )

    # Кнопка для ручного ввода региона (всегда показываем)
    builder.row(
        InlineKeyboardButton(
            text="✍️ Ввести регион вручную",
            callback_data="region_custom"
        )
    )

    # Кнопка "Назад"
    builder.row(
        InlineKeyboardButton(
            text="◀️ Назад",
            callback_data="back_to_price"
        )
    )

    return builder.as_markup()


def get_confirmation_keyboard(action: str) -> InlineKeyboardMarkup:
    """
    Клавиатура подтверждения действия.

    Args:
        action: Действие для подтверждения (например, "analysis", "repeat_search")
    """
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(text="✅ Да", callback_data=f"confirm_{action}"),
        InlineKeyboardButton(text="❌ Нет", callback_data=f"cancel_{action}")
    )

    return builder.as_markup()


def get_tenders_list_keyboard(tenders_count: int) -> InlineKeyboardMarkup:
    """
    Клавиатура со списком тендеров для просмотра.

    Args:
        tenders_count: Количество тендеров в результатах
    """
    builder = InlineKeyboardBuilder()

    # Добавляем кнопки для каждого тендера
    for i in range(tenders_count):
        builder.row(
            InlineKeyboardButton(
                text=f"📄 Тендер #{i+1} - Подробнее",
                callback_data=f"details_{i}"
            )
        )

    # Кнопка возврата в главное меню
    builder.row(
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")
    )

    return builder.as_markup()


def get_region_type_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура выбора между регионами и федеральными округами.
    """
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="🗺️ По регионам",
            callback_data="region_type_regions"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🌐 По федеральным округам",
            callback_data="region_type_districts"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🌍 Все регионы",
            callback_data="region_all"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="◀️ Назад",
            callback_data="back_to_price"
        )
    )

    return builder.as_markup()


def get_federal_districts_keyboard(selected_districts: list = None) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора федеральных округов.

    Args:
        selected_districts: Список уже выбранных федеральных округов
    """
    if selected_districts is None:
        selected_districts = []

    builder = InlineKeyboardBuilder()

    # Добавляем кнопки для каждого федерального округа
    districts = [
        ('ЦФО', 'Центральный'),
        ('СЗФО', 'Северо-Западный'),
        ('ЮФО', 'Южный'),
        ('СКФО', 'Северо-Кавказский'),
        ('ПФО', 'Приволжский'),
        ('УФО', 'Уральский'),
        ('СФО', 'Сибирский'),
        ('ДФО', 'Дальневосточный')
    ]

    # Отображаем по 2 округа в ряд
    for i in range(0, len(districts), 2):
        row = []

        # Первый округ в ряду
        code1, name1 = districts[i]
        checkmark1 = "✓ " if code1 in selected_districts else ""
        row.append(InlineKeyboardButton(
            text=f"{checkmark1}{code1}",
            callback_data=f"district_toggle_{code1}"
        ))

        # Второй округ в ряду (если есть)
        if i + 1 < len(districts):
            code2, name2 = districts[i + 1]
            checkmark2 = "✓ " if code2 in selected_districts else ""
            row.append(InlineKeyboardButton(
                text=f"{checkmark2}{code2}",
                callback_data=f"district_toggle_{code2}"
            ))

        builder.row(*row)

    # Разделитель
    builder.row()

    # Кнопки действий
    if selected_districts:
        # Если есть выбранные округа, показываем кнопку подтверждения
        total_regions = sum(len(FEDERAL_DISTRICTS[d]['regions']) for d in selected_districts)
        builder.row(
            InlineKeyboardButton(
                text=f"✅ Продолжить ({len(selected_districts)} ФО, {total_regions} регионов)",
                callback_data="district_confirm"
            )
        )
        builder.row(
            InlineKeyboardButton(
                text="❌ Сбросить выбор",
                callback_data="district_clear"
            )
        )
    else:
        # Если ничего не выбрано
        builder.row(
            InlineKeyboardButton(
                text="🌍 Все регионы",
                callback_data="region_all"
            )
        )

    # Кнопка возврата
    builder.row(
        InlineKeyboardButton(
            text="◀️ Назад",
            callback_data="region_type_back"
        )
    )

    return builder.as_markup()
