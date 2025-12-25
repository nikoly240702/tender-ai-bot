"""
Обработчики команды /start и главного меню.
"""

import logging
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext

logger = logging.getLogger(__name__)
router = Router()

# Контакт для связи
DEVELOPER_CONTACT = "@nikolai_chizhik"

# Сообщение об ошибке для бета-теста
BETA_ERROR_MESSAGE = (
    "❌ <b>Произошла ошибка</b>\n\n"
    "🧪 Бот находится в стадии бета-тестирования.\n\n"
    f"Если вы столкнулись с ошибкой или багом, пожалуйста, "
    f"свяжитесь с разработчиком: {DEVELOPER_CONTACT}\n\n"
    "Попробуйте нажать /start для перезапуска."
)


def get_main_keyboard(is_monitoring_enabled: bool = True) -> ReplyKeyboardMarkup:
    """
    Возвращает постоянную клавиатуру управления ботом.
    Отображается справа от текстовой строки.

    Args:
        is_monitoring_enabled: Статус автомониторинга для динамической кнопки
    """
    # Динамическая кнопка мониторинга
    if is_monitoring_enabled:
        monitoring_btn = KeyboardButton(text="⏸️ Пауза мониторинга")
    else:
        monitoring_btn = KeyboardButton(text="▶️ Вкл. мониторинг")

    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏠 Главное меню"), monitoring_btn],
            [KeyboardButton(text="🎯 Tender Sniper"), KeyboardButton(text="📊 Мои фильтры")],
            [KeyboardButton(text="📊 Все мои тендеры")],
            [KeyboardButton(text="⭐ Избранное"), KeyboardButton(text="📈 Статистика")]
        ],
        resize_keyboard=True,
        persistent=True  # Клавиатура остается видимой всегда
    )
    return keyboard


async def get_main_keyboard_for_user(telegram_id: int) -> ReplyKeyboardMarkup:
    """
    Возвращает клавиатуру с актуальным статусом мониторинга для пользователя.
    """
    from tender_sniper.database import get_sniper_db
    try:
        db = await get_sniper_db()
        is_monitoring_enabled = await db.get_monitoring_status(telegram_id)
    except Exception:
        is_monitoring_enabled = True  # По умолчанию включен
    return get_main_keyboard(is_monitoring_enabled)


