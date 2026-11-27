# Colab 工作流 · 使用说明书

> 本文件是这套工作流的**唯一说明书**。出问题先查第 7 章「故障表」。

---

## 0. 待办清单（先做完这 5 件，工作流才算通电）

| # | 谁做 | 做什么 | 做完的标志 |
|---|---|---|---|
| ① | **你** | 在 GitHub 建一个 **public** 仓库 `colab-workbench`，把这个目录推上去 | `git remote -v` 有地址，网页能打开 |
| ② | **你** | 第 0 格里的 `github.com/Elisabethhui/colab-workbench.git` 改成真实地址（只改一次） | 模板 notebook 里没有 `<你的账号>` 字样 |
| ③ | **你** | Drive 建 `MyDrive/Colab_Projects/`，把 `secrets.example.toml` 改名 `secrets.toml` 填好，放到 `MyDrive/Colab_Projects/_secrets/` | 该文件在 Drive 里能看到 |
| ④ | **你** | Kaggle 网页 → 任一数据集 → 「Copy API command」里那行 `kaggle datasets download -d X/Y`，把 `X/Y` 给我（两个数据集各一个） | `config/rppg.toml` 里的 `OWNER/SLUG_A` 变成真值 |
| ⑤ | **我** | 把仓库推到 GitHub 前跑一遍 `gitleaks detect`，确认没有密钥漏进去 | 输出 `no leaks found` |

①②③ 做完之前，第 0 格跑不通（它会 clone 失败或找不到 secrets）。这是**一次性**成本。

---

## 1. 一分钟理解这套东西

### 四条事实

```
① 代码   = GitHub（public，**钉死到某个 commit**）→ 每台 VM 重新 clone，几秒钟，拿的是同一个版本
② 数据   = Google Drive            → 一次下载，永久复用
③ 机器   = 临时 Colab VM           → 随时被回收，什么都不存
④ 环境   = 每个 notebook 的第 0 格  → 唯一入口，幂等
```

### 一句话

> **代码每次重新拿（同一个版本），数据一次不重拿。**
> 你只需要：**说需求 → 点运行 → 切内核**。

### 两个环

```
【编辑环】秒级，本地，免费
  你 ──说需求──▶ 本地 Agent（Pi / opencode）──改 .ipynb──▶ 你 review ──▶ git push

【执行环】分钟级，远端，可能被回收
  VS Code 点运行 ──▶ Colab VM ──git clone──▶ /content/rPPG-Toolbox
                        │
                        ├── 读数据 ◀── Drive (cache)
                        └── 写产物 ──▶ Drive (projects/<name>/outputs)
```

---

## 2. 名词表（只记这 4 个）

| 名词 | 是什么 | 在哪 |
|---|---|---|
| **工作流仓库** | 装 bootstrap / 配置 / notebook 的 public 仓库 | 本地 `~/Documents/Pi/colab-workbench` ↔ GitHub ↔ VM `/content/colab-workbench` |
| **上游仓库** | 被研究的项目代码（rPPG-Toolbox） | VM `/content/rPPG-Toolbox`（每次重 clone，不持久） |
| **Drive 根** | 只有它跨 VM 活着 | `MyDrive/Colab_Projects/` |
| **第 0 格** | 环境格，唯一入口 | 每个 notebook 的第一格 |

---

## 3. Drive 目录地图（只在这里建目录）

```
MyDrive/Colab_Projects/                 ← 唯一的工作根。别在 Drive 别处建目录
├── _secrets/                           ← 凭据。只有这一个地方有密钥
│   ├── secrets.toml
│   ├── kaggle.json
│   └── huggingface_token.txt
├── cache/                              ← 【区 1】外面下载来的原件。和项目无关
│   ├── kaggle/<owner>__<dataset>[@<ver>]/
│   │   ├── SOURCE.txt                  ← 人眼看的：从哪个 URL、什么时候下的
│   │   ├── .manifest.json              ← 机器看的：判"已就绪"的唯一依据
│   │   └── subject01_vid.avi ...
│   ├── huggingface/<dataset|model>/<org>__<name>[@<rev>]/
│   └── registry.jsonl                  ← 每次下载追加一行（谁、何时、多少文件）
└── projects/<name>/                    ← 【区 2】你的产物。不可重建，别乱删
    ├── logs/                           ← 运行日志
    └── outputs/                        ← 模型、图表、csv
```

