"""LlmCall 调用节点:prompt → 外部模型调用 → response(异步,宿主完成注入)。

节点封装层(内核引用能力库 eidolon-llm,把能力包装成节点;本层不实现
任何模型调用逻辑):
- 组 "call" 触发 → tick 把 pending 凭证写进 state,不产出 = 等待;
- 外部结果到达 → 宿主经 LlmBridge 注入 run([Event(node, "_result", 结果)]);
- 组 "complete" 触发 → 产出 response(失败时拉高 failed 信号)。

超时/重试在能力库 eidolon-llm 内统一处理;桥只做轮询与完成注入。
"""

from __future__ import annotations

from ...engine.protocol import NodeImpl, TickContext, TickOutput
from ...engine.signal import ACTIVE, INACTIVE
from ...model import (Annot, ConfigField, ControlOut, DataIn, DataOut,
                                 ImplBinding, InputGroup, NodeType, StateField)

LLM_CALL = NodeType(
    name="LlmCall",
    data_in=[DataIn("prompt"), DataIn("_result", optional=True)],  # 完成端口 = 可选参数
    data_out=[DataOut("response")],
    control_out=[ControlOut("failed", default_level=INACTIVE)],
    config=[ConfigField("model", ""),
            ConfigField("temperature", 0.7, Annot(float)),
            ConfigField("max_tokens", 0, Annot(int)),
            ConfigField("timeout", 30.0, Annot(float)),
            ConfigField("retries", 0, Annot(int))],
    state=[StateField("pending", None), StateField("last_response", None),
           StateField("last_error", None), StateField("calls", 0, Annot(int))],
    groups=[
        InputGroup("call", inputs=["prompt"], outputs=[]),            # 发起调用
        InputGroup("complete", inputs=["_result"], outputs=["response"]),  # 完成注入
    ],
    impl=ImplBinding(kind="code", name="LlmCall"),
)


class LlmCallImpl(NodeImpl):
    """外部模型调用节点:等待不产出;结果/错误经完成注入回到因果传播。"""

    def tick(self, ctx: TickContext) -> TickOutput:
        if ctx.group == "call":
            prompt = ctx.data_in.get("prompt")
            seq = ctx.state.get("calls", 0) + 1
            # pending 凭证 = 桥的调用参数(超时/重试等由桥按 config 执行)
            pending = {
                "prompt": str(prompt),
                "opts": {
                    "model": ctx.config.get("model", ""),
                    "temperature": ctx.config.get("temperature", 0.7),
                    "max_tokens": ctx.config.get("max_tokens", 0),
                    "timeout": ctx.config.get("timeout", 30.0),
                    "retries": ctx.config.get("retries", 0),
                },
                "seq": seq,
            }
            return TickOutput(state={"pending": pending, "calls": seq,
                                     "last_error": None})
        # complete:外部结果到达(桥注入 {"value": ...} 或 {"error": ...})。
        # 完成端口是可选参数 → 组在每次节点访问都被"空触发"(wired 为空,
        # 触发条件真空为真,协议 §4 约定):结果缺失必须保持等待、不清 pending。
        pending = ctx.state.get("pending")
        if pending is None:
            return TickOutput()  # 无 pending:忽略(生命周期策略是节点包业务)
        outcome = ctx.data_in.get("_result")
        if not isinstance(outcome, dict) or not outcome:
            return TickOutput()  # 空触发/无结果:继续等待
        value = outcome.get("value")
        error = outcome.get("error")
        if error is not None:
            return TickOutput(
                control_out={"failed": ACTIVE},
                state={"pending": None, "last_error": str(error)},
            )
        return TickOutput(
            data_out={"response": str(value)},
            control_out={"failed": INACTIVE},
            state={"pending": None, "last_response": str(value), "last_error": None},
        )
