from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JsonResponse:
    status: int
    body: Any
    text: str


class HttpJsonError(RuntimeError):
    pass


def post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int = 30,
) -> JsonResponse:
    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body_bytes,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            return JsonResponse(
                status=response.status,
                body=_decode_json(text),
                text=text,
            )
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return JsonResponse(status=exc.code, body=_decode_json(text), text=text)
    except urllib.error.URLError as exc:
        raise HttpJsonError(str(exc)) from exc


def _decode_json(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
