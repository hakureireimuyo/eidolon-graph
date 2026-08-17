"""触发端口(DataIn.trigger 事件端口)语义测试。

覆盖:
- 序列化往返:trigger 标记保留;旧格式缺省为 False(向后兼容);
- 校验:触发端口禁止绑定(事件不是持久值);
- 载荷可用:触发端口的载荷照常进 data_in(事件 + 载荷形态,如 Delay.trigger);
- 行为冒烟:触发判定只基于新值到达,与普通端口一致(引擎零特殊处理)。
"""

import pytest

from eidolon_graph.engine import Event, NodeImpl, NodeRegistry, TickContext, TickOutput, World
from eidolon_graph.engine.builtins import register_builtins
from eidolon_graph.model import (AssetLibrary, DataIn, DataOut, Graph, ImplBinding,
                                 InputGroup, NodeInstance, NodeType, Wire, serialize,
                                 validate)


def make_env():
    lib = AssetLibrary()
    registry = NodeRegistry()
    register_builtins(lib, registry)
    return lib, registry


# ---------------------------------------------------------------------------
# 序列化往返
# ---------------------------------------------------------------------------

def test_trigger_flag_serializes_roundtrip():
    p = DataIn("ev", trigger=True)
    d = serialize.data_in_to_dict(p)
    assert d["trigger"] is True
    assert serialize.data_in_from_dict(d).trigger is True
    # 旧格式(无 trigger 键)缺省 False:向后兼容
    assert serialize.data_in_from_dict({"name": "ev"}).trigger is False


# ---------------------------------------------------------------------------
# 校验:触发端口禁止绑定
# ---------------------------------------------------------------------------

def _type_with(data_in: list) -> NodeType:
    return NodeType(name="T", data_in=data_in,
                    groups=[InputGroup("g", inputs=[p.name for p in data_in], outputs=[])],
                    impl=ImplBinding(kind="code", name="T"))


def test_trigger_port_with_binding_rejected():
    lib = AssetLibrary()
    lib.add_node_type(_type_with([DataIn("ev", trigger=True, const_set=True, const=1)]))
    g = Graph(name="g", nodes=[NodeInstance("n", "T")])
    rep = validate(lib, g)
    assert not rep.ok
    assert any("触发端口" in e and "绑定" in e for e in rep.errors)


def test_trigger_port_with_global_read_rejected():
    lib = AssetLibrary()
    lib.add_node_type(_type_with([DataIn("ev", trigger=True, global_read="g")]))
    g = Graph(name="g", nodes=[NodeInstance("n", "T")])
    rep = validate(lib, g)
    assert not rep.ok
    assert any("触发端口" in e and "绑定" in e for e in rep.errors)


# ---------------------------------------------------------------------------
# 载荷可用 + 触发行为与普通端口一致(引擎零特殊处理)
# ---------------------------------------------------------------------------

ECHO_TRIG = NodeType(
    name="EchoTrig",
    data_in=[DataIn("ev", trigger=True)],
    data_out=[DataOut("out")],
    groups=[InputGroup("fire", inputs=["ev"], outputs=["out"])],
    impl=ImplBinding(kind="code", name="EchoTrig"),
)


class EchoTrigImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        # 载荷照常可用(事件 + 载荷形态):触发端口的值就是本次事件的载荷
        return TickOutput(data_out={"out": ctx.data_in.get("ev")})


def make_echo_graph():
    lib, registry = make_env()
    lib.add_node_type(ECHO_TRIG)
    registry.register("EchoTrig", EchoTrigImpl)
    g = Graph(name="echo", nodes=[
        NodeInstance("in1", "Input"),
        NodeInstance("echo", "EchoTrig"),
        NodeInstance("printer", "Printer"),
    ], wires=[
        Wire("in1", "out", "echo", "ev"),
        Wire("echo", "out", "printer", "msg"),
    ])
    return lib, registry, g


def test_trigger_payload_available_and_fires_group():
    lib, registry, g = make_echo_graph()
    w = World(lib, g, registry)
    w.run([Event("in1", "in", "payload")])
    assert w._states["printer"].state["last_msg"] == "payload"
    w.run([Event("in1", "in", "again")])  # 每次新值到达都触发(无值去重)
    assert w._states["printer"].state["last_msg"] == "again"