### 三个区，永不混用

| 区 | 装什么 | 能重建吗 | 谁写 |
|---|---|---|---|
| `cache/` | Kaggle / HF 下载的原始数据 | 能（但很贵：这套 rPPG 要 30 GB 下载 + 半小时上传） | 只有 `bootstrap.py` |
| `projects/<name>/` | 你的日志、模型、图、csv | **不能** | 你 / agent，通过 `ctx.paths.logs`、`ctx.paths.outputs` |
| `_secrets/` | 三个密钥文件 | 不能（要去官网重发） | 只有你，手填 |

**命名规则**：数据集目录名就是"来源 + 标识"——
`kaggle/maleehanasirkhan__ubfc-rppg-part1`、`huggingface/dataset/OpenRPPG__UBFC@main`。
一看就知道是从哪下的，且永远不会和 `projects/` 混在一起。

> `MyDrive/Colab Notebooks/` 是 Colab 的默认目录，删了它会被自动重建，不用管它。

---

## 4. 日常循环（每次工作都是这 5 步）

### 4.1 开一天

| 步骤 | 你做什么 | 在哪 |
|---|---|---|
| 1 | 打开 notebook | VS Code |
| 2 | 右上角 `Select Kernel` → `Colab` → `Auto Connect` | VS Code |
| 3 | 跑第 0 格（约 40 秒，第一次；之后 1–3 秒） | VS Code |

第 0 格会自己完成：挂 Drive → clone 代码 → 判断新机器 → 装依赖打补丁 → 载凭据 → 备数据 → 打印硬件。**你不需要手动做其中任何一步。**

### 4.2 编辑（改代码）

| 你说什么 | Agent 做什么 |
|---|---|
| "第 3 格加一个 POS 的前处理消融" | 只改第 3 格，不碰第 0 格 |
| "新建项目 xxx，做 yyy" | 复制模板 → 写 `config/xxx.toml` → 写业务格 |
| "这次跑挂了，日志在 outputs 里" | 读日志 → 改对应格 |

Agent 的硬规则（写在 `AGENTS.md` 里，你可以让我随时加）：
- **永不修改第 0 格**
- 永不把凭据写进 notebook
- 大文件 → `ctx.paths.outputs`，临时 → `ctx.paths.cache`

### 4.3 运行（逐格）

按顺序点，**不要 "Run All"**。原因：业务格之间有长任务，一次跑到底你既看不到进度也不好中断。

长任务建议每格开头加一句 `print(f"[{time.strftime('%T')}] start")`，这样输出面板能当进度条。

### 4.4 换硬件（CPU ↔ GPU）— 你最关心的场景

| 你做了什么 | 系统里发生什么 | 你要做什么 |
|---|---|---|
| 切内核到 GPU | 旧 VM 销毁；`/content` 清空；**Drive 全都在** | 无 |
| 回到顶部 | — | **只重跑第 0 格** |
| 第 0 格执行 | 哨兵文件不在 → 判定新机器 → 重装依赖 ≈40 秒；数据 manifest 命中 → **0 次下载** | 等 40 秒 |
| 从第 1 格继续 | — | 点运行 |

**你不需要写任何 mount / pip / 下载代码。** 第二次连"mount 一下 Drive"都是第 0 格自己干的。

### 4.4.1 三种"重跑第 0 格"的语义

