"""
Обработчик профиля компании — пошаговый wizard для заполнения реквизитов.

Используется для автогенерации тендерных документов.
"""

import re
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from bot.states import CompanyProfileStates
from tender_sniper.database import get_sniper_db

logger = logging.getLogger(__name__)

router = Router(name='company_profile')

SKIP_BUTTON = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="⏭ Пропустить", callback_data="profile_skip")]
])

CANCEL_BUTTON = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="❌ Отмена", callback_data="profile_cancel")]
])


def _validate_inn(inn: str) -> bool:
    """Валидация ИНН (10 или 12 цифр)."""
    return bool(re.match(r'^\d{10}(\d{2})?$', inn))


def _validate_bik(bik: str) -> bool:
    """Валидация БИК (9 цифр)."""
    return bool(re.match(r'^\d{9}$', bik))


def _validate_ogrn(ogrn: str) -> bool:
    """Валидация ОГРН (13 или 15 цифр)."""
    return bool(re.match(r'^\d{13}(\d{2})?$', ogrn))


def _validate_bank_account(account: str) -> bool:
    """Валидация расчётного/корр. счёта (20 цифр)."""
    return bool(re.match(r'^\d{20}$', account))


def _validate_email(email: str) -> bool:
    """Базовая валидация email."""
    return bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email))


def _validate_phone(phone: str) -> bool:
    """Валидация телефона (допускаем +7, 8, и т.д.)."""
    digits = re.sub(r'[\s\-\(\)+]', '', phone)
    return len(digits) >= 10 and digits.isdigit()


def _format_profile_text(data: dict) -> str:
    """Форматирование профиля для отображения."""
    lines = ["<b>📋 Профиль компании</b>\n"]

    if data.get('company_name'):
        lines.append(f"🏢 <b>Название:</b> {data['company_name']}")
    if data.get('inn'):
        lines.append(f"📄 <b>ИНН:</b> {data['inn']}")
    if data.get('kpp'):
        lines.append(f"📄 <b>КПП:</b> {data['kpp']}")
    if data.get('ogrn'):
        lines.append(f"📄 <b>ОГРН:</b> {data['ogrn']}")
    if data.get('legal_address'):
        lines.append(f"📍 <b>Юр. адрес:</b> {data['legal_address']}")
    if data.get('director_name'):
        pos = data.get('director_position', 'Директор')
        lines.append(f"👤 <b>Руководитель:</b> {pos} — {data['director_name']}")
    if data.get('phone'):
        lines.append(f"📞 <b>Телефон:</b> {data['phone']}")
    if data.get('email'):
        lines.append(f"📧 <b>Email:</b> {data['email']}")
    if data.get('bank_name'):
        lines.append(f"🏦 <b>Банк:</b> {data['bank_name']}")
    if data.get('bank_bik'):
        lines.append(f"🏦 <b>БИК:</b> {data['bank_bik']}")
    if data.get('bank_account'):
        lines.append(f"🏦 <b>Р/С:</b> {data['bank_account']}")

    return "\n".join(lines)


# ============================================
# ENTRY POINTS
# ============================================

@router.message(Command("profile"))
async def cmd_profile(message: Message, state: FSMContext):
    """Команда /profile — показать или начать заполнение профиля."""
    await show_profile_or_start_wizard(message, state)


@router.callback_query(F.data == "company_profile")
async def cb_profile(callback: CallbackQuery, state: FSMContext):
    """Кнопка 'Профиль компании' из главного меню."""
    await callback.answer()
    await show_profile_or_start_wizard(callback.message, state, edit=True)


async def show_profile_or_start_wizard(message: Message, state: FSMContext, edit: bool = False):
    """Показать профиль или предложить начать заполнение."""
    db = await get_sniper_db()
    user = await db.get_user_by_telegram_id(message.chat.id)
    if not user:
        text = "❌ Пользователь не найден. Нажмите /start"
        if edit:
            await message.edit_text(text)
        else:
            await message.answer(text)
        return

    profile = await db.get_company_profile(user['id'])

    if profile and profile.get('company_name'):
        # Показываем существующий профиль
        text = _format_profile_text(profile)
        complete = profile.get('is_complete', False)
        status = "✅ Профиль заполнен" if complete else "⚠️ Профиль не полностью заполнен"
        text += f"\n\n{status}"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Редактировать", callback_data="profile_edit")],
            [InlineKeyboardButton(text="🔄 Заполнить заново", callback_data="profile_restart")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
        ])

        if edit:
            await message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        # Нет профиля — начинаем wizard
        text = (
            "📋 <b>Профиль компании</b>\n\n"
            "Заполните реквизиты компании один раз — и система будет автоматически "
            "генерировать документы для каждого интересного тендера.\n\n"
            "Начнём с названия компании."
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="▶️ Начать заполнение", callback_data="profile_start_wizard")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
        ])

        if edit:
            await message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


