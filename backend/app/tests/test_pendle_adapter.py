"""HTTP boundary tests: deterministic transport, no external requests or sleeps."""
import httpx
import pytest
from app.adapters.pendle_api import PendleApiClient, PendleHTTPError, PendleTimeoutError, PendleResponseShapeError


@pytest.mark.parametrize("failure", [429, 500, "timeout"])
@pytest.mark.parametrize("recover", [True, False])
def test_transient_failure_retried_then_recovered_or_reported(failure, recover):
    calls, sleeps = [], []
    def handler(request):
        calls.append(request)
        if recover and len(calls) == 3:
            return httpx.Response(200, json={"results": [], "total": 0})
        if failure == "timeout":
            raise httpx.ReadTimeout("test timeout", request=request)
        return httpx.Response(failure, headers={"Retry-After": "0"})
    with PendleApiClient(base_url="https://example.test", max_attempts=3,
                          transport=httpx.MockTransport(handler), sleep=sleeps.append) as client:
        if recover:
            assert client.list_markets_page().items == ()
        else:
            error = PendleTimeoutError if failure == "timeout" else PendleHTTPError
            with pytest.raises(error) as exc:
                client.list_markets_page()
            assert exc.value.attempts == 3
            if failure != "timeout":
                assert exc.value.status_code == failure
    assert len(calls) == 3 and len(sleeps) == 2


def test_missing_payload_is_error_not_empty_success():
    with PendleApiClient(base_url="https://example.test",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))) as client:
        with pytest.raises(PendleResponseShapeError):
            client.list_markets_page()


def test_permanent_http_error_not_retried():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(400)
    with PendleApiClient(base_url="https://example.test", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PendleHTTPError):
            client.list_markets_page()
    assert len(calls) == 1