| 场景 | 哨兵 | 探针 | 实际动作 |
|---|---|---|---|
| 新 VM（切硬件 / 回收后重连） | 无 | — | 全装（≈40s） |
| 同一 VM 再跑 | 有 | 命中 | 全部跳过（1–3s） |
| 同一 VM 但 git pull 带回新依赖 | 有 | 报缺 | 只补缺的 |

### 4.5 收工

| 做完什么 | 动作 |
|---|---|
| 产物写进 `outputs/` 了 | 不用管 |
| 想省 GPU 额度 | `colab stop -s <name>`（CLI）或 VS Code `Stop` |
| **想立刻放掉 VS Code 连的那台机器** | `python3 scripts/sessions.py release <session-id>` |
| 改了 notebook | `git commit`（nbstripout 自动清输出） |

### 4.6 版本钉死：为什么每次拿的是**同一个** commit

"代码每次重新拿"容易被理解成"每次拿最新版"，那是错的。真实规则是：**每次拿 config 里
`repo_ref` 写死的那一个 commit**。

```toml
[project]
repo_url = "https://github.com/ubicomplab/rPPG-Toolbox.git"
repo_ref = "b7500b848f84ad7f86e277b4612563b69f4f88f9"   # 完整 sha，别写短 sha
```

**为什么必须钉**（不是为了洁癖，是有具体故障）：

| 不钉会发生什么 | 机制 |
|---|---|
| hooks 的 6 处源码补丁静默失效 | 补丁是字符串替换（`np.mat`→`np.asmatrix`、`multi_process_quota=16`→`2`）。上游改一个字符就匹配不上，`_patch()` 只打标记不回滚，你会得到一个"跑得起来但结果错的"环境 |
| 依赖按错版本的 requirements 装 | `hooks.install()` 生成过滤版 requirements 时读的是当时那个 commit 的文件 |
| 论文/结果无法复现 | 你不知道上次跑的到底是哪一版代码 |

**bootstrap 的实际行为**：

| 情况 | 动作 |
|---|---|
| `repo_ref` 为空 | 用默认分支最新版，打印 `⚠ 未配 repo_ref`（不推荐） |
| 已有 clone，HEAD ≠ ref | `git fetch --tags --prune` → `git checkout -f --detach <sha>` |
| 已有 clone，HEAD == ref | 什么都不做（秒过） |
| `repo_ref` 在远端找不到 | **直接抛错停下**，拒绝用别的版本糊过去 |
| checkout 后 sha 与期望不符 | 再抛一次错（钉版本这件事不允许"看起来对"） |

**升级上游版本的正确步骤**（4 步）：

1. 本地 clone 上游，`git log` 找到能跑的目标 commit，拿完整 sha
2. 改 `config/<project>.toml` 的 `repo_ref`
3. 逐个检查 `projects/<project>/hooks.py` 里的补丁字符串在新版里是否还存在
   （上游改了 API：补丁要么改写，要么删掉）
4. `WB_FORCE_INSTALL=1` 重跑第 0 格（依赖可能跟着变），然后看 Drive 上
   `projects/<project>/env.json` 里的 `repo.head` 是否已经是新的

**版本戳落在哪**：每次第 0 格跑完会写
`Drive/Colab_Projects/projects/<project>/env.json`（工作流版本、上游 commit、
Python/torch/GPU、已有数据集的 manifest 摘要）。它是**人眼可读的证据**，
每次覆盖，用来回答"上次跑的是哪个版本"。

**`env.json` 里 `repo.dirty: true` 是正常的**（rPPG 项目）：hooks 的源码补丁就是在
钉死的 commit 上改文件，所以工作区永远是 dirty 的。判断"是不是正确版本"看的是
`repo.head == repo.ref`，不是 dirty。

数据也能钉版本：`[[data]]` 里加 `version = 3` → `kagglehub.dataset_download("slug/versions/3")`；
不写就是 latest。钉了版本的目录名会带 `@v3`，和 latest 分开住，不会互相污染。

---

