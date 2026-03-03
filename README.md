# shu-plugin-sdk

Developer SDK for building and testing [Shu](https://openshu.ai) plugins. Install it alongside your plugin — no Shu backend checkout required.

```
pip install git+ssh://git@github.com/Open-Shu/shu-plugin-sdk.git
```

Requires Python 3.11+.

## What's in the box

| Module | Purpose |
|---|---|
| `shu_plugin_sdk.result` | `PluginResult` / `IngestionResult` return types, `Skip`, `SkipReason` |
| `shu_plugin_sdk.retry` | `@with_retry` decorator with exponential backoff, `RetryableError` / `NonRetryableError` |
| `shu_plugin_sdk.testing` | `FakeHostBuilder` (mock host factory), `HttpRequestFailed` stub, `patch_retry_sleep` |
| `shu_plugin_sdk.contracts` | `assert_plugin_contract` — static validation of manifests, schemas, and ops |
| `_cookiecutter/` + CLI | Bundled plugin template with `shu-plugin-template` copy command |

Everything is re-exported from the top-level package:

```python
from shu_plugin_sdk import PluginResult, FakeHostBuilder, with_retry, RetryConfig
```

## Quick start

1. Run `shu-plugin-template my_plugin` (defaults to `plugins/my_plugin`)
2. Rename the class, update `manifest.py` (follow the `MUST UPDATE` comments)
3. Implement your ops in `execute()`
4. Run `pytest` — the contract test runs automatically

Use `--root` to change the parent folder and `--force` to overwrite an existing destination:

```bash
shu-plugin-template my_plugin --root custom_plugins
shu-plugin-template my_plugin --force
```

## Plugin protocol

A Shu plugin is a Python class with these members:

```python
class MyPlugin:
    name: str = "my_plugin"          # must match manifest
    version: str = "1"               # must match manifest

    def get_schema(self) -> dict:             # JSON Schema (Draft 7) with properties.op.enum
    def get_output_schema(self) -> dict:      # JSON Schema for result.data
    def get_schema_for_op(self, op) -> dict | None:  # per-op schema (recommended)

    async def execute(self, params, context, host) -> PluginResult:
        ...
```

The manifest (`PLUGIN_MANIFEST` dict) declares metadata, capabilities, and op visibility:

```python
PLUGIN_MANIFEST = {
    "name": "my_plugin",
    "display_name": "My Plugin",
    "module": "plugins.my_plugin.plugin:MyPlugin",
    "version": "1",
    "capabilities": ["http", "secrets", "log"],
    "chat_callable_ops": ["fetch_data"],
}
```

## Returning results

```python
# Success
return PluginResult.ok(data={"items": results})

# Error
return PluginResult.err("Rate limit exceeded", code="rate_limited")

# Success with skips (ingestion plugins)
return IngestionResult.ok(
    data={"indexed": 42},
    skips=[Skip(id=doc_id, reason=SkipReason.too_large)],
)
```

## Retry with backoff

Wrap HTTP calls in `@with_retry` to handle transient failures. Raise `RetryableError` for 429/5xx and `NonRetryableError` for permanent 4xx errors:

```python
from shu_plugin_sdk import (
    HttpRequestFailed, RetryableError, NonRetryableError,
    RetryConfig, with_retry,
)

@with_retry(RetryConfig(max_retries=3, base_delay=2.0))
async def _fetch(host, url):
    try:
        return await host.http.fetch("GET", url, headers=headers)
    except HttpRequestFailed as e:
        if e.is_retryable:
            raise RetryableError(str(e)) from e
        raise NonRetryableError(str(e)) from e
```

`RetryConfig` fields: `max_retries` (default 3), `base_delay` (1.0s), `max_delay` (60s), `backoff_factor` (2.0).

## Testing with FakeHostBuilder

`FakeHostBuilder` creates a mock host with all 11 capabilities stubbed. Chain `.with_secret()` and `.with_http_response()` / `.with_http_error()` to configure the behaviour your test needs:

```python
from shu_plugin_sdk import FakeHostBuilder

host = (
    FakeHostBuilder()
    .with_secret("api_key", "test_token")
    .with_http_response(
        "GET", "https://api.example.com/data",
        {"status_code": 200, "headers": {}, "body": {"id": 42}},
    )
    .build()
)

result = await plugin.execute({"op": "fetch"}, ctx, host)
assert result.status == "success"
```

### Strict mode (default)

By default, calling `host.http.fetch` with an unregistered URL raises `AssertionError` with the list of registered routes. This catches URL-construction bugs immediately. Pass `strict=False` to return a synthetic fallback response (`{"status_code": 500, "headers": {}, "body": None}`) instead:

```python
host = FakeHostBuilder(strict=False).build()
```

### Query parameters

Register and fetch with `params=` dicts instead of manually constructing URL strings:

```python
host = (
    FakeHostBuilder()
    .with_http_response(
        "GET", "https://api.example.com/search",
        {"status_code": 200, "headers": {}, "body": {"items": []}},
        params={"q": "test", "page": "1"},
    )
    .build()
)

# Plugin calls host.http.fetch("GET", url, params={"q": "test", "page": "1"})
# and the route matches automatically.
```

### Header assertions

Verify that your plugin sends the correct headers without a live integration test:

```python
.with_http_response(
    "GET", "https://api.example.com/data",
    {"status_code": 200, "headers": {}, "body": "ok"},
    assert_headers={"Authorization": "Bearer expected_token"},
)
```

The fetch raises `AssertionError` if the `Authorization` header is missing or wrong. Extra headers are allowed.

### Suppressing retry sleep

Add this `conftest.py` to your test directory so `@with_retry` tests run instantly:

```python
import pytest
from shu_plugin_sdk.testing import patch_retry_sleep

@pytest.fixture(autouse=True)
def _no_retry_sleep():
    with patch_retry_sleep():
        yield
```

## Contract validation

`assert_plugin_contract` validates your plugin class and manifest against the full Shu plugin contract. Add it as a test:

```python
from shu_plugin_sdk.contracts import assert_plugin_contract

def test_contract():
    assert_plugin_contract(MyPlugin, manifest=PLUGIN_MANIFEST)
```

It checks:
- Required manifest keys (`name`, `version`, `module`, `capabilities`)
- Capabilities are from the known set
- `module` string format (`dotted.path:ClassName`)
- Plugin is instantiable with no arguments
- `execute()` is async
- `name` and `version` match between class and manifest
- `get_schema()` and `get_output_schema()` return valid Draft 7 JSON Schemas
- `properties.op.enum` is present and non-empty
- `chat_callable_ops`, `allowed_feed_ops`, and `op_auth` reference valid ops
- Warns on missing `additionalProperties: false`, untyped properties, arrays without `items`

## Development

```
pip install -e ".[dev]"
pytest
```

## License

Dual-licensed under GPLv3 and the Shu Commercial License. See [LICENSE.md](LICENSE.md).
