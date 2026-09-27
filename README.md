# bb-sync — Blackboard 课程资源自动同步

自动登录 Blackboard，把 **课件（lectures） / 作业（assignments） / 指导（tutorials）** 下载到对应课程文件夹，并保存公告。重复运行时会跳过已下载的文件。

> **适用范围**：当前版本仅适配香港中文大学（深圳）的 `bb.cuhk.edu.cn` 及其 ADFS 登录流程。其他学校的 Blackboard 地址和登录方式通常不同，不能直接使用。
>
> **免责声明**：本项目仅供个人学习与研究使用。请遵守学校 IT 使用政策；与 Blackboard Inc. 及任何高校官方均无关联。课程资料版权归原作者和学校所有，请勿二次分发。

## 环境要求

当前文档按 **Windows PowerShell** 编写。macOS / Linux 暂未正式验证。

| 依赖 | 要求 | 用途 |
| --- | --- | --- |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | 最新版 | 安装并运行 bb-sync，无需单独安装 Python |
| [Git](https://git-scm.com/downloads) | 可执行 `git --version` | 从 GitHub 安装 bb-sync |
| [Node.js](https://nodejs.org/) | **22 或更高版本**，包含 npm | 运行 Steel 浏览器后端 |
| 浏览器 | Google Chrome 或 Microsoft Edge | Steel 使用的浏览器内核 |
| 网络 | 可访问 Blackboard、GitHub 和 npm registry | 登录、首次部署 Steel、后续同步 |

校外访问 Blackboard 时，可能还需要先连接学校 VPN。

### 安装依赖（Windows）

```powershell
winget install --id Git.Git -e
winget install --id OpenJS.NodeJS.LTS -e
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

安装完成后**重开一个终端**，再检查：

```powershell
uv --version
git --version
node --version
npm --version
```

四条命令都应输出版本号。`node --version` 必须是 `v22` 或更高；如果提示“无法识别”，通常是 PATH 尚未生效，重新打开终端即可。

Chrome 或 Edge 安装在非默认位置时，需要设置环境变量：

```powershell
$env:CHROME_EXECUTABLE_PATH = "D:\Apps\Chrome\chrome.exe"
```

该设置只对当前终端生效。需要永久配置时，可使用系统“环境变量”设置界面。

## 安装 bb-sync

```powershell
uv tool install git+https://github.com/asdukw/bb-sync
```

如果安装后提示找不到 `bb-sync`：

1. 重开终端；或
2. 执行 `uv tool update-shell`；或
3. 检查 `uv tool list` 是否显示 `bb-sync`。

验证安装：

```powershell
bb-sync --version
bb-sync doctor
```

`bb-sync doctor` 会检查 Python、Git、Node.js、npm、浏览器、GitHub 网络、凭据状态和 Steel 后端。凭据和 Steel 在首次使用前未配置是正常的，不阻止继续。

需要更新时执行：

```powershell
uv tool upgrade bb-sync
```

## 首次使用

建议按下面的顺序执行，不要一上来直接跑普通同步。

### 1. 保存账号凭据

```powershell
bb-sync auth login
```

输入学号和密码。密码不会回显，也不以明文写入配置文件，而是保存到 Windows 凭据管理器。

### 2. 第一次同步使用有头模式

```powershell
bb-sync run --headed
```

有头模式会显示浏览器窗口，便于处理首次登录、MFA 或验证码。

第一次执行 `run` 时，bb-sync 会自动：

- 从 GitHub 下载 Steel 源码；
- 使用 npm 安装 Steel 依赖；
- 将运行文件放在 `~/.bb-sync/.steel`；
- 把安装和启动日志写在 `~/.bb-sync/.steel/steel.log`。

此过程可能需要几分钟，并且需要同时访问 GitHub 和 npm registry。`bb-sync doctor` 目前只检查 GitHub 连通性，不能代替 npm registry 检查。

### 3. 之后使用增量同步

```powershell
bb-sync run
```

登录态会保存在浏览器 profile 中；登录态失效时，bb-sync 会尝试使用系统钥匙串中的凭据重新登录。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `bb-sync run` | 增量同步全部课程 |
| `bb-sync run --course CSC5010` | 只同步指定课程，可重复使用 `--course` |
| `bb-sync run --dry-run` | 只预览待下载文件，不写入课程文件 |
| `bb-sync run --headed` | 显示浏览器窗口，适合首次登录或 MFA |
| `bb-sync run --root D:\courses` | 本次同步临时指定下载目录 |
| `bb-sync course list` | 列出账号下的课程，不下载文件 |
| `bb-sync auth status` | 查看凭据状态，账号会脱敏显示 |
| `bb-sync config path` | 显示实际生效的配置文件路径 |
| `bb-sync doctor` | 检查运行环境 |
| `bb-sync --help` | 查看完整命令帮助 |

注意：`--dry-run`、`course list` 和普通同步一样，也需要登录并启动 Steel。首次运行时，它们同样可能触发 Steel 下载和 npm 安装。

## 文件下载到哪里

- 内置默认下载根目录是用户主目录下的 `courses/`，例如 `C:\Users\<你>\courses`。
- 实际生效的配置位置请以 `bb-sync config path` 为准。
- 配置查找顺序为：`--config <路径>` > 当前目录的 `config.yaml` > `~/.bb-sync/config.yaml`。
- 修改默认下载目录：

```powershell
bb-sync config set root "D:/University/courses"
```

- 只修改本次运行目录：

```powershell
bb-sync run --root "D:/University/courses"
```

下载内容会按标题关键词归类：

| 匹配类型 | 目标目录 |
| --- | --- |
| assignment / homework / 作业 / hw | `assignments/`，有 `hwN` 时再建子目录 |
| tutorial / lab / 实验 / 指导 / recitation | `tutorials/` |
| 其余全部内容 | `lectures/` |

公告会写入每门课程目录下的 `announcements.md`。

## 配置

常用配置命令：

```powershell
bb-sync config path
bb-sync config show
bb-sync config set root "D:/University/courses"
bb-sync config get root
bb-sync config edit
```

配置优先级为：

```text
命令行参数 > BB_SYNC_* 环境变量 > 配置文件 > 内置默认值
```

`config set` 修改的是“实际生效”的那份配置。当前目录存在 `config.yaml` 时，它会优先于 `~/.bb-sync/config.yaml`。在本仓库目录内运行时，仓库根目录的 `config.yaml` 也会生效，因此请先执行 `bb-sync config path` 确认目标文件。

关键词、课程文件夹名、同步范围和递归深度等配置详见 [docs/config.md](docs/config.md)。

## 故障排查

- **`bb-sync` 找不到**：重开终端，执行 `uv tool update-shell`，然后运行 `uv tool list`。
- **环境依赖缺失**：运行 `bb-sync doctor`，按提示安装或配置 Git、Node.js、npm 和浏览器。
- **首次运行很慢或卡住**：检查 GitHub 和 npm registry 网络；国内网络可开启系统代理。详细日志见 `~/.bb-sync/.steel/steel.log`。
- **登录失败或需要 MFA**：运行 `bb-sync auth status` 查看凭据，再运行 `bb-sync run --headed`。如果之前已经启动过普通模式，先关闭残留的 Steel/Node 后端，再使用有头模式。
- **忘记修改了哪个配置**：运行 `bb-sync config path`，然后检查该文件。
- **想更换密码**：运行 `bb-sync auth login` 覆盖原凭据。
- **想查看命令参数**：直接运行 `bb-sync` 或 `bb-sync <命令> --help`。

登录失败时可能生成 `~/.bb-sync/debug_login.html` 和 `~/.bb-sync/debug_login.png`，其中可能包含账号或课程信息，不要公开分享。

## 安全与卸载

建议只在个人电脑上使用，不要在共享电脑中保存学校账号。自动化登录可能触发 MFA、风控或学校 IT 策略，请控制运行频率并遵守学校规定。

卸载软件：

```powershell
bb-sync auth logout
uv tool uninstall bb-sync
```

以下目录可能含有个人数据，删除前请确认：

- `~/.bb-sync/`：用户配置、Steel 后端、浏览器 profile、日志和调试产物；
- 当前目录的 `config.yaml`：可能覆盖用户配置；
- `~/courses/`：默认课程下载目录。

删除 `~/.bb-sync/` 不会自动删除系统钥匙串中的账号密码，需要使用 `bb-sync auth logout`。

## 脚本与开发

- 脚本、JSON 输出和退出码说明：[docs/scripting.md](docs/scripting.md)
- 开发、测试和 CI 说明：[CONTRIBUTING.md](CONTRIBUTING.md)
- 版本变更：[CHANGELOG.md](CHANGELOG.md)

## License

[MIT](LICENSE)
