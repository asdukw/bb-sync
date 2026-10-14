"""环境体检：逐项检查前置条件，输出 ✓/✗ 与修复提示。

必查项（缺失即视为环境不可用）: Python / git / 浏览器 / 网络。
可选展示项: 凭据 / 浏览器后端状态。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from urllib import error, request

from bb_sync import __version__, paths
from bb_sync.blackboard.models import BASE
from bb_sync.browser import launcher
from bb_sync.core.errors import EnvironmentError_
from bb_sync.core.output import Console
from bb_sync.creds import load_credentials


@dataclass
class CheckResult:
    """单项体检结果。"""

    ok: bool
    label: str
    detail: str = ""
    hint: str = ""
    required: bool = True


def _tool_version(cmd: list[str]) -> str | None:
    """运行 ``<tool> --version``，返回首行；失败返回 None。"""
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=6, encoding="utf-8", errors="ignore"
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = (out.stdout or out.stderr).strip().splitlines()
    return lines[0].strip() if lines and out.returncode == 0 else None


def _git_install_hint() -> str:
    """按平台给出 git 安装方式。"""
    if sys.platform == "darwin":
        return "bb-sync 从 GitHub 安装需要 git：brew install git"
    if sys.platform.startswith("win"):
        return "bb-sync 从 GitHub 安装需要 git：winget install --id Git.Git -e"
    return "bb-sync 从 GitHub 安装需要 git：请通过系统包管理器安装"


def check_python() -> CheckResult:
    v = sys.version_info
    return CheckResult(
        (v.major, v.minor) >= (3, 11),
        "Python",
        f"{v.major}.{v.minor}.{v.micro}（需要 ≥3.11，uv 安装 bb-sync 时会自动准备）",
    )


def check_git() -> CheckResult:
    path = shutil.which("git")
    ver = _tool_version([path, "--version"]) if path else None
    return CheckResult(
        bool(ver),
        "git",
        ver or "未找到",
        _git_install_hint(),
    )


def check_browser() -> CheckResult:
    """探测本机 Chrome / Edge——现在它是唯一的外部运行时依赖。"""
    try:
        browser = launcher.detect_browser()
    except EnvironmentError_ as exc:
        return CheckResult(False, "浏览器", exc.message, exc.hint)
    if browser is None:
        return CheckResult(
            False, "浏览器", "未检测到 Chrome / Edge", launcher.browser_install_hint()
        )
    if os.environ.get("CHROME_EXECUTABLE_PATH"):
        return CheckResult(True, "浏览器", f"{browser.label}（自定义 {browser.path}）")
    return CheckResult(True, "浏览器", f"{browser.label} ({browser.path})")


def check_network(retries: int = 3) -> CheckResult:
    """检查 Blackboard 站点可达性（代理抖动会瞬时失败，故重试）。"""
    last_err = ""
    for _ in range(retries):
        try:
            req = request.Request(BASE, headers={"User-Agent": "bb-sync"})
            request.urlopen(req, timeout=6)
            return CheckResult(True, "网络", f"{BASE} 可达")
        except error.HTTPError:  # 有响应即视为可达（如 301/302/403）
            return CheckResult(True, "网络", f"{BASE} 可达")
        except Exception as exc:
            last_err = str(exc)
            time.sleep(1.5)
    return CheckResult(
        False,
        "网络",
        f"{BASE} 不可达 ({last_err})",
        "校外使用需要先连接学校 VPN；代理用户请确认系统代理已开启",
    )


def check_credentials() -> CheckResult:
    try:
        creds = load_credentials()
    except Exception as exc:
        return CheckResult(
            False, "凭据", f"读取失败: {exc}", "运行 bb-sync auth login 重新录入", required=False
        )
    if creds.ok:
        return CheckResult(True, "凭据", f"已保存（{creds.source}）", required=False)
    return CheckResult(
        False, "凭据", "未配置", "运行 bb-sync auth login 录入学号密码", required=False
    )


def check_browser_backend() -> CheckResult:
    """后台浏览器实例是否在跑（不在也没关系，下次同步会自动拉起）。"""
    session = launcher.current_session()
    if session is None:
        return CheckResult(
            True,
            "浏览器后端",
            "未运行（下次同步自动启动）",
            required=False,
        )
    return CheckResult(
        True,
        "浏览器后端",
        f"{session.browser.label} 运行中（{session.mode_label}，端口 {session.port}）",
        required=False,
    )


def legacy_steel_hint() -> str:
    """旧版 Steel 后端目录若还在，提示用户清理（2.0 起不再使用）。"""
    legacy = paths.BB_SYNC_HOME / ".steel"
    if not legacy.exists():
        return ""
    return f"旧版 Steel 后端已不再使用，可删除 {legacy} 释放磁盘空间"


#: 体检项顺序即输出顺序
CHECKS = (
    check_python,
    check_git,
    check_browser,
    check_network,
    check_credentials,
    check_browser_backend,
)


def run_doctor(console: Console, *, skip_network: bool = False) -> list[CheckResult]:
    """执行全部体检项，逐项输出，返回结果列表。"""
    console.step(f"bb-sync {__version__} — doctor")
    console.log("")
    results: list[CheckResult] = []
    for fn in CHECKS:
        if skip_network and fn is check_network:
            continue
        result = fn()
        results.append(result)
        mark = "✓" if result.ok else "✗"
        line = f"[{mark}] {result.label}" + (f"  {result.detail}" if result.detail else "")
        if result.ok:
            console.log(line)
        else:
            console.error(line)
            if result.hint:
                console.hint(result.hint)

    required_fail = sum(1 for r in results if r.required and not r.ok)
    total_ok = sum(1 for r in results if r.ok)
    console.log(
        f"\n{total_ok}/{len(results)} 项通过"
        + ("，存在缺失项，请按提示修复" if required_fail else "")
    )
    # 旧版残留是「提示」而非「缺项」，不要让它影响体检通过与否
    if hint := legacy_steel_hint():
        console.hint(hint)
    return results


def environment_ok(console: Console) -> bool:
    """供 ``run`` 前置校验使用：必查项全过才返回 True。"""
    missing = [r for r in run_doctor(console) if r.required and not r.ok]
    return not missing


__all__ = ["CHECKS", "CheckResult", "environment_ok", "legacy_steel_hint", "run_doctor"]
