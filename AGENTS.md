# AGENTS.md

本文件给 AI 编码代理提供在 bb-sync 仓库中工作的必要上下文。
人类文档见 [README.md](README.md)（安装使用）、[CONTRIBUTING.md](CONTRIBUTING.md)（开发流程）、
[docs/](docs/)（配置与脚本化）。

## 项目速览

- **是什么**：Blackboard 课程资源自动同步 CLI。登录香港中文大学（深圳）的 `bb.cuhk.edu.cn`
  （ADFS SSO），把课件 / 作业 / 指导增量下载到课程目录，公告保存为 Markdown。
- **仅适配** `bb.cuhk.edu.cn` 及其 ADFS 登录流程；不要为其他站点引入通用化假设。
- **目标平台**：Windows 10/11 与 macOS（Intel / Apple Silicon）；文档示例需覆盖
  PowerShell 与 zsh / bash。
- **技术栈**：Python ≥3.11；Typer + Click（CLI）、pydantic v2（配置）、PyYAML、rich（输出）、
  keyring（凭据）、Playwright（浏览器自动化）。
- **浏览器后端**：Steel——本地 Node ≥22 服务，通过 CDP 接入，Chrome profile 持久化登录态。
- **打包**：hatchling，`src/` 布局，入口 `bb-sync = bb_sync.cli:main`。
- **语言**：代码注释、docstring、提交信息、用户文档一律中文。安装与路径示例同时覆盖
  Windows PowerShell 和 macOS zsh / bash。

## 目录导航

| 路径 | 职责 |
| --- | --- |
| `src/bb_sync/cli.py` | 入口：Typer/Click 组装、全局选项、统一异常兜底与退出码 |
| `src/bb_sync/context.py` | 全局 Context（`--json/--quiet/--verbose/--config`），惰性导入 |
| `src/bb_sync/paths.py` | `BB_SYNC_HOME`（默认 `~/.bb-sync`）与配置文件查找 |
| `src/bb_sync/creds.py` | keyring 凭据读写 |
| `src/bb_sync/commands/` | 子命令：run / doctor / config / auth / course |
| `src/bb_sync/core/` | config（分层加载）、config_store（行级 YAML 编辑）、service（同步编排）、doctor、output（Console）、errors（退出码） |
| `src/bb_sync/blackboard/` | login（ADFS SSO）、scraper（列表 / 内容抓取）、downloader、models |
| `src/bb_sync/browser/steel.py` | Steel 后端定位 / 安装 / 启动、CDP 会话、持久 profile |
| `tests/` | 全部离线：`test_units.py`（纯逻辑）+ `test_cli_e2e.py`（进程内 CLI 契约） |
| `scripts/debug_*.py` | 手工调试脚本，会真实登录 / 访问站点；不属于测试，不要在 CI 或非必要时运行 |

## 环境与常用命令

```shell
uv sync                    # 安装依赖（含 dev 组）
uv run bb-sync --help      # 从源码运行 CLI
uv run pytest              # 单元 + CLI 端到端（默认全离线）
uv run pytest tests/test_units.py -k <pattern>   # 跑子集
uv run ruff check .        # 静态检查
uv run ruff format .       # 格式化（检查用 --check）
uv run pyright             # 类型检查
uv run bb-sync doctor      # 环境体检（触网；诊断走 stderr）
```

以上命令在 Windows PowerShell 与 macOS zsh / bash 中相同。macOS 前置依赖可用
`brew install git node uv` 安装。

只有真实运行 `run` / `course list` / `doctor`（未加 `--skip-network`）才需要 Node ≥22、
Chrome 或 Edge 和真实凭据；测试与质量检查完全离线。

### 提交前质量门禁（改动必须全绿）

