# bb-sync — Blackboard 课程资源自动同步

自动登录大学 Blackboard 教学平台，把 **课件 / 作业 / 指导(tutorial)** 下载到对应的
课程文件夹，并保存公告。支持增量同步（已下载的文件自动跳过）。

> **免责声明**：本项目仅供个人学习与研究使用，请自行遵守所在学校 IT 使用政策；
> 与 Blackboard Inc. 及任何高校官方均无关联。下载的课程资料版权归原作者与学校所有，
> 请勿二次分发。

## 安装

依赖管理使用 [uv](https://docs.astral.sh/uv/)。

```powershell
# 1. 安装 uv（二选一）
choco install uv          # 或: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2a. 安装为全局 CLI 命令（推荐）
uv tool install git+https://github.com/asdukw/bb-sync

# 2b. 或者不安装，源码直跑：cd bb-sync && uv sync
```

## 首次配置

```powershell
# 1. 直接开跑即可：首次运行会提示默认下载目录；
#    本地没有凭据时会交互式录入（密码不回显），自动保存，之后无需重复输入
bb-sync

# 也可以先手动录入学号和密码
bb-sync --login
```

> **前置要求**：Node.js ≥ 18（`choco install nodejs`），首次运行需能访问 github.com 与
> npm registry。

## 日常使用

```powershell
bb-sync                     # 增量同步全部课程
bb-sync --course CSC5010    # 只同步一门课
bb-sync --root D:/courses   # 指定下载目录（默认 ~/courses）
bb-sync --dry-run           # 预览会下载什么，不实际下载
bb-sync --version
```

源码直跑则把 `bb-sync` 换成 `uv run python sync.py`。

## 文件下载到哪里

- 课程文件默认下载到 **用户主目录下的 `courses/`**（如 `C:\Users\<你>\courses`），
  可在 `config.yaml` 的 `root` 里改
- 按标题关键词自动归入课程文件夹的子目录：

| 标题匹配关键词                    | 目标目录         |
| --------------------------------- | ---------------- |
| assignment / homework / 作业 / hw | `assignments/` |
| tutorial / lab / 实验 / 指导      | `tutorials/`   |
| 其余（lecture / 课件 / slides…） | `lectures/`    |

- 公告写入课程目录的 `announcements.md`
- 关键词、课程文件夹名、同步范围等都可在 `config.yaml` 里改（复制源码目录的
  `config.yaml` 到 `~/.bb-sync/` 即可生效）

## 常见问题

- **提示用户名或密码不正确** → 学生须填**学号**（ADFS 登录页明确写着
  「学生：学号，教职工：邮箱前缀」），不是 NetID。
- **登录卡住/需要 MFA** → 跑 `bb-sync --headed`，在弹出的窗口里手动完成登录即可，
  之后恢复静默运行。
- **想更换或修改密码** → 再跑一次 `bb-sync --login` 覆盖即可。
- **运行异常** → 删掉 `~/.bb-sync/` 目录后重新 `bb-sync --login` 配置即可重置。

## License

[MIT](LICENSE)
