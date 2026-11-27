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
① 代码   = GitHub（public）        → 每台 VM 重新 clone，几秒钟
② 数据   = Google Drive            → 一次下载，永久复用
③ 机器   = 临时 Colab VM           → 随时被回收，什么都不存
④ 环境   = 每个 notebook 的第 0 格  → 唯一入口，幂等
```

### 一句话

> **代码每次重新拿，数据一次不重拿。**
> 你只需要：**说需求 → 点运行 → 切内核**。

### 两个环

```
【编辑环】秒级，本地，免费
  你 ──说需求──▶ 本地 Agent（Pi / opencode）──改 .ipynb──▶ 你 review ──▶ git push

【执行环】分钟级，远端，可能被回收
  VS Code 点运行 ──▶ Colab VM ──git clone──▶ /content/rPPG-Toolbox
                        │
                        ├── 读数据 ◀── Drive (_data)
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
MyDrive/Colab_Projects/
├── _secrets/
│   └── secrets.toml              ← HF token / Kaggle key（不进 git）
├── _data/                        ← bootstrap 自动管理，你不用手建
│   ├── kaggle/<owner>__<slug>@<tag>/
│   │   └── .manifest.json        ← 判"已就绪"的唯一依据
│   ├── hf/<org>__<repo>@<sha>/
│   └── registry.jsonl            ← 每次下载追加一行（谁、何时、多少文件）
└── projects/
    └── rppg/
        ├── logs/                 ← 运行日志（appendix）
        └── outputs/              ← 模型、图表、csv（要留的东西）
```

**规则：只往 `_data`（由 bootstrap 管）和 `projects/<name>/{logs,outputs}`（你/agent 管）里写。别在 Drive 根目录乱建。**

> 历史遗留：`MyDrive/rppg_project/` 是旧版本布局，确认 `Colab_Projects/` 跑通后可以整个删掉。

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
否则                          →  下载到本地暂存 → 搬进 Drive → 写 manifest + 追加 registry.jsonl
```

**为什么下载要先落本地盘再搬？** Kaggle 直接解压到 Drive 时，42 个视频几千次小写入，在 FUSE 上会慢到怀疑人生甚至中断。

**核验**：
```bash
cat "/Users/huguoqing/Library/CloudStorage/GoogleDrive-*/My Drive/Colab_Projects/_data/registry.jsonl"
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
| Drive 上产物是 0 字节 | 写 Drive 的句柄没关就断了 VM | 用 `with open(...)` 或 `.write_text()`；长任务分段写 |

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
| 凭据 | ❌ | ❌ | 内存 + `~/.kaggle` | ✅ `_secrets` |

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