## 5. 新建一个项目（3 个动作）

1. **你说**：「新建项目 `xxx`，上游仓库 `<url>`，要做 `<yyy>`」
2. **Agent 做**：
   - `config/xxx.toml`（仓库地址 + 依赖探针 + `[[data]]`）
   - `projects/xxx/hooks.py`（如果这个仓库有怪癖：老 requirements、要打补丁）
   - `projects/xxx/01_xxx.ipynb`（第 0 格 `setup("xxx")` + 业务格）
3. **你跑**：Select Kernel → 第 0 格 → 业务格

不需要新建 Drive 目录（第 0 格自动 `mkdir`）。

---

## 6. 数据：怎么加一个新数据集

只改 `config/<项目>.toml` 一处，然后重跑第 0 格：

```toml
[[data]]
source = "kaggle"                    # kaggle | hf | url
slug   = "ubicomplab/ubfc-rppg"      # Kaggle 的 owner/slug
tag    = "v1"                        # 你自己起的版本号，换版本就改它
check  = "**/subject*_vid.avi"       # 就绪判定用的通配符
count  = 42                          # 期望文件数（少于这个数 → 判定未就绪，重下）
```

```toml
[[data]]
source = "hf"
repo   = "your-name/ubfc-processed"  # HF 数据集
revision = "abc1234"                 # 建议钉 sha（留空跟 main，你会后悔）
repo_type = "dataset"
```

**判定逻辑**（这就是"防重复下载"的全部实现）：

```
manifest 存在 且 count 达标  →  跳过，0 次网络请求
否则                          →  直接下载进 Drive → 写 manifest + 追加 registry.jsonl
```

**数据落点（硬规则，别改）**：数据集**只进 Google Drive**，`Colab_Projects/cache/kaggle/<owner>__<dataset>`，
`/content` 上不留任何数据集文件。

实现方式（因为 Drive 的 FUSE **不支持符号链接**，两个官方通道的默认缓存模式都会建软链、必然失败）：

| 通道 | 调用 | 为什么这么写 |
|---|---|---|
| Kaggle | `kagglehub.dataset_download(slug, output_dir=<Drive 路径>)` | `output_dir` = 直落模式，不建 `versions/` 软链 |
| Hugging Face | `snapshot_download(repo, local_dir=<Drive 路径>)` | `local_dir` = 直落模式，不建 `blobs/snapshots` 软链 |

唯一的例外是**下载途中的压缩包临时文件**：15.4G 的 `1.archive` 必须先落到本地盘才能边下边解，
解压完 `_clean_kaggle_temp()` 立刻删掉。这不叫"存在本地"，是流式传输的必经缓冲区。

**核验**：
```bash
cat "/Users/huguoqing/Library/CloudStorage/GoogleDrive-*/我的云端硬盘/Colab_Projects/cache/registry.jsonl"
```

---

## 7. 凭据：放哪、怎么用、泄漏了怎么办

### 放哪

| 东西 | 位置 | 谁能读 |
|---|---|---|
| `secrets.toml` | `MyDrive/Colab_Projects/_secrets/secrets.toml` | 你 + 你的 Google 账号能同步到的设备 |
| HF token（明文） | 只在 VM 内存（环境变量 `HF_TOKEN`） | VM 上的进程 |
| Kaggle key | `~/.kaggle/kaggle.json`（VM 本地盘） | VM 上的进程，VM 一销毁就没了 |

第 0 格只会打印**脱敏摘要**：`huggingface.token = hf_x****abcd`。

### 怎么加一个新凭据

1. 在 `secrets.toml` 里加一段（如 `[wandb] api_key = "..."`）
2. 在 `bootstrap.py::apply_secrets()` 里加 3 行 `os.environ[...] = ...`
3. 重跑第 0 格

### 泄漏了怎么办

