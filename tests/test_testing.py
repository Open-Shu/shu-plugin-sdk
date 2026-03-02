"""Tests for shu_plugin_sdk.testing (HttpRequestFailed and FakeHostBuilder)."""

from __future__ import annotations

import pytest

from shu_plugin_sdk.testing import FakeHostBuilder, HttpRequestFailed

# ---------------------------------------------------------------------------
# HttpRequestFailed tests
# ---------------------------------------------------------------------------


def test_http_request_failed_error_categories() -> None:
    """Each status code maps to the correct error_category string."""
    cases = [
        (401, "auth_error"),
        (403, "forbidden"),
        (404, "not_found"),
        (410, "gone"),
        (429, "rate_limited"),
        (500, "server_error"),
        (502, "server_error"),
        (503, "server_error"),
        (400, "client_error"),
        (422, "client_error"),
        (409, "client_error"),
    ]
    for status_code, expected in cases:
        exc = HttpRequestFailed(status_code, "https://example.com")
        assert exc.error_category == expected, (
            f"status {status_code}: expected {expected!r}, got {exc.error_category!r}"
        )


def test_http_request_failed_is_retryable() -> None:
    """429 and 5xx errors are retryable; 4xx (except 429) are not."""
    assert HttpRequestFailed(429, "u").is_retryable is True
    assert HttpRequestFailed(500, "u").is_retryable is True
    assert HttpRequestFailed(503, "u").is_retryable is True
    assert HttpRequestFailed(404, "u").is_retryable is False
    assert HttpRequestFailed(401, "u").is_retryable is False
    assert HttpRequestFailed(400, "u").is_retryable is False


def test_http_request_failed_retry_after() -> None:
    """retry_after_seconds parses Retry-After header correctly."""
    # Standard integer value
    assert HttpRequestFailed(429, "u", headers={"Retry-After": "30"}).retry_after_seconds == 30
    # Case-insensitive lookup
    assert HttpRequestFailed(429, "u", headers={"retry-after": "60"}).retry_after_seconds == 60
    assert HttpRequestFailed(429, "u", headers={"RETRY-AFTER": "10"}).retry_after_seconds == 10
    # Missing header
    assert HttpRequestFailed(429, "u", headers={}).retry_after_seconds is None
    assert HttpRequestFailed(429, "u").retry_after_seconds is None
    # Non-integer value (HTTP-date) → None
    assert HttpRequestFailed(429, "u", headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}).retry_after_seconds is None


# ---------------------------------------------------------------------------
# FakeHostBuilder — basic capabilities
# ---------------------------------------------------------------------------

_ALL_CAPS = ("http", "auth", "secrets", "storage", "kb", "cursor", "cache", "log", "utils", "identity", "ocr")


def test_fake_host_builder_default_capabilities() -> None:
    """build() returns a host with all 11 capability attributes set."""
    host = FakeHostBuilder(strict=False).build()
    for cap in _ALL_CAPS:
        assert hasattr(host, cap), f"host missing capability: {cap}"


@pytest.mark.asyncio
async def test_fake_host_builder_with_secret() -> None:
    """Configured secret is returned by host.secrets.get(key)."""
    host = FakeHostBuilder(strict=False).with_secret("api_key", "tok123").build()
    assert await host.secrets.get("api_key") == "tok123"


@pytest.mark.asyncio
async def test_fake_host_builder_missing_secret_returns_none() -> None:
    """Unconfigured secret key returns None."""
    host = FakeHostBuilder(strict=False).build()
    assert await host.secrets.get("not_configured") is None


@pytest.mark.asyncio
async def test_fake_host_builder_with_http_response() -> None:
    """Configured HTTP response is returned by host.http.fetch."""
    response = {"status_code": 200, "headers": {"Content-Type": "application/json"}, "body": {"id": 42}}
    host = FakeHostBuilder().with_http_response("GET", "https://api.example.com/item", response).build()
    result = await host.http.fetch("GET", "https://api.example.com/item")
    assert result == response


