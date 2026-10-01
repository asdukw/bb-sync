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


# ---------------------------------------------------------------- Steel 安装目录


def _fake_steel_install(root: Path, marker: str) -> None:
    """造一份「源码 + tsx 都已就位」的假 Steel 安装。"""
    src = root / "api" / "src"
    tsx = root / "node_modules" / "tsx" / "dist"
    src.mkdir(parents=True, exist_ok=True)
    tsx.mkdir(parents=True, exist_ok=True)
    (src / "index.ts").write_text("// fake steel\n", encoding="utf-8")
    (tsx / "cli.mjs").write_text("// fake tsx\n", encoding="utf-8")
    (root / "marker.txt").write_text(marker, encoding="utf-8")


def _point_steel_at(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    from bb_sync.browser import steel

    monkeypatch.setattr(steel, "STEEL_ROOT", root)
    monkeypatch.setattr(steel, "STEEL_DIR", root / "api")
    monkeypatch.setattr(steel, "STEEL_LOG", root / "steel.log")


def _forbid_node(monkeypatch: pytest.MonkeyPatch) -> None:
    """复用已就绪的安装时不应再检查 Node/npm，更不应触网重装。"""
    from bb_sync.browser import steel

    def no_node() -> str:
        raise AssertionError("复用已就绪的后端时不应检查 Node.js")

    monkeypatch.setattr(steel, "_node_bin", no_node)


def _write_old_anchor_meta(root: Path) -> None:
    """把安装标记成「旧锚点」，触发一次替换更新。"""
    import json

    (root / ".bb-sync.json").write_text(
        json.dumps({"tool": "bb-sync", "schema": 1, "zip_url": "https://example.com/old.zip"}),
        encoding="utf-8",
    )


def _stub_fresh_install(monkeypatch: pytest.MonkeyPatch, marker: str) -> dict[str, int]:
    """把下载与 npm 安装替换成本地假安装，并统计调用次数。"""
    from bb_sync.browser import steel

    calls = {"download": 0, "npm": 0}

    def fake_download(console, tmp_dir: Path) -> Path:
        calls["download"] += 1
        source = tmp_dir / f"steel-browser-{steel.STEEL_REF}"
        _fake_steel_install(source, marker)
        return source

    def fake_install(npm: str, console) -> None:
        calls["npm"] += 1

    monkeypatch.setattr(steel, "_download_source", fake_download)
    monkeypatch.setattr(steel, "_install_dependencies", fake_install)
    monkeypatch.setattr(steel, "_npm_bin", lambda: "/fake/npm")
    monkeypatch.setattr(steel, "_node_bin", lambda: "/fake/node")
    return calls


def test_steel_root_is_inside_bb_sync_home() -> None:
    """Steel 装在 BB_SYNC_HOME/.steel，不碰用户家目录的 ~/.steel。"""
    from bb_sync import paths
    from bb_sync.browser import steel

    assert steel.STEEL_ROOT == paths.BB_SYNC_HOME / ".steel"
    assert steel.STEEL_ROOT.parent == paths.BB_SYNC_HOME


def test_steel_macos_common_paths(monkeypatch) -> None:
    """macOS 回退覆盖 Homebrew Node、系统/用户 Applications 下的 Chrome/Edge。"""
    from bb_sync.browser import steel

    monkeypatch.setattr(steel, "IS_WIN", False)
    monkeypatch.setattr(steel, "IS_MACOS", True)

    node_candidates = {str(path).replace("\\", "/") for path in steel._node_candidates()}
    assert {"/opt/homebrew/bin/node", "/usr/local/bin/node"} <= node_candidates

    browser_candidates = {path.replace("\\", "/") for path in steel._browser_candidates()}
    assert any(
        path.endswith("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        for path in browser_candidates
    )
    assert any(
        path.endswith("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
        for path in browser_candidates
    )


def test_steel_finds_npm_next_to_node(monkeypatch, tmp_path: Path) -> None:
    """Node 经非 PATH 的 Homebrew 等位置命中时，npm 也应从同目录解析。"""
    from bb_sync.browser import steel

    node = tmp_path / "bin" / "node"
    npm = tmp_path / "bin" / "npm"
    node.parent.mkdir(parents=True)
    node.write_text("node", encoding="utf-8")
    npm.write_text("npm", encoding="utf-8")
    monkeypatch.setattr(steel.shutil, "which", lambda name: None)
    monkeypatch.setattr(steel, "_node_bin", lambda: str(node))

    assert steel._npm_bin() == str(npm)


def test_steel_start_server_detaches_on_macos(monkeypatch, tmp_path: Path) -> None:
    """macOS 后台进程进入独立会话，终端退出时不应被 SIGHUP 结束。"""
    from bb_sync.browser import steel

    root = tmp_path / "home" / ".bb-sync" / ".steel"
    _fake_steel_install(root, "macos")
    _point_steel_at(monkeypatch, root)
    monkeypatch.setattr(steel, "IS_WIN", False)
    monkeypatch.setattr(steel, "_node_bin", lambda: "/opt/homebrew/bin/node")
    monkeypatch.setattr(steel, "detect_browser", lambda: None)
    monkeypatch.setattr(steel, "healthy", lambda: True)
    monkeypatch.setattr(steel.time, "sleep", lambda _seconds: None)

    captured_cmd: object = None
    captured_kwargs: dict[str, object] = {}

    def fake_popen(cmd, **kwargs):
        nonlocal captured_cmd
        captured_cmd = cmd
        captured_kwargs.update(kwargs)
        return object()

    monkeypatch.setattr(steel.subprocess, "Popen", fake_popen)

    steel.start_server(wait=1)

    assert captured_cmd == [
        "/opt/homebrew/bin/node",
        str(root / "node_modules" / "tsx" / "dist" / "cli.mjs"),
        "src/index.ts",
    ]
    assert captured_kwargs["start_new_session"] is True


def test_doctor_macos_install_hints(monkeypatch) -> None:
    """doctor 在 macOS 上应给出 Homebrew 修复命令，而不是 winget。"""
    from bb_sync.browser import steel
    from bb_sync.core import doctor

    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    monkeypatch.setattr(steel, "IS_WIN", False)
    monkeypatch.setattr(steel, "IS_MACOS", True)

    assert "brew install git" in doctor._git_install_hint()
    assert "brew install node" in steel._node_install_hint()
    assert "brew install --cask" in doctor._browser_install_hint()


def test_steel_zip_url_is_pinned_to_commit() -> None:
    """源码包锚定到具体 commit，不跟随 main / tag 漂移。"""
    from bb_sync.browser import steel

    assert len(steel.STEEL_REF) == 40
    assert all(c in "0123456789abcdef" for c in steel.STEEL_REF)
    url = steel._default_zip_url()
    assert steel.STEEL_REF in url
    assert "refs/heads" not in url


def test_steel_extracted_root_allows_any_ref_name(tmp_path: Path) -> None:
    """zip 根目录名随 ref 变化（分支/tag/commit），按目录探测而不是硬编码。"""
    from bb_sync.browser import steel

    tmp_dir = tmp_path / "tmp"
    extracted = tmp_dir / f"steel-browser-{steel.STEEL_REF}"
    extracted.mkdir(parents=True)
    (tmp_dir / "steel-browser.zip").write_text("zip", encoding="utf-8")  # 压缩包本身不算目录

    assert steel._extracted_root(tmp_dir) == extracted

    (tmp_dir / "steel-browser-main").mkdir()
    with pytest.raises(EnvironmentError_, match="结构异常"):
        steel._extracted_root(tmp_dir)

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(EnvironmentError_, match="结构异常"):
        steel._extracted_root(empty)


def test_steel_deploy_reuses_ready_install(monkeypatch, tmp_path: Path) -> None:
    """已就绪且锚点一致：直接复用，不查 Node、不触网；旧安装补记归属但不谎报修订。"""
    from bb_sync.browser import steel

    root = tmp_path / "home" / ".bb-sync" / ".steel"
    _fake_steel_install(root, "ready")
    _point_steel_at(monkeypatch, root)
    _forbid_node(monkeypatch)

    steel.deploy(steel._SILENT)

    assert (root / "marker.txt").read_text(encoding="utf-8") == "ready"
    assert steel._read_steel_meta()["zip_url"] == steel.STEEL_ZIP_URL
    assert steel.installed_ref() is None


def test_steel_deploy_keeps_recorded_ref_on_reuse(monkeypatch, tmp_path: Path) -> None:
    """已记录锚点的安装复用时不改写元数据（doctor 显示的版本保持准确）。"""
    import json

    from bb_sync.browser import steel

    root = tmp_path / "home" / ".bb-sync" / ".steel"
    _fake_steel_install(root, "ready")
    _point_steel_at(monkeypatch, root)
    recorded = {
        "tool": "bb-sync",
        "schema": 1,
        "steel_ref": steel.STEEL_REF,
        "zip_url": steel.STEEL_ZIP_URL,
        "recorded_at": "2026-09-28T00:00:00+0800",
    }
    steel._steel_meta_path().write_text(json.dumps(recorded), encoding="utf-8")
    _forbid_node(monkeypatch)

    steel.deploy(steel._SILENT)

    assert steel._read_steel_meta()["recorded_at"] == "2026-09-28T00:00:00+0800"


def test_steel_deploy_updates_old_anchor(monkeypatch, tmp_path: Path) -> None:
    """锚点变化：先下载再替换，成功后才删旧目录；之后直接复用。"""
    from bb_sync.browser import steel

    root = tmp_path / "home" / ".bb-sync" / ".steel"
    _fake_steel_install(root, "old")
    _write_old_anchor_meta(root)
    _point_steel_at(monkeypatch, root)
    calls = _stub_fresh_install(monkeypatch, "fresh")

    steel.deploy(steel._SILENT)

    assert calls == {"download": 1, "npm": 1}
    assert (root / "marker.txt").read_text(encoding="utf-8") == "fresh"
    assert steel._read_steel_meta()["zip_url"] == steel.STEEL_ZIP_URL
    assert steel.installed_ref() == steel.STEEL_REF
    assert not list(root.parent.glob(".steel.old-*"))

    _forbid_node(monkeypatch)
    steel.deploy(steel._SILENT)
    assert calls == {"download": 1, "npm": 1}


def test_steel_deploy_keeps_install_when_download_fails(monkeypatch, tmp_path: Path) -> None:
    """下载失败（网络问题）时旧安装原样保留，不会先删再装。"""
    from bb_sync.browser import steel

    root = tmp_path / "home" / ".bb-sync" / ".steel"
    _fake_steel_install(root, "old")
    _write_old_anchor_meta(root)
    _point_steel_at(monkeypatch, root)

    def no_network(console, tmp_dir: Path) -> Path:
        raise NetworkError("下载失败")

    monkeypatch.setattr(steel, "_download_source", no_network)
    monkeypatch.setattr(steel, "_node_bin", lambda: "/fake/node")
    monkeypatch.setattr(steel, "_npm_bin", lambda: "/fake/npm")

    with pytest.raises(NetworkError):
        steel.deploy(steel._SILENT)

    assert (root / "marker.txt").read_text(encoding="utf-8") == "old"
    assert steel.is_deployed()
    assert not list(root.parent.glob(".steel.old-*"))


def test_steel_deploy_rolls_back_when_install_fails(monkeypatch, tmp_path: Path) -> None:
    """依赖装坏时回滚：旧安装恢复原位，下次运行仍可用。"""
    from bb_sync.browser import steel

    root = tmp_path / "home" / ".bb-sync" / ".steel"
    _fake_steel_install(root, "old")
    _write_old_anchor_meta(root)
    _point_steel_at(monkeypatch, root)
    _stub_fresh_install(monkeypatch, "fresh")

    def fail_install(npm: str, console) -> None:
        raise EnvironmentError_("npm install 失败", "看日志")

    monkeypatch.setattr(steel, "_install_dependencies", fail_install)

    with pytest.raises(EnvironmentError_):
        steel.deploy(steel._SILENT)

    assert (root / "marker.txt").read_text(encoding="utf-8") == "old"
    assert steel.is_deployed()
    assert not list(root.parent.glob(".steel.old-*"))


def test_steel_deploy_reinstalls_broken_install(monkeypatch, tmp_path: Path) -> None:
    """残缺目录（上次安装中断）：重新安装，残余随替换清掉。"""
    from bb_sync.browser import steel

    root = tmp_path / "home" / ".bb-sync" / ".steel"
    (root / "api").mkdir(parents=True)  # 上次安装中断留下的空壳
    (root / "leftover.txt").write_text("half-downloaded", encoding="utf-8")
    _point_steel_at(monkeypatch, root)
    calls = _stub_fresh_install(monkeypatch, "fresh")

    steel.deploy(steel._SILENT)

    assert calls == {"download": 1, "npm": 1}
    assert (root / "marker.txt").read_text(encoding="utf-8") == "fresh"
    assert not (root / "leftover.txt").exists()
    assert not list(root.parent.glob(".steel.old-*"))
    assert steel.installed_ref() == steel.STEEL_REF


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

        def close(self) -> None:
            pass

    class FakePlaywrightContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    course = Course(bb_id="_1_1", title="CSC5010: Artificial Intelligence")
    monkeypatch.setattr(service.steel, "ensure_server", lambda *args, **kwargs: None)
    monkeypatch.setattr(service.steel, "create_session", lambda *args, **kwargs: object())
    monkeypatch.setattr(service.steel, "release_session", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        service.steel, "connect", lambda *args, **kwargs: (Dummy(), Dummy(), object())
    )
    monkeypatch.setattr(service, "sync_playwright", lambda: FakePlaywrightContext())
    monkeypatch.setattr(service, "ensure_login", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "sync_course", lambda *args, **kwargs: None)
    monkeypatch.setattr(service.scraper, "scrape_courses", lambda page, include, console: [course])
    monkeypatch.setattr(service.scraper, "scrape_due_items", lambda page, course, console: [])

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


def test_print_due_details_one_item_per_line() -> None:
    from datetime import date
    from typing import cast

    from bb_sync.blackboard.models import DueItem
    from bb_sync.core import service
    from bb_sync.core.output import Console

    class FakeConsole:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def log(self, message: str) -> None:
            self.messages.append(message)

    fake = FakeConsole()
    console = cast(Console, fake)
    service._print_due_details(
        console,
        [
            DueItem("_1_1", "CSC5010", "AI", "Homework 1", date(2026, 10, 10)),
            DueItem("_2_1", "MDS5122", "DL", "Reading", None),
        ],
    )

    assert fake.messages == [
        "[due] 2026-10-10 | CSC5010 | Homework 1",
        "[due] 日期未知 | MDS5122 | Reading",
    ]


def test_print_due_details_empty_writes_congratulations() -> None:
    from typing import cast

    from bb_sync.core import service
    from bb_sync.core.output import Console

    class FakeConsole:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def log(self, message: str) -> None:
            self.messages.append(message)

    fake = FakeConsole()
    console = cast(Console, fake)
    service._print_due_details(console, [])

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


# ---------------------------------------------------------------- 流式下载


class _FakeResponse:
    """最小流式响应替身：支持分块 read / headers / status，并记录读取进度。"""

    def __init__(self, body: bytes, *, headers: dict | None = None, status: int = 200) -> None:
        self._body = body
        self._pos = 0
        self.headers = headers or {}
        self.status = status
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._body) - self._pos
        chunk = self._body[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class _FakeOpener:
    """按顺序弹出响应或异常，并记录请求，便于断言 Range 头。"""

    def __init__(self, *items) -> None:
        self._items = list(items)
        self.requests: list = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeBrowserResponse:
    """Playwright 请求上下文的响应替身（只支持兜底路径用到的接口）。"""

    def __init__(self, body: bytes, headers: dict | None = None) -> None:
        self._body = body
        self.headers = headers or {}

    def body(self) -> bytes:
        return self._body


class _FakeRequestContext:
    """Playwright ``APIRequestContext`` 的替身。"""

    def __init__(self, response) -> None:
        self._response = response
        self.calls: list[tuple] = []

    def get(self, url: str, timeout: int | None = None):
        self.calls.append((url, timeout))
        return self._response


def _file_item(name: str, *, category: str = "lectures"):
    from bb_sync.blackboard.models import FileItem

    return FileItem(name=name, url="https://bb.cuhk.edu.cn/bbcswebdav/f", category=category)


def test_download_item_streams_and_writes_atomically(tmp_path: Path) -> None:
    from bb_sync.blackboard.downloader import DownloadClient, download_item

    body = b"%PDF-1.7" + b"x" * 5000
    opener = _FakeOpener(_FakeResponse(body, headers={"content-length": str(len(body))}))
    client = DownloadClient(opener=opener, retries=0, backoff=0.0)

    status, name = download_item(client, _file_item("Lec 01"), tmp_path, dry_run=False)

    assert (status, name) == ("downloaded", "Lec 01.pdf")
    target = tmp_path / "lectures" / "Lec 01.pdf"
    assert target.read_bytes() == body
    # 原子落盘：完成后不留 .part
    assert not list(tmp_path.rglob("*.part"))


def test_download_item_dry_run_only_reads_header(tmp_path: Path) -> None:
    from bb_sync.blackboard.downloader import SNIFF_BYTES, DownloadClient, download_item

    body = b"%PDF-1.7" + b"y" * (SNIFF_BYTES * 4)
    resp = _FakeResponse(body, headers={"content-length": str(len(body))})
    client = DownloadClient(opener=_FakeOpener(resp), retries=0, backoff=0.0)

    status, name = download_item(client, _file_item("big"), tmp_path, dry_run=True)

    assert (status, name) == ("would-download", "big.pdf")
    assert not (tmp_path / "lectures" / "big.pdf").exists()
    # dry-run 不应把整个文件拉下来，只读到文件头
    assert resp._pos <= SNIFF_BYTES


def test_download_item_skips_same_size_and_suffix(tmp_path: Path) -> None:
    from bb_sync.blackboard.downloader import (
        SIZE_DEDUP_THRESHOLD,
        DownloadClient,
        build_index,
        download_item,
    )

    size = SIZE_DEDUP_THRESHOLD + 100
    (tmp_path / "old.pdf").write_bytes(b"z" * size)
    index = build_index(tmp_path)
    resp = _FakeResponse(b"z" * size, headers={"content-length": str(size)})
    client = DownloadClient(opener=_FakeOpener(resp), retries=0, backoff=0.0)

    status, name = download_item(
        client, _file_item("renamed.pdf"), tmp_path, dry_run=False, index=index
    )

    assert (status, name) == ("exists", "old.pdf")


def test_download_item_updates_index_for_same_run(tmp_path: Path) -> None:
    from bb_sync.blackboard.downloader import SIZE_DEDUP_THRESHOLD, DownloadClient, download_item

    size = SIZE_DEDUP_THRESHOLD + 100
    body = b"q" * size
    first = DownloadClient(
        opener=_FakeOpener(_FakeResponse(body, headers={"content-length": str(size)})),
        retries=0,
        backoff=0.0,
    )
    index: dict = {}

    status1, _ = download_item(first, _file_item("a.pdf"), tmp_path, dry_run=False, index=index)
    second = DownloadClient(
        opener=_FakeOpener(_FakeResponse(body, headers={"content-length": str(size)})),
        retries=0,
        backoff=0.0,
    )
    status2, name2 = download_item(
        second, _file_item("b.pdf"), tmp_path, dry_run=False, index=index
    )

    assert status1 == "downloaded"
    # 同一轮里，不同名但同体积同后缀的大文件应命中新写入的索引
    assert (status2, name2) == ("exists", "lectures/a.pdf")


def test_download_item_empty_body_is_empty(tmp_path: Path) -> None:
    from bb_sync.blackboard.downloader import DownloadClient, download_item

    client = DownloadClient(
        opener=_FakeOpener(_FakeResponse(b"", headers={"content-length": "0"})),
        retries=0,
        backoff=0.0,
    )

    status, name = download_item(client, _file_item("nothing.pdf"), tmp_path, dry_run=False)

    assert (status, name) == ("empty", "nothing.pdf")
    assert not (tmp_path / "lectures" / "nothing.pdf").exists()


def test_download_item_partial_transfer_leaves_no_target(tmp_path: Path) -> None:
    """中途失败只留 .part，绝不产生会被后续当成「已存在」的半截目标文件。"""
    from bb_sync.blackboard.downloader import DownloadClient, download_item

    resp = _FakeResponse(b"0123456789", headers={"content-length": "1000"})
    client = DownloadClient(opener=_FakeOpener(resp), retries=0, backoff=0.0)

    with pytest.raises(OSError):
        download_item(client, _file_item("doc.bin"), tmp_path, dry_run=False)

    assert not (tmp_path / "lectures" / "doc.bin").exists()
    part = tmp_path / "lectures" / "doc.bin.part"
    assert part.read_bytes() == b"0123456789"


def test_download_item_resumes_existing_part_via_range(tmp_path: Path) -> None:
    from bb_sync.blackboard.downloader import PART_SUFFIX, DownloadClient, download_item

    head = b"%PDF-1.7 head"
    tail = b"rest-of-file" * 200
    full = head + tail
    cat = tmp_path / "lectures"
    cat.mkdir(parents=True)
    part = cat / ("doc.pdf" + PART_SUFFIX)
    part.write_bytes(head)

    # 第一次请求（探测取名）返回全量；发现 .part 后关闭并按 Range 重开
    peek = _FakeResponse(full, headers={"content-length": str(len(full))})
    resume = _FakeResponse(
        tail,
        headers={
            "content-length": str(len(tail)),
            "content-range": f"bytes {len(head)}-{len(full) - 1}/{len(full)}",
        },
        status=206,
    )
    opener = _FakeOpener(peek, resume)
    client = DownloadClient(opener=opener, retries=0, backoff=0.0)

    status, name = download_item(client, _file_item("doc.pdf"), tmp_path, dry_run=False)

    assert (status, name) == ("downloaded", "doc.pdf")
    assert (cat / "doc.pdf").read_bytes() == full
    assert not part.exists()
    assert opener.requests[1].get_header("Range") == f"bytes={len(head)}-"


def test_download_item_retries_transient_error(tmp_path: Path) -> None:
    from bb_sync.blackboard.downloader import DownloadClient, download_item

    body = b"%PDF-1.7" + b"r" * 100
    opener = _FakeOpener(
        TimeoutError("瞬时超时"),
        _FakeResponse(body, headers={"content-length": str(len(body))}),
    )
    client = DownloadClient(opener=opener, retries=1, backoff=0.0)

    status, _ = download_item(client, _file_item("retry"), tmp_path, dry_run=False)

    assert status == "downloaded"
    assert len(opener.requests) == 2


def test_download_item_falls_back_to_browser_request(tmp_path: Path) -> None:
    from bb_sync.blackboard.downloader import DownloadClient, download_item

    body = b"%PDF-1.7 fallback"
    browser = _FakeRequestContext(
        _FakeBrowserResponse(body, headers={"content-type": "application/pdf"})
    )
    client = DownloadClient(
        opener=_FakeOpener(RuntimeError("流式通道不可用")),
        retries=0,
        backoff=0.0,
        fallback=browser,
    )

    status, name = download_item(client, _file_item("fallback"), tmp_path, dry_run=False)

    assert (status, name) == ("downloaded", "fallback.pdf")
    assert (tmp_path / "lectures" / "fallback.pdf").read_bytes() == body
    assert browser.calls and browser.calls[0][0] == "https://bb.cuhk.edu.cn/bbcswebdav/f"


def test_build_index_ignores_part_files(tmp_path: Path) -> None:
    from bb_sync.blackboard.downloader import build_index

    (tmp_path / "done.pdf").write_bytes(b"x")
    (tmp_path / "half.pdf.part").write_bytes(b"y" * 60_000)

    index = build_index(tmp_path)

    assert "done.pdf" in index
    assert not any(path.name.endswith(".part") for path in index.values())


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
