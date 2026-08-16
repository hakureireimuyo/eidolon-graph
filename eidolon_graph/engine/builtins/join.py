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
    def doc(self) -> dict:
        return {
            "summary": "拼接节点:双输入齐全后拼成 \"a|b\" 字符串输出。",
            "sections": [
                {"title": "行为", "lines": [
                    "a、b 两个输入都收到新值才触发一次输出(不同时期到达则等待)",
                    "输出 = \"a|b\"(| 分隔的字符串);触发后缓冲清空,重新等待全套新值",
                ]},
                {"title": "典型接法", "lines": [
                    "两个不同来源 → 一个输出:组合事件流(如 数据 + 时间戳)",
                ]},
            ],
        }

    def tick(self, ctx: TickContext) -> TickOutput:
        a = ctx.data_in.get("a")
        b = ctx.data_in.get("b")
        return TickOutput(data_out={"out": f"{a}|{b}"})
