# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.3.0] — 2026-09-28

### 改进

- `bb-sync run` 在同步课程资源后，复用同一浏览器会话扫描 Due / To Do 并更新
  `<root>/due.md`；`--dry-run` 只统计待办，不写文件。
- 完整扫描后没有待办时，`due.md` 写入 `Congratulations! 🎉`；若部分课程抓取失败，
  则明确提示结果可能不完整，避免误报全部完成。

## [1.2.0] — 2026-09-28

### 新增

- 新增 `bb-sync due`：扫描每门课程主页的 Due / To Do 模块，把全部待办整理到
  `<root>/due.md`，按已逾期、今天、明天、未来 7 天、之后和日期未知排列。
- `bb-sync --json due` 输出文件路径、课程列表以及包含 ISO 截止日期的待办明细，
  方便脚本进一步排序或提醒。

### 测试

- 新增 Due 日期解析、条目规范化去重、Markdown 优先级排序和 CLI JSON 契约测试。

## [1.1.0] — 2026-09-28

### 新增

- 普通命令成功后会检查 GitHub Releases（最多每天一次），发现新版本时在 stderr
  提示执行 `uv tool upgrade bb-sync`；检查失败静默跳过，不影响命令结果，也不会
  污染 `--json` 的 stdout。
- `bb-sync config set/unset root` 默认同步更新用户级配置，避免当前目录有项目配置时，
  换目录运行仍使用旧下载目录；相对路径会转为绝对路径，可用 `--no-sync-home`
  只修改当前生效文件。

### 文档

- README 精简为面向学生的安装与使用路径；开发约定移入 `AGENTS.md` 和
  `CONTRIBUTING.md`。

### 测试

- 新增自动更新检查、缓存、禁用开关、stdout/stderr 分流，以及下载目录同步与
  `--no-sync-home` 的测试。

## [1.0.2] — 2026-09-28

### 改进

- Steel 后端源码锚定到验证过的快照（`dacea7e2`，即上游 v0.5.4-beta 之后的
  4 个修复），不再跟随上游 `main` 分支漂移；以后升级只改代码里的锚点。
- 更新 Steel 时改为「先下载、成功后再替换」：中途失败自动回滚旧版本，不会再
  出现先把能用的安装删掉、结果装不回来的情况。
- `bb-sync doctor` 在已部署时显示 Steel 快照的短版本号（无元数据的老安装维持
  原提示）。

### 修复

- 半装状态（源码在、`tsx` 依赖缺失）不再被判定为「已部署」，避免启动报错后每次
  重试都不触发重装。

### 测试

- 新增 Steel 锚点、解压目录探测、平滑更新与失败回滚等单元测试；测试套件增至
  112 例。

## [1.0.1] — 2026-09-28

### 修复

- 同步启动前检查下载根目录所在磁盘或网络共享是否可用。配置指向不存在的盘符
  （如 `D:/courses`）时，CLI 现在返回退出码 4 和明确的修复提示，不再抛出创建
  `D:\` 盘根时的 `WinError 3` 堆栈。

### 测试

- 新增下载根目录创建与磁盘不可用的单元测试；测试套件增至 103 例。

---

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

[1.3.0]: https://github.com/asdukw/bb-sync/releases/tag/v1.3.0
[1.2.0]: https://github.com/asdukw/bb-sync/releases/tag/v1.2.0
[1.1.0]: https://github.com/asdukw/bb-sync/releases/tag/v1.1.0
[1.0.2]: https://github.com/asdukw/bb-sync/releases/tag/v1.0.2
[1.0.1]: https://github.com/asdukw/bb-sync/releases/tag/v1.0.1
[1.0.0]: https://github.com/asdukw/bb-sync/releases/tag/v1.0.0
[0.7.0]: https://github.com/asdukw/bb-sync/releases/tag/v0.7.0
[0.6.0]: https://github.com/asdukw/bb-sync/releases/tag/v0.6.0
[0.5.0]: https://github.com/asdukw/bb-sync/releases/tag/v0.5.0
[0.4.x]: https://github.com/asdukw/bb-sync/releases/tag/v0.4.1
