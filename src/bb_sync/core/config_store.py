"""配置文件的行级编辑器：修改值但保留原有注释与排版。

直接 ``yaml.safe_dump`` 会丢掉用户写的注释，所以 ``config set/unset`` 走这套
逐行正则操作。仅处理顶层标量键与「顶层映射下的一级子键」两种形态，
覆盖面与配置结构（root / include / course_dirs.X）一致。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from bb_sync.core.errors import ConfigError, NotFoundError

# ---------------------------------------------------------------- 读取辅助


def _split_inline(line: str) -> str:
    """键行冒号后的值部分（去注释、去空白）；无冒号返回空串。"""
    return line.split(":", 1)[1].split("#", 1)[0].strip() if ":" in line else ""


def _block_end(lines: list[str], start: int) -> int:
    """顶层键所在映射块的结束行号（不含）：空行/注释/缩进行属于块内。"""
    j = start + 1
    while j < len(lines):
        stripped = lines[j].strip()
        if not stripped or stripped.startswith("#") or lines[j][0] in " \t":
            j += 1
            continue
        break
    return j


def _find_block(lines: list[str], parent: str) -> tuple[int, int] | None:
    """定位顶层键的 ``(起始行, 块结束行)``。"""
    pat = re.compile(rf"^{re.escape(parent)}\s*:")
    for i, line in enumerate(lines):
        if pat.match(line):
            return i, _block_end(lines, i)
    return None


def _child_indent(lines: list[str], start: int, end: int) -> str:
    """取块内已有子键的缩进；没有则默认两个空格。"""
    for k in range(start + 1, end):
        s = lines[k]
        if s.strip() and not s.strip().startswith("#") and s[0] in " \t":
            return s[: len(s) - len(s.lstrip())]
    return "  "


# ---------------------------------------------------------------- 写操作


def set_top_level(lines: list[str], key: str, raw: str) -> None:
    """替换顶层标量键的值（独占整行的注释不受影响）；键不存在则追加。"""
    pat = re.compile(rf"^(\s*){re.escape(key)}(\s*:\s*).*$")
    for i, line in enumerate(lines):
        m = pat.match(line)
        if m:
            lines[i] = f"{m.group(1)}{key}: {raw}"
            return
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(f"{key}: {raw}")


def set_nested(lines: list[str], parent: str, child: str, raw: str) -> None:
    """在 parent 映射下写入/更新 child 子键，保留原有注释与缩进。"""
    blk = _find_block(lines, parent)
    if blk is None:  # 整个键不存在：追加到文件末尾
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{parent}:")
        lines.append(f"  {child}: {raw}")
        return
    start, end = blk
    inline = _split_inline(lines[start])
    if inline:
        if inline[0] in "{[":  # 流式空映射（如 course_dirs: {}）→ 展开为块式
            lines[start] = lines[start].split(":", 1)[0] + ":"
            lines.insert(start + 1, f"  {child}: {raw}")
            return
        raise ConfigError(f"{parent} 的值不是映射，无法设置子键 {child}")
    pat = re.compile(rf"^(\s+){re.escape(child)}(\s*:\s*).*$")
    for k in range(start + 1, end):
        m = pat.match(lines[k])
        if m:
            lines[k] = f"{m.group(1)}{child}: {raw}"
            return
    indent = _child_indent(lines, start, end)
    last_content = start
    for k in range(start + 1, end):
        s = lines[k].strip()
        if s and not s.startswith("#"):
            last_content = k
    lines.insert(last_content + 1, f"{indent}{child}: {raw}")


def unset_top_level(lines: list[str], key: str) -> None:
    """删除顶层键：标量整行删除；映射（含流式 ``{}``）重置为 ``{}``。"""
    blk = _find_block(lines, key)
    if blk is None:
        raise NotFoundError(f"配置里没有：{key}")
    start, end = blk
    inline = _split_inline(lines[start])
    has_children = any(
        lines[k].strip() and not lines[k].strip().startswith("#") and lines[k][0] in " \t"
        for k in range(start + 1, end)
    )
    if (inline and inline[0] in "{[") or (not inline and has_children):
        lines[start] = lines[start].split(":", 1)[0] + ": {}"
        if not inline:  # 只删内容行，保留归属下个键的注释
            for k in range(end - 1, start, -1):
                s = lines[k].strip()
                if s and not s.startswith("#"):
                    del lines[k]
        return
    del lines[start]


def unset_nested(lines: list[str], parent: str, child: str) -> None:
    """删除 ``parent.child`` 子键。"""
    blk = _find_block(lines, parent)
    if blk is None or _split_inline(lines[blk[0]]):
        raise NotFoundError(f"配置里没有：{parent}.{child}")
    start, end = blk
    for k in range(start + 1, end):
        if re.match(rf"^\s+{re.escape(child)}\s*:", lines[k]):
            del lines[k]
            return
    raise NotFoundError(f"配置里没有：{parent}.{child}")


# ---------------------------------------------------------------- 对外接口


def parse_value(raw: str) -> object:
    """把命令行传入的字符串按 YAML 标量解析，并拒绝映射（引导用点号写法）。"""
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"值不是合法的 YAML：{raw}", str(exc)) from exc
    if isinstance(parsed, dict):
        raise ConfigError(
            "set 不支持映射值；嵌套键请用点号写法",
            "例如：bb-sync config set course_dirs.CSC5010 CSC5010_AI",
        )
    return parsed


def apply_to_file(path: Path, key: str, raw: str | None) -> str:
    """在文件上执行 set（``raw`` 非空）或 unset（``raw`` 为 None）。

    返回人类可读的操作描述。文件必须已存在。
    """
    if not path.exists():
        raise ConfigError(f"配置文件不存在：{path}", "运行 bb-sync config init 生成默认配置")
    lines = path.read_text(encoding="utf-8").splitlines()

    if raw is None:
        if "." in key:
            parent, child = key.split(".", 1)
            unset_nested(lines, parent, child)
        else:
            unset_top_level(lines, key)
        action = f"已移除 {key}"
    else:
        if "." in key:
            parent, child = key.split(".", 1)
            set_nested(lines, parent, child, raw)
        else:
            set_top_level(lines, key, raw)
        action = f"{key} = {raw}"

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return action


__all__ = [
    "apply_to_file",
    "parse_value",
    "set_nested",
    "set_top_level",
    "unset_nested",
    "unset_top_level",
]
