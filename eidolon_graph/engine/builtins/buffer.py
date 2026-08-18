"""Buffer 缓冲区:put 逐个存入(按输入顺序累积成列表),flush 触发一次性输出并清空。

- put 组(方法):把输入值追加进 state.items(保持到达顺序——连线输入是
  瞬态事件,触发后消费,因此一次 run 只能存入一个值,逐步累积);
- flush 组(方法):输出全部累积数据(items 列表)并清空;空缓冲不产出
  (没有数据就没有输出事件);
- 同轮 put 与 flush 齐到:按组声明序先 put 后 flush,flush 取走含刚存入的
  全部数据(执行序 = 注入序 + 数据流因果序)。
"""

from __future__ import annotations

from ...model import (ON_TRIGGER, DataIn, DataOut, ImplBinding, InputGroup, NodeType,
                      StateField, TriggerIn)
from ..protocol import NodeImpl, TickContext, TickOutput

BUFFER = NodeType(
    name="Buffer",
    data_in=[DataIn("put")],
    trigger_in=[TriggerIn("flush")],  # 激活入口:触发输出并清空,载荷忽略
    data_out=[DataOut("items")],
    state=[StateField("items", [])],
    groups=[InputGroup("put", inputs=["put"], outputs=[]),
            InputGroup("flush", triggers=["flush"], outputs=["items"],
                       policy=ON_TRIGGER)],
    impl=ImplBinding(kind="code", name="Buffer"),
)


class BufferImpl(NodeImpl):
    def doc(self) -> dict:
        return {
            "summary": "缓冲区:put 逐个存入(按输入顺序累积),flush 一次性输出全部并清空。",
            "sections": [
                {"title": "行为", "lines": [
                    "put 新值到达:追加到列表末尾(保持到达顺序)",
                    "flush 新值到达:输出全部累积数据(列表)并清空;空缓冲不产出",
                    "同轮 put 与 flush 齐到:先存后取,flush 取走含刚存入的全部数据",
                ]},
                {"title": "典型接法", "lines": [
                    "put ← 事件源:逐步收集一批数据",
                    "flush ← 条件信号触发:取走整批并按序处理",
                ]},
            ],
        }

    def tick(self, ctx: TickContext) -> TickOutput:
        if ctx.group == "put":
            items = list(ctx.state.get("items", []))
            items.append(ctx.data_in.get("put"))
            return TickOutput(state={"items": items})
        # flush:输出全部累积数据并清空;空缓冲不产出
        items = ctx.state.get("items", [])
        if not items:
            return TickOutput()
        return TickOutput(data_out={"items": list(items)}, state={"items": []})
