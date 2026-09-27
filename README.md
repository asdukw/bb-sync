# bb-sync — Blackboard 课程资源自动同步

bb-sync 会登录香港中文大学（深圳）的 Blackboard，把课件、作业和指导增量下载到本地，并把公告与全部课程的待办整理成 Markdown。重复运行时会跳过已经下载的文件。

- 仅适配 `bb.cuhk.edu.cn` 和它的 ADFS 登录流程。
- 当前仅支持 Windows PowerShell。
- 课程资料仅供个人学习，请勿二次分发。

## 安装

需要 Git、Node.js 22+（含 npm）、uv 和 Chrome 或 Edge。Windows 自带的 Edge 即可。

在 PowerShell 中执行：

```powershell
winget install --id Git.Git -e
winget install --id OpenJS.NodeJS.LTS -e
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**重开终端**，安装 bb-sync：

```powershell
uv tool install git+https://github.com/asdukw/bb-sync
bb-sync doctor
```

`bb-sync doctor` 用于检查环境；刚安装时凭据或浏览器后端显示“未配置”是正常的。如果提示找不到 `bb-sync`，执行 `uv tool update-shell` 后重开终端。校外使用 Blackboard 时，可能需要先连接学校 VPN。

## 首次使用

保存学号和密码：

```powershell
bb-sync auth login
```

密码会保存在 Windows 凭据管理器，不会写入配置文件。

第一次同步请显示浏览器，便于完成登录、MFA 或验证码：

```powershell
bb-sync run --headed
```

首次运行会自动准备浏览器后端，可能需要几分钟。保持网络连接，不要提前关闭终端。之后同步直接运行：

```powershell
bb-sync run
```

## 文件位置

默认下载到 `C:\Users\<你>\courses`，每门课一个文件夹：

- 课件 → `lectures/`
- 作业 → `assignments/`
- 指导 / 实验 → `tutorials/`
- 公告 → `announcements.md`
- 全部课程待办 → `due.md`（按优先级排列；没有待办时显示 Congratulations）

要修改下载目录：

```powershell
bb-sync config set root "D:/University/courses"
```

更完整的配置说明见 [docs/config.md](docs/config.md)。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `bb-sync run` | 增量同步全部课程并更新 `due.md` |
| `bb-sync run --headed` | 显示浏览器，适合首次登录或 MFA |
| `bb-sync run --course CSC5010` | 只同步指定课程，可重复使用 |
| `bb-sync run --dry-run` | 预览下载和待办，不写文件 |
| `bb-sync due` | 扫描全部课程主页的 Due / To Do，生成 `due.md` |
| `bb-sync due --course CSC5010` | 只整理指定课程的待办 |
| `bb-sync course list` | 列出账号下的课程 |
| `bb-sync doctor` | 检查环境是否就绪 |
| `bb-sync --help` | 查看完整帮助 |

## 遇到问题

- **`bb-sync` 找不到**：重开终端，或执行 `uv tool update-shell`。
- **环境检查不通过**：按 `bb-sync doctor` 的提示安装或配置缺失项。
- **第一次运行很慢**：首次需要准备浏览器后端，耐心等待。
- **登录失败或需要 MFA**：运行 `bb-sync auth status` 查看凭据，再用 `bb-sync run --headed` 重试。
- **更换密码**：重新运行 `bb-sync auth login`。

## License

[MIT](LICENSE)
