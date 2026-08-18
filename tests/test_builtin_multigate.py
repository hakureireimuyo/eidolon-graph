"""MultiGate 多输入组示例节点:两互不相干输入组并行,各组独立触发互不干扰。

覆盖:g1 两输入齐到触发、g2 单输入触发、两组并行互不等待、
组内输入未齐时该组不触发(不影响其他组)。
"""

from eidolon_graph.engine import Event, NodeRegistry, World
from eidolon_graph.engine.builtins import register_builtins
from eidolon_graph.model import AssetLibrary, Graph, NodeInstance, Wire


def make_env():
    lib = AssetLibrary()
    registry = NodeRegistry()
    register_builtins(lib, registry)
    return lib, registry


def make_multigate_graph():
    return Graph(name="mg", nodes=[
        NodeInstance("in_a", "Input"),
        NodeInstance("in_b", "Input"),
        NodeInstance("in_c", "Input"),
        NodeInstance("mg", "MultiGate"),
        NodeInstance("pr_p", "Output"),
        NodeInstance("pr_q", "Output"),
    ], wires=[
        Wire("in_a", "out", "mg", "int_a"),
        Wire("in_b", "out", "mg", "int_b"),
        Wire("in_c", "out", "mg", "int_c"),
        Wire("mg", "out_p", "pr_p", "msg"),
        Wire("mg", "out_q", "pr_q", "msg"),
    ])


def test_g1_requires_both_inputs():
    lib, registry = make_env()
    w = World(lib, make_multigate_graph(), registry)
    w.run([Event("in_a", "in", "x")])  # 只给 int_a:g1 不触发(等 int_b)
    assert w._states["pr_p"].state["last_msg"] is None
    w.run([Event("in_b", "in", "y")])  # int_b 到达:g1 齐到触发
    assert w._states["pr_p"].state["last_msg"] == ["x", "y"]


def test_g2_single_input_echo():
    lib, registry = make_env()
    w = World(lib, make_multigate_graph(), registry)
    w.run([Event("in_c", "in", "z")])
    assert w._states["pr_q"].state["last_msg"] == "z"


def test_groups_parallel_independent():
    """两组并行互不干扰:g1 未齐不影响 g2;g2 触发不消费 g1 的输入。"""
    lib, registry = make_env()
    w = World(lib, make_multigate_graph(), registry)
    w.run([Event("in_a", "in", "x"), Event("in_c", "in", "z")])  # 同轮:g1 未齐、g2 触发
    assert w._states["pr_p"].state["last_msg"] is None   # g1 等待 int_b
    assert w._states["pr_q"].state["last_msg"] == "z"    # g2 照常输出
    w.run([Event("in_b", "in", "y")])                    # g1 齐到,跨轮触发
    assert w._states["pr_p"].state["last_msg"] == ["x", "y"]
    assert w._states["pr_q"].state["last_msg"] == "z"    # g2 不被干扰
