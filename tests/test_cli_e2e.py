"""CLI 端到端测试：把整条 Typer/Click 命令链在进程内跑一遍。

这些用例替代了此前手工执行的全量回归，覆盖三类「改坏了会立刻致残」的契约：

1. **命令行契约**：``--version`` / ``-h`` / 无参数 / 未知命令 / 未知选项 的行为与退出码
2. **输出契约**：stdout 只放结果（``--json`` 时是唯一一个 JSON 文档），诊断走 stderr
3. **配置契约**：``config set/get/unset/show`` 的读写与注释保留

全部离线：不登录、不下载、不起浏览器。数据类命令（run / course list / doctor）
只断言「参数解析与错误分支」，不触发真实 IO。

退出码经 ``cli`` 装置映射为**进程级**语义值（与 ``bb-sync`` 真实退出码一致）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------- 基础命令契约


def test_version_exit_zero(cli) -> None:
    """``--version`` 走 stdout，退出码 0。"""
    result = cli(["--version"])
    assert result.exit_code == 0
    assert result.stdout.startswith("bb-sync ")
    assert result.stderr == ""


def test_no_args_prints_help_and_exit_zero(cli) -> None:
    """无参数 = 显示帮助 + 退出码 0（不用 Click 默认的 2）。"""
    result = cli([])
    assert result.exit_code == 0
    assert "Usage" in result.stdout or "用法" in result.stdout
    for name in ("run", "doctor", "config", "auth", "course"):
        assert name in result.stdout


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_flags(cli, flag: str) -> None:
    result = cli([flag])
    assert result.exit_code == 0
    assert "bb-sync" in result.stdout


def test_root_help_lists_subcommands_without_importing_them(cli) -> None:
    """根帮助的命令列表来自本地元信息，不应把命令模块导进来。

    这是冷启动优化的回归防线：一旦有人在 ``format_commands`` 里做了真实导入
    （例如为了取 help 文本而 import 命令模块），playwright/pydantic 会被拖进来。
    """
    import sys

    for mod in [m for m in sys.modules if m.startswith("bb_sync.commands")]:
        del sys.modules[mod]
    result = cli(["--help"])
    assert result.exit_code == 0
    leaked = [m for m in sys.modules if m.startswith("bb_sync.commands")]
    assert leaked == [], f"根帮助不应导入命令模块，实际导入了：{leaked}"


def test_unknown_command_exits_two(cli) -> None:
    """未知命令 → 用法错误，退出码 2，消息走 stderr。"""
    result = cli(["definitely-not-a-command"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "No such command" in result.stderr or "Error" in result.stderr


def test_unknown_option_exits_two_without_traceback(cli) -> None:
    """未知选项 → 退出码 2 且**不打印堆栈**。

    这曾是一个真实回归：Typer ≥0.27 自带一份 ``_click``，其异常不继承官方
    ``click.ClickException``，导致兜底分支既打堆栈又返回 1。
    """
    result = cli(["--definitely-not-an-option"])
    assert result.exit_code == 2
    assert "Traceback" not in result.stderr
    assert "No such option" in result.stderr


def test_missing_required_argument_exits_two(cli) -> None:
    """``config set`` 缺参数 → 用法错误 2。"""
    result = cli(["config", "set", "root"])
    assert result.exit_code == 2
    assert "Traceback" not in result.stderr


# ---------------------------------------------------------------- 子命令结构


@pytest.mark.parametrize(
    "args",
    [
        ["run", "--help"],
        ["doctor", "--help"],
        ["config", "--help"],
        ["auth", "--help"],
        ["course", "--help"],
    ],
)
def test_subcommand_help_exits_zero(cli, args: list[str]) -> None:
    result = cli(args)
    assert result.exit_code == 0
    assert "Usage" in result.stdout or "用法" in result.stdout


def test_course_list_help_shows_list(cli) -> None:
    """``course list`` 必须是两级形态，而不是被折叠成 ``course``。

    回归点：app 里只有一条与 app 同名的命令时，Typer 会把它折叠成单条命令，
    于是 ``course list`` 里的 ``list`` 会被当成多余参数报 UsageError。
    """
    result = cli(["course", "--help"])
    assert result.exit_code == 0
    assert "list" in result.stdout


def test_config_subcommands_present(cli) -> None:
    result = cli(["config", "--help"])
    assert result.exit_code == 0
    for sub in ("path", "init", "show", "get", "set", "unset"):
        assert sub in result.stdout


def test_auth_subcommands_present(cli) -> None:
    result = cli(["auth", "--help"])
    assert result.exit_code == 0
    for sub in ("login", "logout", "status"):
        assert sub in result.stdout


# ---------------------------------------------------------------- config 全链路


def test_config_path_prints_path_to_stdout(cli) -> None:
    result = cli(["config", "path"])
    assert result.exit_code == 0
    assert result.stdout.strip().endswith("config.yaml")


def test_config_init_creates_file(cli, config_file: Path) -> None:
    result = cli(["config", "init"])
    assert result.exit_code == 0
    assert config_file.exists()
    # 幂等：再跑一次不报错
    again = cli(["config", "init"])
    assert again.exit_code == 0


def test_config_init_json_shape(cli) -> None:
    """``--json`` 必须放在子命令**之前**，stdout 是唯一一个 JSON 文档。"""
    result = cli(["--json", "config", "init"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["created"] is True
    assert payload["path"].endswith("config.yaml")


def test_config_set_then_get_roundtrip(cli) -> None:
    assert cli(["config", "init"]).exit_code == 0
    assert cli(["config", "set", "root", "D:/courses"]).exit_code == 0

    result = cli(["--json", "config", "get", "root"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == "D:/courses"


def test_config_set_preserves_comments(cli, config_file: Path) -> None:
    """行级写入必须保留用户手写的注释（config_store 的核心价值）。"""
    assert cli(["config", "init"]).exit_code == 0
    before = config_file.read_text(encoding="utf-8")
    assert "# bb-sync 配置" in before

    cli(["config", "set", "root", "D:/courses"])
    after = config_file.read_text(encoding="utf-8")
    assert "# bb-sync 配置" in after
    assert "root: D:/courses" in after


def test_config_set_nested_key(cli) -> None:
    assert cli(["config", "init"]).exit_code == 0
    result = cli(["config", "set", "course_dirs.CSC5010", "CSC5010_AI"])
    assert result.exit_code == 0

    got = cli(["--json", "config", "get", "course_dirs.CSC5010"])
    assert got.exit_code == 0
    assert json.loads(got.stdout) == "CSC5010_AI"


def test_config_unset_missing_key_exits_six(cli) -> None:
    """删除不存在的键 → NotFoundError，退出码 6（不是 4）。"""
    assert cli(["config", "init"]).exit_code == 0
    result = cli(["config", "unset", "no_such_key"])
    assert result.exit_code == 6
    assert "Traceback" not in result.stderr


def test_config_get_missing_key_exits_six(cli) -> None:
    assert cli(["config", "init"]).exit_code == 0
    result = cli(["config", "get", "no_such_key"])
    assert result.exit_code == 6


def test_config_show_without_file_exits_four(cli) -> None:
    """配置文件不存在 → ConfigError，退出码 4，并给出修复提示。"""
    result = cli(["config", "show"])
    assert result.exit_code == 4
    assert "config init" in result.stderr


def test_config_set_rejects_mapping_value(cli) -> None:
    """``set`` 不接受映射字面量，应引导用点号写法（退出码 4）。"""
    assert cli(["config", "init"]).exit_code == 0
    result = cli(["config", "set", "course_dirs", "{a: b}"])
    assert result.exit_code == 4
    assert "点号" in result.stderr


def test_config_show_json_is_valid_object(cli) -> None:
    assert cli(["config", "init"]).exit_code == 0
    result = cli(["--json", "config", "show"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["root"] == "~/courses"
    assert payload["max_depth"] == 3


def test_config_show_human_output_is_raw_yaml(cli) -> None:
    assert cli(["config", "init"]).exit_code == 0
    result = cli(["config", "show"])
    assert result.exit_code == 0
    assert "root:" in result.stdout


# ---------------------------------------------------------------- doctor / auth


def test_doctor_list_is_offline(cli) -> None:
    """``doctor --list`` 不碰网络，名字走 stdout。"""
    result = cli(["doctor", "--list"])
    assert result.exit_code == 0
    for name in ("python", "git", "node", "browser"):
        assert name in result.stdout


def test_doctor_list_json(cli) -> None:
    result = cli(["--json", "doctor", "--list"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "python" in payload["checks"]


def test_auth_status_json_when_unconfigured(cli) -> None:
    """未配置凭据时 ``auth status --json`` 仍是合法 JSON，不报错。"""
    result = cli(["--json", "auth", "status"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["configured"] is False


def test_auth_logout_when_empty(cli) -> None:
    """无凭据时 logout 不报错，返回 removed=False。"""
    result = cli(["--json", "auth", "logout"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["removed"] is False


# ---------------------------------------------------------------- 配置分层与错误码


def test_bad_config_file_value_exits_four(cli, config_file: Path) -> None:
    """配置项校验失败（max_depth 越界）：`--json` 会校验，退出码 4。

    注意（实测行为）：``config show`` 的**人读模式**只回显原文、不做校验，
    所以人读模式下退出码仍是 0；校验发生在需要把配置实例化的 ``--json`` 路径。
    """
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("max_depth: 999\n", encoding="utf-8")
    result = cli(["--json", "config", "show"])
    assert result.exit_code == 4
    assert "max_depth" in result.stderr

    # 人读模式不校验，只回显
    human = cli(["config", "show"])
    assert human.exit_code == 0
    assert "max_depth: 999" in human.stdout


def test_broken_yaml_exits_four(cli, config_file: Path) -> None:
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("root: [unclosed\n", encoding="utf-8")
    result = cli(["--json", "config", "show"])
    assert result.exit_code == 4
    assert "Traceback" not in result.stderr


def test_explicit_missing_config_exits_four(cli, tmp_path: Path) -> None:
    """``--config`` 指向不存在的文件 → 明确报错，退出码 4。"""
    missing = tmp_path / "nope.yaml"
    result = cli(["--config", str(missing), "config", "show"])
    assert result.exit_code == 4


def test_env_override_wins_over_file(cli, monkeypatch) -> None:
    """环境变量层 > 配置文件层。"""
    assert cli(["config", "init"]).exit_code == 0
    monkeypatch.setenv("BB_SYNC_ROOT", "E:/from-env")
    result = cli(["--json", "config", "get", "root"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == "E:/from-env"


def test_env_override_reaches_settings(cli, monkeypatch) -> None:
    """``BB_SYNC_*`` 环境变量层 > 配置文件层。"""
    monkeypatch.setenv("BB_SYNC_MAX_DEPTH", "5")
    result = cli(["--json", "config", "init"])
    assert result.exit_code == 0
    got = cli(["--json", "config", "get", "max_depth"])
    assert got.exit_code == 0
    assert json.loads(got.stdout) == 5


# ---------------------------------------------------------------- 退出码语义


def test_exit_code_mapping_is_consistent() -> None:
    """退出码语义表必须与文档一致（docs/scripting.md）。"""
    from bb_sync.core.errors import (
        AuthError,
        ConfigError,
        EnvironmentError_,
        ExitCode,
        NetworkError,
        NotFoundError,
        UsageError,
    )

    assert int(ExitCode.SUCCESS) == 0
    assert UsageError("x").exit_code == 2
    assert AuthError("x").exit_code == 3
    assert ConfigError("x").exit_code == 4
    assert NetworkError("x").exit_code == 5
    assert NotFoundError("x").exit_code == 6
    assert EnvironmentError_("x").exit_code == 7
    assert int(ExitCode.INTERRUPTED) == 8


def test_json_mode_stdout_is_pure_json(cli) -> None:
    """``--json`` 时 stdout 必须能被 json.loads 一次解析（不被诊断信息污染）。"""
    assert cli(["config", "init"]).exit_code == 0
    result = cli(["--json", "config", "show"])
    assert result.exit_code == 0
    json.loads(result.stdout)  # 不允许抛异常
    # 诊断（若有）只在 stderr
    assert "root" not in result.stderr
