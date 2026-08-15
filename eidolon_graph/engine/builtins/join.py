"""Join 拼接:双输入单输出——两个输入转字符串用 | 分割拼接输出。

多输入组的验证节点(两个端口不同时期收到数据的触发语义):
组 = 函数调用,全部有效(未关闭)的连线输入有新值才触发;一格缓冲新值覆盖,
触发后消费清零重新等待全套新值;端口被信号禁用则旁路(值为 None 字符串)。
"""

from __future__ import annotations

from ...model import DataIn, DataOut, ImplBinding, InputGroup, NodeType
from ..protocol import NodeImpl, TickContext, TickOutput

JOIN = NodeType(
    name="Join",
    data_in=[DataIn("a"), DataIn("b")],
    data_out=[DataOut("out")],
    groups=[InputGroup("join", inputs=["a", "b"], outputs=["out"])],
    impl=ImplBinding(kind="code", name="Join"),
)


class JoinImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        a = ctx.data_in.get("a")
        b = ctx.data_in.get("b")
        return TickOutput(data_out={"out": f"{a}|{b}"})