# ============================================
# WIZARD START / RESTART
# ============================================

@router.callback_query(F.data == "profile_start_wizard")
async def start_wizard(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await state.set_state(CompanyProfileStates.waiting_for_company_name)
    await callback.message.edit_text(
        "🏢 <b>Шаг 1/11</b>: Введите <b>полное наименование</b> компании\n\n"
        "Пример: Общество с ограниченной ответственностью «Рога и Копыта»",
        parse_mode="HTML",
        reply_markup=CANCEL_BUTTON
    )


@router.callback_query(F.data == "profile_restart")
async def restart_wizard(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await state.set_state(CompanyProfileStates.waiting_for_company_name)
    await callback.message.edit_text(
        "🏢 <b>Шаг 1/11</b>: Введите <b>полное наименование</b> компании\n\n"
        "Пример: Общество с ограниченной ответственностью «Рога и Копыта»",
        parse_mode="HTML",
        reply_markup=CANCEL_BUTTON
    )


# ============================================
# CANCEL
# ============================================

@router.callback_query(F.data == "profile_cancel")
async def cancel_wizard(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Отменено")
    await state.clear()
    await callback.message.edit_text(
        "❌ Заполнение профиля отменено.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])
    )


@router.callback_query(F.data == "profile_skip")
async def skip_field(callback: CallbackQuery, state: FSMContext):
    """Пропуск необязательного поля."""
    await callback.answer()
    current_state = await state.get_state()

    # Определяем следующий шаг
    transitions = {
        CompanyProfileStates.waiting_for_ogrn.state: ("ogrn", CompanyProfileStates.waiting_for_legal_address,
            "📍 <b>Шаг 4/11</b>: Введите <b>юридический адрес</b>"),
        CompanyProfileStates.waiting_for_director_position.state: ("director_position", CompanyProfileStates.waiting_for_phone,
            "📞 <b>Шаг 7/11</b>: Введите <b>контактный телефон</b>\n\nПример: +7 (495) 123-45-67"),
        CompanyProfileStates.waiting_for_bank_name.state: ("bank_name", CompanyProfileStates.waiting_for_bank_bik,
            "🏦 <b>Шаг 10/11</b>: Введите <b>БИК</b> банка (9 цифр)"),
        CompanyProfileStates.waiting_for_bank_bik.state: ("bank_bik", CompanyProfileStates.waiting_for_bank_account,
            "🏦 <b>Шаг 11/11</b>: Введите <b>расчётный счёт</b> (20 цифр)"),
        CompanyProfileStates.waiting_for_bank_account.state: ("bank_account", None, None),
    }

    if current_state in transitions:
        field, next_state, prompt = transitions[current_state]
        if next_state is None:
            # Финальный шаг — показываем подтверждение
            await show_confirmation(callback.message, state, edit=True)
        else:
            await state.set_state(next_state)
            await callback.message.edit_text(prompt, parse_mode="HTML", reply_markup=SKIP_BUTTON)
    else:
        await callback.message.edit_text("⚠️ Этот шаг нельзя пропустить.")


# ============================================
# WIZARD STEPS
# ============================================

@router.message(CompanyProfileStates.waiting_for_company_name)
async def process_company_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 3:
        await message.answer("❌ Слишком короткое название. Попробуйте ещё раз.")
        return

    # Автоматически определяем правовую форму
    legal_form = None
    for form in ['ООО', 'ИП', 'АО', 'ПАО', 'ЗАО', 'ГУП', 'МУП']:
        if form in name.upper():
            legal_form = form
            break

    await state.update_data(company_name=name, legal_form=legal_form)
    await state.set_state(CompanyProfileStates.waiting_for_inn)
    await message.answer(
        "📄 <b>Шаг 2/11</b>: Введите <b>ИНН</b> (10 цифр для юрлица, 12 для ИП)",
        parse_mode="HTML",
        reply_markup=CANCEL_BUTTON
    )


@router.message(CompanyProfileStates.waiting_for_inn)
async def process_inn(message: Message, state: FSMContext):
    inn = message.text.strip()
    if not _validate_inn(inn):
        await message.answer("❌ ИНН должен содержать 10 или 12 цифр. Попробуйте ещё раз.")
        return

    # Автоопределение КПП (для ИП не нужен)
    kpp = None
    if len(inn) == 10:
        # Юрлицо — КПП обязателен, запросим позже или возьмём из контекста
        pass

    await state.update_data(inn=inn, kpp=kpp)
    await state.set_state(CompanyProfileStates.waiting_for_ogrn)
    await message.answer(
        "📄 <b>Шаг 3/11</b>: Введите <b>ОГРН</b> (13 цифр для юрлица, 15 для ИП)\n\n"
        "Можно пропустить.",
        parse_mode="HTML",
        reply_markup=SKIP_BUTTON
    )


@router.message(CompanyProfileStates.waiting_for_ogrn)
async def process_ogrn(message: Message, state: FSMContext):
    ogrn = message.text.strip()
    if not _validate_ogrn(ogrn):
        await message.answer("❌ ОГРН должен содержать 13 или 15 цифр. Попробуйте ещё раз или нажмите 'Пропустить'.",
                             reply_markup=SKIP_BUTTON)
        return

    await state.update_data(ogrn=ogrn)
    await state.set_state(CompanyProfileStates.waiting_for_legal_address)
    await message.answer(
        "📍 <b>Шаг 4/11</b>: Введите <b>юридический адрес</b>",
        parse_mode="HTML",
        reply_markup=CANCEL_BUTTON
    )


@router.message(CompanyProfileStates.waiting_for_legal_address)
async def process_legal_address(message: Message, state: FSMContext):
    address = message.text.strip()
    if len(address) < 10:
        await message.answer("❌ Адрес слишком короткий. Укажите полный адрес.")
        return

    await state.update_data(legal_address=address)
    await state.set_state(CompanyProfileStates.waiting_for_director_name)
    await message.answer(
        "👤 <b>Шаг 5/11</b>: Введите <b>ФИО руководителя</b>\n\n"
        "Пример: Иванов Иван Иванович",
        parse_mode="HTML",
        reply_markup=CANCEL_BUTTON
    )


@router.message(CompanyProfileStates.waiting_for_director_name)
async def process_director_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 5:
        await message.answer("❌ Введите полное ФИО руководителя.")
        return

    await state.update_data(director_name=name)
    await state.set_state(CompanyProfileStates.waiting_for_director_position)
    await message.answer(
        "👤 <b>Шаг 6/11</b>: Введите <b>должность руководителя</b>\n\n"
        "Примеры: Генеральный директор, Директор, Индивидуальный предприниматель\n\n"
        "Можно пропустить (по умолчанию: Генеральный директор).",
        parse_mode="HTML",
        reply_markup=SKIP_BUTTON
    )


@router.message(CompanyProfileStates.waiting_for_director_position)
async def process_director_position(message: Message, state: FSMContext):
    position = message.text.strip()
    await state.update_data(director_position=position)
    await state.set_state(CompanyProfileStates.waiting_for_phone)
    await message.answer(
        "📞 <b>Шаг 7/11</b>: Введите <b>контактный телефон</b>\n\nПример: +7 (495) 123-45-67",
        parse_mode="HTML",
        reply_markup=CANCEL_BUTTON
    )


@router.message(CompanyProfileStates.waiting_for_phone)
async def process_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    if not _validate_phone(phone):
        await message.answer("❌ Некорректный формат телефона. Попробуйте ещё раз.")
        return

    await state.update_data(phone=phone)
    await state.set_state(CompanyProfileStates.waiting_for_email)
    await message.answer(
        "📧 <b>Шаг 8/11</b>: Введите <b>email</b> компании",
        parse_mode="HTML",
        reply_markup=CANCEL_BUTTON
    )


@router.message(CompanyProfileStates.waiting_for_email)
async def process_email(message: Message, state: FSMContext):
    email = message.text.strip()
    if not _validate_email(email):
        await message.answer("❌ Некорректный email. Попробуйте ещё раз.")
        return

    await state.update_data(email=email)
    await state.set_state(CompanyProfileStates.waiting_for_bank_name)
    await message.answer(
        "🏦 <b>Шаг 9/11</b>: Введите <b>название банка</b>\n\n"
        "Пример: ПАО Сбербанк\n\nМожно пропустить.",
        parse_mode="HTML",
        reply_markup=SKIP_BUTTON
    )


@router.message(CompanyProfileStates.waiting_for_bank_name)
async def process_bank_name(message: Message, state: FSMContext):
    bank_name = message.text.strip()
    await state.update_data(bank_name=bank_name)
    await state.set_state(CompanyProfileStates.waiting_for_bank_bik)
    await message.answer(
        "🏦 <b>Шаг 10/11</b>: Введите <b>БИК</b> банка (9 цифр)\n\nМожно пропустить.",
        parse_mode="HTML",
        reply_markup=SKIP_BUTTON
    )


@router.message(CompanyProfileStates.waiting_for_bank_bik)
async def process_bank_bik(message: Message, state: FSMContext):
    bik = message.text.strip()
    if not _validate_bik(bik):
        await message.answer("❌ БИК должен содержать 9 цифр. Попробуйте ещё раз или пропустите.",
                             reply_markup=SKIP_BUTTON)
        return

    await state.update_data(bank_bik=bik)
    await state.set_state(CompanyProfileStates.waiting_for_bank_account)
    await message.answer(
        "🏦 <b>Шаг 11/11</b>: Введите <b>расчётный счёт</b> (20 цифр)\n\nМожно пропустить.",
        parse_mode="HTML",
        reply_markup=SKIP_BUTTON
    )


@router.message(CompanyProfileStates.waiting_for_bank_account)
async def process_bank_account(message: Message, state: FSMContext):
    account = message.text.strip()
    if not _validate_bank_account(account):
        await message.answer("❌ Расчётный счёт должен содержать 20 цифр. Попробуйте ещё раз или пропустите.",
                             reply_markup=SKIP_BUTTON)
        return

    await state.update_data(bank_account=account)
    await show_confirmation(message, state)


# ============================================
# CONFIRMATION
# ============================================

async def show_confirmation(message: Message, state: FSMContext, edit: bool = False):
    """Показать итоговый экран для подтверждения."""
    data = await state.get_data()
    await state.set_state(CompanyProfileStates.confirming_profile)

    text = _format_profile_text(data)
    text += "\n\n<b>Всё верно?</b>"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сохранить", callback_data="profile_confirm_save")],
        [InlineKeyboardButton(text="✏️ Изменить поле", callback_data="profile_edit_field")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="profile_cancel")],
    ])

    if edit:
        await message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "profile_confirm_save")
