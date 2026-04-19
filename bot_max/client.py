"""
MaxBotClient — HTTP client for VK Max Bot API.

Base URL: https://platform-api.max.ru
Auth: Header 'Authorization: <token>' (no Bearer prefix).
"""

import logging
from typing import Optional, List, Dict, Any

import aiohttp

logger = logging.getLogger(__name__)


class MaxBotClient:
    """Async client for VK Max Bot Platform API."""

    BASE_URL = "https://platform-api.max.ru"

    def __init__(self, token: str):
        self.token = token
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": self.token},
            )
        return self._session

    # ------------------------------------------------------------------
    # API methods
    # ------------------------------------------------------------------

    async def get_me(self) -> Dict[str, Any]:
        """GET /me — bot info."""
        async with self.session.get(f"{self.BASE_URL}/me") as resp:
            resp.raise_for_status()
            return await resp.json()

    async def send_message(
        self,
        chat_id: int,
        text: str,
        fmt: str = "html",
        keyboard: Optional[List[List[Dict]]] = None,
    ) -> Dict[str, Any]:
        """POST /messages — send a message to a chat."""
        body: Dict[str, Any] = {"text": text, "format": fmt}
        if keyboard:
            body["attachments"] = [self._build_keyboard_attachment(keyboard)]

        async with self.session.post(
            f"{self.BASE_URL}/messages",
            params={"chat_id": chat_id},
            json=body,
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def edit_message(
        self,
        message_id: str,
        text: str,
        fmt: str = "html",
        keyboard: Optional[List[List[Dict]]] = None,
    ) -> Dict[str, Any]:
        """PUT /messages — edit an existing message."""
        body: Dict[str, Any] = {"text": text, "format": fmt}
        if keyboard:
            body["attachments"] = [self._build_keyboard_attachment(keyboard)]

        async with self.session.put(
            f"{self.BASE_URL}/messages",
            params={"message_id": message_id},
            json=body,
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def answer_callback(
        self,
        callback_id: str,
        notification: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /answers — acknowledge a callback button press."""
        body: Dict[str, Any] = {}
        if notification:
            body["notification"] = notification

        async with self.session.post(
            f"{self.BASE_URL}/answers",
            params={"callback_id": callback_id},
            json=body,
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def send_file(
        self,
        chat_id: int,
        file_path: str,
        text: str = "",
        fmt: str = "html",
    ) -> Dict[str, Any]:
        """Upload a file and send it as a message attachment."""
        import os

        # 1. Get upload URL
        async with self.session.post(
            f"{self.BASE_URL}/uploads",
            params={"type": "file"},
        ) as resp:
            resp.raise_for_status()
            upload_data = await resp.json()

        upload_url = upload_data.get("url")
        if not upload_url:
            raise ValueError("No upload URL returned")

        # 2. Upload file
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            form = aiohttp.FormData()
            form.add_field("data", f, filename=filename)
            async with self.session.post(upload_url, data=form) as resp:
                resp.raise_for_status()
                file_info = await resp.json()

        # 3. Send message with file attachment
        body: Dict[str, Any] = {"text": text, "format": fmt}
        # file_info should contain the token/attachment info
        if isinstance(file_info, dict):
            body["attachments"] = [file_info]

        async with self.session.post(
            f"{self.BASE_URL}/messages",
            params={"chat_id": chat_id},
            json=body,
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_updates(
        self,
        marker: Optional[int] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """GET /updates — long-polling for new events."""
        params: Dict[str, Any] = {"limit": 100, "timeout": timeout}
        if marker is not None:
            params["marker"] = marker

        async with self.session.get(
            f"{self.BASE_URL}/updates",
            params=params,
            timeout=aiohttp.ClientTimeout(total=timeout + 10),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def close(self):
        """Close underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_keyboard_attachment(buttons: List[List[Dict]]) -> Dict[str, Any]:
        """
        Convert a list of button rows into an inline_keyboard attachment.

        Each row is a list of button dicts, e.g.:
            [{"type": "callback", "text": "OK", "payload": "ok"}]
        """
        return {
            "type": "inline_keyboard",
            "payload": {"buttons": buttons},
        }
