"""触发端口(TriggerIn 独立端口 + 组触发策略,1.0)语义测试。

覆盖:
- 序列化:TriggerIn 往返;旧资产拒绝(data_in.trigger 标记 → SerializationError);
- 版本闸:旧 0.x 图 / 资产库在新内核下拒绝加载;
- 校验:命名唯一、triggers 引用、策略互斥、连线规则(数据线/信号线 → TriggerIn);
- 行为冒烟:数据线载荷进 data_in + 触发;信号线电平变化触发。
"""

import pytest

from eidolon_graph.engine import Event, NodeImpl, NodeRegistry, TickContext, TickOutput, World
from eidolon_graph.engine.builtins import register_builtins
from eidolon_graph.model import (ON_ALL_DATA_READY, ON_TRIGGER, AssetLibrary,
                                 ControlOut, DataIn, DataOut, Graph, ImplBinding,
                                 InputGroup, NodeInstance, NodeType, TriggerIn, Wire,
                                 serialize, validate)


def make_env():
    lib = AssetLibrary()
    registry = NodeRegistry()
    register_builtins(lib, registry)
    return lib, registry


# ---------------------------------------------------------------------------
# 序列化:TriggerIn 往返 + 旧资产拒绝
# ---------------------------------------------------------------------------

def test_trigger_in_serializes_roundtrip():
    nt = NodeType(name="T", trigger_in=[TriggerIn("ev")],
                  groups=[InputGroup("g", triggers=["ev"], outputs=[],
                                     policy=ON_TRIGGER)],
                  impl=ImplBinding(kind="code", name="T"))
    d = serialize.node_type_to_dict(nt)
    assert d["trigger_in"] == [{"name": "ev", "type": None}]
    assert d["groups"][0]["triggers"] == ["ev"]
    assert d["groups"][0]["policy"] == ON_TRIGGER
    nt2 = serialize.node_type_from_dict(d)
    assert [t.name for t in nt2.trigger_in] == ["ev"]
    assert nt2.groups[0].triggers == ["ev"]
    assert nt2.groups[0].policy == ON_TRIGGER


def test_old_trigger_flag_rejected_on_load():
    # 旧 0.x 资产:data_in 带 trigger 标记 → 反序列化直接拒绝
    with pytest.raises(serialize.SerializationError):
        serialize.data_in_from_dict({"name": "ev", "trigger": True})
    # 旧格式节点类型(无 trigger 键)可读,但 trigger 键一旦出现即拒绝
    nt = serialize.node_type_from_dict({"name": "T",
                                        "data_in": [{"name": "ev", "trigger": False}]})
    assert not nt.trigger_in  # False 是显式关闭,不触发拒绝


# ---------------------------------------------------------------------------
# 版本闸:旧 0.x 图 / 资产库拒绝加载
# ---------------------------------------------------------------------------

def test_old_graph_version_rejected():
    with pytest.raises(serialize.SerializationError):
        serialize.graph_from_dict({"name": "old", "kernel_version": "0.1.0-0",
                                   "nodes": [], "wires": []})


def test_old_library_version_rejected():
    with pytest.raises(serialize.SerializationError):
        serialize.library_from_dict({"kernel_version": "0.1.0-0",
                                     "node_types": [], "graphs": []})


def test_current_versions_load():
    g = serialize.graph_from_dict({"name": "g", "kernel_version": "1.0.0-0",
                                   "nodes": [], "wires": []})
    assert g.name == "g"
    lib = serialize.library_from_dict({"kernel_version": "1.0.0-0",
                                       "node_types": [], "graphs": []})
    assert lib is not None


# ---------------------------------------------------------------------------
# 校验:触发输入声明与组声明
# ---------------------------------------------------------------------------

def _nt_with(trigger_in=None, groups=None, data_in=None):
    return NodeType(name="T", data_in=data_in or [], trigger_in=trigger_in or [],
                    groups=groups or [], impl=ImplBinding(kind="code", name="T"))


