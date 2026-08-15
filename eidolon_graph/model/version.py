"""内核版本标记。

图资产记录写入时的内核版本,加载/编辑时比对(见 docs/graph-kernel-engineering.md §4):
主版本一致即可互相加载;次版本/修订号差异仅作提示,不阻塞。
"""

KERNEL_VERSION = "0.1.0-0"


def _major(version: str) -> str:
    """提取主版本段("0.1.0-0" → "0")。格式异常时按整串比较。"""
    try:
        return version.split(".", 1)[0]
    except (AttributeError, IndexError):
        return str(version)


def compatible(recorded: str, current: str) -> bool:
    """不同内核版本间的兼容性判断:主版本一致即兼容(可加载/可编辑)。"""
    return _major(recorded) == _major(current)
