from __future__ import annotations

import argparse
import sys
import unittest

from config import load_settings
from grade_diff import find_new_grades, fingerprint_set, normalize_grade_rows
from notifier import NotifyError, PushPlusNotifier, render_new_grades, render_plain
from ruc_jw_client import (
    RucAuthError,
    RucJwClient,
    RucJwError,
    RucResponseError,
    is_network_error,
)
from state_store import load_state, save_state


class HealthCheckError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor RUC JW grade updates.")
    parser.add_argument("--dry-run", action="store_true", help="Do not notify or save state.")
    parser.add_argument("--notify-test", action="store_true", help="Send a PushPlus test message.")
    parser.add_argument("--baseline-notify", action="store_true", help="Notify on first baseline run.")
    parser.add_argument("--config-check", action="store_true", help="Print a redacted local config check.")
    parser.add_argument("--health-check", action="store_true", help="Run tests, query grades, and notify health status.")
    args = parser.parse_args()

    settings = load_settings()
    notifier = PushPlusNotifier(settings)

    if args.config_check:
        print_config_check(settings)
        return 0

    if args.notify_test:
        try:
            notifier.send("成绩提醒测试", render_plain("PushPlus 通道已配置成功。"))
            print("PushPlus test notification sent.")
            return 0
        except NotifyError as exc:
            print(f"PushPlus test failed: {exc}", file=sys.stderr)
            return 1

    if args.health_check:
        return run_health_check(settings, notifier)

    try:
        client = RucJwClient(settings)
        rows = client.fetch_undergraduate_grades()
        grades = normalize_grade_rows(rows)
        state = load_state(settings.state_file)
        current_fingerprints = fingerprint_set(grades, settings.grade_hash_salt)
        new_grades = find_new_grades(
            grades,
            state.fingerprints,
            settings.grade_hash_salt,
        )

        if state.first_run:
            print("Baseline created.")
            if (settings.baseline_notify or args.baseline_notify) and grades and not args.dry_run:
                notifier.send("成绩提醒基线已建立", render_new_grades(grades))
            if not args.dry_run:
                save_state(settings.state_file, current_fingerprints)
            return 0

        if not new_grades:
            print("No grade changes.")
            if settings.notify_unchanged and not args.dry_run:
                notifier.send("成绩提醒运行正常", render_plain(f"暂无新成绩，共 {len(grades)} 条。"))
            return 0

        print(f"Found {len(new_grades)} new or changed grades.")
        if not args.dry_run:
            notifier.send("新成绩出来了", render_new_grades(new_grades))
            save_state(
                settings.state_file,
                state.fingerprints | current_fingerprints,
                state.created_at,
            )
        return 0
    except RucAuthError as exc:
        return notify_failure(notifier, "教务登录态失效", str(exc), not args.dry_run)
    except RucResponseError as exc:
        return notify_failure(notifier, "教务成绩接口异常", str(exc), not args.dry_run)
    except RucJwError as exc:
        return notify_failure(notifier, "教务查询失败", str(exc), not args.dry_run)
    except Exception as exc:
        if is_network_error(exc):
            return notify_failure(notifier, "教务网络请求失败", str(exc), not args.dry_run)
        raise


def run_health_check(settings, notifier: PushPlusNotifier) -> int:
    try:
        test_count = run_unit_tests()
        client = RucJwClient(settings)
        rows = client.fetch_undergraduate_grades()
        grades = normalize_grade_rows(rows)
        if not grades:
            raise HealthCheckError("教务接口可访问，但没有解析到可见成绩。")

        message = (
            "成绩提醒健康检查正常。"
            f"单元测试 {test_count} 个通过，教务查询正常，"
            "PushPlus 通道正常。"
        )
        notifier.send("成绩提醒健康检查正常", render_plain(message))
        print(message)
        return 0
    except NotifyError as exc:
        print(f"成绩提醒健康检查失败: PushPlus 通道无法发送消息: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        reason = health_failure_message(exc)
        print(f"成绩提醒健康检查失败: {reason}", file=sys.stderr)
        try:
            notifier.send("成绩提醒健康检查失败", render_plain(reason))
        except Exception as notify_exc:
            print(f"Failed to send health failure notification: {notify_exc}", file=sys.stderr)
        return 1


def run_unit_tests() -> int:
    suite = unittest.defaultTestLoader.discover("tests")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        raise HealthCheckError(
            f"代码自检未通过：失败 {len(result.failures)} 个，错误 {len(result.errors)} 个。"
        )
    return result.testsRun


def notify_failure(
    notifier: PushPlusNotifier,
    title: str,
    message: str,
    do_notify: bool = True,
) -> int:
    print(f"{title}: {message}", file=sys.stderr)
    if not do_notify:
        return 1
    try:
        notifier.send(safe_failure_title(title), render_plain(safe_failure_message(title)))
    except Exception as notify_exc:
        print(f"Failed to send failure notification: {notify_exc}", file=sys.stderr)
    return 1


def safe_failure_title(title: str) -> str:
    if "登录态" in title or "TOKEN" in title:
        return "教务登录失效"
    return title


def safe_failure_message(title: str) -> str:
    if "登录态" in title or "TOKEN" in title:
        return "教务登录失败，请检查账号密码配置，或确认统一身份认证没有要求验证码、二次验证。"
    if "接口" in title:
        return "教务成绩接口返回异常，请查看 GitHub Actions 日志。"
    if "网络" in title:
        return "教务网络请求失败，请查看 GitHub Actions 日志。"
    return "成绩监控运行失败，请查看 GitHub Actions 日志。"


def health_failure_message(exc: BaseException) -> str:
    if isinstance(exc, RucAuthError):
        return "教务登录失败，请检查账号密码配置，或确认统一身份认证没有要求验证码、二次验证。"
    if isinstance(exc, RucResponseError):
        return "教务成绩接口返回异常，请查看 GitHub Actions 日志。"
    if isinstance(exc, RucJwError):
        return "教务查询失败，请查看 GitHub Actions 日志。"
    if is_network_error(exc):
        return "网络请求失败，请查看 GitHub Actions 日志。"
    if isinstance(exc, HealthCheckError):
        return str(exc)
    return "成绩提醒健康检查失败，请查看 GitHub Actions 日志。"


def print_config_check(settings) -> None:
    password_login_configured = bool(settings.ruc_username and settings.ruc_password)

    print("本地配置体检（严格隐私模式）")
    print("- 登录方式: 账号密码直登")
    print(f"- 账号密码配置完整: {'是' if password_login_configured else '否'}")
    print(f"- RUC_USERNAME: {format_configured(settings.ruc_username)}")
    print(f"- RUC_PASSWORD: {format_configured(settings.ruc_password)}")
    print(f"- PUSHPLUS_TOKEN: {format_configured(settings.pushplus_token)}")
    print(f"- GRADE_HASH_SALT: {format_configured(settings.grade_hash_salt)}")


def format_configured(value: str) -> str:
    return "已配置" if value else "未配置"


if __name__ == "__main__":
    raise SystemExit(main())
