from __future__ import annotations

import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
from urllib.error import URLError

from check_grades import (
    HealthCheckError,
    format_configured,
    health_failure_message,
    print_config_check,
    safe_failure_message,
    safe_failure_title,
)
from config import Settings
from grade_diff import fingerprint_set, find_new_grades, normalize_grade_rows
from http_json import JsonResponse
from notifier import NotifyError, PushPlusNotifier
from ruc_jw_client import (
    RucAuthError,
    RucJwClient,
    RucResponseError,
    qz_conditions,
)
from ruc_auth import (
    RucPasswordSession,
    describe_login_error,
    extract_csrf_token,
    extract_login_iframe_url,
    format_vruc_username,
    raw_query_value,
    _request_text,
)


class QzEncodingTest(unittest.TestCase):
    def test_empty_conditions_match_frontend_encoding(self) -> None:
        self.assertEqual(
            qz_conditions(),
            "QZDATASOFTJddJJVIJY29uZGl0aW9uR3JvdXAlMjIlM0ElNUIlN0IlMjJsaW5rJTIyJTNBJTIyYW5kJTIyJTJDJTIyY29uZGl0aW9uJTIyJTNBJTVCJTVEJTdEyTTECTTE",
        )


class GradeDiffTest(unittest.TestCase):
    def test_normalize_skips_summary_and_empty_score_rows(self) -> None:
        grades = normalize_grade_rows(
            [
                {"xnxq": "2025-2026-2", "zxf": 10},
                {"kcname": "数据库系统", "zcjname1": "", "jd": ""},
                {
                    "xnxq": "2025-2026-2",
                    "kcname": "数据库系统",
                    "jsname": "张老师",
                    "xf": "3",
                    "zcjname1": "92",
                    "jd": "4.0",
                    "cjbzname": "正常",
                },
            ]
        )
        self.assertEqual(len(grades), 1)
        self.assertEqual(grades[0].course_name, "数据库系统")
        self.assertEqual(grades[0].final_score, "92")

    def test_find_new_grades(self) -> None:
        grades = normalize_grade_rows(
            [
                {"kcname": "A", "zcjname1": "90"},
                {"kcname": "B", "zcjname1": "91"},
            ]
        )
        seen = {grades[0].fingerprint("salt")}
        new_grades = find_new_grades(grades, seen, "salt")
        self.assertEqual([grade.course_name for grade in new_grades], ["B"])

    def test_fingerprint_set_is_stable(self) -> None:
        grades = normalize_grade_rows([{"kcname": "A", "zcjname1": "90"}])
        self.assertEqual(fingerprint_set(grades, "salt"), fingerprint_set(grades, "salt"))


class RucResponseParsingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = RucJwClient(Settings(pushplus_token="", grade_hash_salt=""))

    def test_unwraps_common_paginated_records_shape(self) -> None:
        rows = self.client._unwrap_grade_rows(
            {"code": 0, "data": {"records": [{"kcname": "A", "zcjname1": "90"}]}}
        )
        self.assertEqual(rows, [{"kcname": "A", "zcjname1": "90"}])

    def test_auth_message_is_reported_as_auth_error(self) -> None:
        with self.assertRaises(RucAuthError):
            self.client._unwrap_grade_rows({"code": 401, "msg": "登录超时"})

    def test_wrapped_401_status_is_reported_as_auth_error(self) -> None:
        with self.assertRaises(RucAuthError):
            self.client._unwrap_grade_rows({"code": "security.httpstatu.401.1006"})

    def test_unknown_shape_reports_safe_summary(self) -> None:
        with self.assertRaisesRegex(RucResponseError, "top-level keys"):
            self.client._unwrap_grade_rows({"code": 0, "data": {"total": 1}})

    def test_password_login_runs_before_grade_request(self) -> None:
        client = RucJwClient(
            Settings(
                ruc_username="student",
                ruc_password="password",
                pushplus_token="",
                grade_hash_salt="",
            )
        )
        response = JsonResponse(
            status=200,
            body={"data": {"records": [{"kcname": "A", "zcjname1": "90"}]}},
            text="{}",
        )
        with patch("ruc_jw_client.login_with_password") as login, patch(
            "ruc_jw_client.post_json",
            return_value=response,
        ) as post:
            login.return_value = RucPasswordSession(
                token="fresh-token",
                cookie_header="SESSION=fresh",
            )
            self.assertEqual(client.fetch_undergraduate_grades()[0]["kcname"], "A")
        login.assert_called_once()
        self.assertEqual(post.call_args.kwargs["headers"]["TOKEN"], "fresh-token")
        self.assertEqual(post.call_args.kwargs["headers"]["Cookie"], "SESSION=fresh")

    def test_password_login_retries_once_after_rejected_session(self) -> None:
        client = RucJwClient(
            Settings(
                ruc_username="student",
                ruc_password="password",
                pushplus_token="",
                grade_hash_salt="",
            )
        )
        responses = [
            JsonResponse(status=401, body={}, text=""),
            JsonResponse(
                status=200,
                body={"data": {"records": [{"kcname": "A", "zcjname1": "90"}]}},
                text="{}",
            ),
        ]
        with patch(
            "ruc_jw_client.login_with_password",
            side_effect=[
                RucPasswordSession(token="first-token", cookie_header="SESSION=first"),
                RucPasswordSession(token="fresh-token", cookie_header="SESSION=fresh"),
            ],
        ) as login, patch("ruc_jw_client.post_json", side_effect=responses) as post:
            self.assertEqual(client.fetch_undergraduate_grades()[0]["kcname"], "A")
        self.assertEqual(login.call_count, 2)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0].kwargs["headers"]["TOKEN"], "first-token")
        self.assertEqual(post.call_args_list[1].kwargs["headers"]["TOKEN"], "fresh-token")

    def test_missing_password_config_fails_before_grade_request(self) -> None:
        client = RucJwClient(Settings(pushplus_token="", grade_hash_salt=""))
        with patch("ruc_jw_client.post_json") as post:
            with self.assertRaisesRegex(RucAuthError, "RUC_USERNAME/RUC_PASSWORD"):
                client.fetch_undergraduate_grades()
        post.assert_not_called()


class RucPasswordLoginTest(unittest.TestCase):
    def test_formats_vruc_username_for_student_account(self) -> None:
        self.assertEqual(format_vruc_username("20240001"), "ruc:20240001")
        self.assertEqual(format_vruc_username("ruc:20240001"), "ruc:20240001")
        self.assertEqual(format_vruc_username("name@example.com"), "name@example.com")
        self.assertEqual(format_vruc_username("13800138000"), "%2B86 13800138000")

    def test_extracts_login_iframe_and_csrf_token(self) -> None:
        html = (
            '<iframe id="login-iframe" src="/auth/login?proxy=true&redirect_uri=a%2Fb">'
            "</iframe>"
            '<input type="hidden" name="csrftoken" value="abc123" id="csrftoken" />'
        )
        self.assertEqual(
            extract_login_iframe_url(html, "https://v.ruc.edu.cn/account/login"),
            "https://v.ruc.edu.cn/auth/login?proxy=true&redirect_uri=a%2Fb",
        )
        self.assertEqual(extract_csrf_token(html), "abc123")

    def test_raw_query_value_preserves_encoded_redirect_uri(self) -> None:
        url = "https://v.ruc.edu.cn/auth/login?proxy=true&redirect_uri=a%3Fb%3D1%26c%3D2"
        self.assertEqual(raw_query_value(url, "redirect_uri"), "a%3Fb%3D1%26c%3D2")

    def test_login_errors_are_actionable(self) -> None:
        self.assertIn("账号或密码", describe_login_error("verification failed"))
        self.assertIn("图片验证码", describe_login_error("captcha error"))
        self.assertIn("二次验证码", describe_login_error("need twofactor"))
        self.assertIn("临时锁定", describe_login_error("please try again after 1 hours"))

    def test_request_text_retries_temporary_network_errors(self) -> None:
        opener = FlakyOpener(failures=2, text="ok")
        with patch("ruc_auth.time.sleep") as sleep:
            response = _request_text(opener, "https://v.ruc.edu.cn/auth/login", 30)

        self.assertEqual(response.text, "ok")
        self.assertEqual(opener.calls, 3)
        self.assertEqual(sleep.call_count, 2)


