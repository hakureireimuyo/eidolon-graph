"""Buffer 缓冲区节点:put 按序累积,flush 一次性输出并清空。

覆盖:逐步累积保持顺序、flush 输出全部并清空、空缓冲 flush 不产出、
同轮 put+flush 先存后取、清空后重新累积与旧数据隔离。
"""

from eidolon_graph.engine import Event, NodeRegistry, World
from eidolon_graph.engine.builtins import register_builtins
from eidolon_graph.model import AssetLibrary, Graph, NodeInstance, Wire


def make_env():
    lib = AssetLibrary()
    registry = NodeRegistry()
    register_builtins(lib, registry)
    return lib, registry


def make_buffer_graph():
    return Graph(name="buffer", nodes=[
        NodeInstance("in_put", "Input"),
        NodeInstance("in_flush", "Input"),
        NodeInstance("buffer", "Buffer"),
        NodeInstance("printer", "Output"),
    ], wires=[
        Wire("in_put", "out", "buffer", "put"),
        Wire("in_flush", "out", "buffer", "flush"),
        Wire("buffer", "items", "printer", "msg"),
    ])


def put(w, value):
    w.run([Event("in_put", "in", value)])


def flush(w):
    w.run([Event("in_flush", "in", True)])


def test_buffer_accumulates_in_order_and_flushes_all():
    lib, registry = make_env()
    w = World(lib, make_buffer_graph(), registry)
    put(w, "a")
    put(w, "b")
    put(w, "c")
    assert w._states["printer"].state["last_msg"] is None  # 未 flush:不输出
    flush(w)
    assert w._states["printer"].state["last_msg"] == ["a", "b", "c"]  # 按输入顺序
    assert w._states["buffer"].state["items"] == []                   # 已清空


def test_buffer_flush_on_empty_produces_nothing():
    lib, registry = make_env()
    w = World(lib, make_buffer_graph(), registry)
    put(w, "x")
    flush(w)
    assert w._states["printer"].state["last_msg"] == ["x"]
    flush(w)  # 清空后再次 flush:空缓冲不产出(保持原值)
    assert w._states["printer"].state["last_msg"] == ["x"]


def test_buffer_same_turn_put_then_flush_takes_all():
    """同轮 put 与 flush 齐到:按组声明序先存后取,flush 取走含刚存入的全部。"""
    lib, registry = make_env()
    w = World(lib, make_buffer_graph(), registry)
    w.run([Event("in_put", "in", "a"),
           Event("in_flush", "in", True)])  # 一次 run 注入两个事件(不同端口)
    assert w._states["printer"].state["last_msg"] == ["a"]


def test_buffer_new_accumulation_isolated_after_flush():
    lib, registry = make_env()
    w = World(lib, make_buffer_graph(), registry)
    put(w, "old1")
    put(w, "old2")
    flush(w)
    put(w, "new")
    flush(w)
    assert w._states["printer"].state["last_msg"] == ["new"]  # 与旧数据隔离


def test_buffer_output_is_a_copy():
    """输出列表是拷贝:下游修改产出不会污染节点状态。"""
    lib, registry = make_env()
    w = World(lib, make_buffer_graph(), registry)
    put(w, 1)
    flush(w)
    produced = w._states["printer"].state["last_msg"]
    produced.append(999)  # 下游改动输出值
    assert w._states["buffer"].state["items"] == []  # 状态不受影响
    put(w, 2)
    assert w._states["buffer"].state["items"] == [2]
