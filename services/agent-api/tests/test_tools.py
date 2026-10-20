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


def test_today_reads_the_core_clock():
    c = client(lambda r: httpx.Response(200, json={"status": "ok",
                                                   "reference_date": "2026-10-10T12:00:00"}))
    assert c.today() == "2026-10-10"


def test_create_refund_posts_amount_as_string():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.content))
        return httpx.Response(201, json={"id": "R1", "status": "approved"})

    assert client(handler).create_refund("T1", 18500.0, "duplicate_charge claim")["id"] == "R1"
    method, path, content = seen[0]
    assert (method, path) == ("POST", "/refunds")
    assert b'"amount":"18500.0"' in content.replace(b" ", b"")


def test_approving_a_refund_that_is_not_pending_is_core_error():
    with pytest.raises(CoreError):
        client(lambda r: httpx.Response(409)).approve_refund("R1")