```shell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

涉及 CLI 契约、配置或输出通道的改动必须跑完整四件套；纯文档改动至少自查命令示例。

## 必须遵守的架构约定

### 1. 输出通道：stdout 只放结果，stderr 只放诊断

- 所有输出走 `core.output.Console`，**禁止直接 `print`**。
- `--json` 时 stdout 有且仅有一个 JSON 文档；出错时 stdout 保持为空，错误走 stderr。
- 新增命令或字段时，同步更新 `docs/scripting.md` 的 JSON 结构表与对应测试。

### 2. 退出码语义化

可预期错误一律抛 `BbSyncError` 子类（`core/errors.py`），由自带 `exit_code` 决定进程退出码：
0 成功、1 未分类、2 用法错误、3 登录 / 凭据、4 配置、5 网络、6 目标不存在、7 环境依赖、8 中断。
不要用裸 `sys.exit(1)` 或临时整数代替错误类型。

### 3. 冷启动：入口与 Context 不做重量级顶层导入

`cli.py` / `context.py` / `commands/__init__.py` 顶层不得导入 playwright、pydantic、rich 等，
命令模块按需延迟导入（`LazyGroup`），保证 `--version` / `--help` 快速返回。
`tests/test_cli_e2e.py` 有对应回归防线，不要绕过。

### 4. 配置：四层优先级 + 注释保留

CLI 参数 > `BB_SYNC_<KEY>` 环境变量 > config.yaml > 内置默认。
配置文件查找：CWD 优先，其次 `BB_SYNC_HOME/config.yaml`。
`config set/unset` 通过 `core/config_store.py` 做**行级编辑**，必须保留注释与未知键；
不要改成整份 YAML 重写。路径常量在 import 时快照，测试靠 monkeypatch 覆盖。

### 5. Steel 后端锚定快照

`browser/steel.py` 的 `STEEL_REF` 固定到验证过的 commit，不追随上游 `main`；
升级方式 = 改 `STEEL_REF` 并在 `CHANGELOG.md` 记录。
Steel 安装目录只能是 `BB_SYNC_HOME/.steel`，任何情况下不读写用户家目录的 `~/.steel`。

## 测试要求

- 默认测试套件必须离线：不登录、不下载、不起浏览器、不碰真实钥匙串。
  `tests/conftest.py` 自动隔离 `BB_SYNC_HOME`、CWD、keyring 与 Steel 路径——
  新测试直接依赖既有 fixture（`cli`、`config_file` 等），不要连接真实环境。
- 新行为要带测试：纯逻辑进 `tests/test_units.py`，CLI 契约 / 退出码 / 输出分流进 `tests/test_cli_e2e.py`。
- 真实站点逻辑（登录、抓取、下载）无法离线覆盖时，在提交信息或 PR 里写清可重复的手工验证步骤。

## 代码风格

- ruff（line-length 100、py311、isort 的 first-party 为 `bb_sync`）+ ruff format；pyright standard 零报错。
- 注释 / docstring 用中文解释职责与「为什么」；模块 docstring 说明关键约束。
- 全面类型标注，模块头用 `from __future__ import annotations`；面向用户的错误信息要带可操作 hint。

## 提交与发布

- 提交信息中文，格式 `feat|fix|docs|refactor|test|chore(范围): 摘要`；一个提交只做一件事。
- 只暂存本次相关文件；绝不提交密钥、`.env`、浏览器 profile、课程文件、构建产物（见 `.gitignore`）。
- 版本号在 `pyproject.toml`；打 `v*` tag 触发 `.github/workflows/release.yml`，
  发布会校验 wheel 文件名与 tag 一致，并同步更新 `CHANGELOG.md`。
- CI（`.github/workflows/ci.yml`）：Ruff lint / format、Pyright、pytest、干净容器安装冒烟。

## 常见坑

- 冷启动回归用例会先清掉 `bb_sync.commands.*` 再断言 `--help` 不导入它们；
  新增 help 逻辑时不要触发真实导入。
- `bb-sync doctor` 的诊断输出走 stderr，stdout 只留给 `--json`；断言输出时先确认通道。
- GitHub Actions 的 run 默认 `set -e`，断言非零退出码需用 `|| true` 兜住（见 `ci.yml` 注释）。
- Windows 与 macOS 是主要目标平台，CI 同时跑 Ubuntu、macOS 与 Windows；路径、后台进程和
  浏览器探测相关改动两个平台都要考虑。
- 首次准备 Steel 需从 GitHub / npm 拉依赖，国内网络慢时可临时用代理或镜像，不要写永久全局配置。
- 下载目录默认 `~/courses`，课程资料有版权；禁止提交或外传 `courses/`、`.workbuddy/` 内容。
