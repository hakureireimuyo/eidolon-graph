"""Input 手动输入:宿主经 run(events) 注入新值 → 输出事件向后传播。

事件驱动不在乎事件从哪来——注入数据事件与节点产出数据向后传播完全同构。
每次注入都是新鲜事件(同值重复注入同样触发,与"值是否变化"无关——
手动点击输入键即新事件,内核不做值去重)。
in 是可选参数端口(函数默认参数):不注入时组不产出。展示对接由宿主处理
(编辑器对该节点渲染输入栏 + 注入按钮)。
"""

from __future__ import annotations

from ...model import DataIn, DataOut, ImplBinding, InputGroup, NodeType, StateField
from ..protocol import NodeImpl, TickContext, TickOutput

INPUT = NodeType(
    name="Input",
    data_in=[DataIn("in", optional=True)],
    data_out=[DataOut("out")],
    state=[StateField("last", None)],
    groups=[InputGroup("fire", inputs=["in"], outputs=["out"])],
    impl=ImplBinding(kind="code", name="Input"),
)


class InputImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        value = ctx.data_in.get("in")
        if value is None:
            return TickOutput()  # 无注入:不产出
        return TickOutput(data_out={"out": value}, state={"last": value})
