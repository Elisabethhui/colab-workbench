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

## 读错误（省 token）

`colab exec -f x.ipynb` 会把结果写成同目录的 `x_output.ipynb`。**不要整本读那个 JSON**，用：

```bash
scripts/nb_errors.py runs/<某次输出>.ipynb
```

`colab_run_nb.sh` 已经自动把 `*_output.ipynb` 移进 `runs/` 并跑完这一步，直接看它打印的 traceback。

## 写 notebook 的硬规则

1. **一份 notebook，本地与 Colab 都能跑**：路径必须走 `RUN_ENV` / `os.environ` 判断，禁止裸写 `/content`
2. 装包用 `%pip`，不要用 `!pip`（`!pip` 装的包不作用于当前内核）
3. **不要把密钥写进 notebook**：用 `userdata.get('KEY')` 或 `os.environ`
4. 每个 notebook 顶部保留一格「环境自检」（Python 版本、GPU、关键包版本、磁盘），新增依赖要同步加进去
5. 长任务的产物必须落盘到 Drive 或下载回来，VM 回收后 `/content` 是空的
6. 大对象不要 `print` 全量（DataFrame 用 `.head()`，图用 `display()`，数组用 shape/dtype）
7. 判断命令失败用 `subprocess.run(..., check=True)`；IPython 的 `!cmd` **不会**在非零退出时报错

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
