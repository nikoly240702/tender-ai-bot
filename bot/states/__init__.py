"""
Состояния бота для управления диалогом.
"""

from aiogram.fsm.state import State, StatesGroup


class SearchStates(StatesGroup):
    """Состояния для процесса поиска тендеров."""

    # Ввод параметров поиска
    waiting_for_query = State()           # Ожидание поискового запроса
    waiting_for_tender_type = State()     # Ожидание выбора типа закупки
    waiting_for_price_range = State()     # Ожидание выбора ценового диапазона
    waiting_for_price_min = State()       # Ожидание минимальной цены (если выбран "Свой вариант")
    waiting_for_price_max = State()       # Ожидание максимальной цены
    waiting_for_region = State()          # Ожидание выбора региона
    waiting_for_custom_region = State()   # Ожидание ручного ввода региона
    waiting_for_tender_count = State()    # Ожидание количества тендеров
    waiting_for_custom_count = State()    # Ожидание кастомного количества тендеров

    # Просмотр результатов
    viewing_results = State()             # Просмотр списка результатов
    viewing_tender_details = State()      # Просмотр деталей конкретного тендера

    # Анализ
    confirming_analysis = State()         # Подтверждение запуска анализа


class HistoryStates(StatesGroup):
    """Состояния для работы с историей поисков."""

    viewing_history = State()             # Просмотр истории поисков
    confirming_repeat = State()           # Подтверждение повтора поиска


class CompanyProfileStates(StatesGroup):
    """Состояния для wizard заполнения профиля компании."""

    waiting_for_company_name = State()
    waiting_for_inn = State()
    waiting_for_ogrn = State()
    waiting_for_legal_address = State()
    waiting_for_director_name = State()
    waiting_for_director_position = State()
    waiting_for_phone = State()
    waiting_for_email = State()
    waiting_for_bank_name = State()
    waiting_for_bank_bik = State()
    waiting_for_bank_account = State()
    confirming_profile = State()
    editing_field = State()
