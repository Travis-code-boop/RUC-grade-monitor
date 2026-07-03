from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import quote

from config import Settings
from http_json import HttpJsonError, post_json
from ruc_auth import RucPasswordLoginError, RucPasswordSession, login_with_password


EMPTY_CONDITIONS = {"conditionGroup": [{"link": "and", "condition": []}]}
ROW_CONTAINER_KEYS = (
    "records",
    "rows",
    "items",
    "list",
    "resultList",
    "result",
    "results",
    "content",
    "data",
)


class RucJwError(RuntimeError):
    pass


class RucAuthError(RucJwError):
    pass


class RucResponseError(RucJwError):
    pass


def qz_base64_encrypt(text: str) -> str:
    if not text:
        return ""
    encoded = base64.b64encode(quote(text, safe="-_.!~*'()").encode("utf-8")).decode(
        "ascii"
    )
    original = list(encoded)
    mixed = list(encoded)
    if len(mixed) >= 8:
        mixed[1] = original[-2]
        mixed[3] = original[-4]
        mixed[5] = original[-6]
        mixed[7] = original[-8]
        mixed[-2] = original[1]
        mixed[-4] = original[3]
        mixed[-6] = original[5]
        mixed[-8] = original[7]
    return "QZDATASOFT" + "".join(mixed)


def qz_conditions(value: dict[str, Any] | None = None) -> str:
    raw = json.dumps(value or EMPTY_CONDITIONS, ensure_ascii=False, separators=(",", ":"))
    return qz_base64_encrypt(raw)


class RucJwClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._session: RucPasswordSession | None = None

    def fetch_undergraduate_grades(self) -> list[dict[str, Any]]:
        self._ensure_authenticated()
        payload = {
            "pyfa007id": self.settings.ruc_plan_type,
            "jczy013id": self.settings.ruc_semesters or [],
            "fxjczy005id": "",
            "cjckflag": "xsdcjck",
            "kthList": [],
            "page": {
                "pageIndex": 1,
                "pageSize": self.settings.ruc_page_size,
                "orderBy": json.dumps(
                    [{"field": "jczy013id", "sortType": "asc"}],
                    separators=(",", ":"),
                ),
                "conditions": qz_conditions(),
            },
        }
        response = self._post_grade_request(payload)
        if response.status in {401, 403}:
            self._login_with_password()
            response = self._post_grade_request(payload)
        if response.status in {401, 403}:
            raise RucAuthError(
                f"RUC auth failed with HTTP {response.status}; "
                "账号密码登录成功但教务接口仍拒绝访问"
            )
        if response.status < 200 or response.status >= 300:
            raise RucResponseError(f"RUC request failed with HTTP {response.status}")
        return self._unwrap_grade_rows(response.body)

    def _post_grade_request(self, payload: dict[str, Any]):
        return post_json(
            self.settings.ruc_grades_url,
            payload,
            headers=self._headers(),
            timeout=self.settings.request_timeout,
        )

    def _headers(self) -> dict[str, str]:
        if self._session is None:
            raise RucAuthError("教务登录态未初始化")
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Content-Type": "application/json",
            "Origin": "https://jw.ruc.edu.cn",
            "Referer": "https://jw.ruc.edu.cn/Njw2017/index.html",
            "User-Agent": self.settings.ruc_browser_user_agent,
            "X-Requested-With": "XMLHttpRequest",
            "app": "PCWEB",
            "locale": "zh_CN",
            "TOKEN": self._session.token,
            "userRoleCode": self.settings.ruc_user_role_code,
            "userAgent": self.settings.ruc_user_agent,
            "Simulated-By": "",
        }
        if self._session.cookie_header:
            headers["Cookie"] = self._session.cookie_header
        return headers

    def _ensure_authenticated(self) -> None:
        if self._session is None:
            self._login_with_password()

    def _login_with_password(self) -> None:
        try:
            session = login_with_password(self.settings)
        except RucPasswordLoginError as exc:
            raise RucAuthError(str(exc)) from exc
        self._session = session

    def _unwrap_grade_rows(self, body: Any) -> list[dict[str, Any]]:
        if not isinstance(body, dict):
            if isinstance(body, list):
                rows = _find_grade_rows(body)
                if rows is not None:
                    return rows
            raise RucResponseError("RUC response is not JSON object/list")

        message = _response_message(body)
        status_code = _response_status_code(body)
        if _looks_like_auth_failure(status_code, message):
            raise RucAuthError(message or f"RUC returned auth code {status_code}")

        success = body.get("success")
        if success is False:
            raise RucResponseError(f"RUC returned error: {message or 'success=false'}")
        if status_code and status_code not in {"success", "None", "0", "200"}:
            raise RucResponseError(f"RUC returned error: {message or status_code}")

        rows = _find_grade_rows(body)
        if rows is not None:
            return rows

        raise RucResponseError(
            "RUC response does not contain grade rows "
            f"({_summarize_response_shape(body)})"
        )


def _find_grade_rows(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, list):
        if all(isinstance(item, dict) for item in value):
            return value
        return None
    if not isinstance(value, dict):
        return None

    for key in ROW_CONTAINER_KEYS:
        if key in value:
            rows = _find_grade_rows(value[key])
            if rows is not None:
                return rows

    for nested in value.values():
        rows = _find_grade_rows(nested)
        if rows is not None:
            return rows
    return None


def _response_status_code(body: dict[str, Any]) -> str:
    for key in ("errorCode", "code", "status", "statusCode"):
        value = body.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def _response_message(body: dict[str, Any]) -> str:
    for key in ("errorMessage", "message", "msg", "detail", "description"):
        value = body.get(key)
        if value:
            return str(value).strip()
    return ""


def _looks_like_auth_failure(status_code: str, message: str) -> bool:
    if status_code in {"401", "403", "-401", "-403"}:
        return True
    if ".401." in status_code or ".403." in status_code:
        return True
    auth_words = ("登录", "登陆", "token", "TOKEN", "认证", "授权", "超时", "过期")
    return any(word in status_code or word in message for word in auth_words)


def _summarize_response_shape(body: dict[str, Any]) -> str:
    top_keys = ", ".join(list(body.keys())[:10]) or "none"
    summary = [f"top-level keys: {top_keys}"]

    data = body.get("data")
    if isinstance(data, dict):
        data_keys = ", ".join(list(data.keys())[:10]) or "none"
        summary.append(f"data keys: {data_keys}")
    elif isinstance(data, list):
        summary.append(f"data list length: {len(data)}")

    status_code = _response_status_code(body)
    if status_code:
        summary.append(f"code: {status_code}")

    message = _response_message(body)
    if message:
        summary.append(f"message: {message[:80]}")

    return "; ".join(summary)


def is_network_error(exc: BaseException) -> bool:
    return isinstance(exc, HttpJsonError)
