"""内置节点库:一节点一文件,全部节点归属内核(运行时不缺节点)。

Clock / Counter / Comparator / AND / OR / NOT / Switch / Latch / Timer /
Delay / Buffer / Pulse / Threshold / Printer / Random / Simulate / Join / Output / Input。

- 全部是**普通节点类型资产**(运行时对它们零特殊处理)——它们同时是节点协议
  的自证与编辑器的基础元件;
- 一节点一文件:新节点照此格式单独建文件并在本模块登记;
- 特殊节点(Output 日志输出 / Input 手动输入)同样在内核实现,展示对接由
  宿主做特殊处理(编辑器读 Output 状态喂控制台、为 Input 渲染输入栏);
- 领域节点(LLM / Context Compiler / 工具等)后续照此登记进内核节点库。

约定:
- 数据节点(无控制输出)不触碰信号:输入信号屏蔽由引擎旁路,输出信号由自动传导;
- 信号节点(有控制输出)显式写信号电平:AND/OR/NOT/Latch/Timer/Threshold 等;
- 门控/熔断全部由运行时拦截,实现者无感知;
- 处理不了的非 None 输入 → 抛异常,走引擎异常策略(不产出 + 日志 + 熔断)。
"""

from __future__ import annotations

from ...model import AssetLibrary, NodeType
from ..protocol import NodeImpl
from ..registry import NodeRegistry
from .and_node import AND_NODE, AndImpl
from .buffer import BUFFER, BufferImpl
from .clock import CLOCK, ClockImpl
from .comparator import COMPARATOR, ComparatorImpl
from .counter import COUNTER, CounterImpl
from .delay import DELAY, DelayImpl
from .input import INPUT, InputImpl
from .join import JOIN, JoinImpl
from .latch import LATCH, LatchImpl
from .not_node import NOT_NODE, NotImpl
from .or_node import OR_NODE, OrImpl
from .output import OUTPUT, OutputImpl
from .printer import PRINTER, PrinterImpl
from .pulse import PULSE, PulseImpl
from .random import RANDOM, RandomImpl
from .simulate import SIMULATE, SimulateImpl
from .switch import SWITCH, SwitchImpl
from .threshold import THRESHOLD, ThresholdImpl
from .timer import TIMER, TimerImpl

_BUILTINS: list[tuple[NodeType, type[NodeImpl]]] = [
    (CLOCK, ClockImpl),
    (COUNTER, CounterImpl),
    (THRESHOLD, ThresholdImpl),
    (COMPARATOR, ComparatorImpl),
    (AND_NODE, AndImpl),
    (OR_NODE, OrImpl),
    (NOT_NODE, NotImpl),
    (SWITCH, SwitchImpl),
    (LATCH, LatchImpl),
    (TIMER, TimerImpl),
    (DELAY, DelayImpl),
    (BUFFER, BufferImpl),
    (PULSE, PulseImpl),
    (PRINTER, PrinterImpl),
    (RANDOM, RandomImpl),
    (SIMULATE, SimulateImpl),
    (JOIN, JoinImpl),
    (OUTPUT, OutputImpl),
    (INPUT, InputImpl),
]


def register_builtins(lib: AssetLibrary, registry: NodeRegistry) -> None:
    """把内置节点类型资产与代码实现注册进资产库与实现表(构造 World 前调用)。"""
    for nt, impl in _BUILTINS:
        if nt.name not in lib.node_types:
            lib.add_node_type(nt)
        if not registry.contains(nt.name):
            registry.register(nt.name, impl)
