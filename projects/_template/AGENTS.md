# 项目：__PROJECT_NAME__

继承 `../../AGENTS.md` 的全部规则。本文件只写本项目特有的东西。

## 目标

（一句话写清这个项目要产出什么）

## 输入 / 输出

| | 路径 | 说明 |
| --- | --- | --- |
| 输入数据 | `/content/drive/MyDrive/...` | （远端路径） |
| 输出产物 | `/content/drive/MyDrive/...` | （远端路径） |

## 执行

```bash
./scripts/colab_up.sh __PROJECT_NAME__ --gpu T4
./scripts/colab_run_nb.sh projects/__PROJECT_NAME__/01_experiment.ipynb __PROJECT_NAME__
```

## 本项目的特有坑

（例如：某个包必须在 import 前设置环境变量；某个步骤只能 CPU 跑；等等）
