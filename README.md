# bb-sync — Blackboard 课程资源自动同步

自动登录大学 Blackboard 教学平台，把 **课件(lecutres) / 作业(assignments) / 指导(tutorial)** 下载到对应的课程文件夹，并保存公告。支持增量同步（已下载的文件自动跳过）。

> **免责声明**：本项目仅供个人学习与研究使用，请自行遵守所在学校 IT 使用政策；
> 与 Blackboard Inc. 及任何高校官方均无关联。下载的课程资料版权归原作者与学校所有，请勿二次分发。

## 前置条件

- [uv](https://docs.astral.sh/uv/getting-started/installation/) — Python 包管理器（**不需要单独装 Python**，uv 会自动下载并管理所需版本）

  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

- [Git](https://git-scm.com/downloads) — 安装命令会以 `git+https://` 形式从 GitHub 拉取本项目，因此系统里必须有 git；Windows 可用 `winget install --id Git.Git -e` 或直接运行官网安装包
- [Node.js](https://nodejs.org/) **22+**（自带 npm）— 浏览器后端是 Node 服务，首次运行会自动用 npm 安装依赖；`winget install OpenJS.NodeJS.LTS` 或官网安装包
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
bb-sync                     # 增量同步全部课程
bb-sync --course CSC5010    # 只同步一门课
bb-sync --root D:/courses   # 指定下载目录（默认 ~/courses）
bb-sync --dry-run           # 预览会下载什么，不实际下载
bb-sync --version
```

首次运行会提示输入学号和密码（存入系统凭据管理器，不落明文）；之后运行直接复用登录态。

## 文件下载到哪里

- 课程文件默认下载到 **用户主目录下的 `courses/`**（如 `C:\Users\<你>\courses`）
- 按标题关键词自动归入课程文件夹的子目录：

| 标题匹配关键词                    | 目标目录         |
| --------------------------------- | ---------------- |
| assignment / homework / 作业 / hw | `assignments/` |
| tutorial / lab / 实验 / 指导      | `tutorials/`   |
| 其余（lecture / 课件 / slides…） | `lectures/`    |

- 公告写入课程目录的 `announcements.md`

## 高级配置

关键词、课程文件夹名、同步范围等都可在 `config.yaml` 里改（位于`~/.bb-sync/config.yaml` ），详见 [docs/config.md](docs/config.md)。

## 常见问题

- **想更换或修改密码** → 再跑一次 `bb-sync --login` 覆盖即可。
- **运行异常** → 删掉 `~/.bb-sync/` 目录后重新 `bb-sync --login` 配置即可重置。
- **首次运行卡在下载 Steel** → 需要 github.com 可访问，代理用户请确认系统代理已开启。

## License

[MIT](LICENSE)