| 类型 | 动作 |
|---|---|
| HF token 进了 public notebook | 立刻去 HF 网页 **Revoke**，换新 token 写回 Drive |
| Kaggle key 泄漏 | Kaggle → Settings → **Expire API Token** → 重新下载 `kaggle.json` |
| 怀疑整个 Drive 被人看到 | 那是 Google 账号事件，改密码 + 查 `myaccount.google.com/security` 的登录设备 |

**绝对不要**：把 `secrets.toml`、`kaggle.json`、`*.env` 放进这个仓库。`.gitignore` 已经挡住，但推之前仍然跑一次 `gitleaks detect`。

---

## 8. 故障表

| 症状 | 真正原因 | 修法 |
|---|---|---|
| 第 0 格 `git clone` 404 | ① 仓库是 private ② URL 里的 `<你的账号>` 没改 | 改成真实 public 地址 |
| `Operation not supported`（ln/mkdir） | **Drive 不支持符号链接** | 软链全部建在 `/content`（本地盘），Drive 只放真实文件 |
| `userdata.get() ... timed out. Secrets can only be fetched when running from the Colab UI` | Colab Secrets 在 VS Code/CLI 通道拿不到 | 用 Drive 的 `secrets.toml`，不要用 Colab Secrets |
| `pip install -r requirements.txt` 报找不到 numpy 1.22 | 老仓库钉死 2021 年版本，Python 3.13 没 wheel | 由 `hooks.install()` 剥版本号 + 过滤 CUDA 包，别手装 |
| `ModuleNotFoundError: cv2` 或 cv2 版本是 5.0 | Colab 预装 `opencv-python-headless 5.0`，和 rPPG 不兼容 | `hooks.install()` 里强制 4.10.0.84（先卸 4 个变体 + 删残留 `cv2/`） |
| `Unable to get resolved server information for google.colab:colab:r-xxxx` | 那台 runtime 已被回收，内核配置成了死地址 | 重新 `Select Kernel` → Colab → Auto Connect |
| `colab stop -s xxx` 说 not found，但 `colab sessions` 里还显示 | `colab status/stop` **退出码永远是 0**，孤儿 session 它管不了 | `python3 scripts/sessions.py release <id>` |
| CLI 建的 session 一小时就没了 | CLI 是 fire-and-forget，空闲即回收 | 长任务用 `scripts/sessions.py keepalive`，或干脆在 VS Code 里跑 |
| Chrome 授权页 `ERR_CONNECTION_REFUSED` | 扩展只等 **60 秒**，超时就关本地端口 | 60 秒内点完；或选 "different authentication method" 走手动贴 code |
| 依赖"看起来装过了"却没装 | 探针查了 Colab **预装**的包（如 einops 0.8.2） | 探针只查 `neurokit2` / `yacs` / `opencv` 这类**不在镜像里或版本敏感**的包 |
| 换 GPU 后一切重来 | 这是正常行为：新 VM = 空本地盘 | 重跑第 0 格（40 秒），数据不会再下载 |
| T4 上 `torch.__version__` 带 `+cpu`、`cuda.is_available()` 为 False | Colab 镜像预装的是 **CPU 版** torch wheel（不会报错，只会慢 10~20 倍） | `hooks.ensure_torch()` 自动换成 cu124/cu121 wheel；看第 0 格打 `torch 2.x + CUDA 12.x → Tesla T4` 才算对 |
| Drive 上产物是 0 字节 | 写 Drive 的句柄没关就断了 VM | 用 `with open(...)` 或 `.write_text()`；长任务分段写 |
| `repo_ref=... 在 <url> 里找不到` | 配了一个远端不存在的 commit/tag（打错、或没 push 上去、或写成了本地短 sha） | 用完整 sha；`git ls-remote <url> HEAD` 确认远端有这版 |
| 补丁突然失效 / 结果诡异 | 上游代码变了但 `repo_ref` 没钉（或被人改成最新分支），字符串替换匹配不上 | 钉回原 sha；改了补丁要一起改 |
| `AttributeError: 'Paths' object has no attribute 'cache'` | `Paths` 重构过（`datasets`=Drive 数据、`vmcache`=VM 本地），老 hooks 还在用已删除的 `.cache` | hooks 里把 `ctx.paths.cache` 改成 `ctx.paths.vmcache` |
| `colab drivemount` 报 `Error propagating: 400` | 该 Google 账号**从未**给 Colab 授过 Drive 权限；而且非交互 shell 读 `/dev/tty` 立刻 EOF，CLI 不会停下来等你点授权 | 在**人类终端**跑一次 `colab drivemount -s <session>`，点掉它弹出的浏览器授权页；成功一次后永久生效（本次已打通） |

