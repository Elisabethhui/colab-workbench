"""项目钩子：bootstrap 在固定时点调用这里的函数（没有就跳过）。

四个可选函数，按需实现，别写别的：
    probe(cfg)              -> dict    自定义"这台机器吃过饭没"的探针
    install(ctx)            -> None     装依赖（bootstrap 已按 config 的 [deps] 装过一次）
    patch(ctx)              -> None     给上游代码打补丁（必须幂等：已打过就跳过）
    prepare(ctx)            -> None     数据下载完之后的后处理（拼视图、校验、切分）

ctx 上有：ctx.project / ctx.cfg / ctx.paths（repo=代码, datasets=Drive数据, logs, outputs）
"""


def install(ctx):
    pass


def patch(ctx):
    pass


def prepare(ctx):
    pass