@router.message(CommandStart())
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """
    Обработчик команды /start.
    Приветствует пользователя и показывает главное меню.
    Для новых пользователей запускает онбординг.

    ВАЖНО: Работает в любом состоянии FSM для возврата в главное меню.
    """
    # Получаем текущее состояние для логирования
    current_state = await state.get_state()
    if current_state:
        logger.info(f"Пользователь {message.from_user.id} вызвал /start из состояния {current_state}")

    # Очищаем любое предыдущее состояние
    await state.clear()

    # Проверяем реферальную ссылку (/start ref_XXXXXXXX)
    if message.text and "ref_" in message.text:
        try:
            parts = message.text.split()
            for part in parts:
                if part.startswith("ref_"):
                    referral_code = part[4:].upper()
                    logger.info(f"Referral code detected: {referral_code} for user {message.from_user.id}")
                    # Сохраняем код в state для обработки после регистрации
                    await state.update_data(referral_code=referral_code)
                    break
        except Exception as e:
            logger.error(f"Error parsing referral code: {e}")

    # Проверяем, новый ли пользователь
    # Если команда /start onboarding - принудительно показываем онбординг
    force_onboarding = message.text and "onboarding" in message.text.lower()

    if force_onboarding:
        logger.info(f"Принудительный запуск онбординга для пользователя {message.from_user.id}")
        from bot.handlers.onboarding import start_onboarding
        await start_onboarding(message, state)
        return

    # Проверяем, новый ли пользователь (автоматический онбординг)
    try:
        from bot.handlers.onboarding import is_first_time_user, start_onboarding

        if await is_first_time_user(message.from_user.id):
            logger.info(f"Первый запуск для пользователя {message.from_user.id} - показываем онбординг")
            await start_onboarding(message, state)
            return
    except Exception as e:
        logger.error(f"Ошибка проверки нового пользователя: {e}")

    welcome_text = (
        "👋 <b>Добро пожаловать в Tender Sniper!</b>\n\n"
        "🎯 Автоматический мониторинг и уведомления о тендерах zakupki.gov.ru\n\n"
        "<b>Что я умею:</b>\n"
        "🔍 Мгновенный поиск по вашим критериям\n"
        "🎯 Умное сопоставление (scoring 0-100)\n"
        "📱 Автоматические уведомления о новых тендерах\n"
        "📊 Продвинутые фильтры (регион, закон, тип)\n\n"
        "<b>Ваш тариф:</b> 🆓 Бесплатный\n"
        "• 5 фильтров мониторинга\n"
        "• 10 уведомлений в день\n\n"
        "<i>Нажмите кнопку ниже для начала!</i>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Запустить Tender Sniper", callback_data="sniper_menu")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="sniper_help")]
    ])

    # Получаем клавиатуру с актуальным статусом мониторинга
    reply_keyboard = await get_main_keyboard_for_user(message.from_user.id)

    await message.answer(
        welcome_text,
        reply_markup=reply_keyboard,
        parse_mode="HTML"
    )

    await message.answer(
        "Выберите действие:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Обработчик команды /help."""
    try:
        help_text = (
            "❓ <b>Справка Tender Sniper</b>\n\n"

            "🧪 <i>Бот находится в стадии бета-тестирования</i>\n\n"

            "<b>Что такое Tender Sniper?</b>\n"
            "Это система автоматического мониторинга новых тендеров на zakupki.gov.ru. "
            "Вы создаете фильтры с вашими критериями, и бот автоматически уведомляет вас "
            "о подходящих тендерах.\n\n"

            "<b>Как это работает?</b>\n"
            "1. Создайте фильтр с ключевыми словами и критериями\n"
            "2. Бот проверяет новые тендеры каждые 5 минут\n"
            "3. При совпадении вы получаете уведомление\n"
            "4. Можете сразу перейти к анализу или открыть на zakupki.gov.ru\n\n"

            "<b>Scoring (релевантность)</b>\n"
            "Каждый тендер оценивается по шкале 0-100:\n"
            "• 80-100: Отличное совпадение 🔥\n"
            "• 60-79: Хорошее совпадение ✨\n"
            "• 40-59: Среднее совпадение 📌\n\n"

            "<b>Квоты и лимиты</b>\n"
            "Зависят от вашего тарифа:\n"
            "• Free: 5 фильтров, 10 уведомлений/день\n"
            "• Basic: 15 фильтров, 50 уведомлений/день\n"
            "• Premium: Unlimited\n\n"

            "━━━━━━━━━━━━━━━━━━━━━\n"
            "<b>📬 Контакты</b>\n\n"
            "Вопросы, предложения или нашли баг?\n"
            f"Свяжитесь с разработчиком: {DEVELOPER_CONTACT}"
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎯 Открыть Tender Sniper", callback_data="sniper_menu")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])

        await message.answer(help_text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка в cmd_help: {e}", exc_info=True)
        await message.answer(BETA_ERROR_MESSAGE, parse_mode="HTML")


@router.callback_query(F.data == "main_menu")
async def return_to_main_menu(callback: CallbackQuery, state: FSMContext):
    """
    Возврат в главное меню из любого состояния.
    Очищает FSM state и показывает стартовое сообщение.
    """
    try:
        await callback.answer()

        # Очищаем любое состояние
        await state.clear()

        welcome_text = (
            "👋 <b>Добро пожаловать в Tender Sniper!</b>\n\n"
            "🎯 Автоматический мониторинг и уведомления о тендерах zakupki.gov.ru\n\n"
            "<b>Что я умею:</b>\n"
            "🔍 Мгновенный поиск по вашим критериям\n"
            "🎯 Умное сопоставление (scoring 0-100)\n"
            "📱 Автоматические уведомления о новых тендерах\n"
            "📊 Продвинутые фильтры (регион, закон, тип)\n\n"
            "<b>Ваш тариф:</b> 🆓 Бесплатный\n"
            "• 5 фильтров мониторинга\n"
            "• 15 уведомлений в день\n\n"
            "<i>Нажмите кнопку ниже для начала!</i>"
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎯 Запустить Tender Sniper", callback_data="sniper_menu")],
            [InlineKeyboardButton(text="❓ Помощь", callback_data="sniper_help")]
        ])

        await callback.message.edit_text(
            welcome_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка в return_to_main_menu: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "start_onboarding")
async def callback_start_onboarding(callback: CallbackQuery, state: FSMContext):
    """Запуск онбординга по кнопке."""
    try:
        await callback.answer("👋 Запускаю экскурсию...")

        from bot.handlers.onboarding import start_onboarding
        await start_onboarding(callback.message, state)
    except Exception as e:
        logger.error(f"Ошибка в callback_start_onboarding: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


# ============================================
# ОБРАБОТЧИКИ ПОСТОЯННОЙ КЛАВИАТУРЫ
# ============================================

@router.message(F.text == "🏠 Главное меню")
async def keyboard_main_menu(message: Message, state: FSMContext):
    """Обработчик кнопки 'Главное меню' из постоянной клавиатуры."""
    # Используем существующую логику cmd_start
    await cmd_start(message, state)


@router.message(F.text == "🎯 Tender Sniper")
async def keyboard_tender_sniper(message: Message):
    """Обработчик кнопки 'Tender Sniper' из постоянной клавиатуры."""
    try:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Создать фильтр", callback_data="sniper_new_search")],
            [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
            [InlineKeyboardButton(text="📊 Все мои тендеры", callback_data="sniper_all_tenders")]
        ])

        await message.answer(
            "🎯 <b>Tender Sniper</b>\n\nВыберите действие:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка в keyboard_tender_sniper: {e}", exc_info=True)
        await message.answer(BETA_ERROR_MESSAGE, parse_mode="HTML")


@router.message(F.text == "📊 Мои фильтры")
async def keyboard_my_filters(message: Message):
    """Обработчик кнопки 'Мои фильтры' из постоянной клавиатуры."""
    try:
        # Импортируем и вызываем напрямую handler из sniper.py
        from bot.handlers.sniper import show_my_filters_message
        await show_my_filters_message(message)
    except Exception as e:
        logger.error(f"Ошибка в keyboard_my_filters: {e}", exc_info=True)
        await message.answer(BETA_ERROR_MESSAGE, parse_mode="HTML")


@router.message(F.text == "📊 Все мои тендеры")
async def keyboard_all_tenders(message: Message, state: FSMContext):
    """Обработчик кнопки 'Все мои тендеры' из постоянной клавиатуры."""
    # Импортируем функции напрямую
    from bot.handlers.all_tenders import get_all_user_tenders, show_tenders_menu, AllTendersStates

    try:
        # Показываем промежуточное сообщение
        loading_msg = await message.answer("⏳ Загрузка ваших тендеров...")

        # Получаем все тендеры напрямую
        tenders = await get_all_user_tenders(message.from_user.id)

        # Удаляем сообщение о загрузке
        try:
            await loading_msg.delete()
        except:
            pass

        if not tenders:
            await message.answer(
                "📊 <b>Все мои тендеры</b>\n\n"
                "У вас пока нет найденных тендеров.\n\n"
                "Используйте:\n"
                "• 🔍 <b>Мгновенный поиск</b> для быстрого поиска\n"
                "• 🎨 <b>Фильтры</b> для автоматического мониторинга",
                parse_mode="HTML"
            )
            return

        # Сохраняем тендеры в состоянии
        await state.update_data(all_tenders=tenders, filter_params={'sort_by': 'date_desc'})
        await state.set_state(AllTendersStates.viewing_list)

        # Показываем меню фильтрации
        await show_tenders_menu(message, tenders, {}, state)

    except Exception as e:
        logger.error(f"Ошибка загрузки тендеров: {e}", exc_info=True)
        await message.answer("❌ Ошибка при загрузке тендеров")


@router.message(F.text == "⭐ Избранное")
async def keyboard_favorites(message: Message):
    """Обработчик кнопки 'Избранное' из постоянной клавиатуры."""
    try:
        # Импортируем обработчик из user_management
        from bot.handlers.user_management import favorites_command
        await favorites_command(message)
    except Exception as e:
        logger.error(f"Ошибка в keyboard_favorites: {e}", exc_info=True)
        await message.answer(BETA_ERROR_MESSAGE, parse_mode="HTML")


@router.message(F.text == "📈 Статистика")
async def keyboard_stats(message: Message):
    """Обработчик кнопки 'Статистика' из постоянной клавиатуры."""
    try:
        # Импортируем обработчик из user_management
        from bot.handlers.user_management import stats_command
        await stats_command(message)
    except Exception as e:
        logger.error(f"Ошибка в keyboard_stats: {e}", exc_info=True)
        await message.answer(BETA_ERROR_MESSAGE, parse_mode="HTML")


# ============================================
# АДМИНСКИЕ КОМАНДЫ
# ============================================

APOLOGY_MESSAGE = """
🔧 <b>Технические работы завершены</b>

Уважаемый пользователь!

Приносим извинения за временные неполадки. В период 17-18 декабря некоторые HTML-отчеты формировались некорректно (приходили пустыми).

✅ <b>Проблема устранена</b>

Мы улучшили алгоритм поиска для повышения точности результатов.

📋 <b>Если вы делали мгновенный поиск</b> и получили пустой отчет — пожалуйста, повторите поиск через меню бота. Теперь всё работает корректно.

Спасибо за понимание! 🙏

<i>С уважением, команда Tender Sniper</i>
"""


@router.message(Command("send_apology"))
async def admin_send_apology(message: Message):
    """Админская команда для отправки извинения + отчетов СЕБЕ (тест перед рассылкой)."""
    from bot.config import BotConfig
    from tender_sniper.database import get_sniper_db
    from tender_sniper.instant_search import InstantSearch
    from aiogram.types import BufferedInputFile
    from datetime import datetime
    import json

    if BotConfig.ADMIN_USER_ID and message.from_user.id != BotConfig.ADMIN_USER_ID:
        return  # Только для админа

    telegram_id = message.from_user.id

    # Отправляем извинение
    await message.answer(APOLOGY_MESSAGE, parse_mode="HTML")

    # Получаем фильтры текущего пользователя
    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(telegram_id)

        if not user:
            await message.answer("⚠️ Пользователь не найден в базе")
            return

        user_id = user['id']
        filters = await db.get_user_filters(user_id)

        if not filters:
            await message.answer("⚠️ У вас нет активных фильтров")
            return

        await message.answer(f"📋 Найдено {len(filters)} фильтров. Генерирую отчеты...")

        searcher = InstantSearch()
        reports_sent = 0

        for filter_data in filters:
            filter_name = filter_data.get('name', 'Без названия')
            keywords = filter_data.get('keywords', [])

            if isinstance(keywords, str):
                try:
                    keywords = json.loads(keywords)
                except:
                    keywords = []

            if not keywords:
                continue

            try:
                results = await searcher.search_by_filter(
                    filter_data=filter_data,
                    max_tenders=20,
                    expanded_keywords=[]
                )

                matches = results.get('matches', [])
                total_found = results.get('total_found', 0)

                if matches:
                    html_content = searcher.generate_html_report(
                        tenders=matches,
                        filter_name=filter_name,
                        stats=results.get('stats', {})
                    )

                    filename = f"{filter_name[:20]}_{datetime.now().strftime('%H%M%S')}.html"
                    file = BufferedInputFile(
                        html_content.encode('utf-8'),
                        filename=filename
                    )

                    await message.answer_document(
                        document=file,
                        caption=f"📄 <b>{filter_name}</b>\n\n"
                               f"RSS: {total_found} → После скоринга: {len(matches)}\n"
                               f"Ключевые слова: {', '.join(keywords[:3])}",
                        parse_mode="HTML"
                    )
                    reports_sent += 1
                else:
                    await message.answer(
                        f"⚠️ <b>{filter_name}</b>\n"
                        f"RSS: {total_found}, после скоринга: 0",
                        parse_mode="HTML"
                    )

            except Exception as e:
                logger.error(f"Ошибка для фильтра {filter_name}: {e}")
                await message.answer(f"❌ Ошибка для фильтра {filter_name}: {e}")

        await message.answer(f"✅ Тест завершен! Отправлено отчетов: {reports_sent}")

    except Exception as e:
        logger.error(f"Ошибка в send_apology: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("send_apology_all"))
async def admin_send_apology_all(message: Message):
    """Админская команда для отправки извинений + отчетов пользователям за сегодня.

    Использование:
        /send_apology_all - отправить тем, кто был активен сегодня
        /send_apology_all 2 - отправить тем, кто был активен за последние 2 дня
    """
    from bot.config import BotConfig
    from tender_sniper.database import get_sniper_db
    from tender_sniper.instant_search import InstantSearch
    from aiogram.types import BufferedInputFile
    from datetime import datetime, timedelta
    import asyncio
    import json

    if BotConfig.ADMIN_USER_ID and message.from_user.id != BotConfig.ADMIN_USER_ID:
        return  # Только для админа

    # Парсим количество дней из команды
    parts = message.text.split()
    days = 1  # По умолчанию - только сегодня
    if len(parts) > 1:
        try:
            days = int(parts[1])
        except ValueError:
            days = 1

    await message.answer(f"📋 Получаю список пользователей за последние {days} дн...")

    try:
        db = await get_sniper_db()
        filters = await db.get_all_active_filters()

        # Фильтруем по last_activity - только активные за указанный период
        cutoff_date = datetime.utcnow() - timedelta(days=days)

        # Группируем фильтры по пользователям
        user_filters = {}
        skipped_inactive = 0

        for f in filters:
            tid = f.get('telegram_id')
            if not tid:
                continue

            # Получаем данные пользователя для проверки last_activity
            user = await db.get_user_by_telegram_id(tid)
            if not user:
                continue

            last_activity = user.get('last_activity')
            if last_activity:
                # Преобразуем строку в datetime если нужно
                if isinstance(last_activity, str):
                    try:
                        last_activity = datetime.fromisoformat(last_activity.replace('Z', '+00:00'))
                    except:
                        last_activity = None

                # Проверяем активность
                if last_activity and last_activity < cutoff_date:
                    skipped_inactive += 1
                    continue

            if tid not in user_filters:
                user_filters[tid] = []
            user_filters[tid].append(f)

        total_users = len(user_filters)
        total_filters_active = sum(len(fl) for fl in user_filters.values())
        await message.answer(
            f"📊 <b>Статистика:</b>\n\n"
            f"• Активных за {days} дн: <b>{total_users}</b> пользователей\n"
            f"• Фильтров у них: <b>{total_filters_active}</b>\n"
            f"• Пропущено неактивных: {skipped_inactive}\n\n"
            f"Начинаю отправку...",
            parse_mode="HTML"
        )

        searcher = InstantSearch()
        success_users = 0
        failed_users = 0
        total_reports = 0

        for telegram_id, user_filter_list in user_filters.items():
            try:
                # Отправляем извинение
                await message.bot.send_message(telegram_id, APOLOGY_MESSAGE, parse_mode="HTML")

                # Для каждого фильтра пользователя делаем поиск и отправляем отчет
                for filter_data in user_filter_list:
                    filter_name = filter_data.get('name', 'Без названия')
                    keywords_raw = filter_data.get('keywords', '[]')

                    try:
                        keywords = json.loads(keywords_raw) if isinstance(keywords_raw, str) else keywords_raw
                    except:
                        keywords = []

                    if not keywords:
                        continue

                    # Выполняем поиск
                    try:
                        results = await searcher.search_by_filter(
                            filter_data=filter_data,
                            max_tenders=20,
                            expanded_keywords=[]
                        )

                        matches = results.get('matches', [])

                        if matches:
                            html_content = searcher.generate_html_report(
                                tenders=matches,
                                filter_name=filter_name,
                                stats=results.get('stats', {})
                            )

                            filename = f"{filter_name[:20]}_{datetime.now().strftime('%H%M%S')}.html"
                            file = BufferedInputFile(
                                html_content.encode('utf-8'),
                                filename=filename
                            )

                            await message.bot.send_document(
                                chat_id=telegram_id,
                                document=file,
                                caption=f"📄 <b>{filter_name}</b>\n\n"
                                       f"Найдено тендеров: {len(matches)}\n"
                                       f"Ключевые слова: {', '.join(keywords[:3])}{'...' if len(keywords) > 3 else ''}",
                                parse_mode="HTML"
                            )
                            total_reports += 1

                    except Exception as search_err:
                        logger.error(f"Ошибка поиска для фильтра {filter_name}: {search_err}")

                    await asyncio.sleep(0.5)  # Задержка между отчетами

                success_users += 1

            except Exception as e:
                logger.error(f"Ошибка отправки пользователю {telegram_id}: {e}")
                failed_users += 1

            await asyncio.sleep(0.3)  # Задержка между пользователями

        await message.answer(
            f"✅ <b>Готово!</b>\n\n"
            f"Пользователей: {success_users} успешно, {failed_users} ошибок\n"
            f"Отчетов отправлено: {total_reports}",
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка в send_apology_all: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("test_search"))
async def admin_test_search(message: Message):
    """Админская команда для тестового поиска с HTML отчетом.

    Использование: /test_search компьютеры, ноутбуки
    """
    from bot.config import BotConfig
    from tender_sniper.instant_search import InstantSearch
    from aiogram.types import BufferedInputFile
    from datetime import datetime

    if BotConfig.ADMIN_USER_ID and message.from_user.id != BotConfig.ADMIN_USER_ID:
        return  # Только для админа

    # Парсим ключевые слова из команды
    text = message.text.replace('/test_search', '').strip()
    if not text:
        await message.answer("Использование: /test_search компьютеры, ноутбуки")
        return

    keywords = [k.strip() for k in text.split(',')]

    await message.answer(f"🔍 Ищу по: {', '.join(keywords)}...")

    try:
        searcher = InstantSearch()

        temp_filter = {
            'id': 0,
            'name': 'Тестовый поиск',
            'keywords': keywords,
            'exclude_keywords': [],
            'price_min': None,
            'price_max': None,
            'regions': [],
            'tender_types': [],
            'law_types': []
        }

        results = await searcher.search_by_filter(
            filter_data=temp_filter,
            max_tenders=20,
            expanded_keywords=[]
        )

        matches = results.get('matches', [])
        total_found = results.get('total_found', 0)

        await message.answer(f"📊 RSS: {total_found} тендеров\n🎯 После скоринга: {len(matches)}")

        if matches:
            html_content = searcher.generate_html_report(
                tenders=matches,
                filter_name='Тестовый поиск',
                stats=results.get('stats', {})
            )

            filename = f"test_{datetime.now().strftime('%H%M%S')}.html"
            file = BufferedInputFile(
                html_content.encode('utf-8'),
                filename=filename
            )

            await message.answer_document(
                document=file,
                caption=f"📄 Отчет: {len(matches)} тендеров"
            )
        else:
            await message.answer("⚠️ Нет результатов после скоринга")

    except Exception as e:
        logger.error(f"Ошибка test_search: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {e}")
