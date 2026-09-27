"""自动更新提示的 CLI 端到端测试。

更新检查默认关闭在 ``tests/conftest.py`` 的隔离装置里；本文件只验证成功命令后的
提示通道，网络与版本比较本身由 ``tests/test_units.py`` 覆盖。
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_successful_command_reports_available_update(cli, monkeypatch) -> None:
    """普通命令成功后，更新提示走 stderr，stdout 保持干净。"""
    from bb_sync.core import update

    monkeypatch.delenv(update.DISABLE_ENV, raising=False)
    monkeypatch.setattr(
        update,
        "check_for_update",
        lambda **_: update.UpdateInfo(
            current="1.0.0",
            latest="1.1.0",
            release_url="https://example.test/v1.1.0",
        ),
    )

    result = cli(["--json", "config", "path"])

    assert result.exit_code == 0
    assert "bb-sync 有新版本" in result.stderr
    assert "uv tool upgrade bb-sync" in result.stderr
    assert "新版本" not in result.stdout


@pytest.mark.parametrize("args", [["--help"], ["--version"]])
def test_early_exit_does_not_check_update(cli, monkeypatch, args: list[str]) -> None:
    """帮助 / 版本查询保持冷启动，不应为更新检查访问网络。"""
    from bb_sync.core import update

    monkeypatch.delenv(update.DISABLE_ENV, raising=False)
    monkeypatch.setattr(
        update,
        "check_for_update",
        lambda **_: pytest.fail("--help / --version 不应触发更新检查"),
    )

    result = cli(args)

    assert result.exit_code == 0


def test_quiet_suppresses_update_check(cli, monkeypatch) -> None:
    """``--quiet`` 是脚本模式，不应触发额外的网络检查/提示。"""
    from bb_sync.core import update

    monkeypatch.delenv(update.DISABLE_ENV, raising=False)
    monkeypatch.setattr(
        update,
        "check_for_update",
        lambda **_: pytest.fail("--quiet 不应触发更新检查"),
    )

    result = cli(["--quiet", "config", "path"])

    assert result.exit_code == 0
    assert "新版本" not in result.stderr


def test_failed_command_does_not_report_update(cli, monkeypatch, config_file: Path) -> None:
    """失败命令不提示更新；更新检查只在成功返回后执行。"""
    from bb_sync.core import update

    config_file.write_text("root: x\n", encoding="utf-8")
    monkeypatch.delenv(update.DISABLE_ENV, raising=False)
    monkeypatch.setattr(
        update,
        "check_for_update",
        lambda **_: pytest.fail("失败命令不应触发更新检查"),
    )

    result = cli(["config", "get", "no_such_key"])

    assert result.exit_code == 6
    assert "新版本" not in result.stderr
