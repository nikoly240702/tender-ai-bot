"""Tender-GPT падал на 403 unsupported_country_region_territory на КАЖДОМ
сообщении (подтверждено 06.10.2026, воспроизведено на боевом сервере с
реальным пользователем).

Причина: правка от 15.09.2026 (d94853c) развела все точки создания
OpenAI-клиента по исходящему прокси с не-российским выходом — кроме этой.
`tender_gpt/graph.py` строит LLM через `langchain_openai.ChatOpenAI`, а не
через `make_openai_client`/`make_async_openai_client`, и grep на старый
паттерн конструктора её не нашёл.
"""
import httpx
import pytest


@pytest.mark.unit
class TestChatLlmUsesExitProxy:
    def test_proxy_is_wired_when_configured(self, monkeypatch):
        monkeypatch.setenv("OPENAI_PROXY_URL", "http://exit.example:3128")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        captured = {}

        class _StubChatOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        import tender_sniper.tender_gpt.graph as graph
        monkeypatch.setattr(graph, "ChatOpenAI", _StubChatOpenAI)

        graph.make_chat_llm()

        assert isinstance(captured.get("http_async_client"), httpx.AsyncClient)

    def test_no_proxy_configured_means_no_proxy_client(self, monkeypatch):
        """Совместимость со средами без блокировки (зарубежный хостинг) —
        то же поведение, что у make_openai_client() в openai_client.py."""
        monkeypatch.delenv("OPENAI_PROXY_URL", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        captured = {}

        class _StubChatOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        import tender_sniper.tender_gpt.graph as graph
        monkeypatch.setattr(graph, "ChatOpenAI", _StubChatOpenAI)

        graph.make_chat_llm()

        assert captured.get("http_async_client") is None
