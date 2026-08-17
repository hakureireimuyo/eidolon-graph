"""Simulate 模拟节点(测试用):模拟外部调用(LLM/服务)长时间运行的三种结局。

mode 配置选择场景(默认 "ok",即场景一):
- ok:    长时间运行(阻塞 work_ms 毫秒)后正常输出触发值;
- error: 长时间运行后抛异常——走引擎异常策略(不产出 + 日志 + 熔断);
- hang:  真实卡死——无限阻塞,无任何输出(模拟外部调用无响应;实时模式
         世界线程停摆,同步模式 run 永不返回)。

work_ms = 模拟工作时长(毫秒,默认 200,三种场景共用前置工作段);
error_msg = error 模式抛出的异常消息(默认给出说明)。
"""

from __future__ import annotations

import time

from ...model import Annot, ConfigField, DataIn, DataOut, ImplBinding, InputGroup, NodeType
from ..protocol import NodeImpl, TickContext, TickOutput

SIMULATE = NodeType(
    name="Simulate",
    data_in=[DataIn("trigger")],
    data_out=[DataOut("result")],
    config=[ConfigField("mode", "ok", Annot(str)),
            ConfigField("work_ms", 200, Annot(int)),
            ConfigField("error_msg", "Simulate 节点模拟异常", Annot(str))],
    groups=[InputGroup("run", inputs=["trigger"], outputs=["result"])],
    impl=ImplBinding(kind="code", name="Simulate"),
)


class SimulateImpl(NodeImpl):
    def doc(self) -> dict:
        return {
            "summary": "模拟外部调用长时间运行的三种结局(测试用)。",
            "sections": [
                {"title": "场景(mode 配置,默认 ok)", "lines": [
                    "ok:阻塞 work_ms 毫秒后正常输出触发值(长时间运行 → 正确输出)",
                    "error:阻塞后抛异常 → 引擎异常策略(不产出 + 日志 + 熔断)",
                    "hang:真实卡死——无限阻塞,无任何输出(世界线程停摆)",
                ]},
                {"title": "配置", "lines": [
                    "mode: ok / error / hang(默认 ok)",
                    "work_ms: 模拟工作时长毫秒数(三种场景共用前置工作段)",
                    "error_msg: error 模式的异常消息",
                ]},
            ],
        }

    def tick(self, ctx: TickContext) -> TickOutput:
        mode = ctx.config.get("mode", "ok")
        work_ms = max(0, int(ctx.config.get("work_ms", 200) or 0))
        if work_ms:
            time.sleep(work_ms / 1000.0)  # 前置工作段:三种场景共用
        if mode == "hang":
            while True:  # 真实卡死:无限阻塞,永不返回(引擎无超时可救)
                time.sleep(3600.0)
        if mode == "error":
            raise RuntimeError(ctx.config.get("error_msg", "Simulate 节点模拟异常"))
        # ok:长时间运行后正常产出触发值
        return TickOutput(data_out={"result": ctx.data_in.get("trigger")})
