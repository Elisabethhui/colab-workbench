# Colab 通用工作台

本仓库是一个**与具体课题无关**的 Google Colab 工作台。任何需要 GPU/TPU 的实验都放在
`projects/<项目名>/` 下，共用同一套约定、同一套脚本、同一套执行通道。

`projects/rppg/` 只是第一个已验证端到端的项目，**不要**把它的路径、字段、业务逻辑写进本文件或脚本里。

## 目录约定

```
AGENTS.md               本文件：全局规则
projects/<name>/        每个项目一个目录，互不依赖
projects/_template/     新项目模板，scripts/new-project.sh 会复制它
runs/                   执行输出与实验记录（git 忽略，不提交）
scripts/                执行与诊断脚本
```

## 运行环境（唯一事实来源：远端的 Colab VM）

**不要试图在本地复现 Colab 环境。** 本地 Python 是 3.14，Colab 是 3.13，GitHub 仓库各要各的版本，
本地对齐是纯浪费。所有执行都在远端。

- 执行通道：**Google Colab CLI**（`colab` 命令，已安装在 `~/.local/bin/colab`）
- 本机账号已授权（`colab whoami` 可验证），免费档 **T4 可用**，`--gpu T4` 不消耗 compute unit
- 远端默认工作目录 `/content`；Python 3.13；预装 torch（CUDA 可用）、node v22

## 执行规范

```bash
scripts/sessions.py                        # 预览：CLI 管理的 session + 其他客户端的 runtime
scripts/sessions.py stop <name|all>        # 清理（只对 CLI 管理的有效）
scripts/sessions.py keepalive <name> --interval 300   # 保活（免费档慎用）

scripts/colab_up.sh <session> [--gpu T4]        # 确保 session 存在（幂等）
scripts/colab_run_nb.sh projects/<name>/x.ipynb [session]   # 跑 notebook + 抽错误
```

脚本内部等价于：

```bash
colab new -s <session> [--gpu T4]     # 只在不存在时
colab exec -s <session> -f <notebook> # 逐格执行，遇首个非零 cell 停止
```

- **session 名必须是显式的**，不要省略（省略会生成随机 id，之后难以引用）
- 交互命令**只能由人执行**：`colab repl`、不带 `-- CMD` 的 `colab ssh`、`colab auth`、`colab drivemount`
- shell 命令用一次性形式：`colab ssh -s <session> -- <cmd>`，或 `echo '!<cmd>' | colab exec -s <session>`
- 用完必须 `colab stop -s <session>`；空闲的 GPU 会烧掉每日免费时长

## session 生命周期（必读）

- **Colab 按活动度保活。** CLI 是 fire-and-forget：`colab exec` 跑完就断开 websocket，
  随后**零连接、零活动 → 后端会回收 VM**（实测空闲不到 1 小时就没了）。
- 对比：VS Code 的 Colab 扩展会**保持一个常驻的 kernel 连接**，所以它的 runtime 看起来“一直都在”。
  这不是 CLI 不行，是两种客户端的连接模型不同。
- **重建成本极低**（`colab new` 约 4 秒），所以默认策略是「断了就重开」，不要花力气抢救。
- `scripts/sessions.py keepalive` 可以定时打心跳续命，但免费档靠挂机保活属于灰色地带，慎用。
- `colab sessions` 里带 `[?]` 的条目是别的客户端（如 VS Code 扩展）创建的 runtime：服务端有分配，
  但 CLI 没有它的 JWT，`stop` / `exec` / `status` 一律 not found。**这是设计，不是 bug**，用 `sessions.py list` 看它的真身。

## 环境注意事项

- `colab` 安装在 `~/.local/bin/colab`，并已软链到 `/opt/homebrew/bin/colab`。
  **原因**：VS Code 的扩展宿主不读 `.zshrc`，最小 PATH 里没有 `~/.local/bin`，
  软链之后 agent 的 bash 工具和 `@pi` RPC 子进程才稳定找得到 `colab`。换机器要重做这条软链。
- 判断 session 是否存不存在，**不要看 `colab status` 的退出码**（not found 也返回 0），
  解析 `colab sessions` 的 `[name]` 前缀，见 `scripts/sessions.py`。


