# bb-sync — Blackboard 课程资源自动同步

自动登录大学 Blackboard 教学平台（ADFS SSO），抓取全部课程，
把 **课件 / 作业 / 指导(tutorial)** 下载到对应的课程文件夹，并保存公告。
支持增量同步（已下载的文件自动跳过）。

浏览器后端使用 **Steel**（独立进程、默认静默无窗口、自带 stealth 指纹伪装），
通过 CDP 与 Playwright 对接，登录态持久化在 Steel profile 里。

> **免责声明**：本项目仅供个人学习与研究使用，请自行遵守所在学校 IT 使用政策；
> 与 Blackboard Inc. 及任何高校官方均无关联。下载的课程资料版权归原作者与学校所有，
> 请勿二次分发。

## 首次配置

依赖管理使用 [uv](https://docs.astral.sh/uv/)（不污染系统 Python 环境）：

```powershell
# 1. 安装 uv（二选一）
choco install uv          # 或: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. 克隆后进入目录，一条命令建好环境（自动创建 .venv 并装依赖）
uv sync

# 3. 复制凭据模板并填入学号和密码
Copy-Item .env.example .env
notepad .env              # STUDENT_ID 填「学号」（ADFS 提示：学生=学号，教职工=邮箱前缀）

# 4. 部署 Steel 后端（仅首次，之后无需重复）
git clone --depth 1 https://github.com/steel-dev/steel-browser .steel
cd .steel\api
npm install
cd ..\..
# 若 Steel 未装依赖会报错提示；Chrome 路径与静默开关在 .steel/api/.env 中
```

## 日常使用

```powershell
uv run python sync.py                  # 增量同步全部课程（静默后台）
uv run python sync.py --course CSC5010 # 只同步一门课
uv run python sync.py --dry-run        # 预览会下载什么，不实际下载
uv run python sync.py --headed         # 需要人工过 MFA 时（弹出可见窗口）
```

- 课程文件默认下载到 **用户主目录下的 `courses/`**（如 `C:\Users\<你>\courses`），
  可在 `config.yaml` 的 `root` 里改
- Steel 服务端会在需要时自动以后台进程启动（`127.0.0.1:3000`），日志在 `.steel/steel.log`
- 登录态保存在 `.browser-profile/steel-chrome`（持久 Chrome profile），无需每次登录
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
