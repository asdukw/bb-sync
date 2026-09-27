# 在脚本 / CI 中使用 bb-sync

bb-sync 按 Unix CLI 惯例严格分流输出：**结果走 stdout，诊断走 stderr**。
这让它可以安全地接进管道、被脚本捕获。

## 输出通道

| 通道 | 内容 | 例子 |
| --- | --- | --- |
| stdout | 命令的「数据结果」 | `bb-sync course list \| jq '.[].code'` |
| stderr | 日志 / 进度 / 警告 / 错误 | 人读，或用 `2>/dev/null` 丢弃 |

> 自动更新检查的提示也走 stderr，不会污染 stdout；`--quiet` 下会跳过检查。

## `--json`：机器可读输出

`--json` 是**全局选项**，位置在子命令之前：

```bash
bb-sync --json <命令> [参数...]
```

加 `--json` 后，stdout 上**有且仅有一个 JSON 文档**（无日志混入），可直接交给
`jq` / `python -m json.tool` 解析：

```bash
bb-sync --json course list | jq '.[].code'
bb-sync --json run --dry-run | jq '.would_download'
bb-sync --json auth status  | jq -r '.account'
bb-sync --json doctor       | jq '.ok'
```

### 各命令的 JSON 结构

| 命令 | stdout 的 JSON |
| --- | --- |
| `course list` | 数组：`[{"id","code","title","folder"}, ...]` |
| `run` | 对象：`{"downloaded","exists","would_download","empty","failed","courses"}` |
| `due` | 对象：`{"path","total","failed","courses","items"}`；`items` 含课程、标题与 ISO 日期 |
| `doctor` | 对象：`{"ok": bool, "checks": [{"label","ok","detail","hint","required"}]}` |
| `doctor --list` | 对象：`{"checks": ["python","git", ...]}` |
| `auth status` | 对象：`{"configured","source","account"}`（account 已脱敏） |
| `config show` | 对象：整份配置（等价于解析后的 config.yaml） |
| `config get <key>` | 该项的值（标量 / 列表 / 对象，视键而定） |
| `config set/unset` | 对象：`{"path","action","key","value"?,"synced_path"?}`（同步 root 时有 `synced_path`） |
| `config init` | 对象：`{"path","created": bool}` |
| `config path` | 纯文本路径（此命令恒为文本，不带 JSON 包装） |

### fail 时 stdout 保持为空

出错时 stdout **不会**输出半个 JSON 文档（保持为空、便于脚本判断），
错误信息走 stderr 并以非零退出码结束。所以「先看退出码、再读 stdout」是最稳的写法：

```bash
if out=$(bb-sync --json course list 2>/dev/null); then
    echo "$out" | jq -r '.[].code'
else
    echo "列课失败（退出码 $?），请检查登录/网络" >&2
fi
```

## 退出码

退出码有明确语义，便于在 CI / 脚本里分支：

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | 未分类错误 |
| 2 | 命令行用法错误 |
| 3 | 登录 / 凭据失败 |
| 4 | 配置文件问题 |
| 5 | 网络 / 代理 / 下载失败 |
| 6 | 目标不存在（课程、配置键等） |
| 7 | 环境依赖缺失（Node / 浏览器 / Steel） |
| 8 | 用户中断 |

> 未捕获的意外异常统一归为 1，并会把堆栈打到 stderr。

### 在脚本里分支

```bash
bb-sync run
case $? in
    0) echo "同步完成" ;;
    3) echo "需要先 bb-sync auth login" >&2 ;;
    5) echo "网络/代理异常，检查 Clash 是否开启" >&2 ;;
    7) echo "缺 Node/浏览器，先跑 bb-sync doctor" >&2 ;;
    *) echo "其它错误" >&2 ;;
esac
```

## 退出码速查（实操）

| 场景 | 命令 | 退出码 |
| --- | --- | --- |
| 正常运行 | `bb-sync --version` | 0 |
| 无参数（显示帮助） | `bb-sync` | 0 |
| 未知命令 | `bb-sync nonsense` | 2 |
| 非法参数 | `bb-sync run --bogus` | 2 |
| 配置文件不存在 | `bb-sync --config /nope/a.yaml config get root` | 4 |
| 配置键不存在 | `bb-sync config get no_such_key` | 6 |
| 删除不存在的键 | `bb-sync config unset no_such_key` | 6 |

## CI 示例

一个「用 dry-run 做同步预检」的片段：

```yaml
- name: 预检（不实际下载）
  run: |
    bb-sync doctor --skip-network
    pending=$(bb-sync --json run --dry-run | jq '.would_download')
    echo "待下载 $pending 个文件"
```

> **提示**：`bb-sync run` 的诊断信息（含「同步计划」）全部走 stderr，
> 所以 `--json` 模式下 stdout 依然干净，可以放心重定向。
>
> 正常模式下 `bb-sync run` 还会生成或更新 `<root>/due.md`；`--dry-run` 只统计待办，
> 不会写文件。

## 相关

- 命令与参数总览：`bb-sync <命令> --help`
- 配置项说明：[config.md](config.md)