---

## 9. 附录 A：第 0 格原文（这就是全部魔法）

```python
# ============ 第 0 格：环境（固定块，永远不要改）============
import os, subprocess, sys
from pathlib import Path

repo = Path("/content/colab-workbench")
if not repo.exists():
    subprocess.run(["git", "clone", "-q",
        "https://github.com/Elisabethhui/colab-workbench.git",
        str(repo)], check=True)
else:
    subprocess.run(["git", "-C", str(repo), "pull", "-q"], check=False)
sys.path.insert(0, str(repo))

from bootstrap import setup
ctx = setup("PROJECT_NAME")   # ← 只改这一行
```

`bootstrap.setup()` 依次做：

| 步 | 动作 | 幂等依据 |
|---|---|---|
| 1 | 挂 Drive | `MyDrive` 目录存在就跳过 |
| 2 | 判新机器 | 哨兵 `/content/.wb/<project>.ready` |
| 3 | 建目录 | `mkdir(exist_ok=True)` |
| 4 | 载凭据（脱敏打印） | 凭据文件存在才载 |
| 5 | clone / pull 上游代码 | 有 `.git` 就 pull，否则 clone |
| 6 | 装依赖 + 打补丁 | 探针按**版本**比对；补丁按**注释标记**判断（`[ok] 已打过`） |
| 7 | 备数据 | `.manifest.json` + `count` 核验 |
| 8 | 写哨兵 + 打印硬件 | — |

---

## 10. 附录 B：三个地方的文件对照

| 东西 | 本地 Mac | GitHub | Colab VM | Drive |
|---|---|---|---|---|
| bootstrap / config / notebook | ✅ 编辑 | ✅ 存 | ✅ clone 下来只读 | ❌ |
| 上游项目代码 | ❌ | ✅ 上游 | ✅ clone 到 `/content` | ❌ **不要** |
| 数据集 | ❌ | ❌ | 只读（软链视图） | ✅ 唯一副本 |
| 产物 / 日志 | ❌ | ❌ | 写到 Drive | ✅ |
| 凭据 | ❌ | ❌ | 只在内存（环境变量） | ✅ `_secrets` |

---

## 11. 附录 C：命令速查

```bash
# 列 session（区分 CLI 管理 / 其他客户端如 VS Code）
python3 scripts/sessions.py list

# 立刻释放被 VS Code 占着的那台机器
python3 scripts/sessions.py release <session-id>

# 保活（长任务期间）
python3 scripts/sessions.py keepalive <session-id>

# 新建 GPU session / 停掉
colab new -s work --gpu T4
colab stop -s work

# 看余额与配额
colab usage
```

---

## 12. 这套东西的边界（诚实的部分）

| 能做到 | 做不到 |
|---|---|
| 你在 VS Code 里改 notebook、点运行、看到图 | 真正的双向实时同步（VS Code 改了，浏览器那份不会自动变） |
| 换硬件后一条命令回到工作状态 | 免费档长期占用 GPU（会被回收） |
| 数据只下载一次 | Drive 上放代码 / 软链 / 大量小文件 |
| 凭据只手工配置一次 | 完全不用你管凭据（Drive 里仍是明文，靠 Google 账号安全） |
