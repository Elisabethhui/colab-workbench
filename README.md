# colab-workbench

与课题无关的 Google Colab 通用工作台：本地编辑器 + 远端 Colab 执行。

> **先读 [`MANUAL.md`](MANUAL.md)** —— 那是唯一的使用说明书（含待办清单、故障表、目录地图）。
> 本文件只讲"为什么这样组织"和"仓库里都有什么"。

## 一分钟版

```
代码 = GitHub（public）→ 每台 VM 重新 clone
数据 = Google Drive    → 一次下载，永久复用
机器 = 临时 Colab VM   → 随时回收，什么都不存
环境 = 每个 notebook 的第 0 格（调用 bootstrap.setup()，幂等）
```

你只做三件事：**说需求 → 点运行 → 切内核**。

## 核心文件

| 文件 | 作用 |
|---|---|
| `bootstrap.py` | 第 0 格的引擎：挂 Drive / 判新 VM / 装依赖打补丁 / 载凭据 / 备数据 |
| `config/<name>.toml` | 每个项目的声明：上游仓库、依赖探针、数据集清单 |
| `projects/<name>/hooks.py` | 项目特有逻辑（老 requirements、源码补丁、数据视图） |
| `projects/_template/00_template.ipynb` | 新 notebook 的起点（第 0 格固定 + 业务格占位） |
| `scripts/sessions.py` | 列/停/保活/**释放** session（含 VS Code 占用的孤儿） |

## 为什么这样组织

- **代码在本地 git**：可 diff、可回滚，agent 能直接读
- **执行在远端 Colab**：环境永远是 Colab 那个，不需要在本地对齐 Python / CUDA / 仓库依赖
- **数据在 Drive**：跨 runtime 复用，不占 git
- **输出在 runs/**：agent 定位报错用，永不提交

## 快速开始

```bash
# 1. 新建项目（从模板复制）
./scripts/new-project.sh myproject

# 2. 起一台机器（CPU 省略 --gpu；免费 T4 用 --gpu T4）
./scripts/colab_up.sh t4 --gpu T4

# 3. 跑 notebook（自动把输出收进 runs/ 并抽出 traceback）
./scripts/colab_run_nb.sh projects/myproject/01_experiment.ipynb t4

# 4. 收工
colab stop -s t4
```

## 前置条件

```bash
uv tool install google-colab-cli   # colab 命令
colab whoami                       # 验证授权（邮箱 / scopes / 过期时间）
colab usage                        # 看 compute unit 与 usage rate
```

## 目录

```
AGENTS.md                agent 必读的全局规则（Pi / Codex / opencode 共用）
projects/_template/      新项目模板
projects/<name>/         你的项目
runs/                    执行输出（git 忽略）
scripts/                 colab_up.sh / colab_run_nb.sh / sessions.py / nb_errors.py / new-project.sh
```

## session 预览与清理

```bash
./scripts/sessions.py                  # 预览所有 runtime（分类：CLI 管理 / 其他客户端）
./scripts/sessions.py stop <name|all>  # 清理 CLI 管理的 session
./scripts/sessions.py stop all         # 全清
```

CLI 只能操作自己创建的 session（本地 `~/.config/colab-cli/sessions.json` 里存有该 runtime 的 JWT）。
VS Code 扩展创建的 runtime 会以 `[?]` 形式出现，`sessions.py` 会交叉比对 VS Code 的 Jupyter 内核缓存，
标出它的区域 / 内核 id / 最后活动时间。处理它只能去 Colab 网页 `Runtime → Manage sessions`，或在 VS Code 断开内核。

## 常见坑

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `colab new --gpu T4` 返回 400 | 免费 GPU 每日时长用尽，或没有该加速器权限 | 等额度恢复，或退回 CPU |
| notebook 里 `drive.mount()` 卡住 | 交互授权，agent 无法完成 | 人先跑 `colab drivemount -s <session>` |
| `colab exec` 中途断开但任务像还在跑 | websocket 断了，远端内核仍存活 | `colab status -s <session>` / `colab log -s <session> -n 40`，别急着重跑 |
| `colab sessions` 出现 `[?]` 条目 | 那是别的客户端（如 VS Code 扩展）创建的 runtime，CLI 没有它的 JWT | CLI 无法接管；不用的在 Colab 网页 Manage sessions 里终止 |
| CLI 的 session 一小时不到就没了 | CLI 是 fire-and-forget，跑完就断连接，Colab 按活动度回收 | 正常现象，`colab_up.sh` 重建约 4 秒；需要长活用 `sessions.py keepalive` |
| `colab: command not found`（在 VS Code 扩展 / agent 工具里） | 扩展宿主不读 `.zshrc`，最小 PATH 没有 `~/.local/bin` | 已软链到 `/opt/homebrew/bin/colab`；换机器重做一次 |