@pytest.mark.asyncio
async def test_fake_host_builder_with_http_error() -> None:
    """Configured HTTP error raises HttpRequestFailed with the correct status code."""
    host = FakeHostBuilder().with_http_error("POST", "https://api.example.com/submit", 429).build()
    with pytest.raises(HttpRequestFailed) as exc_info:
        await host.http.fetch("POST", "https://api.example.com/submit")
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_fake_host_builder_fluent_chaining() -> None:
    """Full fluent chain with_secret().with_http_response().with_http_error().build() works."""
    ok_response = {"status_code": 200, "headers": {}, "body": "ok"}
    host = (
        FakeHostBuilder()
        .with_secret("token", "abc")
        .with_http_response("GET", "https://api.example.com/data", ok_response)
        .with_http_error("DELETE", "https://api.example.com/resource", 403)
        .build()
    )
    assert await host.secrets.get("token") == "abc"
    assert (await host.http.fetch("GET", "https://api.example.com/data")) == ok_response
    with pytest.raises(HttpRequestFailed) as exc_info:
        await host.http.fetch("DELETE", "https://api.example.com/resource")
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Unregistered routes raise in strict mode (default on)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strict_mode_raises_on_unregistered_route() -> None:
    """Default strict mode raises AssertionError for unregistered routes."""
    host = FakeHostBuilder().build()
    with pytest.raises(AssertionError, match="no route registered"):
        await host.http.fetch("GET", "https://unregistered.example.com/")


@pytest.mark.asyncio
async def test_strict_mode_error_lists_registered_routes() -> None:
    """The AssertionError message includes the registered routes for debugging."""
    host = (
        FakeHostBuilder()
        .with_http_response("GET", "https://api.example.com/a", {"status_code": 200, "headers": {}, "body": "ok"})
        .build()
    )
    with pytest.raises(AssertionError, match="https://api.example.com/a"):
        await host.http.fetch("GET", "https://api.example.com/b")


@pytest.mark.asyncio
async def test_non_strict_mode_returns_fallback_for_unregistered() -> None:
    """With strict=False, unregistered routes return a deterministic fallback response."""
    host = FakeHostBuilder(strict=False).build()
    result = await host.http.fetch("GET", "https://unconfigured.example.com/")
    assert result == {"status_code": 500, "headers": {}, "body": None}


# ---------------------------------------------------------------------------
# Query-parameter dict support on stubs and fetch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_with_http_response_params_dict() -> None:
    """Registering a route with params= and fetching with params= matches."""
    response = {"status_code": 200, "headers": {}, "body": {"results": []}}
    host = (
        FakeHostBuilder()
        .with_http_response(
            "GET",
            "https://api.example.com/search",
            response,
            params={"q": "test", "page": "1"},
        )
        .build()
    )
    result = await host.http.fetch(
        "GET", "https://api.example.com/search", params={"q": "test", "page": "1"},
    )
    assert result == response


@pytest.mark.asyncio
async def test_with_http_response_params_dict_order_insensitive() -> None:
    """Route matching works even when params dict insertion order differs."""
    response = {"status_code": 200, "headers": {}, "body": {"results": []}}
    host = (
        FakeHostBuilder()
        .with_http_response(
            "GET",
            "https://api.example.com/search",
            response,
            params={"q": "test", "page": "1"},
        )
        .build()
    )
    result = await host.http.fetch(
        "GET", "https://api.example.com/search", params={"page": "1", "q": "test"},
    )
    assert result == response


@pytest.mark.asyncio
async def test_with_http_error_params_dict() -> None:
    """Registering an error route with params= and fetching with params= matches."""
    host = (
        FakeHostBuilder()
        .with_http_error(
            "GET",
            "https://api.example.com/search",
            422,
            params={"q": "bad"},
        )
        .build()
    )
    with pytest.raises(HttpRequestFailed) as exc_info:
        await host.http.fetch(
            "GET", "https://api.example.com/search", params={"q": "bad"},
        )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_params_manual_url_still_works() -> None:
    """Registering with a full URL string and fetching the same string still works."""
    response = {"status_code": 200, "headers": {}, "body": "ok"}
    host = (
        FakeHostBuilder()
        .with_http_response("GET", "https://api.example.com/search?q=test&page=1", response)
        .build()
    )
    result = await host.http.fetch("GET", "https://api.example.com/search?q=test&page=1")
    assert result == response


@pytest.mark.asyncio
async def test_params_manual_url_query_order_insensitive() -> None:
    """Manual URL query-string order does not affect route matching."""
    response = {"status_code": 200, "headers": {}, "body": "ok"}
    host = (
        FakeHostBuilder()
        .with_http_response("GET", "https://api.example.com/search?q=test&page=1", response)
        .build()
    )
    result = await host.http.fetch("GET", "https://api.example.com/search?page=1&q=test")
    assert result == response


