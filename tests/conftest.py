"""pytest 公共装置：把 CLI 跑在进程内、隔离配置目录、杜绝真实网络/浏览器。

关键约束——测试**永不**触网、**永不**碰用户真实钥匙串：

- ``BB_SYNC_HOME`` 指向临时目录，避免污染 ``~/.bb-sync``
- CWD 切到临时目录，避免读到仓库里的 ``config.yaml``
- ``keyring`` 换成内存假后端，凭据读写不落真钥匙串
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result


@dataclass
class CliResult:
    """CLI 调用结果：退出码/stderr 已按 ``main()`` 语义归一。

    ``exit_code`` 与 ``stderr`` 就是真实 ``bb-sync`` 进程会呈现的值；
    ``raw`` 保留原始 Click 结果以备需要 ``output`` 等字段时使用。
    """

    exit_code: int
    stdout: str
    stderr: str
    exception: BaseException | None
    raw: Result

    @property
    def output(self) -> str:
        return self.raw.output

    def json(self) -> Any:
        """解析 ``--json`` 的 stdout（顺便断言其纯净可解析）。"""
        import json

        return json.loads(self.stdout)


@pytest.fixture
def runner() -> CliRunner:
    """Click 的进程内测试驱动。

    Click ≥8.5 移除了 ``mix_stderr``：``result.stdout`` / ``result.stderr``
    已天然分离，``result.output`` 才是混合流。
    """
    return CliRunner()


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """**自动生效**的隔离：``BB_SYNC_HOME`` 与 CWD 都指向临时目录。

    必须是 autouse——只要有一个用例忘了隔离，就会读到仓库里真实的 ``config.yaml``
    并把它写坏。``paths.BB_SYNC_HOME`` 在 import 时固化了取值，所以直接改写模块
    属性（而非只设环境变量）。
    """
    import bb_sync.paths as paths

    home = tmp_path / ".bb-sync"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("BB_SYNC_HOME", str(home))
    monkeypatch.setattr(paths, "BB_SYNC_HOME", home)
    monkeypatch.chdir(tmp_path)
    # 清掉可能干扰配置分层的环境变量（保留夹具，避免误伤）
    for key in list(os.environ):
        if key.startswith("BB_SYNC_"):
            monkeypatch.delenv(key, raising=False)

    # Steel 安装目录默认在 ~/.steel，不跟随 BB_SYNC_HOME；同样隔离到临时目录，
    # 防止测试误操作真实安装目录。
    steel_root = tmp_path / ".steel"
    monkeypatch.setenv("BB_SYNC_STEEL_ROOT", str(steel_root))
    from bb_sync.browser import steel

    monkeypatch.setattr(steel, "STEEL_ROOT", steel_root)
    monkeypatch.setattr(steel, "STEEL_DIR", steel_root / "api")
    monkeypatch.setattr(steel, "STEEL_LOG", steel_root / "steel.log")
    monkeypatch.setattr(steel, "PROFILE_DIR", home / ".browser-profile" / "steel-chrome")
    yield tmp_path


@pytest.fixture
def isolated_home(isolated_env: Path) -> Path:
    """``isolated_env`` 的显式别名（供用例声明依赖，语义更清楚）。"""
    return isolated_env


@pytest.fixture
def config_file(isolated_env: Path) -> Path:
    """本用例实际生效的配置文件路径（``$BB_SYNC_HOME/config.yaml``）。"""
    return isolated_env / ".bb-sync" / "config.yaml"


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    """内存版钥匙串：任何测试都不得读写真实系统凭据。"""
    store: dict[str, str] = {}

    def get_password(service: str, account: str) -> str | None:
        return store.get(f"{service}:{account}")

    def set_password(service: str, account: str, value: str) -> None:
        store[f"{service}:{account}"] = value

    def delete_password(service: str, account: str) -> None:
        if f"{service}:{account}" not in store:
            import keyring.errors

            raise keyring.errors.PasswordDeleteError("not found")
        del store[f"{service}:{account}"]

    monkeypatch.setattr("keyring.get_password", get_password)
    monkeypatch.setattr("keyring.set_password", set_password)
    monkeypatch.setattr("keyring.delete_password", delete_password)
    monkeypatch.setattr("keyring.get_keyring", lambda: "memory-keyring")
    yield store


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch):
    """把 CLI 跑在进程内，并复刻 :func:`bb_sync.cli.main` 的错误渲染与退出码。

    直接 ``CliRunner.invoke(_build_cli())`` 会绕过 ``main()`` 的异常兜底，导致
    两个后果，都会让测试失真：

    1. ``BbSyncError`` 被 Click 直接抛出，**stderr 是空的**（真实二进制会打印
       ``✗ 消息`` 与 ``→ 提示``）；
    2. 拿到的 ``exit_code`` 是异常的属性（如 ``1``），而不是 ``main()`` 里
       ``raise SystemExit(exc.exit_code)`` 的语义码。

    因此这里在 invoke 之后做一次与 ``main()`` 等价的收尾：按异常类型渲染 stderr、
    计算进程级退出码，再包成 :class:`CliResult` 返回——``exit_code`` / ``stderr``
    断言即等价于断言真实 ``bb-sync`` 的 ``$?`` 与 stderr。
    """
    from bb_sync.cli import _build_cli, _clickish_exception
    from bb_sync.core.errors import BbSyncError

    runner = CliRunner()

    def invoke(args: list[str]) -> CliResult:
        result = runner.invoke(_build_cli(), args, catch_exceptions=True)
        exc = result.exception
        code = result.exit_code
        stderr = result.stderr
        rendered: list[str] = []

        if isinstance(exc, BbSyncError):
            code = int(exc.exit_code)
            rendered.append(f"✗ {exc.message}")
            if exc.hint:
                rendered.append(f"  → {exc.hint}")
        elif isinstance(exc, SystemExit):
            code = int(exc.code or 0)
        elif exc is not None and _clickish_exception(exc):
            code = int(getattr(exc, "exit_code", 2))
            if not stderr:
                rendered.append(str(exc))
        else:
            import typer

            if isinstance(exc, typer.Exit):
                code = exc.exit_code

        if rendered:
            stderr = stderr + "\n".join(rendered) + "\n"
        return CliResult(
            exit_code=code,
            stdout=result.stdout,
            stderr=stderr,
            exception=exc,
            raw=result,
        )

    return invoke