class ConfigCheckTest(unittest.TestCase):
    def test_format_configured_only_reports_presence(self) -> None:
        self.assertEqual(format_configured("secret"), "已配置")
        self.assertEqual(format_configured(""), "未配置")

    def test_config_check_does_not_reveal_secret_details(self) -> None:
        settings = Settings(
            ruc_username="student123",
            ruc_password="password-secret",
            pushplus_token="658716a2add245a7b3dc4346d83fb594",
            grade_hash_salt="salt-secret",
        )
        output = StringIO()
        with redirect_stdout(output):
            print_config_check(settings)

        text = output.getvalue()
        self.assertIn("严格隐私模式", text)
        self.assertIn("RUC_USERNAME: 已配置", text)
        self.assertNotIn("student123", text)
        self.assertNotIn("password-secret", text)
        self.assertNotIn("658716a2add245a7b3dc4346d83fb594", text)
        self.assertNotIn("salt-secret", text)
        self.assertNotIn("长度", text)
        self.assertNotIn("值", text)

    def test_failure_notification_avoids_sensitive_words(self) -> None:
        self.assertEqual(safe_failure_title("教务登录态失效"), "教务登录失效")
        message = safe_failure_message("教务登录态失效")
        self.assertNotIn("TOKEN", message)
        self.assertNotIn("COOKIE", message)

    def test_health_failure_message_classifies_auth_and_tests(self) -> None:
        self.assertIn("教务登录失败", health_failure_message(RucAuthError("401")))
        self.assertIn("代码自检未通过", health_failure_message(HealthCheckError("代码自检未通过")))


class NotifierPrivacyTest(unittest.TestCase):
    def test_http_error_does_not_include_response_body(self) -> None:
        notifier = PushPlusNotifier(
            Settings(
                pushplus_token="token-secret",
                grade_hash_salt="",
            )
        )
        with patch(
            "notifier.post_json",
            return_value=JsonResponse(
                status=500,
                body="echoed sensitive content",
                text="echoed sensitive content",
            ),
        ):
            with self.assertRaisesRegex(NotifyError, "^PushPlus HTTP 500$"):
                notifier.send("title", "private message")

    def test_rejected_message_does_not_include_response_message(self) -> None:
        notifier = PushPlusNotifier(
            Settings(
                pushplus_token="token-secret",
                grade_hash_salt="",
            )
        )
        with patch(
            "notifier.post_json",
            return_value=JsonResponse(
                status=200,
                body={"code": 400, "msg": "echoed sensitive content"},
                text='{"code":400,"msg":"echoed sensitive content"}',
            ),
        ):
            with self.assertRaisesRegex(
                NotifyError,
                "^PushPlus rejected message with code 400$",
            ):
                notifier.send("title", "private message")


class FakeResponse:
    status = 200
    headers = {}

    def __init__(self, text: str, url: str) -> None:
        self._text = text
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._text.encode("utf-8")

    def geturl(self) -> str:
        return self._url


class FlakyOpener:
    def __init__(self, failures: int, text: str) -> None:
        self.failures = failures
        self.text = text
        self.calls = 0

    def open(self, request, timeout: int):
        self.calls += 1
        if self.calls <= self.failures:
            raise URLError("Temporary failure in name resolution")
        return FakeResponse(self.text, request.full_url)


if __name__ == "__main__":
    unittest.main()