def test_trigger_in_duplicate_name_rejected():
    lib = AssetLibrary()
    lib.add_node_type(_nt_with(data_in=[DataIn("ev")], trigger_in=[TriggerIn("ev")],
                               groups=[InputGroup("g", inputs=["ev"], outputs=[])]))
    rep = validate(lib, Graph(name="g", nodes=[NodeInstance("n", "T")]))
    assert not rep.ok
    assert any("重名" in e for e in rep.errors)


def test_group_triggers_must_reference_trigger_in():
    lib = AssetLibrary()
    lib.add_node_type(_nt_with(
        data_in=[DataIn("x")],
        groups=[InputGroup("g", triggers=["x"], policy=ON_TRIGGER)]))
    rep = validate(lib, Graph(name="g", nodes=[NodeInstance("n", "T")]))
    assert not rep.ok
    assert any("未声明的触发输入" in e for e in rep.errors)


def test_trigger_in_belongs_to_one_group_only():
    lib = AssetLibrary()
    lib.add_node_type(_nt_with(
        trigger_in=[TriggerIn("ev")],
        groups=[InputGroup("g1", triggers=["ev"], policy=ON_TRIGGER),
                InputGroup("g2", triggers=["ev"], policy=ON_TRIGGER)]))
    rep = validate(lib, Graph(name="g", nodes=[NodeInstance("n", "T")]))
    assert not rep.ok
    assert any("同时属于输入组" in e for e in rep.errors)


def test_illegal_policy_rejected():
    lib = AssetLibrary()
    lib.add_node_type(_nt_with(
        groups=[InputGroup("g", policy="never")]))  # 未实现的策略字符串
    rep = validate(lib, Graph(name="g", nodes=[NodeInstance("n", "T")]))
    assert not rep.ok
    assert any("触发策略" in e and "非法" in e for e in rep.errors)


def test_dead_triggers_with_data_policy_rejected():
    # triggers 非空但策略不用它 = 死声明
    lib = AssetLibrary()
    lib.add_node_type(_nt_with(
        trigger_in=[TriggerIn("ev")],
        groups=[InputGroup("g", triggers=["ev"], policy=ON_ALL_DATA_READY)]))
    rep = validate(lib, Graph(name="g", nodes=[NodeInstance("n", "T")]))
    assert not rep.ok
    assert any("死声明" in e for e in rep.errors)


def test_trigger_policy_without_triggers_rejected():
    # ON_TRIGGER 但无 triggers = 死组
    lib = AssetLibrary()
    lib.add_node_type(_nt_with(
        groups=[InputGroup("g", policy=ON_TRIGGER)]))
    rep = validate(lib, Graph(name="g", nodes=[NodeInstance("n", "T")]))
    assert not rep.ok
    assert any("死组" in e for e in rep.errors)


def test_bare_trigger_in_is_legal():
    # 未连线的 TriggerIn 合法(激活入口是可选来源)
    lib = AssetLibrary()
    lib.add_node_type(_nt_with(
        trigger_in=[TriggerIn("ev")],
        groups=[InputGroup("g", triggers=["ev"], policy=ON_TRIGGER)]))
    rep = validate(lib, Graph(name="g", nodes=[NodeInstance("n", "T")]))
    assert rep.ok


def test_subgraph_with_trigger_in_rejected():
    lib = AssetLibrary()
    lib.add_node_type(_nt_with(
        trigger_in=[TriggerIn("ev")],
        groups=[InputGroup("g", triggers=["ev"], policy=ON_TRIGGER)]))
    lib.add_graph(Graph(name="inner", nodes=[NodeInstance("i", "T")]))
    lib.add_node_type(NodeType(
        name="Sub",
        trigger_in=[TriggerIn("ev")],
        groups=[InputGroup("g", triggers=["ev"], policy=ON_TRIGGER)],
        impl=ImplBinding(kind="subgraph", name="Sub", graph="inner")))
    rep = validate(lib, Graph(name="g", nodes=[NodeInstance("n", "Sub")]))
    assert not rep.ok
    assert any("触发事件无法穿过" in e for e in rep.errors)


