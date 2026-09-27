# bb-sync — Blackboard 课程资源自动同步

自动登录大学 Blackboard 教学平台（ADFS SSO），抓取全部课程，
把 **课件 / 作业 / 指导(tutorial)** 下载到对应的课程文件夹，并保存公告。
支持增量同步（已下载的文件自动跳过）。

浏览器后端使用 **Steel**（独立进程、默认静默无窗口、自带 stealth 指纹伪装），
通过 CDP 与 Playwright 对接，登录态持久化在 Steel profile 里。

> **免责声明**：本项目仅供个人学习与研究使用，请自行遵守所在学校 IT 使用政策；
> 与 Blackboard Inc. 及任何高校官方均无关联。下载的课程资料版权归原作者与学校所有，
> 请勿二次分发。

## 安装

依赖管理使用 [uv](https://docs.astral.sh/uv/)（不污染系统 Python 环境）。

```powershell
# 1. 安装 uv（二选一）
choco install uv          # 或: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2a. 安装为全局 CLI 命令（推荐）
uv tool install git+https://github.com/asdukw/bb-sync
# 或本地克隆后可编辑安装（跟随本地修改）：
#   git clone https://github.com/asdukw/bb-sync && cd bb-sync
#   uv tool install -e .

# 2b. 或者不安装，源码直跑：cd bb-sync && uv sync
```

## 首次配置

所有用户配置与运行时数据统一放在 `~/.bb-sync/`（可用环境变量 `BB_SYNC_HOME` 覆盖）：

```powershell
# 1. 准备配置目录
mkdir ~/.bb-sync
Copy-Item config.yaml ~/.bb-sync/     # 源码直跑且在项目目录内运行时可跳过（自动读当前目录）
notepad ~/.bb-sync/config.yaml

# 2. 凭据：交互式录入，保存到系统钥匙串（Windows 凭据管理器 / macOS 钥匙串）
bb-sync --login

# 3. 部署 Steel 后端（仅首次，之后无需重复）
git clone --depth 1 https://github.com/steel-dev/steel-browser "$env:USERPROFILE\.bb-sync\.steel"
cd "$env:USERPROFILE\.bb-sync\.steel\api"
npm install
cd -
# 若 Steel 未装依赖会报错提示；Chrome 路径与静默开关在 .steel/api/.env 中
```

> `config.yaml` 与 `.env` 放**当前工作目录**优先于 `~/.bb-sync/`，方便多学校/多账号并存。

### 凭据存储

- **默认：系统钥匙串**（经 [keyring](https://pypi.org/project/keyring/)）：Windows 凭据管理器、
  macOS 钥匙串、Linux Secret Service。`bb-sync --login` 录入，`bb-sync --logout` 删除，
  明文不落盘。
- **兜底：`~/.bb-sync/.env`**（键名 `STUDENT_ID` / `PASSWORD`），供 CI 或无桌面环境使用；
  钥匙串的优先级高于 `.env`，两者并存时建议删除 `.env`。
- 重新录入学号/密码：再跑一次 `bb-sync --login` 即可覆盖。

## 日常使用

```powershell
bb-sync                     # 增量同步全部课程（静默后台）
bb-sync --course CSC5010    # 只同步一门课
bb-sync --dry-run           # 预览会下载什么，不实际下载
bb-sync --headed            # 需要人工过 MFA 时（弹出可见窗口）
bb-sync --version
```

源码直跑则把 `bb-sync` 换成 `uv run python sync.py`。

- 课程文件默认下载到 **用户主目录下的 `courses/`**（如 `C:\Users\<你>\courses`），
  可在 `config.yaml` 的 `root` 里改
- Steel 服务端会在需要时自动以后台进程启动（`127.0.0.1:3000`），日志在 `~/.bb-sync/.steel/steel.log`
- 登录态保存在 `~/.bb-sync/.browser-profile/steel-chrome`（持久 Chrome profile），无需每次登录
- 登录失败的调试截图/源码写入 `~/.bb-sync/debug_login.*`
- 本机回环已自动绕过系统代理，无需额外配置

## 文件落盘规则

按内容区/条目标题关键词，自动归入课程文件夹的子目录：

| 内容区/条目标题匹配关键词 | 目标目录 |
|---|---|
| assignment / homework / 作业 / hw | `assignments/` |
| tutorial / lab / 实验 / 指导 | `tutorials/` |
| 其余（lecture / 课件 / slides…） | `lectures/` |

- 课程文件夹名 = 课程代码 + 标题关键词（如 `CSC5010_AI`）；可在 `config.yaml` 的
  `course_dirs` 里显式覆盖。
- 关键词、同步范围、递归深度都在 `config.yaml` 里改。
- 公告写入课程目录的 `announcements.md`。

## 开发

```powershell
uv sync --dev                                    # 安装开发依赖（ruff / pyright）
uv run ruff check . && uv run ruff format .      # lint + 格式化
uv run pyright                                   # 类型检查
```

CI 会在每次 push/PR 时运行以上三项门禁；打 `v*` tag 会自动构建并发布 GitHub Release。

## 常见问题

- **提示用户名或密码不正确** → `STUDENT_ID` 学生须填**学号**（ADFS 登录页明确写着
  「学生：学号，教职工：邮箱前缀」），不是 NetID。
- **凭据明明对却登录失败** → 别用 `username` 这种键名：Windows 自带 `USERNAME`
  环境变量且大小写不敏感，会顶掉 `.env` 的值。本脚本直接从 `.env` 文件解析，
  已规避该问题。
- **登录卡住/需要 MFA** → 跑 `--headed`：Steel 会弹出可见窗口，脚本给你 120 秒手动完成。
- **Steel 服务端起不来** → 看 `.steel/steel.log`；确认 `.steel/api/node_modules` 已安装。
- **改了静默开关不生效** → `CHROME_HEADLESS` 在服务端启动时读取，改完 `.steel/api/.env`
  需先结束已有 Steel 进程再运行脚本。
- 调试工具：`uv run python debug_login.py` 会分步 dump 登录各阶段（URL/文本/截图）。
- `.env`、`.steel/`、`.browser-profile/` 均在 `.gitignore` 中，凭据不会外泄。

## License

[MIT](LICENSE)
