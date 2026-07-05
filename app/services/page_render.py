"""Inject server-built JSON into static HTML shells."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.responses import HTMLResponse

_HTML_NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


def _json_script_payload(data: Any) -> str:
    return (
        json.dumps(data, default=str)
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
    if page_data is not None:
        payload = _json_script_payload(page_data)
        inject = (
            f'<script type="application/json" id="ntg-page-data">{payload}</script>\n'
        )
        if "</head>" in html:
            html = html.replace("</head>", inject + "</head>", 1)
        else:
            html = inject + html
    return HTMLResponse(content=html, headers=_HTML_NO_CACHE)