# ---------------------------------------------------------------------------
# 校验:连线规则(数据线 / 信号线 → TriggerIn 均合法)
# ---------------------------------------------------------------------------

def test_data_wire_to_trigger_in_legal():
    lib = AssetLibrary()
    lib.add_node_type(NodeType(name="Src", data_out=[DataOut("o")], auto=True,
                               impl=ImplBinding(kind="code", name="Src")))
    lib.add_node_type(_nt_with(
        trigger_in=[TriggerIn("ev")],
        groups=[InputGroup("g", triggers=["ev"], policy=ON_TRIGGER)]))
    g = Graph(name="g", nodes=[NodeInstance("s", "Src"), NodeInstance("n", "T")],
              wires=[Wire("s", "o", "n", "ev")])  # 数据线:载荷 + 激活
    assert validate(lib, g).ok


def test_signal_wire_to_trigger_in_legal():
    lib = AssetLibrary()
    lib.add_node_type(NodeType(name="Src", control_out=[ControlOut("o")],
                               impl=ImplBinding(kind="code", name="Src")))
    lib.add_node_type(_nt_with(
        trigger_in=[TriggerIn("ev")],
        groups=[InputGroup("g", triggers=["ev"], policy=ON_TRIGGER)]))
    g = Graph(name="g", nodes=[NodeInstance("s", "Src"), NodeInstance("n", "T")],
              wires=[Wire("s", "o", "n", "ev", dst_slot="signal")])  # 信号线:电平触发
    assert validate(lib, g).ok


def test_data_output_signal_slot_to_trigger_in_legal():
    # 数据输出的信号端口 → TriggerIn 信号槽(电平触发,与数据线载荷区分)
    lib = AssetLibrary()
    lib.add_node_type(NodeType(name="Src", data_out=[DataOut("o")], auto=True,
                               impl=ImplBinding(kind="code", name="Src")))
    lib.add_node_type(_nt_with(
        trigger_in=[TriggerIn("ev")],
        groups=[InputGroup("g", triggers=["ev"], policy=ON_TRIGGER)]))
    g = Graph(name="g", nodes=[
        NodeInstance("s", "Src"),
        NodeInstance("n", "T"),
    ], wires=[
        Wire("s", "o", "n", "ev", dst_slot="signal"),
    ])
    assert validate(lib, g).ok


# ---------------------------------------------------------------------------
# 行为冒烟:载荷 + 触发
# ---------------------------------------------------------------------------

ECHO_TRIG = NodeType(
    name="EchoTrig",
    trigger_in=[TriggerIn("fire")],
    data_out=[DataOut("out")],
    groups=[InputGroup("g", triggers=["fire"], outputs=["out"], policy=ON_TRIGGER)],
    impl=ImplBinding(kind="code", name="EchoTrig"),
)


class EchoTrigImpl(NodeImpl):
    def tick(self, ctx: TickContext) -> TickOutput:
        # 触发端口的载荷照常可用(事件 + 载荷形态)
        return TickOutput(data_out={"out": ctx.data_in.get("fire")})


def test_data_payload_fires_and_is_available():
    lib, registry = make_env()
    lib.add_node_type(ECHO_TRIG)
    registry.register("EchoTrig", EchoTrigImpl)
    g = Graph(name="echo", nodes=[
        NodeInstance("in1", "Input"),
        NodeInstance("echo", "EchoTrig"),
        NodeInstance("printer", "Printer"),
    ], wires=[
        Wire("in1", "out", "echo", "fire"),
        Wire("echo", "out", "printer", "msg"),
    ])
    w = World(lib, g, registry)
    w.run([Event("in1", "in", "payload")])
    assert w._states["printer"].state["last_msg"] == "payload"
    w.run([Event("in1", "in", "again")])  # 每次新到达都触发(无值去重)
    assert w._states["printer"].state["last_msg"] == "again"
