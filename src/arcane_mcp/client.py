"""Thin async HTTP layer over the Arcane REST API.

Every tool goes through :func:`api`, :func:`api_stream` or :func:`api_text`, so
error handling, response unwrapping and URL-encoding live in one place.
Failures raise :class:`fastmcp.exceptions.ToolError`, which FastMCP turns into a
tool result with ``isError=true`` (the client can tell a failure from data).
"""

from __future__ import annotations

import json
import logging
from collections import deque
from typing import Any
from urllib.parse import quote

import httpx
from fastmcp.exceptions import ToolError

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None
_client_settings: Settings | None = None

MAX_ERROR_DETAIL = 2000


def seg(value: str) -> str:
    """URL-encode a single path segment (IDs, names). Prevents '../' or '?' injection."""
    value = str(value)
    if not value:
        raise ToolError("Empty identifier passed where a resource ID/name was expected.")
    return quote(value, safe="")


def env_path(env_id: str | None) -> str:
    """Base path for an environment-scoped endpoint."""
    return f"/api/environments/{seg(env_id or get_settings().default_env)}"


def _same_connection(a: Settings | None, b: Settings) -> bool:
    return a is not None and (a.base_url, a.api_key, a.timeout, a.verify) == (b.base_url, b.api_key, b.timeout, b.verify)


def get_client() -> httpx.AsyncClient:
    """Return the shared AsyncClient, creating it on first use."""
    global _client, _client_settings
    settings = get_settings()
    if _client is not None and not _client.is_closed and _same_connection(_client_settings, settings):
        return _client
    if not settings.base_url or not settings.api_key:
        raise ToolError("Arcane MCP is not configured: set ARCANE_BASE_URL and ARCANE_API_KEY.")
    _client = httpx.AsyncClient(
        base_url=settings.base_url,
        headers={"X-API-Key": settings.api_key, "Accept": "application/json"},
        timeout=httpx.Timeout(settings.timeout),
        verify=settings.verify,
        follow_redirects=False,
    )
    _client_settings = settings
    logger.info("Arcane client created for %s", settings.base_url)
    return _client


async def close_client() -> None:
    global _client, _client_settings
    if _client is not None:
        await _client.aclose()
    _client = None
    _client_settings = None


def _error_message(resp: httpx.Response) -> str:
    """Build a readable message from Huma (RFC 9457) or Echo error bodies."""
    detail: str
    try:
        body = resp.json()
    except ValueError:
        detail = resp.text.strip()
    else:
        if isinstance(body, dict):
            parts = [str(body.get(k)) for k in ("title", "detail", "error", "message") if body.get(k)]
            for err in body.get("errors") or []:
                if isinstance(err, dict):
                    loc = f" ({err['location']})" if err.get("location") else ""
                    parts.append(f"{err.get('message', '')}{loc}")
            detail = " | ".join(parts) or json.dumps(body)
        else:
            detail = json.dumps(body)
    detail = detail[:MAX_ERROR_DETAIL]
    hint = ""
    if resp.status_code == 401:
        hint = " Check ARCANE_API_KEY."
    elif resp.status_code == 403:
        hint = " The API key lacks permission for this action."
    elif resp.status_code == 404:
        hint = " Resource or endpoint not found (check the ID, env_id and Arcane version)."
    return f"Arcane API {resp.request.method} {resp.request.url.path} failed with HTTP {resp.status_code}: {detail}{hint}"


def unwrap(body: Any) -> Any:
    """Strip Arcane's ``{"success": true, "data": ...}`` envelope (and ``$schema``), keeping pagination/counts."""
    if not isinstance(body, dict):
        return body
    body = {k: v for k, v in body.items() if k != "$schema"}
    if "data" not in body or "success" not in body:
        return body
    extras = {k: v for k, v in body.items() if k not in ("success", "data", "resourceSortSupported")}
    if not extras:
        return body["data"]
    return {"items": body["data"], **extras}


def _clean(params: dict[str, Any] | None) -> dict[str, Any] | None:
    if params is None:
        return None
    out: dict[str, Any] = {}
    for k, v in params.items():
        if v is None or v == "":
            continue
        out[k] = str(v).lower() if isinstance(v, bool) else v
    return out or None


