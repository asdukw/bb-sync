# bb-sync 配置详解

`bb-sync` 的所有行为都由一份 `config.yaml` 控制。本文档介绍每个配置项的作用、默认值和常见改法。

## 配置文件在哪

- **位置**：`~/.bb-sync/config.yaml`（Windows 即 `C:\Users\<你>\.bb-sync\config.yaml`）
- **自动生成**：首次运行 `bb-sync` 时如果找不到配置，会自动生成一份默认配置到上述位置
- **优先级**：当前工作目录下的 `config.yaml` 优先于 `~/.bb-sync/` 里的（方便项目内覆盖）；
  `--config <路径>` 可以指定任意配置文件
- **环境变量**：整个运行时目录（配置、浏览器数据等）可用 `BB_SYNC_HOME` 重定向

## 配置项一览

| 配置项 | 默认值 | 作用 |
| --- | --- | --- |
| `root` | `~/courses` | 课程资源下载根目录 |
| `include` | `all` | 同步哪些课程 |
| `course_dirs` | `{}` | 课程代码 → 本地文件夹名的映射 |
| `keywords` | 见下文 | 内容归类关键词 |
| `announcements` | `true` | 是否同步公告 |
| `max_depth` | `3` | 内容子文件夹递归深度 |

## root — 下载根目录

```yaml
root: ~/courses
```

- 支持 `~`（用户主目录）和相对路径；相对路径以 **配置文件所在目录** 为基准
- 每门课会在 `root` 下建一个课程文件夹（命名规则见 `course_dirs`）
- **命令行 `--root` 参数优先于这里的值**：`bb-sync --root D:/courses`

## include — 同步范围

```yaml
include: all                # 同步全部课程

include: [CSC5010, DDA5002] # 只同步列表里的课程代码
```

- `all`：同步 Blackboard 上的所有课程
- 列表：按课程代码过滤（课程代码即标题里的 `CSC5010` 这类编号），写错的代码会被忽略
- 命令行 `--course CSC5010`（可重复多次）优先于这里的值

## course_dirs — 课程文件夹命名

```yaml
course_dirs:
  CSC5010: CSC5010_AI
  DDA5002: DDA5002_Optimization
```

- 键是课程代码，值是本地文件夹名；未列出的课程自动命名为 `课程代码_标题关键词`
  （如 `MDS5122_Deep_Learning`）
- 已经在用的目录想保持原名不变，就把它显式写进这里

## keywords — 内容归类

下载的文件按 **标题关键词** 自动归入课程文件夹下的三个子目录：

```yaml
keywords:
  assignments: ["assignment", "homework", "作业", "hw"]
  tutorials: ["tutorial", "lab", "实验", "指导", "recitation"]
  lectures: ["lecture", "课件", "讲义", "slides", "notes", "note"]
```

| 子目录 | 命中的文件归入 |
| --- | --- |
| `assignments/` | 作业类；且按 `hwN` 再建子文件夹（如 `hw1/`） |
| `tutorials/` | 实验/指导类 |
| `lectures/` | 其余全部（未命中任何关键词的兜底分类） |

- 匹配规则是「包含即命中」，大小写不敏感；匹配顺序：assignments → tutorials → lectures
- 优先看 **文件标题**，标题没命中再看 **内容区名称**，都没命中归 `lectures/`
- 想让某类文件换目录，加关键词即可，比如把 `quiz` 归入 tutorials：
  `tutorials: [..., "quiz"]`

## announcements — 公告同步

```yaml
announcements: true
```

- `true`：每门课的公告写入课程目录下的 `announcements.md`
- `false`：跳过公告抓取（公告失败不影响主流程）

## max_depth — 递归深度

```yaml
max_depth: 3
```

- 抓取内容区时，下钻子文件夹的最大层数
- `1` = 只下载内容区第一层的文件；课程内容嵌套很深时调大即可

## 完整示例

```yaml
root: D:/University/courses

include: [CSC5010, MDS5122]

course_dirs:
  CSC5010: AI
  MDS5122: DL

keywords:
  assignments: ["assignment", "homework", "作业", "hw"]
  tutorials: ["tutorial", "lab", "实验", "指导"]
  lectures: ["lecture", "课件", "slides"]

announcements: true

max_depth: 2
```

## 常见问题

- **改了配置不生效** → 确认改的是实际生效的那份：当前目录和 `~/.bb-sync/` 可能各有一份，
  优先读当前目录的。
- **`--root` 和 `root` 都写了用哪个** → 命令行 `--root` 优先。
- **恢复默认配置** → 删掉 `~/.bb-sync/config.yaml`，下次运行会自动重新生成。
