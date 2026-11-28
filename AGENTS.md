# Colab 通用工作台

本仓库是一个**与具体课题无关**的 Google Colab 工作台。任何需要 GPU/TPU 的实验都放在
`projects/<项目名>/` 下，共用同一套约定、同一套脚本、同一套执行通道。

`projects/rppg/` 只是第一个已验证端到端的项目，**不要**把它的路径、字段、业务逻辑写进本文件或脚本里。

## 和人协作的方式：先说计划，再动手，边做边报

用户明确要求的两条：**看懂了才批准**，**执行中要能看到进度**。

1. **动手前先给计划**：目标、要改哪些文件、几步、有没有风险（花钱、动数据、碰摄像头/麦克风）。
   等用户明确同意再执行。用户没点头就只做只读检查。
2. **执行中每完成一步报一行**，格式固定：`[2/5] 改 X — 完成 / 证据：<命令输出或文件路径> / 下一步：Y`。
   不带证据的"完成了"不算完成。
3. **卡住立刻停**：说明卡在哪、需要用户做什么决定，不要自己换个做法继续。
4. **收尾给一张表**：完成的、没做的、为什么没做。
5. **摄像头/麦克风/个人数据**：一律先申请，得到同意才碰；临时产物用完就删，并告知用户删了什么。
6. 说话**简短直白**，别堆术语和长清单。

## 目录约定

```
AGENTS.md               本文件：全局规则
bootstrap.py            第 0 格引擎（唯一会写 Drive 的代码）
config/<name>.toml      项目声明：上游仓库 / [deps] 探针 / [[data]] 清单
projects/<name>/        一个项目 = 一个目录 = 一个 notebook + hooks.py，互不依赖
projects/_template/     新项目模板，scripts/new-project.sh 会复制它
archive/legacy/         历史 notebook，只备查，不改不跑
runs/                   执行输出与实验记录（git 忽略，不提交）
scripts/                执行与诊断脚本
```

**一个项目只有三样东西**：`<name>.ipynb`（唯一 notebook）、`hooks.py`、`README.md`。
不要再加第二个 notebook、不要加 `src/`、不要把配置写成 py 文件（配置只能放 `config/<name>.toml`）。

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

