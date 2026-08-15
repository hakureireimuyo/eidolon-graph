"""内置节点白名单:Clock / Counter / Comparator / AND / OR / NOT / Switch /
Latch / Timer / Threshold / Random / Printer。

全部是**普通节点类型资产**(运行时对它们零特殊处理)——它们同时是节点协议的
自证与编辑器预览 stub 的基础。领域节点(LLM / Context Compiler / 工具等)一律
不进内核,由宿主注册。

约定:
- 数据节点(无控制输出)不触碰信号:输入信号屏蔽由引擎旁路,输出信号由自动传导;
- 信号节点(有控制输出)显式写信号电平:AND/OR/NOT/Latch/Timer/Threshold 等;
- 门控/熔断全部由运行时拦截,实现者无感知;
- 处理不了的非 None 输入 → 抛异常,走引擎异常策略(不产出 + 日志 + 熔断)。
"""

from __future__ import annotations

from ..model import (Annot, AssetLibrary, ConfigField, ControlIn, ControlOut, DataIn,
                     DataOut, ImplBinding, InputGroup, NodeType, StateField)
from .protocol import NodeImpl, TickContext, TickOutput
from .registry import NodeRegistry
from .signal import ACTIVE, INACTIVE

# ---------------------------------------------------------------------------
# Clock 时钟:源节点(自走);速率是状态字段,可被 set_rate 组(方法)调制
# ---------------------------------------------------------------------------

CLOCK = NodeType(
    name="Clock",
    data_in=[DataIn("rate", type_annot=Annot(int), const_set=True, const=1)],
    data_out=[DataOut("count", type_annot=Annot(int))],
    control_in=[ControlIn("enable")],
    state=[StateField("count", 0, Annot(int)), StateField("rate", 1, Annot(int))],
    groups=[InputGroup("set_rate", inputs=["rate"], outputs=[])],  # rate 绑定端口=值源,不参与触发
    auto=True,
    impl=ImplBinding(kind="code", name="Clock"),
)


class ClockImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        if ctx.group == "step":
            count = ctx.state.get("count", 0)
            rate = ctx.state.get("rate", 1)
            new_count = count + rate
            return TickOutput(data_out={"count": new_count}, state={"count": new_count})
        # set_rate:参数调制(普通方法,写状态,无输出)
        return TickOutput(state={"rate": ctx.data_in.get("rate")})


# ---------------------------------------------------------------------------
# Counter 计数器:组 [increment] → [count];屏蔽 increment 端口信号 = 计数暂停
# ---------------------------------------------------------------------------

COUNTER = NodeType(
    name="Counter",
    data_in=[DataIn("increment", type_annot=Annot(int))],
    data_out=[DataOut("count", type_annot=Annot(int))],
    control_in=[ControlIn("enable")],
    state=[StateField("count", 0, Annot(int))],
    groups=[InputGroup("tick", inputs=["increment"], outputs=["count"])],
    impl=ImplBinding(kind="code", name="Counter"),
)


class CounterImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        inc = ctx.data_in.get("increment")
        count = ctx.state.get("count", 0)
        if inc is None:  # 上游无事发生:不计
            return TickOutput(data_out={"count": count})
        return TickOutput(data_out={"count": count + inc}, state={"count": count + inc})


# ---------------------------------------------------------------------------
# Threshold 阈值(条件):数据入、数据+控制出;极性由声明决定(低于阈值 = active)
# ---------------------------------------------------------------------------

THRESHOLD = NodeType(
    name="Threshold",
    data_in=[DataIn("value")],
    data_out=[DataOut("over")],
    control_out=[ControlOut("under", default_level=ACTIVE)],
    config=[ConfigField("limit", None)],
    groups=[InputGroup("judge", inputs=["value"], outputs=["over"])],
    impl=ImplBinding(kind="code", name="Threshold"),
)


class ThresholdImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        v = ctx.data_in.get("value")
        limit = ctx.config.get("limit")
        over = v is not None and limit is not None and v >= limit
        return TickOutput(data_out={"over": over},
                          control_out={"under": INACTIVE if over else ACTIVE})


# ---------------------------------------------------------------------------
# Comparator 比较器:两个数据入,比较结果数据出 + 控制出
# ---------------------------------------------------------------------------

COMPARATOR = NodeType(
    name="Comparator",
    data_in=[DataIn("a"), DataIn("b")],
    data_out=[DataOut("gt"), DataOut("eq")],
    control_out=[ControlOut("a_gt_b")],
    groups=[InputGroup("compare", inputs=["a", "b"], outputs=["gt", "eq"])],
    impl=ImplBinding(kind="code", name="Comparator"),
)


class ComparatorImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        a = ctx.data_in.get("a")
        b = ctx.data_in.get("b")
        gt = a is not None and b is not None and a > b
        eq = a is not None and b is not None and a == b
        return TickOutput(data_out={"gt": gt, "eq": eq},
                          control_out={"a_gt_b": ACTIVE if gt else INACTIVE})


# ---------------------------------------------------------------------------
# AND / OR / NOT:信号逻辑元件(level 输入,引擎不介入;无数据输入 → 源节点)
# ---------------------------------------------------------------------------

AND_NODE = NodeType(
    name="AND",
    control_in=[ControlIn("a", semantic="level"), ControlIn("b", semantic="level")],
    control_out=[ControlOut("out")],
    impl=ImplBinding(kind="code", name="AND"),
)


class AndImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        a = ctx.control_in.get("a")
        b = ctx.control_in.get("b")
        return TickOutput(control_out={"out": ACTIVE if a == ACTIVE and b == ACTIVE else INACTIVE})


