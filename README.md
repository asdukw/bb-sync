# bb-sync — Blackboard 课程资源自动同步

自动登录大学 Blackboard 教学平台，把 **课件(lectures) / 作业(assignments) / 指导(tutorial)** 下载到对应的课程文件夹，并保存公告。支持增量同步（已下载的文件自动跳过）。

> **免责声明**：本项目仅供个人学习与研究使用，请自行遵守所在学校 IT 使用政策；
> 与 Blackboard Inc. 及任何高校官方均无关联。下载的课程资料版权归原作者与学校所有，请勿二次分发。

## 前置条件

- [uv](https://docs.astral.sh/uv/getting-started/installation/) — Python 包管理器（**不需要单独装 Python**，uv 会自动下载并管理所需版本）

  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

- [Git](https://git-scm.com/downloads) — 安装命令会以 `git+https://` 形式从 GitHub 拉取本项目，因此系统里必须有 git
- [Node.js](https://nodejs.org/) **22+**（自带 npm）— 浏览器后端是 Node 服务，首次运行会自动用 npm 安装依赖
- 浏览器内核 — **Google Chrome 或 Microsoft Edge 均可**（自动检测，Windows 自带的 Edge 就能用）；装在非默认位置时设置环境变量 `CHROME_EXECUTABLE_PATH` 指向 chrome.exe / msedge.exe

装完后**重开一个终端**（让 PATH 生效）

## 验证

```powershell
uv --version    # 输出 uv 版本号
git --version   # 输出 git 版本号
node --version  # 输出 node 版本号
```

两条命令都能出版本号即可继续；如果提示"无法识别"，说明 PATH 还没生效，再重开一个终端试试。

## 安装

```powershell
# 安装为全局 CLI 命令
uv tool install git+https://github.com/asdukw/bb-sync

# 更新到最新版
uv tool upgrade bb-sync
```

## 日常使用

```powershell
bb-sync run                         # 增量同步全部课程
bb-sync run --course CSC5010        # 只同步一门课
bb-sync run --dry-run               # 预览会下载什么，不实际下载
bb-sync run --headed                # 有头模式（首次登录 / 需要人工过 MFA）
bb-sync course list                 # 列出账号下的课程，不下载
bb-sync config set root D:/courses  # 修改默认下载目录（永久生效）
bb-sync doctor                      # 体检：检查前置条件（git/Node/浏览器/网络等）
bb-sync --version
```

直接运行 `bb-sync`（无参数）只显示帮助，不会同步。

首次运行会提示输入学号和密码（存入系统凭据管理器，不落明文）；之后运行直接复用登录态。

### 命令一览

```
bb-sync
├── run                    执行一次同步（--dry-run / --headed / --course / --root）
├── doctor                 环境体检（--list 只列检查项 / --skip-network 跳过网络检查）
├── course list            列出账号下的课程
├── auth                   凭据管理
│   ├── login              交互式录入学号与密码
│   ├── logout             从系统钥匙串删除凭据
│   └── status             查看凭据状态（账号脱敏）
└── config                 配置读写（改文件时保留原有注释）
    ├── path               显示实际生效的配置文件路径
    ├── init               生成一份默认配置
    ├── show / get / set / unset / edit
    └── …
```

### 给脚本用

所有命令都支持 `--json`（结果走 stdout、日志走 stderr，可直接接管道），退出码也有明确语义。
详见 [docs/scripting.md](docs/scripting.md)。

## 文件下载到哪里

- 课程文件默认下载到 **用户主目录下的 `courses/`**（如 `C:\Users\<你>\courses`）
- 改默认下载目录：`bb-sync config set root <目录>`（写入 config.yaml，永久生效）；
  或每次运行时用 `bb-sync run --root <目录>` 临时指定（优先级更高）
- 按标题关键词自动归入课程文件夹的子目录：

| 标题匹配关键词                    | 目标目录         |
| --------------------------------- | ---------------- |
| assignment / homework / 作业 / hw | `assignments/` |
| tutorial / lab / 实验 / 指导      | `tutorials/`   |
| 其余（lecture / 课件 / slides…） | `lectures/`    |

- 公告写入课程目录的 `announcements.md`

## 高级配置

关键词、课程文件夹名、同步范围等都可在 `config.yaml` 里改（位于`~/.bb-sync/config.yaml` ），详见 [docs/config.md](docs/config.md)。

配置优先级：**命令行参数 > 环境变量（`BB_SYNC_*`）> 配置文件 > 内置默认值**。

## 常见问题

- **想更换或修改密码** → 跑 `bb-sync auth login` 覆盖，或 `bb-sync auth logout` 清除。
- **不确定环境缺什么** → `bb-sync doctor` 逐项体检，缺什么、怎么装都会告诉你。
- **运行异常** → 删掉 `~/.bb-sync/` 目录后重新 `bb-sync auth login` 配置即可重置。
- **记不住命令** → 直接运行 `bb-sync` 查看帮助；配置读写见 [docs/config.md](docs/config.md)。
- **首次运行卡在下载 Steel** → 需要 github.com 可访问，代理用户请确认系统代理已开启。
- **`--version` / `--help` 启动稍慢？** → 这是 Python 解释器冷启动的固有开销（本机约 0.85s）；
  命令本身只加载 typer/click，playwright、rich、pydantic、keyring **都不会**在 `--help` 时导入。

## 开发

```powershell
uv sync                # 装依赖（含 ruff / pyright / pytest）
uv run pytest          # 单元 + CLI 端到端测试（全离线，不登录不下载）
uv run ruff check .    # 静态检查
uv run ruff format .   # 格式化
uv run pyright         # 类型检查
```

测试分两层：

- `tests/test_units.py` — 纯逻辑单元测试（配置分层与行级编辑、文件名/分类推断、异常与退出码映射）
- `tests/test_cli_e2e.py` — 把整条命令链跑在进程内的端到端测试，覆盖**命令行契约、退出码语义、
  stdout/stderr 分流、配置读写**，等价于一次全量手工回归

CI（`.github/workflows/ci.yml`）在每次 push / PR 时自动跑 lint、format、类型检查与 pytest。

## License

[MIT](LICENSE)
