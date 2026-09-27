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
