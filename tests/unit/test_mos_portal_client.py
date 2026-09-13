import base64
import json
import time

import pytest
import responses

from tender_sniper.sources.mos_portal_client import MosPortalClient, decode_jwt_exp


def _fake_jwt(exp: int) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b'=').decode()
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b'=').decode()
    return f"{header}.{payload}.sig"


@pytest.mark.unit
class TestDecodeJwtExp:
    def test_reads_exp_claim(self):
        token = _fake_jwt(exp=2000000000)
        assert decode_jwt_exp(token) == 2000000000

    def test_malformed_token_returns_none(self):
        assert decode_jwt_exp("not-a-jwt") is None


@pytest.mark.unit
class TestMosPortalClientAuth:
    @responses.activate
    def test_sends_bearer_token(self, monkeypatch):
        monkeypatch.setenv("PP_TOKEN", _fake_jwt(exp=int(time.time()) + 86400 * 3650))
        monkeypatch.delenv("PROXY_URL", raising=False)
        responses.add(
            responses.GET,
            "https://api.zakupki.mos.ru/api/v2/auction/public/Search",
            json={"data": [], "total": 0},
            status=200,
        )
        client = MosPortalClient()
        result = client.search_auctions_sync("2026-09-10", "2026-09-11")
        assert result == {"data": [], "total": 0}
        sent_auth = responses.calls[0].request.headers["Authorization"]
        assert sent_auth == f"Bearer {client.token}"