async def api(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    files: Any = None,
    data: dict[str, Any] | None = None,
    raw: bool = False,
) -> Any:
    """Call the Arcane API and return the (unwrapped) JSON payload, raising ToolError on failure."""
    client = get_client()
    kwargs: dict[str, Any] = {"params": _clean(params)}
    if json_body is not None:
        kwargs["json"] = json_body
    if files is not None:
        kwargs["files"] = files
    if data is not None:
        kwargs["data"] = data
    try:
        resp = await client.request(method.upper(), path, **kwargs)
    except httpx.TimeoutException as e:
        raise ToolError(f"Timed out calling Arcane {method.upper()} {path} (ARCANE_TIMEOUT={get_settings().timeout}s): {e}") from e
    except httpx.HTTPError as e:
        raise ToolError(f"Could not reach Arcane at {get_settings().base_url}: {e}") from e
    if resp.status_code >= 400:
        logger.warning("HTTP %s on %s %s", resp.status_code, method.upper(), path)
        raise ToolError(_error_message(resp))
    if resp.status_code == 204 or not resp.content:
        return {"success": True}
    try:
        body = resp.json()
    except ValueError:
        return {"text": resp.text[:MAX_ERROR_DETAIL]}
    return body if raw else unwrap(body)


def _summarize_stream_line(obj: dict[str, Any]) -> str | None:
    """Turn one NDJSON progress line into a short human line (or None to skip noise)."""
    if obj.get("progressDetail") and obj.get("status") in ("Downloading", "Extracting", "Waiting"):
        return None
    if "status" in obj:
        ident = f"{obj['id']}: " if obj.get("id") else ""
        return f"{ident}{obj['status']}"
    if "stream" in obj:
        text = str(obj["stream"]).strip()
        return text or None
    if "message" in obj:
        return str(obj["message"])
    if obj.get("type") == "activity" or obj.get("done"):
        return None
    return json.dumps(obj, ensure_ascii=False)[:300]


async def api_stream(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    keep_lines: int = 40,
) -> dict[str, Any]:
    """Call an endpoint that answers with a JSON-lines progress stream (pull, build, up, redeploy).

    Returns a compact summary instead of thousands of progress lines; raises ToolError if the
    stream reports an error.
    """
    client = get_client()
    timeout = httpx.Timeout(get_settings().timeout, read=get_settings().stream_timeout)
    lines: deque[str] = deque(maxlen=keep_lines)
    activity_id: str | None = None
    done = False
    error: str | None = None
    try:
        async with client.stream(method.upper(), path, params=_clean(params), json=json_body, timeout=timeout) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                raise ToolError(_error_message(resp))
            async for raw_line in resp.aiter_lines():
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    obj = json.loads(raw_line)
                except ValueError:
                    lines.append(raw_line[:300])
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("activityId") and not activity_id:
                    activity_id = str(obj["activityId"])
                if obj.get("done"):
                    done = True
                if obj.get("error"):
                    error = str(obj["error"])
                summary = _summarize_stream_line(obj)
                if summary and (not lines or lines[-1] != summary):
                    lines.append(summary)
    except httpx.TimeoutException as e:
        raise ToolError(f"Stream from {path} timed out (ARCANE_STREAM_TIMEOUT={get_settings().stream_timeout}s): {e}") from e
    except httpx.HTTPError as e:
        raise ToolError(f"Could not reach Arcane at {get_settings().base_url}: {e}") from e
    if error:
        tail = "\n".join(list(lines)[-10:])
        raise ToolError(f"Arcane reported an error: {error}\nLast output:\n{tail}")
    return {"done": done, "activity_id": activity_id, "output_tail": list(lines)}


async def api_text(path: str, *, params: dict[str, Any] | None = None, tail: int = 200, max_line: int = 2000) -> dict[str, Any]:
    """Fetch a plain-text endpoint (e.g. log download) and keep only the last ``tail`` lines."""
    client = get_client()
    lines: deque[str] = deque(maxlen=max(1, tail))
    total = 0
    try:
        async with client.stream("GET", path, params=_clean(params)) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                raise ToolError(_error_message(resp))
            async for line in resp.aiter_lines():
                total += 1
                lines.append(line[:max_line])
    except httpx.HTTPError as e:
        raise ToolError(f"Could not read {path} from Arcane: {e}") from e
    return {"total_lines": total, "returned_lines": len(lines), "lines": list(lines)}
