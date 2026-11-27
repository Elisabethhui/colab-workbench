# __PROJECT_NAME__

## 目标

## 怎么跑

```bash
# 在仓库根目录
./scripts/colab_up.sh __PROJECT_NAME__ --gpu T4
./scripts/colab_run_nb.sh projects/__PROJECT_NAME__/01_experiment.ipynb __PROJECT_NAME__
colab stop -s __PROJECT_NAME__
```

## 文件

| 文件 | 作用 |
| --- | --- |
| `01_experiment.ipynb` | 主 notebook（环境自检 → 配置 → 执行） |
| `requirements.txt` | 额外依赖（Colab 镜像已装的不要重复列） |
| `AGENTS.md` | 本项目给 agent 的额外规则 |

## 结果记录

| 日期 | 机器 | 关键指标 | 输出位置 |
| --- | --- | --- | --- |