async def confirm_save(callback: CallbackQuery, state: FSMContext):
    """Сохранение профиля в БД."""
    await callback.answer("Сохраняем...")
    data = await state.get_data()

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)
        if not user:
            await callback.message.edit_text("❌ Пользователь не найден.")
            await state.clear()
            return

        # Устанавливаем дефолтные значения
        if not data.get('director_position'):
            data['director_position'] = 'Генеральный директор'
        if not data.get('director_basis'):
            data['director_basis'] = 'Устав'

        await db.upsert_company_profile(user['id'], data)
        await db.check_profile_completeness(user['id'])
        await state.clear()

        await callback.message.edit_text(
            "✅ <b>Профиль компании сохранён!</b>\n\n"
            "Теперь при нажатии «Интересно» на тендер система автоматически "
            "сгенерирует пакет документов:\n"
            "📄 Заявка на участие\n"
            "📄 Декларация соответствия\n"
            "📄 Согласие с условиями\n"
            "📄 Техническое предложение",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Мой профиль", callback_data="company_profile")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
            ]),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка сохранения профиля: {e}", exc_info=True)
        await callback.message.edit_text(f"❌ Ошибка сохранения: {str(e)[:200]}")
        await state.clear()


# ============================================
# FIELD EDITING
# ============================================

