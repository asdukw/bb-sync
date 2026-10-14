"""纯逻辑单元测试：配置分层、行级编辑、抓取辅助函数、下载推断、异常映射。

这些模块全部可离线导入（不碰网络/浏览器/钥匙串），因此测试直接调用函数即可，
不必经过 CLI 层。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bb_sync.core import config_store
from bb_sync.core.config import (
    DEFAULT_CONFIG_TEXT,
    Settings,
    config_target,
    ensure_config_file,
    load_settings,
    read_raw,
)
from bb_sync.core.errors import (
    AuthError,
    BbSyncError,
    ConfigError,
    EnvironmentError_,
    ExitCode,
    NetworkError,
    NotFoundError,
    UsageError,
    exit_code_for,
)

# ---------------------------------------------------------------- config_store：行级编辑


BASE_YAML = """\
# 顶层注释
root: ~/courses        # 行尾注释
include: all

# 课程映射
course_dirs:
  CSC5010: CSC5010_AI
  DDA5002: DDA5002_DS

max_depth: 3
"""


def _lines(text: str) -> list[str]:
    return text.splitlines()


def test_set_top_level_replaces_value_keeps_comments() -> None:
    lines = _lines(BASE_YAML)
    config_store.set_top_level(lines, "root", "D:/courses")
    out = "\n".join(lines)
    assert "root: D:/courses" in out
    assert "# 顶层注释" in out  # 独立注释行保留


def test_set_top_level_appends_when_missing() -> None:
    lines = _lines("include: all\n")
    config_store.set_top_level(lines, "max_depth", "5")
    assert "max_depth: 5" in "\n".join(lines)


def test_set_nested_updates_existing_child() -> None:
    lines = _lines(BASE_YAML)
    config_store.set_nested(lines, "course_dirs", "CSC5010", "Renamed")
    out = "\n".join(lines)
    assert "  CSC5010: Renamed" in out
    assert "DDA5002: DDA5002_DS" in out  # 兄弟键不受影响


def test_set_nested_adds_new_child_under_existing_block() -> None:
    lines = _lines(BASE_YAML)
    config_store.set_nested(lines, "course_dirs", "NEW1000", "NEW_1000")
    out = "\n".join(lines)
    assert "NEW1000: NEW_1000" in out
    # 新子键必须落在 course_dirs 块内，而不是 max_depth 之后
    block = out.split("course_dirs:")[1].split("max_depth:")[0]
    assert "NEW1000" in block


def test_set_nested_expands_flow_mapping() -> None:
    """``course_dirs: {}`` 应被展开为块式并插入子键。"""
    lines = _lines("course_dirs: {}\nroot: ~/courses\n")
    config_store.set_nested(lines, "course_dirs", "CSC5010", "X")
    out = "\n".join(lines)
    assert "course_dirs:" in out
    assert "CSC5010: X" in out


def test_set_nested_creates_block_when_parent_missing() -> None:
    lines = _lines("root: ~/courses\n")
    config_store.set_nested(lines, "course_dirs", "CSC5010", "X")
    out = "\n".join(lines)
    assert "course_dirs:" in out
    assert "  CSC5010: X" in out


def test_set_nested_rejects_scalar_parent() -> None:
    lines = _lines("root: ~/courses\n")
    with pytest.raises(ConfigError, match="不是映射"):
        config_store.set_nested(lines, "root", "child", "v")


def test_unset_top_level_scalar_removes_line() -> None:
    lines = _lines(BASE_YAML)
    config_store.unset_top_level(lines, "max_depth")
    assert "max_depth" not in "\n".join(lines)


def test_unset_top_level_mapping_resets_to_flow_empty() -> None:
    lines = _lines(BASE_YAML)
    config_store.unset_top_level(lines, "course_dirs")
    out = "\n".join(lines)
    assert "course_dirs: {}" in out
    assert "CSC5010" not in out


def test_unset_top_level_missing_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        config_store.unset_top_level(_lines("root: x\n"), "nope")


def test_unset_nested_removes_child() -> None:
    lines = _lines(BASE_YAML)
    config_store.unset_nested(lines, "course_dirs", "CSC5010")
    out = "\n".join(lines)
    assert "CSC5010" not in out
    assert "DDA5002" in out


def test_unset_nested_missing_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        config_store.unset_nested(_lines(BASE_YAML), "course_dirs", "NOPE")


def test_parse_value_yaml_scalars() -> None:
    assert config_store.parse_value("3") == 3
    assert config_store.parse_value("true") is True
    assert config_store.parse_value("all") == "all"
    assert config_store.parse_value("[a, b]") == ["a", "b"]


def test_parse_value_rejects_mapping() -> None:
    with pytest.raises(ConfigError, match="点号"):
        config_store.parse_value("{a: b}")


def test_apply_to_file_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    target.write_text(BASE_YAML, encoding="utf-8")
    config_store.apply_to_file(target, "root", "D:/x")
    assert "root: D:/x" in target.read_text(encoding="utf-8")
    config_store.apply_to_file(target, "course_dirs.CSC5010", None)
    assert "CSC5010" not in target.read_text(encoding="utf-8")


def test_apply_to_file_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="不存在"):
        config_store.apply_to_file(tmp_path / "nope.yaml", "root", "x")


# ---------------------------------------------------------------- Settings 校验与分层


def test_settings_defaults() -> None:
    s = Settings()
    assert s.root == "~/courses"
    assert s.include == "all"
    assert s.max_depth == 3
    assert s.announcements is True
    assert s.course_dirs == {}


def test_settings_normalizes_scalar_include() -> None:
    """``include: CSC5010`` 归一为列表。

    ``mode="before"`` 校验器刻意接受宽松输入（标量/None），所以这里绕过静态
    类型标注，直接用 model_validate 走真实的「原始 YAML 值」路径。
    """
    assert Settings.model_validate({"include": "CSC5010"}).include == ["CSC5010"]
    assert Settings(include="all").include == "all"
    assert Settings.model_validate({"include": None}).include == "all"


def test_settings_normalizes_empty_course_dirs() -> None:
    assert Settings.model_validate({"course_dirs": None}).course_dirs == {}


@pytest.mark.parametrize("depth", [0, 11, 999])
def test_settings_rejects_out_of_range_max_depth(depth: int) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(max_depth=depth)


def test_settings_ignores_unknown_keys() -> None:
    """``extra=ignore``：旧配置里已删除的键不应导致启动失败。"""
    s = Settings.model_validate({"root": "x", "removed_key": "y"})
    assert s.root == "x"


def test_settings_resolve_root_expands_user() -> None:
    s = Settings(root="~/courses")
    assert str(s.resolve_root()).endswith("courses")
    assert not str(s.resolve_root()).startswith("~")


def test_settings_resolve_root_relative_to_config_dir(tmp_path: Path) -> None:
    cfg = tmp_path / "sub" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    s = Settings(root="courses")
    assert s.resolve_root(cfg) == (tmp_path / "sub" / "courses").resolve()


def test_load_settings_defaults_when_file_missing(tmp_path: Path) -> None:
    s, path = load_settings(tmp_path / "nope.yaml")
    assert s.root == "~/courses"
    assert path == tmp_path / "nope.yaml"


def test_load_settings_file_layer(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("root: D:/from-file\nmax_depth: 4\n", encoding="utf-8")
    s, _ = load_settings(cfg)
    assert s.root == "D:/from-file"
    assert s.max_depth == 4


def test_load_settings_env_beats_file(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("root: D:/from-file\n", encoding="utf-8")
    monkeypatch.setenv("BB_SYNC_ROOT", "E:/from-env")
    s, _ = load_settings(cfg)
    assert s.root == "E:/from-env"


def test_load_settings_cli_beats_env(tmp_path: Path, monkeypatch) -> None:
    """四层优先级最高层：CLI 参数。"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("root: D:/from-file\n", encoding="utf-8")
    monkeypatch.setenv("BB_SYNC_ROOT", "E:/from-env")
    s, _ = load_settings(cfg, cli_overrides={"root": "F:/from-cli"})
    assert s.root == "F:/from-cli"


