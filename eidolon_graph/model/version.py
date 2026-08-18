"""内核版本标记。

图资产记录写入时的内核版本,加载/编辑时比对(见 docs/graph-kernel-engineering.md §4):
主版本一致即可互相加载;次版本/修订号差异仅作提示,不阻塞。

1.0.0-0:触发端口独立化(边界 1 修正)——DataIn.trigger 标记移除,触发语义由
独立 TriggerIn 端口 + 组触发策略表达;旧 0.x 资产(含快照)主版本不兼容,直接拒绝。
"""

KERNEL_VERSION = "1.0.0-0"


def _major(version: str) -> str:
    """提取主版本段("0.1.0-0" → "0")。格式异常时按整串比较。"""
    try:
        return version.split(".", 1)[0]
    except (AttributeError, IndexError):
        return str(version)


def compatible(recorded: str, current: str) -> bool:
    """不同内核版本间的兼容性判断:主版本一致即兼容(可加载/可编辑)。"""
    return _major(recorded) == _major(current)