EDITABLE_FIELDS = {
    'company_name': ('🏢 Название компании', CompanyProfileStates.waiting_for_company_name),
    'inn': ('📄 ИНН', CompanyProfileStates.waiting_for_inn),
    'ogrn': ('📄 ОГРН', CompanyProfileStates.waiting_for_ogrn),
    'legal_address': ('📍 Юр. адрес', CompanyProfileStates.waiting_for_legal_address),
    'director_name': ('👤 ФИО руководителя', CompanyProfileStates.waiting_for_director_name),
    'director_position': ('👤 Должность руководителя', CompanyProfileStates.waiting_for_director_position),
    'phone': ('📞 Телефон', CompanyProfileStates.waiting_for_phone),
    'email': ('📧 Email', CompanyProfileStates.waiting_for_email),
    'bank_name': ('🏦 Банк', CompanyProfileStates.waiting_for_bank_name),
    'bank_bik': ('🏦 БИК', CompanyProfileStates.waiting_for_bank_bik),
    'bank_account': ('🏦 Расчётный счёт', CompanyProfileStates.waiting_for_bank_account),
}


@router.callback_query(F.data == "profile_edit")
async def edit_profile_menu(callback: CallbackQuery, state: FSMContext):
    """Меню выбора поля для редактирования (из просмотра профиля)."""
    await callback.answer()

    # Загружаем текущий профиль из БД в FSM data
    db = await get_sniper_db()
    user = await db.get_user_by_telegram_id(callback.from_user.id)
    if user:
        profile = await db.get_company_profile(user['id'])
        if profile:
            await state.update_data(**{k: v for k, v in profile.items()
                                       if k not in ('id', 'user_id', 'is_complete', 'created_at', 'updated_at')})

    await _show_edit_field_menu(callback.message, edit=True)


