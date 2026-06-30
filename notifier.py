from __future__ import annotations

import html
from typing import Iterable

from config import Settings
from grade_diff import Grade
from http_json import post_json


class NotifyError(RuntimeError):
    pass


class PushPlusNotifier:
    def __init__(self, settings: Settings):
        self.settings = settings

    def send(self, title: str, content: str) -> None:
        if not self.settings.pushplus_token:
            raise NotifyError("PUSHPLUS_TOKEN is not configured")
        response = post_json(
            self.settings.pushplus_url,
            {
                "token": self.settings.pushplus_token,
                "title": title,
                "content": content,
                "template": self.settings.pushplus_template,
            },
            headers={"Content-Type": "application/json"},
            timeout=self.settings.request_timeout,
        )
        if response.status < 200 or response.status >= 300:
            raise NotifyError(f"PushPlus HTTP {response.status}: {response.text[:200]}")
        body = response.body
        if isinstance(body, dict):
            code = str(body.get("code", "200"))
            if code not in {"200", "0"}:
                message = body.get("msg") or body.get("message") or response.text[:200]
                raise NotifyError(f"PushPlus rejected message: {message}")


def render_new_grades(grades: Iterable[Grade]) -> str:
    items = "".join(
        f"<li>{html.escape(grade.display_line())}</li>" for grade in grades
    )
    return f"<p>教务系统出现新的课程成绩：</p><ul>{items}</ul>"


def render_plain(text: str) -> str:
    return f"<p>{html.escape(text)}</p>"
