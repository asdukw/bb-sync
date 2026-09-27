"""配置模型与加载：Pydantic 校验 + 四层优先级。

优先级（高 → 低），对齐成熟 CLI 的惯例::

    1. CLI 参数      bb-sync run --root ~/University/courses
    2. 环境变量      BB_SYNC_ROOT=~/University/courses
    3. 配置文件      ~/.bb-sync/config.yaml
    4. 内置默认值    DEFAULT_CONFIG 同源

配置文件位置解析：``--config`` 显式指定 > 当前目录 config.yaml > ``~/.bb-sync/config.yaml``。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from bb_sync import paths
from bb_sync.core.errors import ConfigError

#: 环境变量前缀，如 ``BB_SYNC_ROOT``、``BB_SYNC_MAX_DEPTH``
ENV_PREFIX = "BB_SYNC_"

DEFAULT_KEYWORDS: dict[str, list[str]] = {
    "assignments": ["assignment", "homework", "作业", "hw"],
    "tutorials": ["tutorial", "lab", "实验", "指导", "recitation"],
    "lectures": ["lecture", "课件", "讲义", "slides", "notes", "note"],
}


class Settings(BaseModel):
    """bb-sync 全部可配置项。"""

    root: str = "~/courses"
    """课程资源根目录，支持 ``~`` 与相对路径（相对路径以配置文件所在目录为基准）。"""

    include: Literal["all"] | list[str] = "all"
    """同步哪些课程：``all`` 或课程代码列表。"""

    course_dirs: dict[str, str] = Field(default_factory=dict)
    """课程代码 → 本地文件夹名的显式映射。"""

    keywords: dict[str, list[str]] = Field(default_factory=lambda: dict(DEFAULT_KEYWORDS))
    """内容归类关键词。"""

    announcements: bool = True
    """是否同步公告到 ``announcements.md``。"""

    max_depth: int = Field(default=3, ge=1, le=10)
    """内容子文件夹递归最大深度。"""

    model_config = {"extra": "ignore"}

    @field_validator("include", mode="before")
    @classmethod
    def _normalize_include(cls, v: Any) -> Any:
        """允许 ``include: CSC5010`` 这类单个标量写法。"""
        if v is None:
            return "all"
        if isinstance(v, str) and v.lower() != "all":
            return [v]
        return v

    @field_validator("course_dirs", mode="before")
    @classmethod
    def _normalize_course_dirs(cls, v: Any) -> Any:
        """``course_dirs:`` 写成空值时归一为 ``{}``。"""
        return v or {}

    def resolve_root(self, config_path: Path | None = None) -> Path:
        """把 ``root`` 解析为绝对路径（支持 ``~`` 与相对路径）。"""
        raw = Path(self.root).expanduser()
        if raw.is_absolute():
            return raw
        base = config_path.parent if config_path else Path.cwd()
        return (base / raw).resolve()


def config_target(explicit: str | None = None) -> Path:
    """配置文件位置：``--config`` > 当前目录 config.yaml > ``~/.bb-sync/``。"""
    if explicit:
        return Path(explicit).expanduser()
    cwd_candidate = Path.cwd() / paths.CONFIG_NAME
    if cwd_candidate.exists():
        return cwd_candidate
    return paths.BB_SYNC_HOME / paths.CONFIG_NAME


#: 首次运行自动生成的默认配置（带注释，供用户手改）
DEFAULT_CONFIG_TEXT = """\
# bb-sync 配置（首次运行自动生成，可按需修改）
# 也可用命令修改：bb-sync config set root <目录>

# 课程资源根目录（支持 ~ 和相对路径）
root: ~/courses

# 同步哪些课程：all = 全部，或列表如 [CSC5010, DDA5002]
include: all

# 课程代码 -> 本地文件夹名 的显式映射（未列出的自动命名）
course_dirs: {}

# 内容归类关键词：先匹配 assignments，再 tutorials，其余归 lectures
keywords:
  assignments: ["assignment", "homework", "作业", "hw"]
  tutorials: ["tutorial", "lab", "实验", "指导", "recitation"]
  lectures: ["lecture", "课件", "讲义", "slides", "notes", "note"]

# 公告是否同步（写入课程目录 announcements.md）
announcements: true

# 内容子文件夹递归深度（1 = 只下内容区第一层）
max_depth: 3
"""


def ensure_config_file(path: Path) -> bool:
    """配置文件不存在时落一份默认配置；返回是否新建。"""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
    return True


def read_raw(path: Path) -> dict[str, Any]:
    """读取 YAML 原文；文件缺失或语法错误抛 :class:`ConfigError`。"""
    if not path.exists():
        raise ConfigError(f"配置文件不存在：{path}", "运行 bb-sync config init 生成默认配置")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件格式错误：{path}", str(exc)) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件顶层必须是映射：{path}")
    return data


def _env_overrides() -> dict[str, Any]:
    """收集 ``BB_SYNC_*`` 环境变量（键名小写，下划线保留）。"""
    out: dict[str, Any] = {}
    for key, raw in os.environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        name = key[len(ENV_PREFIX) :].lower()
        if name in Settings.model_fields:
            out[name] = raw
    return out


def load_settings(
    path: Path | None = None,
    *,
    cli_overrides: dict[str, Any] | None = None,
) -> tuple[Settings, Path]:
    """按四层优先级加载配置，返回 ``(Settings, 实际配置文件路径)``。

    ``cli_overrides`` 里的 ``None`` 值会被忽略，方便直接传 argparse/Typer 的可选参数。
    """
    path = path or config_target()
    layers: dict[str, Any] = {}
    if path.exists():
        layers.update(read_raw(path))
    layers.update(_env_overrides())
    if cli_overrides:
        layers.update({k: v for k, v in cli_overrides.items() if v is not None})

    try:
        settings = Settings.model_validate(layers)
    except ValidationError as exc:
        raise ConfigError(f"配置项校验失败：{path}", _format_validation_error(exc)) from exc
    return settings, path


def _format_validation_error(exc: ValidationError) -> str:
    """把 Pydantic 报错压成人类可读的单行提示。"""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "(顶层)"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def dump_settings(settings: Settings) -> dict[str, Any]:
    """导出为可 JSON/YAML 序列化的普通字典。"""
    return settings.model_dump(mode="json")


__all__ = [
    "DEFAULT_CONFIG_TEXT",
    "DEFAULT_KEYWORDS",
    "ENV_PREFIX",
    "Settings",
    "config_target",
    "dump_settings",
    "ensure_config_file",
    "load_settings",
    "read_raw",
]