def test_load_settings_ignores_none_cli_overrides(tmp_path: Path) -> None:
    """Typer 的可选参数常为 None，必须忽略而不是覆盖成 None。"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("root: D:/from-file\n", encoding="utf-8")
    s, _ = load_settings(cfg, cli_overrides={"root": None, "max_depth": None})
    assert s.root == "D:/from-file"


def test_load_settings_invalid_value_raises_config_error(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("max_depth: 999\n", encoding="utf-8")
    with pytest.raises(ConfigError) as ei:
        load_settings(cfg)
    assert "max_depth" in ei.value.hint


def test_read_raw_rejects_non_mapping(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="映射"):
        read_raw(cfg)


def test_read_raw_broken_yaml(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("root: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="格式错误"):
        read_raw(cfg)


def test_ensure_config_file_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / ".bb-sync" / "config.yaml"
    assert ensure_config_file(target) is True
    assert target.read_text(encoding="utf-8") == DEFAULT_CONFIG_TEXT
    assert ensure_config_file(target) is False


def test_default_config_text_is_parseable() -> None:
    """默认配置必须能被自己解析成合法 Settings（防止模板写错）。"""
    import yaml

    data = yaml.safe_load(DEFAULT_CONFIG_TEXT)
    s = Settings.model_validate(data)
    assert s.max_depth == 3
    assert set(s.keywords) == {"assignments", "tutorials", "lectures"}


def test_config_target_prefers_explicit(tmp_path: Path) -> None:
    assert config_target(str(tmp_path / "x.yaml")) == tmp_path / "x.yaml"


def test_config_target_prefers_cwd(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("root: x\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert config_target() == cfg


# ---------------------------------------------------------------- 下载根目录


def test_ensure_root_directory_creates_nested_path(tmp_path: Path) -> None:
    from bb_sync.core.service import ensure_root_directory

    target = tmp_path / "nested" / "courses"
    assert ensure_root_directory(target) is True
    assert target.is_dir()
    assert ensure_root_directory(target) is False


def test_ensure_root_directory_reports_unavailable_drive(tmp_path: Path, monkeypatch) -> None:
    from bb_sync.core import service

    target = tmp_path / "courses"
    monkeypatch.setattr(service, "_drive_available", lambda root: False)

    with pytest.raises(ConfigError, match="磁盘不可用"):
        service.ensure_root_directory(target)
    assert not target.exists()


def test_macos_unmounted_volume_is_unavailable(monkeypatch) -> None:
    """macOS 的 /Volumes/<名称> 未挂载时，不应在 /Volumes 下误建普通目录。"""
    from bb_sync.core import service

    monkeypatch.setattr(service.sys, "platform", "darwin")
    root = Path("/Volumes/bb-sync-never-mounted/courses")

    assert service._drive_available(root) is False


# ---------------------------------------------------------------- 浏览器后端


def _launcher_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """把 launcher 的运行期文件指到临时目录，避免碰真实 profile / 状态文件。"""
    from bb_sync.browser import launcher

    monkeypatch.setattr(launcher, "PROFILE_ROOT", tmp_path / "profile")
    monkeypatch.setattr(launcher, "STATE_FILE", tmp_path / "session.json")
    monkeypatch.setattr(launcher, "BROWSER_LOG", tmp_path / "browser.log")
    return launcher


def _counting_clock():
    """每次调用前进 1 秒的假时钟，让等待循环迅速跑完。"""
    ticks = iter(range(1000))
    return lambda: next(ticks)


def test_browser_candidates_put_chrome_first(monkeypatch) -> None:
    """候选顺序里 Chrome 排在 Edge 前面（两个都装时用 Chrome）。"""
    from bb_sync.browser import launcher

    names = [name for name, _ in launcher._browser_candidates()]
    assert names.count("chrome") > 0 and names.count("edge") > 0
    assert names == sorted(names, key=lambda name: 0 if name == "chrome" else 1)


def test_detect_browser_picks_first_existing(monkeypatch, tmp_path: Path) -> None:
    """探测按候选顺序取第一个真实存在的可执行文件。"""
    from bb_sync.browser import launcher

    chrome = tmp_path / "chrome.exe"
    edge = tmp_path / "msedge.exe"
    edge.write_text("", encoding="utf-8")  # Chrome 缺席、Edge 在位
    monkeypatch.delenv("CHROME_EXECUTABLE_PATH", raising=False)
    monkeypatch.setattr(
        launcher, "_browser_candidates", lambda: (("chrome", str(chrome)), ("edge", str(edge)))
    )

    picked = launcher.detect_browser()
    assert picked is not None
    assert (picked.name, picked.path) == ("edge", edge)


def test_detect_browser_respects_env_override(monkeypatch, tmp_path: Path) -> None:
    """显式指定的浏览器优先，且按其文件名决定用哪个 profile 目录。"""
    from bb_sync.browser import launcher

    custom = tmp_path / "msedge.exe"
    custom.write_text("", encoding="utf-8")
    monkeypatch.setenv("CHROME_EXECUTABLE_PATH", str(custom))

    picked = launcher.detect_browser()
    assert picked is not None
    assert (picked.name, picked.path) == ("edge", custom)
    assert picked.profile_dir.name == "edge"


def test_detect_browser_rejects_missing_override(monkeypatch, tmp_path: Path) -> None:
    """路径写错时报错，而不是静默换成别的浏览器。"""
    from bb_sync.browser import launcher
    from bb_sync.core.errors import EnvironmentError_

    monkeypatch.setenv("CHROME_EXECUTABLE_PATH", str(tmp_path / "missing.exe"))
    with pytest.raises(EnvironmentError_):
        launcher.detect_browser()


def test_detect_browser_returns_none_without_candidates(monkeypatch) -> None:
    from bb_sync.browser import launcher

    monkeypatch.delenv("CHROME_EXECUTABLE_PATH", raising=False)
    monkeypatch.setattr(launcher, "_browser_candidates", lambda: ())
    assert launcher.detect_browser() is None


def test_launch_args_headless_and_headed(monkeypatch, tmp_path: Path) -> None:
    """无窗口模式带 --headless=new；有窗口模式不带；profile 按浏览器分目录。"""
    launcher = _launcher_paths(monkeypatch, tmp_path)
    browser = launcher.Browser("edge", tmp_path / "msedge.exe")

    headless = launcher._launch_args(browser, headed=False)
    headed = launcher._launch_args(browser, headed=True)

    assert headless[0] == str(tmp_path / "msedge.exe")
    assert "--headless=new" in headless
    assert "--headless=new" not in headed
    # 端口交给浏览器自己挑，避免固定端口被占用
    assert "--remote-debugging-port=0" in headless
    assert f"--user-data-dir={tmp_path / 'profile' / 'edge'}" in headless


def test_read_port_file(tmp_path: Path) -> None:
    """DevToolsActivePort：首行端口、次行 WebSocket 路径。"""
    from bb_sync.browser import launcher

    (tmp_path / launcher.PORT_FILE_NAME).write_text(
        "51234\n/devtools/browser/abc\n", encoding="utf-8"
    )
    assert launcher._read_port_file(tmp_path) == (51234, "/devtools/browser/abc")

    (tmp_path / launcher.PORT_FILE_NAME).write_text("", encoding="utf-8")
    assert launcher._read_port_file(tmp_path) is None
    assert launcher._read_port_file(tmp_path / "not-a-dir") is None


def test_current_session_ignores_stale_port_file(monkeypatch, tmp_path: Path) -> None:
    """端口文件还在、但实例已经没了：不能误判成「运行中」。"""
    launcher = _launcher_paths(monkeypatch, tmp_path)
    browser = launcher.Browser("chrome", tmp_path / "chrome.exe")
    profile = tmp_path / "profile" / "chrome"
    profile.mkdir(parents=True)
    (profile / launcher.PORT_FILE_NAME).write_text(
        "51234\n/devtools/browser/abc\n", encoding="utf-8"
    )
    monkeypatch.setattr(launcher, "detect_browser", lambda explicit=None: browser)
    monkeypatch.setattr(launcher, "_probe", lambda port, timeout=2.0: None)

    assert launcher.current_session() is None


def test_current_session_reads_mode_from_state(monkeypatch, tmp_path: Path) -> None:
    """端口文件给出端口，显示模式只能从状态文件读。"""
    launcher = _launcher_paths(monkeypatch, tmp_path)
    browser = launcher.Browser("chrome", tmp_path / "chrome.exe")
    profile = tmp_path / "profile" / "chrome"
    profile.mkdir(parents=True)
    (profile / launcher.PORT_FILE_NAME).write_text(
        "51234\n/devtools/browser/abc\n", encoding="utf-8"
    )
    monkeypatch.setattr(launcher, "detect_browser", lambda explicit=None: browser)
    monkeypatch.setattr(launcher, "_probe", lambda port, timeout=2.0: {"Browser": "Chrome/155"})

    launcher._write_state(launcher.Session(browser, 51234, "ws://x/", headed=True))
    session = launcher.current_session()
    assert session is not None
    assert session.headed is True
    assert session.mode_label == "有窗口模式"


def test_launch_waits_for_devtools_port(monkeypatch, tmp_path: Path) -> None:
    """_launch 以浏览器自己写的端口文件为准，确认可用后记录运行状态。"""
    launcher = _launcher_paths(monkeypatch, tmp_path)
    launched: list[list[str]] = []

    class FakeProc:
        pass

    monkeypatch.setattr(
        launcher.subprocess, "Popen", lambda args, **kwargs: launched.append(args) or FakeProc()
    )
    monkeypatch.setattr(launcher, "_read_port_file", lambda profile: (4321, "/devtools/browser/x"))
    monkeypatch.setattr(launcher, "_probe", lambda port, timeout=2.0: {"Browser": "Chrome/155"})

    browser = launcher.Browser("chrome", tmp_path / "chrome.exe")
    session = launcher._launch(launcher._SILENT, browser, headed=False)

    assert session.port == 4321
    assert session.ws_url == "ws://127.0.0.1:4321/devtools/browser/x"
    assert launched and "--headless=new" in launched[0]
    assert launcher._read_state()["port"] == 4321


def test_launch_times_out_with_actionable_hint(monkeypatch, tmp_path: Path) -> None:
    """起不来时给可操作的错误（退出码 7），并指向日志文件。"""
    launcher = _launcher_paths(monkeypatch, tmp_path)
    from bb_sync.core.errors import EnvironmentError_, ExitCode

    monkeypatch.setattr(launcher.subprocess, "Popen", lambda args, **kwargs: object())
    monkeypatch.setattr(launcher, "_read_port_file", lambda profile: None)
    monkeypatch.setattr(launcher.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(launcher.time, "monotonic", _counting_clock())

    browser = launcher.Browser("chrome", tmp_path / "chrome.exe")
    with pytest.raises(EnvironmentError_) as excinfo:
        launcher._launch(launcher._SILENT, browser, headed=False, wait=1)
    assert excinfo.value.exit_code == ExitCode.ENVIRONMENT_ERROR
    assert "browser.log" in excinfo.value.hint


def test_ensure_browser_reuses_running_instance(monkeypatch, tmp_path: Path) -> None:
    """参数一致时复用已在跑的实例，不再启动新进程。"""
    launcher = _launcher_paths(monkeypatch, tmp_path)
    browser = launcher.Browser("chrome", tmp_path / "chrome.exe")
    live = launcher.Session(browser, 1234, "ws://127.0.0.1:1234/x", headed=False)
    monkeypatch.setattr(launcher, "detect_browser", lambda explicit=None: browser)
    monkeypatch.setattr(launcher, "current_session", lambda: live)
    monkeypatch.setattr(launcher, "_launch", lambda *a, **k: pytest.fail("不该重新启动浏览器"))

    assert launcher.ensure_browser(launcher._SILENT, headed=False) is live


def test_ensure_browser_restarts_when_mode_changes(monkeypatch, tmp_path: Path) -> None:
    """无窗口 → 有窗口必须先关掉旧实例：同一个 profile 开不了第二个。"""
    launcher = _launcher_paths(monkeypatch, tmp_path)
    browser = launcher.Browser("chrome", tmp_path / "chrome.exe")
    live = launcher.Session(browser, 1234, "ws://127.0.0.1:1234/x", headed=False)
    fresh = launcher.Session(browser, 4321, "ws://127.0.0.1:4321/x", headed=True)
    stopped: list[int] = []
    monkeypatch.setattr(launcher, "detect_browser", lambda explicit=None: browser)
    monkeypatch.setattr(launcher, "current_session", lambda: live)
    monkeypatch.setattr(launcher, "stop_browser", lambda console=None: stopped.append(1) or True)
    monkeypatch.setattr(launcher, "_launch", lambda *a, **k: fresh)

    assert launcher.ensure_browser(launcher._SILENT, headed=True) is fresh
    assert stopped == [1]


def test_ensure_browser_without_browser_raises(monkeypatch, tmp_path: Path) -> None:
    launcher = _launcher_paths(monkeypatch, tmp_path)
    from bb_sync.core.errors import EnvironmentError_

    monkeypatch.setattr(launcher, "detect_browser", lambda explicit=None: None)
    with pytest.raises(EnvironmentError_):
        launcher.ensure_browser(launcher._SILENT)


def test_browser_session_closes_by_default(monkeypatch, tmp_path: Path) -> None:
    """默认跑完就关：命令结束不留后台浏览器。"""
    launcher = _launcher_paths(monkeypatch, tmp_path)
    session = launcher.Session(
        launcher.Browser("chrome", tmp_path / "chrome.exe"), 1, "ws://x/", headed=False
    )
    closed: list[int] = []
    monkeypatch.setattr(launcher, "ensure_browser", lambda *a, **k: session)
    monkeypatch.setattr(launcher, "stop_browser", lambda console=None: closed.append(1) or True)

    with launcher.browser_session(launcher._SILENT) as got:
        assert got is session
    assert closed == [1]


def test_browser_session_closes_even_when_command_fails(monkeypatch, tmp_path: Path) -> None:
    """命令中途报错也要收尾，不能把浏览器留在后台。"""
    launcher = _launcher_paths(monkeypatch, tmp_path)
    session = launcher.Session(
        launcher.Browser("chrome", tmp_path / "chrome.exe"), 1, "ws://x/", headed=False
    )
    closed: list[int] = []
    monkeypatch.setattr(launcher, "ensure_browser", lambda *a, **k: session)
    monkeypatch.setattr(launcher, "stop_browser", lambda console=None: closed.append(1) or True)

    with (
        pytest.raises(RuntimeError),
        launcher.browser_session(launcher._SILENT),
    ):
        raise RuntimeError("boom")
    assert closed == [1]


def test_browser_session_keep_open_skips_close(monkeypatch, tmp_path: Path) -> None:
    """``keep_open`` 时保留实例，交给 ``bb-sync browser stop`` 收场。"""
    launcher = _launcher_paths(monkeypatch, tmp_path)
    session = launcher.Session(
        launcher.Browser("chrome", tmp_path / "chrome.exe"), 1, "ws://x/", headed=False
    )
    monkeypatch.setattr(launcher, "ensure_browser", lambda *a, **k: session)
    monkeypatch.setattr(launcher, "stop_browser", lambda console=None: pytest.fail("不该关闭"))

    with launcher.browser_session(launcher._SILENT, keep_open=True) as got:
        assert got is session


def test_stop_browser_returns_false_when_idle(monkeypatch, tmp_path: Path) -> None:
    """没有实例在跑时是正常的空操作，不报错。"""
    launcher = _launcher_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, "current_session", lambda: None)

    assert launcher.stop_browser(launcher._SILENT) is False


def test_stop_browser_closes_over_cdp(monkeypatch, tmp_path: Path) -> None:
    """关闭走 CDP 的 Browser.close（让浏览器自己落盘），而不是按 PID 杀进程。"""
    launcher = _launcher_paths(monkeypatch, tmp_path)
    browser = launcher.Browser("chrome", tmp_path / "chrome.exe")
    session = launcher.Session(browser, 1234, "ws://127.0.0.1:1234/x", headed=False)
    monkeypatch.setattr(launcher, "current_session", lambda: session)
    monkeypatch.setattr(launcher.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(launcher, "_probe", lambda port, timeout=2.0: None)

    sent: list[str] = []

    class FakeCdp:
        def send(self, method: str) -> None:
            sent.append(method)

    class FakeBrowser:
        def new_browser_cdp_session(self) -> FakeCdp:
            return FakeCdp()

    class FakeChromium:
        def connect_over_cdp(self, url: str, timeout: int) -> FakeBrowser:
            assert url == session.ws_url
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

        def __enter__(self) -> FakePlaywright:
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakePlaywright())

    assert launcher.stop_browser(launcher._SILENT) is True
    assert sent == ["Browser.close"]


def test_legacy_steel_hint(monkeypatch, tmp_path: Path) -> None:
    """旧版 Steel 目录还在时提示清理；不在时不出声。"""
    from bb_sync import paths
    from bb_sync.core import doctor

    monkeypatch.setattr(paths, "BB_SYNC_HOME", tmp_path)
    assert doctor.legacy_steel_hint() == ""

    (tmp_path / ".steel").mkdir()
    assert ".steel" in doctor.legacy_steel_hint()


def test_doctor_checks_do_not_require_node() -> None:
    """Node / npm 只为旧的 Steel 后端存在，不该再出现在体检项里。"""
    from bb_sync.core import doctor

    names = [fn.__name__ for fn in doctor.CHECKS]
    assert "check_browser" in names
    assert "check_browser_backend" in names
    assert "check_node" not in names
    assert "check_npm" not in names


def test_doctor_check_browser_reports_detected(monkeypatch, tmp_path: Path) -> None:
    from bb_sync.browser import launcher
    from bb_sync.core import doctor

    monkeypatch.delenv("CHROME_EXECUTABLE_PATH", raising=False)
    monkeypatch.setattr(
        launcher,
        "detect_browser",
        lambda explicit=None: launcher.Browser("chrome", tmp_path / "chrome.exe"),
    )
    result = doctor.check_browser()
    assert result.ok is True
    assert "Chrome" in result.detail


def test_doctor_check_browser_reports_missing(monkeypatch) -> None:
    from bb_sync.browser import launcher
    from bb_sync.core import doctor

    monkeypatch.delenv("CHROME_EXECUTABLE_PATH", raising=False)
    monkeypatch.setattr(launcher, "detect_browser", lambda explicit=None: None)
    result = doctor.check_browser()
    assert result.ok is False
    assert result.required is True


def test_doctor_network_checks_blackboard_site() -> None:
    """网络体检盯的是 Blackboard 站点，而不是已经不再依赖的 github。"""
    import inspect

    from bb_sync.blackboard.models import BASE
    from bb_sync.core import doctor

    source = inspect.getsource(doctor.check_network)
    assert "BASE" in source
    assert "github" not in source
    assert BASE == "https://bb.cuhk.edu.cn"


# ---------------------------------------------------------------- 抓取辅助函数


def test_match_category_priority() -> None:
    from bb_sync.blackboard.scraper import match_category
    from bb_sync.core.config import DEFAULT_KEYWORDS

    # assignments 优先于 tutorials
    assert match_category("Homework and lab notes", DEFAULT_KEYWORDS) == "assignments"
    assert match_category("Tutorial slides", DEFAULT_KEYWORDS) == "tutorials"
    assert match_category("Lecture 01", DEFAULT_KEYWORDS) == "lectures"
    assert match_category("misc", DEFAULT_KEYWORDS) is None


def test_categorize_falls_back_to_lectures() -> None:
    from bb_sync.blackboard.scraper import categorize
    from bb_sync.core.config import DEFAULT_KEYWORDS

    assert categorize("随便什么", DEFAULT_KEYWORDS) == "lectures"


def test_match_category_is_case_insensitive() -> None:
    from bb_sync.blackboard.scraper import match_category
    from bb_sync.core.config import DEFAULT_KEYWORDS

    assert match_category("HOMEWORK 1", DEFAULT_KEYWORDS) == "assignments"


def test_make_slug_with_code() -> None:
    from bb_sync.blackboard.models import Course
    from bb_sync.blackboard.scraper import make_slug

    c = Course(bb_id="_1_1", title="CSC5010: Deep Learning and Their Applications_L01")
    slug = make_slug(c)
    assert slug.startswith("CSC5010_")
    assert len(slug) <= 40 + len("CSC5010_")
    assert " " not in slug


def test_make_slug_skips_stop_words() -> None:
    from bb_sync.blackboard.models import Course
    from bb_sync.blackboard.scraper import make_slug

    c = Course(bb_id="_1_1", title="DDA5002 The Foundations of Data Science")
    slug = make_slug(c)
    assert "THE" not in slug.upper()
    assert slug.startswith("DDA5002")


def test_make_slug_without_code_sanitizes_title() -> None:
    from bb_sync.blackboard.models import Course
    from bb_sync.blackboard.scraper import make_slug

    c = Course(bb_id="_1_1", title="随便 课程 / name")
    slug = make_slug(c)
    assert "/" not in slug
    assert slug  # 非空


def test_sanitize_filename_strips_illegal_chars() -> None:
    from bb_sync.blackboard.scraper import sanitize_filename

    assert sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"


def test_sanitize_filename_truncates_and_defaults() -> None:
    from bb_sync.blackboard.scraper import sanitize_filename

    assert len(sanitize_filename("x" * 500)) == 150
    assert sanitize_filename("   ") == "untitled"


def test_sanitize_filename_normalizes_unicode() -> None:
    from bb_sync.blackboard.scraper import sanitize_filename

    # 全角字符 NFKC 归一为半角
    assert sanitize_filename("ＡＢＣ") == "ABC"


# ---------------------------------------------------------------- Due / To Do


def test_parse_due_date_supports_blackboard_formats() -> None:
    from datetime import date

    from bb_sync.blackboard.scraper import parse_due_date

    assert parse_due_date(" - Due 10/21/26") == date(2026, 10, 21)
    assert parse_due_date("Due 1/2/2027") == date(2027, 1, 2)
    assert parse_due_date("No date") is None
    assert parse_due_date("Due 13/40/26") is None


def test_parse_due_entries_normalizes_and_deduplicates() -> None:
    from bb_sync.blackboard.models import Course
    from bb_sync.blackboard.scraper import parse_due_entries

    course = Course(bb_id="_18482_1", title="CSC5010: Artificial Intelligence")
    entries = [
        {"title": "  Homework   1  ", "due_text": " - Due 10/10/26 "},
        {"title": "Homework 1", "due_text": "- Due 10/10/2026"},
        {"title": "", "due_text": "Due 10/11/26"},
    ]

    items = parse_due_entries(entries, course)

    assert len(items) == 1
    assert items[0].title == "Homework 1"
    assert items[0].course_code == "CSC5010"
    assert items[0].due_date is not None
    assert items[0].due_date.isoformat() == "2026-10-10"


def test_scrape_due_items_extracts_only_leaf_rows() -> None:
    from bb_sync.blackboard.models import Course
    from bb_sync.blackboard.scraper import scrape_due_items
    from bb_sync.core.output import Console

    class FakePage:
        def goto(self, url: str, **kwargs):
            assert "id=_18482_1" in url
            return None

        def wait_for_selector(
            self, selector: str, timeout: int | None = None, state: str | None = None
        ) -> None:
            assert selector == "#pastDueView li, #dueView li"

        def wait_for_load_state(self, state: str | None = None, timeout: int | None = None) -> None:
            return None

        def wait_for_timeout(self, timeout: int) -> None:
            assert timeout <= 300

        def eval_on_selector_all(self, selector: str, script: str):
            assert selector == "#pastDueView li, #dueView li"
            assert "querySelector('.due')" in script
            return [{"title": "Homework 1", "due_text": "- Due 10/10/26"}]

    course = Course(bb_id="_18482_1", title="CSC5010: Artificial Intelligence")
    items = scrape_due_items(FakePage(), course, Console(quiet=True))  # type: ignore[arg-type]

    assert [item.title for item in items] == ["Homework 1"]


def test_parse_announcement_date_supports_common_formats() -> None:
    from datetime import date

    from bb_sync.blackboard.scraper import parse_announcement_date

    reference = date(2026, 9, 29)
    assert parse_announcement_date(
        "scheduled for Tuesday, October 13, 2026", reference=reference
    ) == date(2026, 10, 13)
    assert parse_announcement_date("moved to Oct.13", reference=reference) == date(2026, 10, 13)
    assert parse_announcement_date("on 13 November 2026", reference=reference) == date(2026, 11, 13)
    assert parse_announcement_date("2026年10月13日 9:00", reference=reference) == date(2026, 10, 13)
    assert parse_announcement_date("Due 10/13/26", reference=reference) == date(2026, 10, 13)
    assert parse_announcement_date("no date here", reference=reference) is None
    assert parse_announcement_date("Due 13/40/26", reference=reference) is None


def test_parse_announcement_due_items_keeps_only_dated_quiz() -> None:
    """只收录带近期日期的测验：资料/回顾类公告不能变成待办。"""
    from datetime import date

    from bb_sync.blackboard.models import Course
    from bb_sync.blackboard.scraper import parse_announcement_due_items

    course = Course(bb_id="_18482_1", title="MDS5020:Data Mining_L01")
    entries = [
        {
            "title": "In-Class Quiz-1 (Oct.13)",
            "posted_on": "Posted on: Tuesday, September 29, 2026 5:02:06 PM CST",
            "body_markdown": (
                "Dear Students,\n\n"
                "In-Class Quiz-1 is scheduled for **Tuesday, Octorber 13, 2026**.\n\n"
                "Please note that after the quiz ends at 9:20, return to the classroom."
            ),
        },
        {
            "title": "Week 4 Materials Uploaded",
            "posted_on": "Posted on: Tuesday, September 29, 2026 9:02:16 AM CST",
            "body_markdown": (
                "The submission of the Assignment 1 deadline to **11:59 PM on October 11**, "
                "and review the questions in **coding_quiz-1.zip on**Blackboard."
            ),
        },
        {
            "title": "Quiz 3 details",
            "posted_on": "Posted on: Monday, October 5, 2026 10:00:00 AM CST",
            "body_markdown": "The date will be announced later.",
        },
        {
            "title": "Quiz 1 Recap",
            "posted_on": "Posted on: Tuesday, October 20, 2026 10:00:00 AM CST",
            "body_markdown": "The quiz was held on October 13 and went well.",
        },
    ]

    items = parse_announcement_due_items(entries, course, today=date(2026, 10, 13))

    assert len(items) == 1
    assert items[0].kind == "quiz"
    assert items[0].title == "In-Class Quiz-1 (Oct.13)"
    assert items[0].course_code == "MDS5020"
    assert items[0].due_date is not None
    assert items[0].due_date.isoformat() == "2026-10-13"


def test_parse_announcement_due_items_drops_events_past_retention() -> None:
    """考完超过保留期的公告事件不再收录；最近几天内的仍保留，供回看。"""
    from datetime import date

    from bb_sync.blackboard.models import Course
    from bb_sync.blackboard.scraper import parse_announcement_due_items

    course = Course(bb_id="_1_1", title="MDS5020:Data Mining_L01")
    entries = [
        {
            "title": "In-Class Quiz-1 (Oct.13)",
            "posted_on": "Posted on: Tuesday, September 29, 2026 5:02:06 PM CST",
            "body_markdown": "In-Class Quiz-1 is scheduled for **Octorber 13, 2026**.",
        },
        {
            "title": "In-Class Quiz-0 (Oct.7)",
            "posted_on": "Posted on: Tuesday, September 22, 2026 5:02:06 PM CST",
            "body_markdown": "In-Class Quiz-0 is scheduled for **October 7, 2026**.",
        },
        {
            "title": "In-Class Quiz-Past (Oct.1)",
            "posted_on": "Posted on: Tuesday, September 15, 2026 5:02:06 PM CST",
            "body_markdown": "In-Class Quiz-Past is scheduled for **October 1, 2026**.",
        },
    ]

    items = parse_announcement_due_items(entries, course, today=date(2026, 10, 14))

    # 10/13 与 10/7（恰好 7 天）在保留期内，10/1 已超期。
    assert [item.title for item in items] == [
        "In-Class Quiz-1 (Oct.13)",
        "In-Class Quiz-0 (Oct.7)",
    ]


def test_parse_announcement_due_items_infers_year_across_term_boundary() -> None:
    from datetime import date

    from bb_sync.blackboard.models import Course
    from bb_sync.blackboard.scraper import parse_announcement_due_items

    course = Course(bb_id="_1_1", title="MDS5020:Data Mining_L01")
    entries = [
        {
            "title": "Quiz 2 Details",
            "posted_on": "Posted on: Monday, December 7, 2026 10:00:00 AM CST",
            "body_markdown": "It will be held on January 10.",
        }
    ]

    items = parse_announcement_due_items(entries, course, today=date(2026, 12, 7))

    assert [item.due_date.isoformat() for item in items if item.due_date] == ["2027-01-10"]


def test_parse_announcement_due_items_recognizes_exams() -> None:
    """exam 系关键词与 midterm / final + test 组合算考试，日常措辞不算。"""
    from datetime import date

    from bb_sync.blackboard.models import Course
    from bb_sync.blackboard.scraper import parse_announcement_due_items

    course = Course(bb_id="_1_1", title="CSC5010: Artificial Intelligence")
    entries = [
        {
            "title": "Final Exam Schedule",
            "posted_on": "Posted on: Monday, November 2, 2026 10:00:00 AM CST",
            "body_markdown": "The final exam will be held on **December 15, 2026** in LT-1.",
        },
        {
            "title": "Midterm Test",
            "posted_on": "Posted on: Monday, October 5, 2026 10:00:00 AM CST",
            "body_markdown": "Midterm Test on November 3.",
        },
        {
            "title": "期末考试安排",
            "posted_on": "Posted on: Monday, November 2, 2026 10:00:00 AM CST",
            "body_markdown": "考试时间为 2026年12月20日 9:00。",
        },
        {
            "title": "Mid-term Examination",
            "posted_on": "Posted on: Monday, November 2, 2026 10:00:00 AM CST",
            "body_markdown": "The examination takes place on December 20, 2026.",
        },
        {
            "title": "Final project report",
            "posted_on": "Posted on: Monday, November 2, 2026 10:00:00 AM CST",
            "body_markdown": "Submit the final report by December 1, 2026.",
        },
        {
            "title": "Lab 3 reminder",
            "posted_on": "Posted on: Monday, November 2, 2026 10:00:00 AM CST",
            "body_markdown": "Please test your code on the sample data before Friday.",
        },
        {
            "title": "Week 6 slides",
            "posted_on": "Posted on: Monday, November 2, 2026 10:00:00 AM CST",
            "body_markdown": "For example, the dataset posted on December 8, 2026 is optional.",
        },
    ]

    items = parse_announcement_due_items(entries, course, today=date(2026, 11, 2))

    assert [(item.kind, item.title) for item in items] == [
        ("exam", "Final Exam Schedule"),
        ("exam", "Midterm Test"),
        ("exam", "期末考试安排"),
        ("exam", "Mid-term Examination"),
    ]
    assert [item.due_date.isoformat() for item in items if item.due_date] == [
        "2026-12-15",
        "2026-11-03",
        "2026-12-20",
        "2026-12-20",
    ]


def test_parse_announcement_due_items_prefers_exam_over_quiz() -> None:
    """一条公告同时提到考试和测验时归为考试：覆盖测验内容的期末考不该被当成测验。"""
    from datetime import date

    from bb_sync.blackboard.models import Course
    from bb_sync.blackboard.scraper import parse_announcement_due_items

    course = Course(bb_id="_1_1", title="MDS5020:Data Mining_L01")
    entries = [
        {
            "title": "Final Exam and Quiz 4",
            "posted_on": "Posted on: Monday, November 2, 2026 10:00:00 AM CST",
            "body_markdown": "The Final Exam covers Quiz 1-3 and is set for December 15, 2026.",
        }
    ]

    items = parse_announcement_due_items(entries, course, today=date(2026, 11, 2))

    assert [
        (item.kind, item.due_date.isoformat() if item.due_date else None) for item in items
    ] == [("exam", "2026-12-15")]


def test_scrape_announcements_keeps_distinct_titles_and_bodies(tmp_path: Path) -> None:
    from bb_sync.blackboard.models import Course
    from bb_sync.blackboard.scraper import scrape_announcements
    from bb_sync.core.output import Console

    class FakePage:
        def goto(self, url: str, **kwargs):
            assert "course_id=_18443_1" in url
            return None

        def wait_for_selector(
            self, selector: str, timeout: int | None = None, state: str | None = None
        ) -> None:
            assert selector == "#announcementList > li"

        def wait_for_load_state(self, state: str | None = None, timeout: int | None = None) -> None:
            return None

        def wait_for_timeout(self, timeout: int) -> None:
            assert timeout <= 300

        def eval_on_selector_all(self, selector: str, script: str):
            assert selector == "#announcementList > li"
            assert "h3.item" in script
            assert ".vtbegenerated" in script
            assert "body_markdown" in script
            assert "toMarkdown" in script
            return [
                {
                    "title": "DDA5002 Tutorial 1 Recording",
                    "posted_on": "Posted on: Tuesday, September 15, 2026",
                    "body_markdown": (
                        "The recording is now available.\\n\\n"
                        "[Recording](https://example.com/recording)\\n\\n"
                        "![公告图片](https://example.com/qr.png)"
                    ),
                    "posted_by": "Posted by: Zhiqi\\nPosted to: DDA5002",
                },
                {
                    "title": "Assignment 1 Released",
                    "posted_on": "Posted on: Monday, September 14, 2026",
                    "body_markdown": "Homework assignment 1 is now available.",
                    "posted_by": "Posted by: Junchi\\nPosted to: DDA5002",
                },
            ]

    course = Course(bb_id="_18443_1", title="DDA5002:Optimization_L02")
    course_dir = tmp_path / "DDA5002_Optimization"
    count = scrape_announcements(
        FakePage(),  # type: ignore[arg-type]
        course,
        course_dir,
        False,
        Console(quiet=True),
    )

    markdown = (course_dir / "announcements.md").read_text(encoding="utf-8")
    assert count == 2
    assert "## DDA5002 Tutorial 1 Recording" in markdown
    assert "## Assignment 1 Released" in markdown
    assert "The recording is now available." in markdown
    assert "Homework assignment 1 is now available." in markdown
    assert "[Recording](https://example.com/recording)" in markdown
    assert "![公告图片](https://example.com/qr.png)" in markdown
    assert markdown.count("Posted by:") == 2
    assert "(无标题)" not in markdown


def test_build_due_markdown_prioritizes_and_sorts() -> None:
    from datetime import date, datetime

    from bb_sync.blackboard.models import Course, DueItem
    from bb_sync.core.due import build_due_markdown

    courses = [
        Course(bb_id="_1_1", title="CSC5010: Artificial Intelligence"),
        Course(bb_id="_2_1", title="MDS5122: Deep Learning"),
    ]
    items = [
        DueItem(
            "_2_1", "MDS5122", courses[1].title, "Assignment_1", date(2026, 10, 21), "Due 10/21/26"
        ),
        DueItem(
            "_1_1", "CSC5010", courses[0].title, "Homework 2", date(2026, 10, 10), "Due 10/10/26"
        ),
        DueItem(
            "_1_1", "CSC5010", courses[0].title, "Homework 1", date(2026, 10, 10), "Due 10/10/26"
        ),
        DueItem("_1_1", "CSC5010", courses[0].title, "Overdue", date(2026, 9, 27), "Due 09/27/26"),
        DueItem(
            "_1_1", "CSC5010", courses[0].title, "Today task", date(2026, 9, 28), "Due 09/28/26"
        ),
        DueItem("_1_1", "CSC5010", courses[0].title, "Unknown", None, ""),
    ]

    markdown = build_due_markdown(
        courses,
        items,
        generated_at=datetime(2026, 9, 28, 8, 30),
        today=date(2026, 9, 28),
    )

    assert markdown.index("## 已逾期（1）") < markdown.index("## 今天（1）")
    assert markdown.index("## 今天（1）") < markdown.index("## 之后（3）")
    assert markdown.index("## 之后（3）") < markdown.index("## 日期未知（1）")
    assert "**2026-09-27**（逾期 1 天）" in markdown
    assert "**2026-09-28**（今天）" in markdown
    assert markdown.index("Homework 1") < markdown.index("Homework 2")
    assert "Assignment\\_1" in markdown
    assert "[CSC5010](https://bb.cuhk.edu.cn/webapps/blackboard/execute/launcher" in markdown


def test_build_due_markdown_marks_announcement_quiz_and_exam() -> None:
    from datetime import date, datetime

    from bb_sync.blackboard.models import Course, DueItem
    from bb_sync.core.due import build_due_markdown

    courses = [Course(bb_id="_1_1", title="MDS5020:Data Mining_L01")]
    items = [
        DueItem(
            "_1_1",
            "MDS5020",
            courses[0].title,
            "In-Class Quiz-1 (Oct.13)",
            date(2026, 10, 13),
            kind="quiz",
        ),
        DueItem(
            "_1_1",
            "MDS5020",
            courses[0].title,
            "Final Exam",
            date(2026, 12, 15),
            kind="exam",
        ),
    ]

    markdown = build_due_markdown(
        courses,
        items,
        generated_at=datetime(2026, 10, 2, 8, 30),
        today=date(2026, 10, 2),
    )

    assert "其中公告里的测验 1 项、考试 1 项已一并作为待办列出。" in markdown
    assert "【测验】In-Class Quiz-1 (Oct.13)" in markdown
    assert "【考试】Final Exam" in markdown


def test_build_due_markdown_separates_past_events() -> None:
    """考完的公告事件单独成区：既不占「已逾期」，也不计进待办汇总。"""
    from datetime import date, datetime

    from bb_sync.blackboard.models import Course, DueItem
    from bb_sync.core.due import build_due_markdown

    courses = [Course(bb_id="_1_1", title="MDS5020:Data Mining_L01")]
    items = [
        DueItem(
            "_1_1",
            "MDS5020",
            courses[0].title,
            "In-Class Quiz-1 (Oct.13)",
            date(2026, 10, 13),
            kind="quiz",
        ),
        DueItem(
            "_1_1",
            "MDS5020",
            courses[0].title,
            "Assignment 1",
            date(2026, 10, 10),
            "Due 10/10/26",
        ),
        DueItem(
            "_1_1",
            "MDS5020",
            courses[0].title,
            "Midterm Exam",
            date(2026, 11, 17),
            kind="exam",
        ),
    ]

    markdown = build_due_markdown(
        courses,
        items,
        generated_at=datetime(2026, 10, 14, 21, 30),
        today=date(2026, 10, 14),
    )

    assert "## 已逾期（1）" in markdown
    assert "**2026-10-10**（逾期 4 天）" in markdown
    assert "## 已过期事件（1）" in markdown
    assert "**2026-10-13**（已过 1 天）" in markdown
    # 过期事件排在待办分组之后，不挤占优先级
    assert markdown.index("## 之后（1）") < markdown.index("## 已过期事件（1）")
    # 汇总只把还能行动的考试算进待办
    assert "其中公告里的考试 1 项已一并作为待办列出。" in markdown
    assert "另有 1 项公告里的测验/考试日期已过，列在最后仅供回看。" in markdown


def test_due_item_as_dict_includes_kind() -> None:
    from datetime import date

    from bb_sync.blackboard.models import DueItem

    item = DueItem(
        "_1_1",
        "MDS5020",
        "MDS5020:Data Mining_L01",
        "In-Class Quiz-1",
        date(2026, 10, 13),
        kind="quiz",
    )

    assert item.as_dict()["kind"] == "quiz"

    exam = DueItem(
        "_1_1",
        "MDS5020",
        "MDS5020:Data Mining_L01",
        "Final Exam",
        date(2026, 12, 15),
        kind="exam",
    )

    assert exam.as_dict()["kind"] == "exam"


def test_build_due_markdown_empty_writes_congratulations() -> None:
    from datetime import date, datetime

    from bb_sync.blackboard.models import Course
    from bb_sync.core.due import build_due_markdown

    markdown = build_due_markdown(
        [Course(bb_id="_1_1", title="CSC5010")],
        [],
        generated_at=datetime(2026, 9, 28, 8, 30),
        today=date(2026, 9, 28),
    )

    assert "## Congratulations! 🎉" in markdown
    assert "当前没有待办事项" in markdown


def test_build_due_markdown_partial_failure_does_not_congratulate() -> None:
    from datetime import date, datetime

    from bb_sync.blackboard.models import Course
    from bb_sync.core.due import build_due_markdown

    markdown = build_due_markdown(
        [Course(bb_id="_1_1", title="CSC5010")],
        [],
        failed=1,
        generated_at=datetime(2026, 9, 28, 8, 30),
        today=date(2026, 9, 28),
    )

    assert "Congratulations" not in markdown
    assert "结果可能不完整" in markdown
    assert "1 门课程抓取失败" in markdown


@pytest.mark.parametrize("dry_run", [False, True])
def test_run_sync_integrates_due_markdown(monkeypatch, tmp_path: Path, dry_run: bool) -> None:
    from bb_sync.blackboard.models import Course
    from bb_sync.core import service
    from bb_sync.core.config import Settings
    from bb_sync.core.output import Console

    class Dummy:
        browser = None
        request = None

        def close(self) -> None:
            pass

    class FakePlaywrightContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    course = Course(bb_id="_1_1", title="CSC5010: Artificial Intelligence")
    monkeypatch.setattr(service.launcher, "ensure_browser", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        service.launcher, "connect", lambda *args, **kwargs: (Dummy(), Dummy(), object())
    )
    monkeypatch.setattr(service, "sync_playwright", lambda: FakePlaywrightContext())
    monkeypatch.setattr(service, "ensure_login", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "sync_course", lambda *args, **kwargs: None)
    monkeypatch.setattr(service.scraper, "scrape_courses", lambda page, include, console: [course])
    monkeypatch.setattr(service.scraper, "scrape_due_items", lambda page, course, console: [])
    monkeypatch.setattr(service.scraper, "scrape_announcement_entries", lambda page, course: [])

    options = service.SyncOptions(
        settings=Settings(root=str(tmp_path), announcements=False),
        config_path=tmp_path / "config.yaml",
        root=tmp_path,
        console=Console(quiet=True),
        dry_run=dry_run,
    )
    service.run_sync(options)

    due_file = tmp_path / "due.md"
    assert due_file.exists() is (not dry_run)
    if not dry_run:
        assert "## Congratulations! 🎉" in due_file.read_text(encoding="utf-8")


def test_scan_due_items_merges_quiz_and_tracks_failures(monkeypatch) -> None:
    from datetime import date

    from bb_sync.blackboard.models import Course, DueItem
    from bb_sync.core import service
    from bb_sync.core.output import Console

    course_a = Course(bb_id="_1_1", title="CSC5010: Artificial Intelligence")
    course_b = Course(bb_id="_2_1", title="MDS5020:Data Mining_L01")
    course_c = Course(bb_id="_3_1", title="DDA5002:Optimization_L02")
    homework = DueItem("_1_1", "CSC5010", course_a.title, "Homework 1", date(2026, 10, 10))
    quiz = DueItem(
        "_2_1",
        "MDS5020",
        course_b.title,
        "In-Class Quiz-1 (Oct.13)",
        date(2026, 10, 13),
        kind="quiz",
    )

    def fake_due(page, course, console):
        if course is not course_a:
            raise RuntimeError("主页超时")
        return [homework]

    def fake_entries(page, course):
        if course is course_c:
            raise RuntimeError("公告超时")
        return [{"quiz": "entry"}] if course is course_b else []

    monkeypatch.setattr(service.scraper, "scrape_due_items", fake_due)
    monkeypatch.setattr(service.scraper, "scrape_announcement_entries", fake_entries)
    monkeypatch.setattr(
        service.scraper,
        "parse_announcement_due_items",
        lambda entries, course: [quiz] if course is course_b else [],
    )

    scan = service._scan_due_items(None, [course_a, course_b, course_c], Console(quiet=True))

    assert [item.title for item in scan.items] == ["Homework 1", "In-Class Quiz-1 (Oct.13)"]
    assert scan.todo_failed == 2
    assert scan.announcement_failed == 1
    assert scan.both_failed == 1
    assert scan.incomplete == 2


def test_print_due_details_one_item_per_line() -> None:
    from datetime import date
    from typing import cast

    from bb_sync.blackboard.models import DueItem
    from bb_sync.core import service
    from bb_sync.core.output import Console

    class FakeConsole:
        def __init__(self) -> None:
            self.messages: list[str] = []
            self.rules: list[str] = []

        def log(self, message: str) -> None:
            self.messages.append(message)

        def rule(self, title: str = "") -> None:
            self.rules.append(title)

    fake = FakeConsole()
    console = cast(Console, fake)
    service._print_due_details(
        console,
        [
            DueItem("_1_1", "CSC5010", "AI", "Homework 1", date(2026, 10, 10)),
            DueItem("_2_1", "MDS5122", "DL", "Reading", None),
            DueItem("_3_1", "MDS5020", "DM", "Quiz 1", date(2026, 10, 13), kind="quiz"),
            DueItem("_4_1", "CSC5010", "AI", "Final Exam", date(2026, 12, 15), kind="exam"),
        ],
        today=date(2026, 10, 14),
    )

    # 只有已经过去的条目带状态：逾期的是「还没交」，考完的是「已经考过」。
    assert fake.rules == ["待办明细"]
    assert fake.messages == [
        "[due] 2026-10-10（逾期 4 天） | CSC5010 | Homework 1",
        "[due] 日期未知 | MDS5122 | Reading",
        "[due] 2026-10-13（已过 1 天） | MDS5020 | 【测验】Quiz 1",
        "[due] 2026-12-15 | CSC5010 | 【考试】Final Exam",
    ]


def test_print_due_details_empty_writes_congratulations() -> None:
    from typing import cast

    from bb_sync.core import service
    from bb_sync.core.output import Console

    class FakeConsole:
        def __init__(self) -> None:
            self.messages: list[str] = []
            self.rules: list[str] = []

        def log(self, message: str) -> None:
            self.messages.append(message)

        def rule(self, title: str = "") -> None:
            self.rules.append(title)

    fake = FakeConsole()
    console = cast(Console, fake)
    service._print_due_details(console, [])

    # 没有待办时不打印空的明细标题
    assert fake.rules == []
    assert fake.messages == ["[due] Congratulations! 没有待办。"]


# ---------------------------------------------------------------- 下载推断


def test_sniff_ext_pdf() -> None:
    from bb_sync.blackboard.downloader import sniff_ext

    assert sniff_ext(b"%PDF-1.7 ...") == ".pdf"


def test_sniff_ext_zip_and_office() -> None:
    from bb_sync.blackboard.downloader import sniff_ext

    assert sniff_ext(b"PK\x03\x04rest") == ".zip"
    assert sniff_ext(b"\xd0\xcf\x11\xe0\xa1\xb1") == ".doc"


def test_sniff_ext_notebook_vs_json() -> None:
    from bb_sync.blackboard.downloader import sniff_ext

    assert sniff_ext(b'{"cells": [], "nbformat": 4}') == ".ipynb"
    assert sniff_ext(b'{"key": "value"}') == ".json"


def test_sniff_ext_gzip_and_unknown() -> None:
    from bb_sync.blackboard.downloader import sniff_ext

    assert sniff_ext(b"\x1f\x8b\x08") == ".gz"
    assert sniff_ext(b"\x00\x01\x02\x03") is None


def test_hw_num_extracts_number() -> None:
    from bb_sync.blackboard.downloader import HW_NUM

    m = HW_NUM.search("Homework 3.pdf")
    assert m is not None and m.group(1) == "3"
    m = HW_NUM.search("hw12 solution")
    assert m is not None and m.group(1) == "12"
    assert HW_NUM.search("lecture 1") is None


def test_build_index_maps_names_and_sizes(tmp_path: Path) -> None:
    from bb_sync.blackboard.downloader import SIZE_DEDUP_THRESHOLD, build_index

    (tmp_path / "a.pdf").write_bytes(b"x" * 10)
    big = tmp_path / "big.bin"
    big.write_bytes(b"y" * (SIZE_DEDUP_THRESHOLD + 1))

    index = build_index(tmp_path)
    assert "a.pdf" in index
    assert f"__size__{SIZE_DEDUP_THRESHOLD + 1}.bin" in index
    # 小文件不进体积索引
    assert "__size__10.pdf" not in index


def test_build_index_missing_dir_is_empty(tmp_path: Path) -> None:
    from bb_sync.blackboard.downloader import build_index

    assert build_index(tmp_path / "nope") == {}


# ---------------------------------------------------------------- creds


def test_mask_keeps_tail() -> None:
    from bb_sync.creds import mask

    assert mask("1234567890", tail=4) == "******7890"
    assert mask("abcd", tail=4) == "****"
    assert mask("") == ""


def test_credentials_ok_requires_both_fields() -> None:
    from bb_sync.creds import Credentials

    assert Credentials("id", "pw", "keyring").ok is True
    assert Credentials("id", "", "keyring").ok is False
    assert Credentials("", "pw", "keyring").ok is False


def test_save_load_delete_credentials_roundtrip() -> None:
    """配合 conftest 的内存钥匙串，验证存取删全链路。"""
    from bb_sync.creds import delete_credentials, load_credentials, save_credentials

    save_credentials("2023001", "secret")
    creds = load_credentials()
    assert (creds.student_id, creds.password, creds.source) == ("2023001", "secret", "keyring")
    assert creds.ok

    assert delete_credentials() is True
    assert load_credentials().ok is False
    assert delete_credentials() is False


def test_load_credentials_handles_corrupt_blob(monkeypatch) -> None:

    from bb_sync.creds import load_credentials

    monkeypatch.setattr("keyring.get_password", lambda s, a: "not-json")
    assert load_credentials().ok is False


def test_load_credentials_handles_keyring_error(monkeypatch) -> None:
    import keyring.errors

    from bb_sync.creds import load_credentials

    def boom(service: str, account: str):
        raise keyring.errors.KeyringError("no backend")

    monkeypatch.setattr("keyring.get_password", boom)
    assert load_credentials().ok is False


# ---------------------------------------------------------------- 版本更新检查


def test_update_version_comparison() -> None:
    from bb_sync.core.update import is_outdated

    assert is_outdated("1.0.2", "1.1.0") is True
    assert is_outdated("v1.0.2", "1.0.2") is False
    assert is_outdated("1.0", "1.0.0") is False
    assert is_outdated("2.0.0", "1.9.9") is False
    assert is_outdated("not-a-version", "1.0.0") is False


def test_fetch_latest_release_parses_github_payload(monkeypatch) -> None:
    import io
    import json

    from bb_sync.core import update

    def fake_urlopen(req, timeout: float) -> io.BytesIO:
        assert req.full_url == update.RELEASES_API
        assert timeout == 1.5
        return io.BytesIO(
            json.dumps({"tag_name": "v2.0.0", "html_url": "https://example.test/v2.0.0"}).encode(
                "utf-8"
            )
        )

    monkeypatch.setattr(update.request, "urlopen", fake_urlopen)

    assert update.fetch_latest_release(timeout=1.5) == update.ReleaseInfo(
        version="2.0.0",
        tag="v2.0.0",
        url="https://example.test/v2.0.0",
    )


def test_update_check_uses_fresh_cache(monkeypatch) -> None:
    from bb_sync.core import update

    monkeypatch.delenv(update.DISABLE_ENV, raising=False)
    calls: list[float] = []

    def fake_fetch(timeout: float) -> update.ReleaseInfo:
        calls.append(timeout)
        return update.ReleaseInfo("1.1.0", "v1.1.0", "https://example.test/v1.1.0")

    monkeypatch.setattr(update, "fetch_latest_release", fake_fetch)

    first = update.check_for_update(current="1.0.0", now=1000.0)
    second = update.check_for_update(current="1.0.0", now=1001.0)

    assert first is not None and first.latest == "1.1.0"
    assert second is not None and second.latest == "1.1.0"
    assert len(calls) == 1, "缓存有效期内不应重复访问 GitHub"


def test_update_check_caches_failure(monkeypatch) -> None:
    from bb_sync.core import update

    monkeypatch.delenv(update.DISABLE_ENV, raising=False)
    calls: list[float] = []

    def fake_fetch(timeout: float) -> None:
        calls.append(timeout)
        return None

    monkeypatch.setattr(update, "fetch_latest_release", fake_fetch)

    assert update.check_for_update(current="1.0.0", now=2000.0) is None
    assert update.check_for_update(current="1.0.0", now=2001.0) is None
    assert len(calls) == 1, "检查失败也应缓存，避免离线时反复等待超时"


def test_update_check_can_use_stale_cache_when_offline(monkeypatch) -> None:
    from bb_sync.core import update

    monkeypatch.delenv(update.DISABLE_ENV, raising=False)
    calls: list[float] = []

    def fake_fetch(timeout: float) -> update.ReleaseInfo | None:
        calls.append(timeout)
        if len(calls) == 1:
            return update.ReleaseInfo("1.1.0", "v1.1.0", "https://example.test/v1.1.0")
        return None

    monkeypatch.setattr(update, "fetch_latest_release", fake_fetch)

    assert update.check_for_update(current="1.0.0", now=3000.0) is not None
    stale = update.check_for_update(
        current="1.0.0",
        now=3000.0 + update.CACHE_TTL_SECONDS + 1,
    )
    assert stale is not None and stale.latest == "1.1.0"
    assert len(calls) == 2


def test_update_check_respects_disable_env(monkeypatch) -> None:
    from bb_sync.core import update

    monkeypatch.setenv(update.DISABLE_ENV, "1")
    monkeypatch.setattr(
        update,
        "fetch_latest_release",
        lambda timeout: pytest.fail("关闭更新检查后不应访问网络"),
    )

    assert update.check_for_update(current="1.0.0", now=4000.0) is None


# ---------------------------------------------------------------- 异常与退出码


def test_bb_sync_error_carries_message_and_hint() -> None:
    err = ConfigError("出错了", "试试这样")
    assert err.message == "出错了"
    assert err.hint == "试试这样"
    assert str(err) == "出错了"


def test_exit_code_for_maps_known_types() -> None:
    assert exit_code_for(UsageError("")) == ExitCode.USAGE_ERROR
    assert exit_code_for(AuthError("")) == ExitCode.AUTH_ERROR
    assert exit_code_for(ConfigError("")) == ExitCode.CONFIG_ERROR
    assert exit_code_for(NetworkError("")) == ExitCode.NETWORK_ERROR
    assert exit_code_for(NotFoundError("")) == ExitCode.NOT_FOUND
    assert exit_code_for(EnvironmentError_("")) == ExitCode.ENVIRONMENT_ERROR


def test_exit_code_for_generic_and_interrupt() -> None:
    assert exit_code_for(ValueError("x")) == ExitCode.GENERAL_ERROR
    assert exit_code_for(KeyboardInterrupt()) == ExitCode.INTERRUPTED


def test_all_errors_share_base_and_unique_codes() -> None:
    from bb_sync.core.errors import __all__ as _exported  # noqa: F401

    classes = [UsageError, ConfigError, AuthError, NetworkError, NotFoundError, EnvironmentError_]
    for cls in classes:
        assert issubclass(cls, BbSyncError)
    codes = [int(c("").exit_code) for c in classes]
    assert len(set(codes)) == len(codes), "各异常类型的退出码必须互不相同"


# ---------------------------------------------------------------- 下载（浏览器原生）


class _FakeAPIResponse:
    """Playwright ``APIResponse`` 替身：只需 headers / status / body。"""

    def __init__(
        self, *, body: bytes = b"", headers: dict | None = None, status: int = 200
    ) -> None:
        self._body = body
        self.headers = headers or {}
        self.status = status

    def body(self) -> bytes:
        return self._body


class _FakeRequestContext:
    """Playwright ``APIRequestContext`` 替身：分别配置 HEAD 与 GET 的行为。"""

    def __init__(self, *, head=None, get=None) -> None:
        self._head = head
        self._get = get
        self.calls: list[tuple] = []

    def fetch(self, url: str, method: str = "GET", timeout=None, **kwargs):
        self.calls.append(("fetch", method))
        if isinstance(self._head, Exception):
            raise self._head
        if self._head is None:
            raise RuntimeError("HEAD 不可用")
        return self._head

    def get(self, url: str, timeout=None, headers=None, **kwargs):
        self.calls.append(("get", headers))
        if isinstance(self._get, Exception):
            raise self._get
        if self._get is None:
            raise RuntimeError("GET 不可用")
        return self._get


class _FakeDownload:
    """Playwright ``Download`` 替身：save_as 直接写字节。"""

    def __init__(self, body: bytes, *, failure: str | None = None) -> None:
        self._body = body
        self._failure = failure

    def save_as(self, path: str) -> None:
        Path(path).write_bytes(self._body)

    def failure(self) -> str | None:
        return self._failure


class _FakeDownloadInfo:
    def __init__(self, download) -> None:
        self.value = download


class _ExpectDownload:
    """``page.expect_download(...)`` 返回的上下文管理器替身。"""

    def __init__(self, download) -> None:
        self._download = download

    def __enter__(self) -> _FakeDownloadInfo:
        return _FakeDownloadInfo(self._download)

    def __exit__(self, *exc_info) -> bool:
        return False


class _FakePage:
    """只需要 expect_download / evaluate 两个接口的假 Page。"""

    def __init__(self, *, download=None, error: Exception | None = None) -> None:
        self._download = download
        self._error = error
        self.expect_calls = 0
        self.evaluated: list[str | None] = []

    def expect_download(self, timeout=None):
        self.expect_calls += 1
        if self._error is not None:
            raise self._error
        return _ExpectDownload(self._download)

    def evaluate(self, script: str, arg: str | None = None):
        self.evaluated.append(arg)
        return None


def _file_item(name: str, *, category: str = "lectures"):
    from bb_sync.blackboard.models import FileItem

    return FileItem(name=name, url="https://bb.cuhk.edu.cn/bbcswebdav/f", category=category)


def _head_response(size: int | None, *, disposition: str | None = None) -> _FakeAPIResponse:
    headers: dict[str, str] = {}
    if size is not None:
        headers["content-length"] = str(size)
    if disposition is not None:
        headers["content-disposition"] = disposition
    return _FakeAPIResponse(headers=headers)


def test_build_index_ignores_part_files(tmp_path: Path) -> None:
    from bb_sync.blackboard.downloader import build_index

    (tmp_path / "done.pdf").write_bytes(b"x")
    (tmp_path / "half.pdf.part").write_bytes(b"y" * 60_000)

    index = build_index(tmp_path)

    assert "done.pdf" in index
    assert not any(path.name.endswith(".part") for path in index.values())


def test_download_skips_existing_without_fetching_body(tmp_path: Path) -> None:
    """已存在时只做一次 HEAD 就跳过，绝不下载文件体。"""
    from bb_sync.blackboard.downloader import DownloadSession

    lectures = tmp_path / "lectures"
    lectures.mkdir()
    (lectures / "Topic1.pdf").write_bytes(b"x" * 10)
    request = _FakeRequestContext(head=_head_response(10))
    page = _FakePage(download=_FakeDownload(b"should-not-be-used"))
    session = DownloadSession(page, request)

    status, name = session.download_item(_file_item("Topic1.pdf"), tmp_path, dry_run=False)

    assert (status, name) == ("exists", "Topic1.pdf")
    assert page.expect_calls == 0
    assert page.evaluated == []
    assert [call[0] for call in request.calls] == ["fetch"]  # 只有 HEAD，没有 GET


def test_download_uses_browser_channel_and_atomic_write(tmp_path: Path) -> None:
    from bb_sync.blackboard.downloader import DownloadSession

    body = b"%PDF-1.7" + b"y" * 5000
    request = _FakeRequestContext(head=_head_response(len(body)))
    page = _FakePage(download=_FakeDownload(body))
    session = DownloadSession(page, request)

    status, name = session.download_item(_file_item("Lecture 1.pdf"), tmp_path, dry_run=False)

    assert (status, name) == ("downloaded", "Lecture 1.pdf")
    target = tmp_path / "lectures" / "Lecture 1.pdf"
    assert target.read_bytes() == body
    assert not list(tmp_path.rglob("*.part"))
    assert page.evaluated == ["https://bb.cuhk.edu.cn/bbcswebdav/f"]


def test_download_falls_back_to_buffered_when_browser_fails(tmp_path: Path) -> None:
    from bb_sync.blackboard.downloader import DownloadSession

    body = b"%PDF-1.7 buffered"
    request = _FakeRequestContext(head=_head_response(len(body)), get=_FakeAPIResponse(body=body))
    page = _FakePage(error=RuntimeError("浏览器下载不可用"))
    session = DownloadSession(page, request)

    status, _ = session.download_item(_file_item("doc.pdf"), tmp_path, dry_run=False)

    assert status == "downloaded"
    assert (tmp_path / "lectures" / "doc.pdf").read_bytes() == body


def test_download_stops_trying_browser_after_first_failure(tmp_path: Path) -> None:
    """浏览器通道首次失败即熔断，本轮后续文件不再重复等超时。"""
    from bb_sync.blackboard.downloader import DownloadSession

    body = b"%PDF-1.7 data"
    request = _FakeRequestContext(head=_head_response(len(body)), get=_FakeAPIResponse(body=body))
    page = _FakePage(error=RuntimeError("浏览器下载不可用"))
    session = DownloadSession(page, request)

    session.download_item(_file_item("a.pdf"), tmp_path, dry_run=False)
    session.download_item(_file_item("b.pdf"), tmp_path, dry_run=False)

    assert session.browser_download_failed is True
    assert page.expect_calls == 1  # 只尝试过一次浏览器下载


def test_download_dry_run_does_not_fetch_body(tmp_path: Path) -> None:
    from bb_sync.blackboard.downloader import DownloadSession

    request = _FakeRequestContext(head=_head_response(1234))
    page = _FakePage(download=_FakeDownload(b"unused"))
    session = DownloadSession(page, request)

    status, name = session.download_item(_file_item("new.pdf"), tmp_path, dry_run=True)

    assert (status, name) == ("would-download", "new.pdf")
    assert not (tmp_path / "lectures" / "new.pdf").exists()
    assert page.expect_calls == 0


def test_download_sniffs_extension_via_range_peek(tmp_path: Path) -> None:
    """标题没有扩展名时，补一次 Range 读取来嗅探真实类型。"""
    from bb_sync.blackboard.downloader import DownloadSession

    body = b"%PDF-1.7" + b"z" * 100
    request = _FakeRequestContext(head=_head_response(len(body)), get=_FakeAPIResponse(body=body))
    page = _FakePage(download=_FakeDownload(body))
    session = DownloadSession(page, request)

    status, name = session.download_item(_file_item("Lec 01"), tmp_path, dry_run=False)

    assert (status, name) == ("downloaded", "Lec 01.pdf")
    assert (tmp_path / "lectures" / "Lec 01.pdf").exists()
    range_calls = [call for call in request.calls if call[0] == "get"]
    assert range_calls and range_calls[0][1] == {"Range": "bytes=0-4095"}


def test_download_rejects_incomplete_file(tmp_path: Path) -> None:
    from bb_sync.blackboard.downloader import DownloadSession
    from bb_sync.core.errors import NetworkError

    request = _FakeRequestContext(head=_head_response(1000))
    page = _FakePage(download=_FakeDownload(b"short"))
    session = DownloadSession(page, request)

    with pytest.raises(NetworkError):
        session.download_item(_file_item("doc.pdf"), tmp_path, dry_run=False)

    assert not (tmp_path / "lectures" / "doc.pdf").exists()
    assert not (tmp_path / "lectures" / "doc.pdf.part").exists()


def test_download_updates_index_for_same_run(tmp_path: Path) -> None:
    """同一轮里新落盘的文件要写入索引，供后续不同名同内容的大文件去重。"""
    from bb_sync.blackboard.downloader import SIZE_DEDUP_THRESHOLD, DownloadSession

    size = SIZE_DEDUP_THRESHOLD + 100
    body = b"q" * size
    request = _FakeRequestContext(head=_head_response(size))
    page = _FakePage(download=_FakeDownload(body))
    session = DownloadSession(page, request)
    index: dict = {}

    status1, _ = session.download_item(_file_item("a.pdf"), tmp_path, dry_run=False, index=index)
    status2, name2 = session.download_item(
        _file_item("b.pdf"), tmp_path, dry_run=False, index=index
    )

    assert status1 == "downloaded"
    assert (status2, name2) == ("exists", "lectures/a.pdf")


# ---------------------------------------------------------------- 等待策略


class _FakeWaitPage:
    """只实现 ``_wait_for_content`` 会用到的三个方法，并记录调用顺序。"""

    def __init__(self, *, has_selector: bool = True) -> None:
        self.calls: list[tuple] = []
        self._has_selector = has_selector

    def wait_for_selector(self, selector, timeout=None, state=None):
        self.calls.append(("selector", selector, timeout, state))
        if not self._has_selector:
            from playwright.sync_api import TimeoutError as PWTimeout

            raise PWTimeout("not found")

    def wait_for_load_state(self, state=None, timeout=None):
        self.calls.append(("load_state", state, timeout))

    def wait_for_timeout(self, ms):
        self.calls.append(("settle", ms))


def test_wait_for_content_settles_after_selector() -> None:
    from bb_sync.blackboard.scraper import _wait_for_content

    page = _FakeWaitPage()
    _wait_for_content(page, "a.x", 3500)  # type: ignore[arg-type]

    kinds = [call[0] for call in page.calls]
    assert kinds == ["selector", "load_state", "settle"]
    assert page.calls[0][2] == 3500  # 关键元素等待上限 = 原来的固定等待
    assert page.calls[1][1] == "networkidle"
    assert 0 <= page.calls[1][2] <= 3500  # 网络空闲等待不超过剩余预算
    assert 0 <= page.calls[2][1] <= 300  # settle 不超过剩余预算


def test_wait_for_content_missing_selector_does_not_wait_twice() -> None:
    from bb_sync.blackboard.scraper import _wait_for_content

    page = _FakeWaitPage(has_selector=False)
    _wait_for_content(page, "a.x", 1000)  # type: ignore[arg-type]

    # 选择器未出现时 wait_for_selector 已耗尽 timeout，不能再叠加第二次等待
    assert [call[0] for call in page.calls] == ["selector"]


# ---------------------------------------------------------------- 登录失败兜底


class _NullNavigation:
    """``page.expect_navigation(...)`` 的上下文替身。"""

    def __enter__(self):
        return None

    def __exit__(self, *exc_info) -> bool:
        return False


class _FakeLoginPage:
    """登录流程的假 Page，只覆盖 ``do_login`` / ``is_logged_in`` 会触达的接口。

    默认模拟真实失败：登录页主按钮存在，点「登录」后跳到 ADFS，但账号输入框不出现。
    ``land_on="portal"`` 则模拟「其实已登录」——点登录后直接回到 BB 门户。
    """

    ADFS_URL = "https://adfs.cuhk.edu.cn/adfs/ls/"
    PORTAL_URL = (
        "https://bb.cuhk.edu.cn/webapps/portal/execute/tabs/tabAction?tab_tab_group_id=_1_1"
    )
    LOGIN_URL = "https://bb.cuhk.edu.cn/webapps/login/"

    def __init__(
        self,
        *,
        url: str = "https://bb.cuhk.edu.cn/webapps/login/",
        land_on: str = "adfs",
        goto_url: str | None = None,
    ) -> None:
        self.url = url
        self.goto_error: Exception | None = None
        self.screenshots: list[str] = []
        self._land_on = land_on
        self._goto_url = goto_url  # 非空则 goto 后停在指定页面（模拟被踢回登录页）

    def goto(self, url: str, **kwargs):
        if self.goto_error is not None:
            raise self.goto_error
        self.url = self._goto_url or url
        return None

    def wait_for_selector(self, selector: str, timeout=None, state=None):
        # 登录页主按钮存在；其余选择器（ADFS 账号/密码框）一律超时
        if "input[name='login']" in selector:
            return None
        from playwright.sync_api import TimeoutError as PWTimeout

        raise PWTimeout(f"waiting for {selector}")

    def wait_for_timeout(self, ms: int) -> None:
        return None

    def expect_navigation(self, **kwargs) -> _NullNavigation:
        return _NullNavigation()

    def click(self, selector: str, **kwargs) -> None:
        self.url = self.ADFS_URL if self._land_on == "adfs" else self.PORTAL_URL

    def screenshot(self, path=None, full_page=False, timeout=None):
        if path:
            Path(path).write_bytes(b"PNG")
            self.screenshots.append(path)

    def content(self) -> str:
        return "<html><body>login page</body></html>"


def test_do_login_timeout_becomes_auth_error_with_artifacts(isolated_env: Path) -> None:
    """选择器超时必须转成退出码 3 的 AuthError，并落截图/HTML，而不是未预期错误。"""
    from bb_sync.blackboard.login import do_login
    from bb_sync.core.errors import AuthError

    page = _FakeLoginPage(url="https://adfs.cuhk.edu.cn/adfs/ls/")
    with pytest.raises(AuthError) as excinfo:
        do_login(page, "s123", "pw")  # type: ignore[arg-type]

    error = excinfo.value
    assert int(error.exit_code) == 3
    assert _FakeLoginPage.ADFS_URL in error.message
    home = isolated_env / ".bb-sync"
    assert (home / "debug_login.png").read_bytes() == b"PNG"
    assert "login page" in (home / "debug_login.html").read_text(encoding="utf-8")


def test_do_login_wraps_unexpected_error_and_dumps(isolated_env: Path) -> None:
    from bb_sync.blackboard.login import do_login
    from bb_sync.core.errors import AuthError

    page = _FakeLoginPage()
    page.goto_error = RuntimeError("连接被重置")
    with pytest.raises(AuthError) as excinfo:
        do_login(page, "s123", "pw")  # type: ignore[arg-type]

    assert "连接被重置" in excinfo.value.message
    assert (isolated_env / ".bb-sync" / "debug_login.html").exists()


def test_is_logged_in_true_when_portal_reached() -> None:
    """停在 BB 门户即视为已登录，不再按页面文案关键词误判。"""
    from bb_sync.blackboard.login import is_logged_in

    assert is_logged_in(_FakeLoginPage()) is True  # type: ignore[arg-type]


def test_is_logged_in_false_when_bounced_to_login() -> None:
    from bb_sync.blackboard.login import is_logged_in

    page = _FakeLoginPage(goto_url=_FakeLoginPage.LOGIN_URL)
    assert is_logged_in(page) is False  # type: ignore[arg-type]


def test_do_login_accepts_already_authenticated_portal() -> None:
    """登录态有效、点登录后回到门户时，不应再去等 ADFS 表单。"""
    from bb_sync.blackboard.login import do_login

    page = _FakeLoginPage(land_on="portal")
    do_login(page, "s123", "pw")  # type: ignore[arg-type]

    assert page.url == _FakeLoginPage.PORTAL_URL
    assert page.screenshots == []  # 成功路径不落调试产物
