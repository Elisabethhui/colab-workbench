# colab-workbench

与课题无关的 Google Colab 通用工作台：本地编辑器 + 远端 Colab 执行。

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
scripts/                 colab_up.sh / colab_run_nb.sh / nb_errors.py / new-project.sh
```

## 常见坑

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `colab new --gpu T4` 返回 400 | 免费 GPU 每日时长用尽，或没有该加速器权限 | 等额度恢复，或退回 CPU |
| notebook 里 `drive.mount()` 卡住 | 交互授权，agent 无法完成 | 人先跑 `colab drivemount -s <session>` |
| `colab exec` 中途断开但任务像还在跑 | websocket 断了，远端内核仍存活 | `colab status -s <session>` / `colab log -s <session> -n 40`，别急着重跑 |
| `colab sessions` 出现 `[?]` 条目 | 那是别的客户端（如 VS Code 扩展）创建的 runtime，CLI 没有它的 JWT | CLI 无法接管；不用的在 Colab 网页 Manage sessions 里终止 |
