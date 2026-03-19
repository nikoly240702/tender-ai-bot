"""
Middleware для отправки алертов об ошибках в Telegram-чат администратора.

Ошибки в обработчиках + фоновых задачах отправляются боту.
Rate limiting: 1 алерт на одну ошибку за 5 минут.
"""

import time
import logging
import traceback
from typing import Any, Callable, Awaitable, Dict

from aiogram import Bot, BaseMiddleware
from aiogram.types import TelegramObject
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)

# Cooldown-словарь: key → timestamp последнего алерта
_last_alert: Dict[str, float] = {}
ALERT_COOLDOWN = 300  # секунд (5 минут)


async def send_error_alert(bot: Bot, admin_id: int, error: Exception, context: str = "") -> None:
    """
    Отправляет алерт об ошибке администратору.

    Rate-limited: одна и та же ошибка не чаще раза в 5 минут.
    Ошибка при отправке алерта тихо логируется — бот не падает.
    """
    if not admin_id:
        return

    # Rate limiting по сигнатуре ошибки
    key = f"{type(error).__name__}:{str(error)[:120]}"
    now = time.monotonic()
    if key in _last_alert and now - _last_alert[key] < ALERT_COOLDOWN:
        return
    _last_alert[key] = now

    # Трасировка (обрезаем до 3000 симв., оставляем начало и конец)
    tb = traceback.format_exc()
    if len(tb) > 3000:
        tb = tb[:1500] + "\n...[обрезано]...\n" + tb[-1500:]

    ctx_line = f"\n<b>Контекст:</b> <code>{context}</code>" if context else ""
    text = (
        f"🚨 <b>Ошибка в Tender Sniper</b>\n\n"
        f"<b>Тип:</b> {type(error).__name__}\n"
        f"<b>Сообщение:</b> {str(error)[:500]}"
        f"{ctx_line}\n\n"
        f"<pre>{tb}</pre>"
    )

    try:
        await bot.send_message(
            chat_id=admin_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as send_err:
        # Алерт об алерте не отправляем — только в лог
        logger.error(f"Не удалось отправить error alert: {send_err}")


class ErrorAlertMiddleware(BaseMiddleware):
    """
    Middleware, которая перехватывает необработанные исключения
    из aiogram-обработчиков и отправляет алерт администратору.

    Исключение пробрасывается дальше (для dp.error() обработчика).
    """

    def __init__(self, bot: Bot, admin_id: int):
        self.bot = bot
        self.admin_id = admin_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return  # Безвредная ошибка — пользователь нажал кнопку повторно
            context = f"update={type(event).__name__}"
            await send_error_alert(self.bot, self.admin_id, e, context)
            raise
        except Exception as e:
            context = f"update={type(event).__name__}"
            await send_error_alert(self.bot, self.admin_id, e, context)
            raise  # Пробрасываем — dp.error() обработает ответ пользователю
