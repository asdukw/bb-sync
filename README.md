# bb-sync — Blackboard 课程资源自动同步

自动登录大学 Blackboard 教学平台，把 **课件 / 作业 / 指导(tutorial)** 下载到对应的课程文件夹，并保存公告。支持增量同步（已下载的文件自动跳过）。

> **免责声明**：本项目仅供个人学习与研究使用，请自行遵守所在学校 IT 使用政策；
> 与 Blackboard Inc. 及任何高校官方均无关联。下载的课程资料版权归原作者与学校所有，请勿二次分发。

## 安装

依赖管理使用 [uv](https://docs.astral.sh/uv/)。

```powershell
# 1. 安装 uv（二选一）
choco install uv

# 或
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. 安装为全局 CLI 命令
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

## License

[MIT](LICENSE)
