# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.0] — 2026-09-28

首个正式版。架构定型、CLI 契约稳定、测试与 CI 就位。

### ⚠️ 破坏性变更

- **包结构重组**：顶层模块 `sync.py` / `steel_backend.py` / `paths.py` / `creds.py`
  全部移入 `bb_sync` 包（src 布局），控制台入口由 `sync:main` 改为 `bb_sync.cli:main`。
  已按 README 用 `uv tool install` 安装的用户，`uv tool upgrade bb-sync` 即可，
  **配置文件与已下载的课程数据位置不变，无需迁移**。
- **参数形态子命令化**：旧版挂在顶层的 `bb-sync --headed` / `--dry-run` / `--doctor`
  分别迁移为 `bb-sync run --headed`、`bb-sync run --dry-run`、`bb-sync doctor`。
- **不再支持 `.env`**：凭据一律只存系统钥匙串，明文 `.env` 兜底已移除。

### 新增

- **`--json` 全局开关**：结果以单个 JSON 文档输出到 stdout，日志走 stderr，
  可直接接管道（如 `bb-sync --json course list | jq '.[].code'`）。
  注意 `--json` 须置于子命令之前。
- **语义化退出码**（0–8）：成功 0、未分类错误 1、用法错误 2、登录失败 3、
  配置问题 4、网络失败 5、目标不存在 6、环境缺失 7、用户中断 8，
  便于在 CI / 脚本中分支处理。
- **`course list`**：列出账号下可同步的课程，不下载任何文件。
- **`auth` 命令族**：`login` / `logout` / `status`，凭据管理独立成组。
- **同步计划预览**：开始下载前打印每门课程的落盘绝对路径，便于确认写入位置。
- **`docs/scripting.md`**：脚本/CI 集成的完整说明（输出通道、JSON 结构、退出码表）。
- **测试套件**：101 例 pytest（单元 + CLI 端到端），全离线、约 1.4 秒，
  由 CI 在每次 push / PR 时自动运行。

### 变更

- **架构分层**：CLI → Command → Service → Domain → Browser，各层职责单一；
  全局选项经统一的 `Context` 传递。
- **配置体系**：Pydantic 模型校验 + 四层优先级
  （CLI 参数 > `BB_SYNC_*` 环境变量 > 配置文件 > 内置默认值）。
- **冷启动优化**：`--version` / `--help` 只加载 typer/click，
  playwright、pydantic、rich、keyring 延迟到真正使用时才导入。
- **构建后端**：setuptools（`py-modules`）→ hatchling（`src/bb_sync`）。
- **`config set` 行级写入**：修改值时保留用户手写的注释与排版。
- 调试脚本统一移入 `scripts/`。

### 修复

- 修正一处导入期路径快照：`BB_SYNC_HOME` 曾被多个模块在导入时拷贝，
  导致运行期改写在部分代码路径上失效。
- CI 冒烟测试：`doctor` 输出走 stderr，改用 `2>&1` 落盘供断言。

---

## [0.7.0] — 2026-09-27

- 新增 `bb-sync doctor` 环境体检（git / Node / npm / 浏览器 / 网络 / 凭据 / 后端）。
- CI 增加 install-smoke 冒烟任务（干净容器验证安装），并支持并发取消。

## [0.6.0] — 2026-09-27

- 浏览器内核自动回退：Chrome 优先，Edge 兜底（适配仅预装 Edge 的裸机）。

## [0.5.0] — 2026-09-27

- 课程文件夹命名改用完整关键词，不再只取首词。
- Windows 下抑制后端控制台闪窗（`CREATE_NO_WINDOW`）。
- 首次运行自动生成默认配置。

## [0.4.x] — 2026-09-27

- 新增 `--root` 下载目录参数与首次运行提示，无凭据时交互录入。
- 首次运行自动部署 Steel 后端。
- 凭据改存系统钥匙串（keyring），不再落明文。

## [0.3.0] 及更早

- 支持安装为 CLI（`uv tool install`），用户级配置目录 `~/.bb-sync/`。
- 项目初始版本：Blackboard 课程资源自动同步。

---

[1.0.0]: https://github.com/asdukw/bb-sync/releases/tag/v1.0.0
[0.7.0]: https://github.com/asdukw/bb-sync/releases/tag/v0.7.0
[0.6.0]: https://github.com/asdukw/bb-sync/releases/tag/v0.6.0
[0.5.0]: https://github.com/asdukw/bb-sync/releases/tag/v0.5.0
[0.4.x]: https://github.com/asdukw/bb-sync/releases/tag/v0.4.1