@router.callback_query(F.data == "profile_edit_field")
async def edit_field_menu(callback: CallbackQuery, state: FSMContext):
    """Меню выбора поля для редактирования (из wizard confirmation)."""
    await callback.answer()
    await _show_edit_field_menu(callback.message, edit=True)


async def _show_edit_field_menu(message: Message, edit: bool = False):
    """Показать меню выбора поля."""
    buttons = []
    for field_key, (label, _) in EDITABLE_FIELDS.items():
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"profile_edit_{field_key}")])
    buttons.append([InlineKeyboardButton(text="✅ Готово", callback_data="profile_edit_done")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    text = "✏️ <b>Выберите поле для редактирования:</b>"

    if edit:
        await message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("profile_edit_") & ~F.data.in_({"profile_edit_field", "profile_edit_done"}))
async def select_field_to_edit(callback: CallbackQuery, state: FSMContext):
    """Выбор конкретного поля для редактирования."""
    await callback.answer()
    field_key = callback.data.replace("profile_edit_", "")

    if field_key not in EDITABLE_FIELDS:
        return

    label, target_state = EDITABLE_FIELDS[field_key]
    await state.update_data(_editing_field=field_key)
    await state.set_state(CompanyProfileStates.editing_field)

    await callback.message.edit_text(
        f"✏️ Введите новое значение для <b>{label}</b>:",
        parse_mode="HTML",
        reply_markup=CANCEL_BUTTON
    )


@router.message(CompanyProfileStates.editing_field)
async def process_field_edit(message: Message, state: FSMContext):
    """Обработка ввода нового значения поля."""
    data = await state.get_data()
    field_key = data.get('_editing_field')

    if not field_key:
        await message.answer("⚠️ Ошибка. Попробуйте заново.")
        await state.clear()
        return

    value = message.text.strip()

    # Валидация
    validators = {
        'inn': (_validate_inn, "ИНН должен содержать 10 или 12 цифр"),
        'ogrn': (_validate_ogrn, "ОГРН должен содержать 13 или 15 цифр"),
        'phone': (_validate_phone, "Некорректный формат телефона"),
        'email': (_validate_email, "Некорректный email"),
        'bank_bik': (_validate_bik, "БИК должен содержать 9 цифр"),
        'bank_account': (_validate_bank_account, "Счёт должен содержать 20 цифр"),
    }

    if field_key in validators:
        validator, error_msg = validators[field_key]
        if not validator(value):
            await message.answer(f"❌ {error_msg}. Попробуйте ещё раз.")
            return

    await state.update_data(**{field_key: value, '_editing_field': None})
    await show_confirmation(message, state)


@router.callback_query(F.data == "profile_edit_done")
async def edit_done(callback: CallbackQuery, state: FSMContext):
    """Завершение редактирования — показать подтверждение."""
    await callback.answer()
    await show_confirmation(callback.message, state, edit=True)
