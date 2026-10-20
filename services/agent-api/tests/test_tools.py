"""core-mock client tests over a mock transport: no running service."""
import httpx
import pytest

from app.tools.core import TIMEOUT_S, CoreClient, CoreError, NotFound


def client(handler) -> CoreClient:
    return CoreClient("http://core", transport=httpx.MockTransport(handler))


def test_list_transactions_sends_lookback_window():
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, json=[])

    assert client(handler).list_transactions("C0001") == []
    assert seen[0].path == "/customers/C0001/transactions"
    assert seen[0].params["days"] == "30"


def test_404_is_not_found():
    with pytest.raises(NotFound):
        client(lambda r: httpx.Response(404)).get_transaction("T404")


def test_server_error_is_core_error():
    with pytest.raises(CoreError) as e:
        client(lambda r: httpx.Response(500)).get_customer("C0001")
    assert not isinstance(e.value, NotFound)


def test_timeout_is_core_error():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(CoreError):
        client(handler).get_customer("C0001")


def test_timeout_and_url_defaults(monkeypatch):
    monkeypatch.setenv("CORE_URL", "http://core-mock:8000")
    c = CoreClient()
    assert str(c._http.base_url) == "http://core-mock:8000"
    assert c._http.timeout.read == TIMEOUT_S
