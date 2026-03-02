"""Test scaffolding for Shu plugin development.

Provides :class:`HttpRequestFailed` (a standalone stub matching the real host
exception interface) and :class:`FakeHostBuilder` (a fluent mock-host factory).

This module has **no** ``shu.*`` imports — it is fully standalone so the SDK
can be installed and used without a local Shu backend checkout.
"""

from __future__ import annotations

import urllib.parse
from contextlib import contextmanager
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch


@contextmanager
def patch_retry_sleep() -> Generator[AsyncMock, None, None]:
    """Suppress ``asyncio.sleep`` delays inside ``@with_retry`` decorated functions.

    Use this in tests that exercise retry logic so they don't sleep between attempts::

        with patch_retry_sleep():
            with pytest.raises(RetryableError):
                await plugin.execute(params, ctx, host)

    Yields:
        The :class:`~unittest.mock.AsyncMock` replacing ``asyncio.sleep``, in case
        you want to assert on call count or arguments.
    """
    with patch("shu_plugin_sdk.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        yield mock_sleep


class HttpRequestFailed(Exception):
    """Standalone test stub for ``shu.plugins.host.exceptions.HttpRequestFailed``.

    Matches the exact interface of the real exception so plugin tests can
    ``from shu_plugin_sdk.testing import HttpRequestFailed`` without importing
    from the internal Shu package.

    Attributes:
        status_code: HTTP status code as int.
        url: Request URL as str.
        body: Parsed response body (dict, list, str, or None).
        headers: Response headers dict.

    Properties:
        error_category: Semantic category string.
        is_retryable: True for 429 and 5xx errors.
        retry_after_seconds: Parsed value of the ``Retry-After`` header, or None.
        provider_message: Best-effort error message extracted from the body.
        provider_error_code: Provider-specific error code extracted from the body.
    """

    def __init__(
        self,
        status_code: int,
        url: str,
        body: object = None,
        headers: dict | None = None,
    ) -> None:
        self.status_code = int(status_code)
        self.url = str(url)
        self.body = body
        self.headers = dict(headers or {})
        super().__init__(f"HTTP {self.status_code} calling {self.url}")

    @property
    def error_category(self) -> str:
        """Semantic error category based on HTTP status code.

        Returns:
            One of: ``auth_error`` (401), ``forbidden`` (403), ``not_found``
            (404), ``gone`` (410), ``rate_limited`` (429), ``server_error``
            (5xx), ``client_error`` (all other 4xx).
        """
        if self.status_code == 401:
            return "auth_error"
        if self.status_code == 403:
            return "forbidden"
        if self.status_code == 404:
            return "not_found"
        if self.status_code == 410:
            return "gone"
        if self.status_code == 429:
            return "rate_limited"
        if self.status_code >= 500:
            return "server_error"
        return "client_error"

    @property
    def is_retryable(self) -> bool:
        """True for errors that may succeed on retry (429, 5xx)."""
        return self.status_code == 429 or self.status_code >= 500

    @property
    def retry_after_seconds(self) -> int | None:
        """Parse the ``Retry-After`` header value.

        Performs case-insensitive header lookup per RFC 7230. Returns the
        integer number of seconds, or ``None`` if the header is absent or
        cannot be parsed as an integer.
        """
        retry_after = None
        for key, value in self.headers.items():
            if key.lower() == "retry-after":
                retry_after = value
                break
        if not retry_after:
            return None
        try:
            return int(retry_after)
        except (ValueError, TypeError):
            return None

    @property
    def provider_message(self) -> str:
        """Best-effort extraction of an error message from the response body.

        Attempts to extract from common API error formats:

        - ``{"error": {"message": "..."}}`` (Microsoft Graph, Google APIs)
        - ``{"error_description": "..."}`` (OAuth)
        - ``{"error": "...", "message": "..."}`` (various)
        - ``{"message": "..."}`` (simple)
        - Plain string body
        """
        if self.body is None:
            return ""
        if isinstance(self.body, str):
            return self.body[:500] if len(self.body) > 500 else self.body
        if isinstance(self.body, dict):
            error_obj = self.body.get("error")
            if isinstance(error_obj, dict):
                msg = error_obj.get("message")
                if msg:
                    return str(msg)
            for key in ("error_description", "message", "error", "detail"):
                val = self.body.get(key)
                if val and isinstance(val, str):
                    return val
            return str(self.body)[:500]
        return str(self.body)[:500]

    @property
    def provider_error_code(self) -> str | None:
        """Extract a provider-specific error code from the response body.

        Attempts to extract from common API error formats:

        - ``{"error": {"code": "..."}}`` (Microsoft Graph)
        - ``{"error": {"status": "..."}}`` (Google APIs)
        - ``{"code": "..."}`` (simple)
        """
        if not isinstance(self.body, dict):
            return None
        error_obj = self.body.get("error")
        if isinstance(error_obj, dict):
            code = error_obj.get("code") or error_obj.get("status")
            if code:
                return str(code)
        code = self.body.get("code")
        if code:
            return str(code)
        return None


class FakeHostBuilder:
    """Fluent builder for constructing a mock Shu ``Host`` for plugin unit tests.

    Usage::

        host = FakeHostBuilder().with_secret("api_key", "s3cr3t").build()
        result = await plugin.execute({"op": "fetch"}, ctx, host)

    Args:
        strict: When ``True`` (the default), ``host.http.fetch`` raises
            :class:`AssertionError` if called with a URL that was not
            registered via :meth:`with_http_response` or :meth:`with_http_error`.
            Set to ``False`` to return a deterministic synthetic fallback
            response ``{"status_code": 500, "headers": {}, "body": None}``
            for unregistered routes.
    """

    def __init__(self, *, strict: bool = True) -> None:
        self._secrets: dict[str, object] = {}
        self._http_routes: dict[tuple[str, str], dict] = {}
        self._strict = strict

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _canonicalize_url(url: str) -> str:
        """Normalize query-string ordering so equivalent URLs compare equal."""
        parsed = urllib.parse.urlsplit(url)
        query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not query_items:
            return url
        normalized_query = urllib.parse.urlencode(sorted(query_items), doseq=True)
        return urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, normalized_query, parsed.fragment)
        )

    @staticmethod
    def _build_url(url: str, params: dict | None) -> str:
        """Append URL-encoded query parameters to *url*.

        If *params* is ``None`` or empty the URL is returned unchanged except
        for query-string canonicalization.
        """
        if not params:
            return FakeHostBuilder._canonicalize_url(url)
        qs = urllib.parse.urlencode(params, doseq=True)
        sep = "&" if "?" in url else "?"
        return FakeHostBuilder._canonicalize_url(f"{url}{sep}{qs}")

    @staticmethod
    def _normalize_headers(headers: object) -> dict[str, object]:
        """Normalize header keys to lowercase for case-insensitive comparisons."""
        if not isinstance(headers, dict):
            return {}
        return {str(key).lower(): value for key, value in headers.items()}

    # ------------------------------------------------------------------
    # Builder methods
    # ------------------------------------------------------------------

    def build(self) -> MagicMock:
        """Build and return the configured mock host.

        Returns:
            A :class:`unittest.mock.MagicMock` with all 11 standard host
            capability attributes stubbed as :class:`~unittest.mock.AsyncMock`.
        """
        host = MagicMock()
        capabilities = (
            "http", "auth", "secrets", "storage", "kb",
            "cursor", "cache", "log", "utils", "identity", "ocr",
        )
        for cap in capabilities:
            setattr(host, cap, AsyncMock())

        # Wire secrets.get to return configured values
        secrets_store = self._secrets

        async def _secrets_get(key: str) -> object:
            return secrets_store.get(key)

        host.secrets.get = _secrets_get

        # Wire http.fetch to return configured responses / raise configured errors
        http_routes = self._http_routes
        strict = self._strict

        async def _http_fetch(method: str, url: str, **kwargs: object) -> dict:
            # Assemble full URL from params kwarg (mirrors real host behaviour)
            params = kwargs.get("params")
            if params and isinstance(params, dict):
                full_url = FakeHostBuilder._build_url(url, params)
            else:
                full_url = FakeHostBuilder._build_url(url, None)

            route_key = (method.upper(), full_url)
            route = http_routes.get(route_key)

            if route is None:
                if strict:
                    registered = ", ".join(
                        f"{m} {u}" for m, u in sorted(http_routes)
                    )
                    raise AssertionError(
                        f"FakeHostBuilder (strict mode): no route registered "
                        f"for {method.upper()} {full_url}.\n"
                        f"Registered routes: [{registered}]"
                    )
                return {"status_code": 500, "headers": {}, "body": None}

            # Verify expected headers when configured on the route
            expected_headers = route.get("assert_headers")
            if expected_headers:
                received = FakeHostBuilder._normalize_headers(kwargs.get("headers"))
                expected = FakeHostBuilder._normalize_headers(expected_headers)
                for key, expected_val in expected.items():
                    actual_val = received.get(key)
                    assert actual_val == expected_val, (
                        f"FakeHostBuilder header assertion failed for "
                        f"{method.upper()} {full_url}: "
                        f"expected header {key!r}={expected_val!r} (case-insensitive), "
                        f"got {actual_val!r}"
                    )

            if route["type"] == "error":
                raise route["exc"]
            return route["data"]

        host.http.fetch = _http_fetch
        return host

    def with_secret(self, key: str, value: object) -> "FakeHostBuilder":
        """Configure ``host.secrets.get(key)`` to return *value*.

        Args:
            key: The secret key.
            value: The value to return.

        Returns:
            ``self`` for method chaining.
        """
        self._secrets[key] = value
        return self

    def with_http_response(
        self,
        method: str,
        url: str,
        response: dict,
        *,
        params: dict | None = None,
        assert_headers: dict[str, str] | None = None,
    ) -> "FakeHostBuilder":
        """Configure ``host.http.fetch(method, url, ...)`` to return *response*.

        Args:
            method: HTTP method string (case-insensitive).
            url: Base URL to match (query parameters may be appended via *params*).
            response: Dict in the shape ``{"status_code": int, "headers": dict,
                "body": str | dict}`` — matching the real ``http_capability`` format.
            params: Optional query-parameter dict.  When provided the parameters
                are URL-encoded and appended to *url* before registering the route.
                The plugin can then call ``host.http.fetch(method, url, params=...)``
                and the lookup will match automatically.
            assert_headers: Optional dict of header key-value pairs that must be
                present in the ``headers`` kwarg when ``host.http.fetch`` is called.
                Raises :class:`AssertionError` on mismatch.

        Returns:
            ``self`` for method chaining.
        """
        full_url = self._build_url(url, params)
        route: dict = {"type": "response", "data": response}
        if assert_headers:
            route["assert_headers"] = assert_headers
        self._http_routes[(method.upper(), full_url)] = route
        return self

    def with_http_error(
        self,
        method: str,
        url: str,
        status_code: int,
        body: object = None,
        headers: dict | None = None,
        *,
        params: dict | None = None,
        assert_headers: dict[str, str] | None = None,
    ) -> "FakeHostBuilder":
        """Configure ``host.http.fetch(method, url, ...)`` to raise :class:`HttpRequestFailed`.

        Args:
            method: HTTP method string (case-insensitive).
            url: Base URL to match (query parameters may be appended via *params*).
            status_code: HTTP status code for the raised exception.
            body: Optional response body.
            headers: Optional response headers dict.
            params: Optional query-parameter dict (see :meth:`with_http_response`).
            assert_headers: Optional header assertions (see :meth:`with_http_response`).

        Returns:
            ``self`` for method chaining.
        """
        full_url = self._build_url(url, params)
        exc = HttpRequestFailed(status_code, full_url, body, headers)
        route: dict = {"type": "error", "exc": exc}
        if assert_headers:
            route["assert_headers"] = assert_headers
        self._http_routes[(method.upper(), full_url)] = route
        return self
