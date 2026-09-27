"""输出通道：结果走 stdout，诊断走 stderr。

Unix CLI 的核心约定，本项目严格遵守：

======================  =========================  ====================
通道                    内容                       典型用法
======================  =========================  ====================
stdout                  命令的「数据结果」           ``bb-sync course list | jq '.[].code'``
stderr                  日志 / 进度 / 警告 / 错误    人读，或 ``2>/dev/null`` 丢弃
======================  =========================  ====================

:class:`Console` 是唯一的输出入口，两个职责：

1. **结果输出**（``result`` / ``table`` / ``emit``）—— 支持「人读」与「JSON」双模式。
   机器模式（``--json``）下，只有 ``emit`` 会往 stdout 吐**一个** JSON 文档，
   Rich 表格与人类可读文本全部被抑制，保证 stdout 是干净可解析的。
2. **诊断输出**（``log`` / ``warn`` / ``error`` / ``success`` / ``hint``）—— 一律走 stderr，
   且随 ``--quiet`` 降级。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Sequence
from typing import Any

from rich.console import Console as RichConsole
from rich.table import Table

_NO_COLOR_ENV = "NO_COLOR"


class Console:
    """统一输出出口。

    :param json_mode: ``--json``：stdout 只输出机器可读 JSON。
    :param quiet: ``--quiet``：抑制 stderr 上的普通日志（错误与警告仍显示）。
    :param verbose: ``--verbose``：stderr 上追加调试信息。
    """

    def __init__(self, *, json_mode: bool = False, quiet: bool = False, verbose: bool = False):
        self.json_mode = json_mode
        self.quiet = quiet
        self.verbose = verbose
        # 结果通道：人读内容可能带颜色；JSON 模式绝不带颜色
        self._out = RichConsole(
            file=sys.stdout,
            soft_wrap=True,
            no_color=json_mode,
            highlight=False,
        )
        # 诊断通道：始终 stderr，颜色随 TTY 自适应
        self._err = RichConsole(file=sys.stderr, stderr=True, soft_wrap=True, highlight=False)

    # ------------------------------------------------------------ 诊断（stderr）
    def log(self, message: str = "", *, level: str = "info") -> None:
        """常规日志 → stderr。``--quiet`` 时不显示。"""
        if self.quiet:
            return
        style = {"info": "dim", "step": "cyan"}.get(level, "dim")
        self._err.print(message, style=style, markup=False, highlight=False)

    def step(self, message: str) -> None:
        """阶段性标题 → stderr，如 ``[scan] 获取课程列表 ...``。"""
        if self.quiet:
            return
        self._err.print(message, style="bold cyan", markup=False, highlight=False)

    def success(self, message: str) -> None:
        """成功提示 → stderr，带 ✓。"""
        if self.quiet:
            return
        self._err.print(f"✓ {message}", style="green", markup=False, highlight=False)

    def warn(self, message: str) -> None:
        """警告 → stderr，``--quiet`` 时仍显示。"""
        self._err.print(f"! {message}", style="yellow", markup=False, highlight=False)

    def error(self, message: str) -> None:
        """错误 → stderr，始终显示。"""
        self._err.print(f"✗ {message}", style="bold red", markup=False, highlight=False)

    def hint(self, message: str) -> None:
        """修复建议 → stderr，缩进显示在错误下方。"""
        self._err.print(f"  → {message}", style="dim", markup=False, highlight=False)

    def debug(self, message: str) -> None:
        """调试信息 → stderr，仅 ``--verbose`` 显示。"""
        if self.verbose and not self.quiet:
            self._err.print(f"[debug] {message}", style="dim", markup=False, highlight=False)

    def rule(self, title: str = "") -> None:
        """分隔线 → stderr。"""
        if not self.quiet:
            self._err.rule(title, style="dim")

    # ------------------------------------------------------------ 结果（stdout）
    def emit(self, data: Any) -> None:
        """输出结构化结果到 stdout。

        人读模式（默认）只输出**标量**——因为 ``bb-sync run`` 的结果人类是通过
        stderr 上的汇总看的，stdout 应保持干净，避免干扰 ``$(...)`` 取值。
        容器类型（dict/list）在人读模式下**不输出**，仅在 ``--json`` 时输出，
        这样 ``--json`` 的语义就是「stdout 上有且仅有一个 JSON 文档」。
        """
        if self.json_mode:
            self._write_json(data)
            return
        if isinstance(data, (str, int, float, bool)):
            self._out.print(str(data), markup=False, highlight=False)

    def _write_json(self, data: Any) -> None:
        """stdout 上输出单个 JSON 文档，UTF-8 原样保留中文。"""
        text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write(text + "\n")
        sys.stdout.flush()

    def table(
        self,
        columns: Sequence[str],
        rows: Iterable[Sequence[Any]],
        *,
        title: str = "",
        json_data: Any = None,
    ) -> None:
        """表格输出。

        ``--json`` 模式下抑制表格，改为输出 ``json_data``（缺省时把 rows 转成
        以 columns 为键的记录列表），保证脚本拿到的是稳定结构。
        """
        rows = list(rows)
        if self.json_mode:
            if json_data is None:
                json_data = [dict(zip(columns, r, strict=False)) for r in rows]
            self._write_json(json_data)
            return
        table = Table(title=title or None, header_style="bold", box=None, pad_edge=False)
        for col in columns:
            table.add_column(str(col))
        for row in rows:
            table.add_row(*[str(c) for c in row])
        # 表格本身是「人读结果」，所以走 stdout
        self._out.print(table if table.row_count else "", markup=False)

    def print_raw(self, text: str) -> None:
        """逐字输出到 stdout（不加样式），用于 ``config show`` 之类。"""
        self._out.print(text, markup=False, highlight=False, soft_wrap=True)


__all__ = ["Console"]