scripts/colab_run.sh -s <session> -f <脚本或notebook> [--max-wait 1800]  # 上传+执行，跑完立刻返回
scripts/wait_log.sh <日志> [--pid PID] [--max-wait 600]                 # 轮询单个日志，结束就返回
```

脚本内部等价于：

```bash
colab new -s <session> [--gpu T4]     # 只在不存在时
colab exec -s <session> -f <notebook> # 逐格执行，遇首个非零 cell 停止
```

- **session 名必须是显式的**，不要省略（省略会生成随机 id，之后难以引用）
- 交互命令**只能由人执行**：`colab repl`、不带 `-- CMD` 的 `colab ssh`、`colab drivemount`，
  以及任何停在 `Enter the authorization code:` 的授权流程
- shell 命令用一次性形式：`colab ssh -s <session> -- <cmd>`，或 `echo '!<cmd>' | colab exec -s <session>`
- 用完必须 `colab stop -s <session>`；空闲的 GPU 会烧掉每日免费时长

## 执行的时间成本（血泪教训，必须有意识地防）

实测数据（2026-11-28，一个 32 小时的会话）：**工具等待 277 分钟，其中 267 分钟是 68 次超过 60 秒的等待**，
而模型本身每轮只要 2–17 秒。慢的从来不是模型，是等待方式。六条硬规则
（那组数字是这一次会话的遥测，只能当量级参考，不是基准）：

1. **禁止 `nohup colab exec ... & sleep 300; grep 日志`。**
   远端多半 1–5 秒就结束了（常常还是秒失败），却被固定罚站 5 分钟。
   改用 `scripts/colab_run.sh -s <session> -f <file>`：上传 → 后台执行 → 轮询进程，**一结束立刻返回并打印 traceback**。
   已经手工起过的后台任务，用 `scripts/wait_log.sh <日志> --pid <pid>` 等，不要睡固定时长。
2. **秒级能抓到的错不要上远端。** `NameError` / `AttributeError` / 拼错的变量名这类，
   先在本地 `exec(compile(...))` 空跑一遍（不需要装 torch，只检查到那一行为止）。
   实测有 3 个连续迭代各花 300 秒，只为修同一个 `NameError: name 'ctx' is not defined`。
3. **授权流程只能是人工前台的一次性动作。** CLI 没有 `colab auth` 这类独立子命令：令牌失效时，
   任何一条命令自己会打印授权链接并停在 `Enter the authorization code:` 等输入。
   一个挂在 tty 上等输入的这类进程会把所有账号 API（`colab sessions` / `new` / `drivemount`）
   拖到 **60–70 秒**甚至无响应；杀掉之后同样命令回到 **1.1–1.8 秒**。
   发现卡住：`ps aux | grep colab` → `kill <pid>`。
4. **账号 API 不放进循环重试。** `colab sessions / new / drivemount` 走 `colab.research.google.com`，
   而 `upload / exec` 走已缓存的 runtime endpoint（快且稳）。循环里只用后者，前者一律套 `timeout 15`。
5. **数值等价性先拿 1 个真实样本，通过再放开全量。** 用合成图/理想输入做的对照会掩盖边界情况：
   实测用合成图判「ffmpeg 裁剪 ≈ cv2 裁剪」（差 0.3 灰阶），但真实数据里人脸框会被放大到越界
   （`y + bh = 492 > 480`），numpy 切片与 ffmpeg 的越界语义不同 → 真实差 **18.5 灰阶**，
   42 个视频白跑 893 秒才发现。规则：**先挑 1 条真实样本做像素级对照，再全量**。
6. **失败后先问「是不是同一个原因」。** 连着三次修同一个 `NameError`/`AttributeError`，
   说明缺的是上游检查（见第 2 条），不是再试一次。

配套：`scripts/colab_up.sh` 的 `session_exists` 已改成 `timeout 12 colab sessions`，超时则退回读本地
`sessions.json`（只读）判断，不再被账号 API 卡死。

判断一轮迭代是否健康：**单次工具等待 >60 秒就要问“为什么”**，并在下一步改掉等待方式，而不是重试。

## 缓存与数值等价性（钱和时间都烧在这）

1. **缓存旁边必须有可机读的元数据**：分块长度、resize、裁框系数、dtype、每个视频的 mean/std、
   repo HEAD、生成时间。没有元数据的缓存，第二次使用时无法判断它是否还可信。
2. **指纹里必须带预处理代码版本号**：`PREPROC_VERSION = N` 参与哈希，改动裁剪/缩放逻辑就 +1。
   实测项目里，修好越界 bug 后靠这个把旧缓存自动作废（指纹 `f6b37f0d2822` → `648c31d15021`）；
   否则「代码修好了但还在读旧缓存」是最难查的一类问题。
3. **指纹只由数据内容与参数决定**，与 session/VM/时间无关：同样的数据必须得到同一个指纹，
   换台机器、换个人跑，只要指纹相同就应命中同一份缓存。
4. 缓存落 Drive 前先量体积：本项目实测 **78.3 GB 原始视频 → 1.23 GB 缓存**（uint8 + mean/std，
   与上游 float64 逐位等价），新 VM 从零到可推理 = 下载 9.5 秒 + 解包 30.6 秒。

## session 生命周期（必读）

### 多 agent 共存：只碰自己的 session

`~/.config/colab-cli/sessions.json` 是**按操作系统用户共享的，不按工作区分**。
同一个用户下的其他 agent（别的 harness、另一个工作区里的脚本）建的 session 会出现在同一张表里。

1. **本仓库的 session 一律用 `rppg-` 前缀**（如 `rppg-cpu`、`rppg-gpu`），便于和别人的 `loop*` 等名字区分。
2. **只 stop 自己 `colab new` 时用过的那个名字**，停之前先在 `sessions.json` 里确认有它的记录。
   绝不按列表批量停（CLI 本来也没有 `--all`），绝不凭「看起来像我建的」下手。
3. **每个任务收尾必须停**，并在汇报里写明停掉了哪个。空闲的 GPU 会烧掉每日免费额度。
4. 看到不认识的名字，当别人的处理：只在汇报里列出来，不动它。

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

每个 notebook 的**第一格是环境格**，内容固定（见 `MANUAL.md` 附录 A、`projects/_template/template.ipynb`）：

```python
from bootstrap import setup
ctx = setup("<项目名>")   # 项目名 = config/<项目名>.toml
```

`bootstrap.setup()` 幂等地完成：挂 Drive → 判新 VM → 建目录 → 载凭据（脱敏）→ clone/pull 上游代码
→ 装依赖 + 打补丁 → 备数据（看 manifest）→ 写哨兵 → 打印硬件。

**Agent 的硬规则：**

1. **永不修改第 0 格。** 需要额外的环境动作，写进 `projects/<name>/hooks.py`，不要往第 0 格塞代码。
2. **数据只进 Drive（铁律）**：数据集/缓存/模型权重一律落 `ctx.paths.datasets`（Drive），
   **禁止**把数据集下载到 `/content`、`~`、`/tmp` 等本地盘位置。Colab 本地盘只配放
   「随 VM 生灭的代码与临时文件」。实现必须走直落模式（kaggle `output_dir` / hf `local_dir`），
   因为 Drive 不支持符号链接；仅允许压缩包在本地停留（流式解压所需的临时缓冲，解压完立即删）。
3. **业务格只用 `ctx.paths.*`**：`repo`（随 VM 消失）/ `datasets`（Drive 的 `cache/`）/ `logs` / `outputs`（Drive 的 `projects/<name>/`）/ `vmcache`（本地盘临时）。禁止裸写 `/content/...` 路径常量。
   Drive 上只有三个区，永不混用：`cache/`（外部数据原件，只有 bootstrap 写）、`projects/<name>/`（你的产物，不可重建）、`_secrets/`（密钥，只有人写）。
4. **新增依赖**：改 `config/<name>.toml` 的 `[deps]` 探针（只写**镜像里没有的**或**版本敏感的**包；写 `einops` 这种预装包等于没探），复杂逻辑写进 `hooks.install()`。
5. **新增数据**：只加一条 `[[data]]`（`source/slug/tag/check`），不要写下载代码。下载逻辑归 bootstrap。
6. **打补丁**必须带幂等标记：`_patch(...)` 用 `marker` 判「已打过」，重复执行不得改变文件。
7. 大文件 → `ctx.paths.outputs`；临时 → `ctx.paths.vmcache`；代码 → 只在 `ctx.paths.repo`，且不提交。

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
ps aux | grep colab | grep -v grep          # 确认没有挂在授权/交互输入上的进程（会拖慢所有账号 API）
git status               # 确认没有 runs/ 或带输出的 notebook 混进提交
```