OR_NODE = NodeType(
    name="OR",
    control_in=[ControlIn("a", semantic="level"), ControlIn("b", semantic="level")],
    control_out=[ControlOut("out")],
    impl=ImplBinding(kind="code", name="OR"),
)


class OrImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        a = ctx.control_in.get("a")
        b = ctx.control_in.get("b")
        return TickOutput(control_out={"out": ACTIVE if a == ACTIVE or b == ACTIVE else INACTIVE})


NOT_NODE = NodeType(
    name="NOT",
    control_in=[ControlIn("in", semantic="level")],
    control_out=[ControlOut("out")],
    impl=ImplBinding(kind="code", name="NOT"),
)


class NotImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        level = ctx.control_in.get("in")
        return TickOutput(control_out={"out": INACTIVE if level == ACTIVE else ACTIVE})


# ---------------------------------------------------------------------------
# Switch 开关:数据入、门控转发 + 数据→信号转换(真值电平)
# ---------------------------------------------------------------------------

SWITCH = NodeType(
    name="Switch",
    data_in=[DataIn("value")],
    data_out=[DataOut("selected")],
    control_in=[ControlIn("enable")],
    control_out=[ControlOut("out")],
    groups=[InputGroup("pass", inputs=["value"], outputs=["selected"])],
    impl=ImplBinding(kind="code", name="Switch"),
)


class SwitchImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        v = ctx.data_in.get("value")
        return TickOutput(data_out={"selected": v},
                          control_out={"out": ACTIVE if v else INACTIVE})


# ---------------------------------------------------------------------------
# Latch 锁存器(SR):set 优先;信号节点(无数据输入 → 源节点,每轮运行)
# ---------------------------------------------------------------------------

LATCH = NodeType(
    name="Latch",
    control_in=[ControlIn("set", semantic="level"), ControlIn("reset", semantic="level")],
    control_out=[ControlOut("q")],
    state=[StateField("q", False, Annot(bool))],
    impl=ImplBinding(kind="code", name="Latch"),
)


class LatchImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        q = ctx.state.get("q", False)
        if ctx.control_in.get("set") == ACTIVE:
            q = True
        elif ctx.control_in.get("reset") == ACTIVE:
            q = False
        return TickOutput(control_out={"q": ACTIVE if q else INACTIVE}, state={"q": q})


# ---------------------------------------------------------------------------
# Timer 计时器:start 电平持续 → 倒计时;stop 归零;信号节点 + 源节点
# ---------------------------------------------------------------------------

TIMER = NodeType(
    name="Timer",
    control_in=[ControlIn("start", semantic="level"), ControlIn("stop", semantic="level")],
    data_out=[DataOut("remaining")],
    control_out=[ControlOut("running")],
    state=[StateField("remaining", 0, Annot(int))],
    config=[ConfigField("duration", 5, Annot(int))],
    impl=ImplBinding(kind="code", name="Timer"),
)


class TimerImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        remaining = ctx.state.get("remaining", 0)
        if ctx.control_in.get("stop") == ACTIVE:
            remaining = 0
        elif ctx.control_in.get("start") == ACTIVE:
            if remaining <= 0:
                remaining = ctx.config.get("duration", 5)
            remaining = max(0, remaining - 1)
        return TickOutput(data_out={"remaining": remaining},
                          control_out={"running": ACTIVE if remaining > 0 else INACTIVE},
                          state={"remaining": remaining})


# ---------------------------------------------------------------------------
# Random 随机:源节点(自走);门控 inactive 不消耗随机数(引擎拦截,不进入 tick)
# ---------------------------------------------------------------------------

RANDOM = NodeType(
    name="Random",
    data_out=[DataOut("draw")],
    control_in=[ControlIn("enable")],
    config=[ConfigField("low", 0.0, Annot(float)), ConfigField("high", 1.0, Annot(float)),
            ConfigField("as_int", False, Annot(bool))],
    auto=True,
    impl=ImplBinding(kind="code", name="Random"),
)


class RandomImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        rng = ctx.rng
        if ctx.config.get("as_int"):
            v = rng.randint(int(ctx.config.get("low", 0)), int(ctx.config.get("high", 1)))
        else:
            v = rng.uniform(ctx.config.get("low", 0.0), ctx.config.get("high", 1.0))
        return TickOutput(data_out={"draw": v})


# ---------------------------------------------------------------------------
# Printer 打印(阶段零验证用):记录最近消息,echo 供反馈连线与断言
# ---------------------------------------------------------------------------

PRINTER = NodeType(
    name="Printer",
    data_in=[DataIn("msg")],
    data_out=[DataOut("echo")],
    state=[StateField("last_msg", None)],
    groups=[InputGroup("print", inputs=["msg"], outputs=["echo"])],
    impl=ImplBinding(kind="code", name="Printer"),
)


class PrinterImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        msg = ctx.data_in.get("msg")
        return TickOutput(data_out={"echo": msg}, state={"last_msg": msg})


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------

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
    (RANDOM, RandomImpl),
    (PRINTER, PrinterImpl),
]


def register_builtins(lib: AssetLibrary, registry: NodeRegistry) -> None:
    """把内置节点类型资产与代码实现注册进资产库与实现表(构造 World 前调用)。"""
    for nt, impl in _BUILTINS:
        if nt.name not in lib.node_types:
            lib.add_node_type(nt)
        if not registry.contains(nt.name):
            registry.register(nt.name, impl)
