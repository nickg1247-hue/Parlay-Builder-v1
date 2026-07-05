"""Inject server-built JSON into static HTML shells."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from fastapi.responses import HTMLResponse

_HTML_NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}
_PAGE_DATA_JS = "/static/page-data.js?v=20260737"


def _json_default(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _sanitize_for_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {str(k): _sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_for_json(v) for v in value]
    return _json_default(value)


def _json_script_payload(data: Any) -> str:
    clean = _sanitize_for_json(data)
    return (
        json.dumps(clean, default=_json_default, allow_nan=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def render_static_page(
    static_dir: Path,
    html_name: str,
    page_data: dict[str, Any] | None = None,
) -> HTMLResponse:
    html_path = static_dir / html_name
    html = html_path.read_text(encoding="utf-8")
    inject_parts = [f'<script src="{_PAGE_DATA_JS}"></script>']
    if page_data is not None:
        payload = _json_script_payload(page_data)
        inject_parts.append(
            f'<script type="application/json" id="ntg-page-data">{payload}</script>'
        )
    inject = "\n".join(inject_parts) + "\n"
    if "</head>" in html:
        html = html.replace("</head>", inject + "</head>", 1)
    else:
        html = inject + html
    return HTMLResponse(content=html, headers=_HTML_NO_CACHE)
