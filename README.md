# bb-sync — Blackboard 课程资源自动同步

自动登录香港中文大学（深圳）的 Blackboard，把 **课件 / 作业 / 指导** 下载到对应课程文件夹，并保存公告。重复运行会跳过已下载的文件。

> 当前仅适配 `bb.cuhk.edu.cn` 和它的 ADFS 登录流程，其他学校不能直接使用。
>
> 本项目仅供个人学习使用。请遵守学校 IT 政策；课程资料版权归原作者和学校所有，请勿二次分发。

当前文档按 **Windows PowerShell** 编写。macOS / Linux 暂未验证。

## 安装

需要：Git、Node.js 22+（含 npm）、uv、Chrome 或 Edge（Windows 自带 Edge 即可）。

在 PowerShell 中执行：

```powershell
winget install --id Git.Git -e
winget install --id OpenJS.NodeJS.LTS -e
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**重开终端**，然后安装 bb-sync：

```powershell
uv tool install git+https://github.com/asdukw/bb-sync
bb-sync doctor
```

`bb-sync doctor` 用于检查环境和依赖。刚安装时，凭据和浏览器后端可能显示“未配置”，这是正常的，继续下面的步骤即可。如果提示找不到 `bb-sync`，重开终端，或执行：

```powershell
uv tool update-shell
```

校外使用 Blackboard 时，可能需要先连接学校 VPN。

## 首次使用

1. 保存账号密码：

```powershell
bb-sync auth login
```

输入学号和密码。密码会保存到 Windows 凭据管理器，不会以明文写进配置文件。

2. 第一次同步：

```powershell
bb-sync run --headed
```

第一次运行会打开浏览器，方便完成登录、MFA 或验证码；同时会自动准备浏览器后端，可能需要几分钟。请保持网络连接，不要关闭终端。

3. 以后同步直接用：

```powershell
bb-sync run
```

## 文件下载到哪里

默认下载到 `C:\Users\<你>\courses`，每门课一个文件夹：

- 课件 → `lectures/`
- 作业 → `assignments/`
- 指导 / 实验 → `tutorials/`
- 公告 → `announcements.md`

修改默认下载目录：

```powershell
bb-sync config set root "D:/University/courses"
```

只修改本次运行的目录：

```powershell
bb-sync run --root "D:/University/courses"
```

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `bb-sync run` | 增量同步全部课程 |
| `bb-sync run --headed` | 显示浏览器，适合首次登录或 MFA |
| `bb-sync run --course CSC5010` | 只同步指定课程，可重复使用 `--course` |
| `bb-sync run --dry-run` | 只预览待下载文件，不实际下载 |
| `bb-sync course list` | 列出账号下的课程，不下载文件 |
| `bb-sync doctor` | 检查环境是否就绪 |
| `bb-sync --help` | 查看完整命令帮助 |

首次使用任意需要登录的命令时，都会自动准备浏览器后端，可能需要几分钟。

## 常见问题

- **`bb-sync` 找不到**：重开终端，或执行 `uv tool update-shell`。
- **环境检查不通过**：按 `bb-sync doctor` 的提示安装或配置缺失项。
- **第一次运行很慢**：首次需要准备浏览器后端，耐心等待并保持网络连接。
- **登录失败 / 需要 MFA**：先运行 `bb-sync auth status` 查看凭据，再用 `bb-sync run --headed` 重新登录。
- **想更换密码**：重新运行 `bb-sync auth login`。
- **配置不生效**：运行 `bb-sync config path`，确认实际生效的配置文件。
- **想更新**：`uv tool upgrade bb-sync`。
- **想卸载**：先 `bb-sync auth logout`，再执行 `uv tool uninstall bb-sync`。

建议只在个人电脑上使用。卸载软件不会自动删除已下载的课程文件；如果不再需要，请手动删除下载目录。

更多配置见 [docs/config.md](docs/config.md)。

## License

[MIT](LICENSE)
