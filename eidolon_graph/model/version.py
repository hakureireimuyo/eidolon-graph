"""内核版本标记。

图资产记录写入时的内核版本,加载/编辑时比对(见 docs/graph-kernel-engineering.md §4):
主版本一致即可互相加载;次版本/修订号差异仅作提示,不阻塞。

1.0.0-0:触发端口独立化(边界 1 修正)——DataIn.trigger 标记移除,触发语义由
独立 TriggerIn 端口 + 组触发策略表达;旧 0.x 资产(含快照)主版本不兼容,直接拒绝。
1.1.0-0:内置节点语义收敛——Clock 吸收 Pulse(周期源双输出面)、Timer 吸收 Delay
(倒计时器双装填面)、Output 吸收 Printer(日志回显);旧资产中的 Pulse/Delay/
Printer 类型不再存在,加载后校验报未声明类型(无迁移)。
1.2.0-0:Script 可编程节点——impl.kind="script" 内嵌 Python 脚本(声明 = 编译
产物,权威在脚本;实现映射到 NodeImpl 全能力),新增声明一致性校验。
1.3.0-0:节点域分类——NodeType.category 六值严格枚举(signal/data/source/
encapsulation/host/custom),声明必填、构造点校验;旧资产缺字段反序列化兜底
custom,向后兼容。
"""

KERNEL_VERSION = "1.3.0-0"


def _major(version: str) -> str:
    """提取主版本段("0.1.0-0" → "0")。格式异常时按整串比较。"""
    try:
        return version.split(".", 1)[0]
    except (AttributeError, IndexError):
        return str(version)


def compatible(recorded: str, current: str) -> bool:
    """不同内核版本间的兼容性判断:主版本一致即兼容(可加载/可编辑)。"""
    return _major(recorded) == _major(current)