`colab exec -f x.ipynb` 会把结果写成同目录的 `x_output.ipynb`。**不要整本读那个 JSON**，用：

```bash
scripts/nb_errors.py runs/<某次输出>.ipynb
```

`colab_run_nb.sh` 已经自动把 `*_output.ipynb` 移进 `runs/` 并跑完这一步，直接看它打印的 traceback。

## 环境契约：第 0 格（最重要的一条）

每个 notebook 的**第一格是环境格**，内容固定（见 `MANUAL.md` 附录 A、`projects/_template/00_template.ipynb`）：

```python
from bootstrap import setup
ctx = setup("<项目名>")   # 项目名 = config/<项目名>.toml
```

`bootstrap.setup()` 幂等地完成：挂 Drive → 判新 VM → 建目录 → 载凭据（脱敏）→ clone/pull 上游代码
→ 装依赖 + 打补丁 → 备数据（看 manifest）→ 写哨兵 → 打印硬件。

**Agent 的硬规则：**

1. **永不修改第 0 格。** 需要额外的环境动作，写进 `projects/<name>/hooks.py`，不要往第 0 格塞代码。
2. **业务格只用 `ctx.paths.*`**：`repo`（随 VM 消失）/ `data`（Drive）/ `logs` / `outputs`（Drive）/ `cache`（本地盘）。禁止裸写 `/content/...` 路径常量。
3. **新增依赖**：改 `config/<name>.toml` 的 `[deps]` 探针（只写**镜像里没有的**或**版本敏感的**包；写 `einops` 这种预装包等于没探），复杂逻辑写进 `hooks.install()`。
4. **新增数据**：只加一条 `[[data]]`（`source/slug/tag/check/count`），不要写下载代码。下载逻辑归 bootstrap。
5. **打补丁**必须带幂等标记：`_patch(...)` 用 `marker` 判「已打过」，重复执行不得改变文件。
6. 大文件 → `ctx.paths.outputs`；临时 → `ctx.paths.cache`；代码 → 只在 `ctx.paths.repo`，且不提交。

## 写 notebook 的硬规则

1. 装包用 `%pip`，不要用 `!pip`（`!pip` 装的包不作用于当前内核）——但**常规依赖应走 config，不要手装**
2. **不要把密钥写进 notebook**：凭据只来自 Drive 的 `secrets.toml`，由第 0 格注入环境变量
3. 不要用 Colab Secrets（`userdata.get`）：VS Code / CLI 通道下必抛 `Secrets can only be fetched when running from the Colab UI`
4. 长任务的产物必须落盘到 `ctx.paths.outputs`（Drive），VM 回收后 `/content` 是空的
5. 大对象不要 `print` 全量（DataFrame 用 `.head()`，图用 `display()`，数组用 shape/dtype）
6. 判断命令失败用 `subprocess.run(..., check=True)`；IPython 的 `!cmd` **不会**在非零退出时报错
7. 换硬件（CPU↔GPU）后**只重跑第 0 格**，不要改代码去适配「新环境」

## 产物归属

| 类型 | 位置 | 是否进 git |
| --- | --- | --- |
| notebook 源码（无输出） | `projects/<name>/*.ipynb` | ✅ |
| 执行输出 / traceback | `runs/` | ❌ |
| 数据集 / checkpoint / 大文件 | Google Drive（远端 `/content/drive`） | ❌ |
| 一次实验的完整记录 | `colab log -s <session> -o runs/<ts>-<name>.ipynb` | ❌ |

## 禁止

- 禁止手动编辑 `~/.config/colab-cli/sessions.json`
- 禁止把带输出的 notebook 提交进 git（装好的 `nbstripout` 会兜底剥掉）
- 禁止为了"本地能跑"去装一套与 Colab 不同的依赖树
- 禁止在 `projects/_template/` 里塞任何具体课题的内容

## 收尾检查

```bash
colab sessions           # 确认没有遗留的 active session
colab stop -s <session>  # 收工前必须
git status               # 确认没有 runs/ 或带输出的 notebook 混进提交
```
