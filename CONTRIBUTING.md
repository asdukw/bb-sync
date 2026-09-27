# 参与开发

本文面向修改 bb-sync 源码的开发者。普通安装与使用请先阅读 [README.md](README.md)。

## 开发环境

需要：

- Python 3.11 或更高版本；
- [uv](https://docs.astral.sh/uv/)；
- Git；
- Node.js 22+ 与 npm（运行 Steel 集成测试时可能需要）。

初始化并安装开发依赖：

```powershell
uv sync
```

## 常用检查

```powershell
uv run pytest          # 单元测试 + CLI 端到端测试（默认全离线）
uv run ruff check .    # 静态检查
uv run ruff format .   # 格式化
uv run pyright         # 类型检查
```

提交前至少运行一次能覆盖改动的测试；涉及 CLI 契约、配置或输出通道时，应运行完整测试：

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

## 测试分层

- `tests/test_units.py`：纯逻辑单元测试，覆盖配置分层与行级编辑、文件名和分类推断、异常与退出码映射等。
- `tests/test_cli_e2e.py`：把完整命令链跑在进程内的端到端测试，覆盖命令行契约、退出码、stdout/stderr 分流和配置读写。

测试环境通过 `tests/conftest.py` 隔离 `BB_SYNC_HOME`、工作目录和系统钥匙串，不应访问真实网站、下载文件或读取真实凭据。

## CI

`.github/workflows/ci.yml` 在 push 和 PR 时运行：

- Ruff lint；
- Ruff format check；
- Pyright；
- pytest；
- 干净容器中的安装冒烟测试。

启动 Steel、登录 Blackboard 和真实下载不属于默认离线测试范围。修改相关逻辑时，请在本地补充可重复的手工验证步骤。

## 提交习惯

- 提交信息使用 `feat|fix|docs|refactor|test|chore(范围): 摘要`；
- 一个提交只处理一件事；
- 不提交密钥、`.env`、浏览器 profile、课程文件、构建产物或其他生成物；
- 只暂存本次改动相关的文件。