@pytest.mark.asyncio
async def test_params_appended_to_existing_query_string() -> None:
    """params= appends to a URL that already has a query string."""
    response = {"status_code": 200, "headers": {}, "body": "ok"}
    host = (
        FakeHostBuilder()
        .with_http_response(
            "GET",
            "https://api.example.com/search?type=pr",
            response,
            params={"page": "2"},
        )
        .build()
    )
    result = await host.http.fetch(
        "GET", "https://api.example.com/search?type=pr", params={"page": "2"},
    )
    assert result == response


# ---------------------------------------------------------------------------
# Header assertions on stub registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assert_headers_passes_when_present() -> None:
    """Route with assert_headers succeeds when expected headers are present."""
    response = {"status_code": 200, "headers": {}, "body": "ok"}
    host = (
        FakeHostBuilder()
        .with_http_response(
            "GET",
            "https://api.example.com/data",
            response,
            assert_headers={"Authorization": "Bearer tok123"},
        )
        .build()
    )
    result = await host.http.fetch(
        "GET", "https://api.example.com/data", headers={"Authorization": "Bearer tok123"},
    )
    assert result == response


@pytest.mark.asyncio
async def test_assert_headers_passes_case_insensitive_keys() -> None:
    """Header key matching is case-insensitive."""
    response = {"status_code": 200, "headers": {}, "body": "ok"}
    host = (
        FakeHostBuilder()
        .with_http_response(
            "GET",
            "https://api.example.com/data",
            response,
            assert_headers={"Authorization": "Bearer tok123"},
        )
        .build()
    )
    result = await host.http.fetch(
        "GET", "https://api.example.com/data", headers={"authorization": "Bearer tok123"},
    )
    assert result == response


@pytest.mark.asyncio
async def test_assert_headers_fails_on_mismatch() -> None:
    """Route with assert_headers raises AssertionError on wrong header value."""
    response = {"status_code": 200, "headers": {}, "body": "ok"}
    host = (
        FakeHostBuilder()
        .with_http_response(
            "GET",
            "https://api.example.com/data",
            response,
            assert_headers={"Authorization": "Bearer expected"},
        )
        .build()
    )
    with pytest.raises(AssertionError, match="header assertion failed"):
        await host.http.fetch(
            "GET", "https://api.example.com/data", headers={"Authorization": "Bearer wrong"},
        )


@pytest.mark.asyncio
async def test_assert_headers_fails_on_missing() -> None:
    """Route with assert_headers raises AssertionError when header is absent."""
    response = {"status_code": 200, "headers": {}, "body": "ok"}
    host = (
        FakeHostBuilder()
        .with_http_response(
            "GET",
            "https://api.example.com/data",
            response,
            assert_headers={"Authorization": "Bearer tok"},
        )
        .build()
    )
    with pytest.raises(AssertionError, match="header assertion failed"):
        await host.http.fetch("GET", "https://api.example.com/data")


@pytest.mark.asyncio
async def test_assert_headers_allows_extra_headers() -> None:
    """assert_headers only checks specified keys; extra headers are ignored."""
    response = {"status_code": 200, "headers": {}, "body": "ok"}
    host = (
        FakeHostBuilder()
        .with_http_response(
            "GET",
            "https://api.example.com/data",
            response,
            assert_headers={"Authorization": "Bearer tok"},
        )
        .build()
    )
    result = await host.http.fetch(
        "GET",
        "https://api.example.com/data",
        headers={"Authorization": "Bearer tok", "Accept": "application/json"},
    )
    assert result == response


@pytest.mark.asyncio
async def test_assert_headers_on_error_route() -> None:
    """assert_headers works on error routes too — assertion runs before raising."""
    host = (
        FakeHostBuilder()
        .with_http_error(
            "POST",
            "https://api.example.com/submit",
            403,
            assert_headers={"Authorization": "Bearer tok"},
        )
        .build()
    )
    # Correct headers → raises HttpRequestFailed (not AssertionError)
    with pytest.raises(HttpRequestFailed):
        await host.http.fetch(
            "POST", "https://api.example.com/submit",
            headers={"Authorization": "Bearer tok"},
        )
    # Wrong headers → raises AssertionError
    with pytest.raises(AssertionError, match="header assertion failed"):
        await host.http.fetch(
            "POST", "https://api.example.com/submit",
            headers={"Authorization": "Bearer wrong"},
        )
