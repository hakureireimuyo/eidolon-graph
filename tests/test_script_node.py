"""Script 可编程节点:内嵌脚本声明端口 + 方法重载(1.2 新增)。

覆盖:
- 编译:类属性声明 → NodeType(端口/组/状态/配置/init_in/auto),docstring → 说明书;
- 行为:tick 返回 dict 分发(data_out/control_out/"state")、ctx 属性访问、
  组触发策略、init/schedule 重载、缺省方法;
- 引擎集成:World 运行(数据流/信号/触发)、多实例共享编译、编辑事务、快照;
- 错误路径:语法错误、无 Node 类、未知返回键、声明不一致校验、轻防护命名空间。
"""

import pytest

from eidolon_graph.engine import (AddEdge, AddNode, Event, NodeRegistry, World,
                                  apply_edits, capture, restore_world)
from eidolon_graph.engine.builtins import register_builtins
from eidolon_graph.engine.protocol import NodeImpl, ScheduleContext, TickContext
from eidolon_graph.engine.rng import Rng
from eidolon_graph.engine.script import ScriptError, compile_script
from eidolon_graph.engine.signal import ACTIVE, INACTIVE
from eidolon_graph.model import (
    CATEGORY_CUSTOM,
    Annot, AssetLibrary, ConfigField, ControlIn, ControlOut,
    DataIn, DataOut, Graph, ImplBinding, InputGroup,
    NodeInstance, NodeType, StateField, TriggerIn, Wire,
    serialize, validate
)

# ---------------------------------------------------------------------------
# 脚本样例
# ---------------------------------------------------------------------------

ADDER = '''
class Node:
    """两数相加,记录调用次数。"""
    data_in = [DataIn("a", Annot(int)), DataIn("b", Annot(int))]
    data_out = [DataOut("sum", Annot(int))]
    state = [StateField("calls", 0, Annot(int))]
    groups = [InputGroup("add", inputs=["a", "b"], outputs=["sum"])]

    def tick(self, ctx):
        return {"sum": ctx.a + ctx.b,
                "state": {"calls": ctx.state.get("calls", 0) + 1}}
'''

TRIGGER_NODE = '''
class Node:
    """数据齐 + 触发才执行(ON_DATA_AND_TRIGGER)。"""
    data_in = [DataIn("value", Annot(int))]
    trigger_in = [TriggerIn("go")]
    data_out = [DataOut("out", Annot(int))]
    groups = [InputGroup("arm", inputs=["value"], triggers=["go"], outputs=["out"],
                         policy=ON_DATA_AND_TRIGGER)]

    def tick(self, ctx):
        return {"out": ctx.value * 2}
'''

SIGNAL_NODE = '''
class Node:
    """输入电平取反的信号节点(自走)。"""
    control_in = [ControlIn("a", semantic="level")]
    control_out = [ControlOut("out", default_level=INACTIVE)]
    auto = True

    def tick(self, ctx):
        return {"out": INACTIVE if ctx.control_in["a"] == ACTIVE else ACTIVE}
'''

INIT_NODE = '''
class Node:
    """初始化输入(seed)经 init 方法播种;fire 方法输出 base + value。"""
    data_in = [DataIn("seed", Annot(int)), DataIn("value", Annot(int))]
    data_out = [DataOut("out", Annot(int))]
    state = [StateField("base", 0, Annot(int))]
    init_in = ["seed"]
    groups = [InputGroup("fire", inputs=["value"], outputs=["out"])]

    def init(self, ctx):
        return {"base": ctx.seed * 10}

    def tick(self, ctx):
        return {"out": ctx.state.get("base", 0) + ctx.value}
'''

AUTO_NODE = '''
class Node:
    """自走递增源节点,实时模式按 schedule 发射。"""
    data_out = [DataOut("count", Annot(int))]
    state = [StateField("count", 0, Annot(int))]
    auto = True

    def tick(self, ctx):
        c = ctx.state.get("count", 0) + 1
        return {"count": c, "state": {"count": c}}

    def schedule(self, ctx):
        return 0.5
'''


def make_env():
    lib = AssetLibrary()
    registry = NodeRegistry()
    register_builtins(lib, registry)
    return lib, registry


def add_script(lib, source, type_name):
    """编译脚本并注册为节点类型资产(kind=script 不走实现注册表)。"""
    nt, _ = compile_script(source, type_name)
    lib.add_node_type(nt)
    return nt


# ---------------------------------------------------------------------------
# 编译与声明
# ---------------------------------------------------------------------------

def test_compile_declares_ports_and_doc():
    nt, impl_cls = compile_script(ADDER, "Adder")
    assert nt.name == "Adder"
    assert [p.name for p in nt.data_in] == ["a", "b"]
    assert [p.name for p in nt.data_out] == ["sum"]
    assert [p.name for p in nt.state] == ["calls"]
    assert [(g.name, g.inputs, g.outputs) for g in nt.groups] == \
           [("add", ["a", "b"], ["sum"])]
    assert nt.impl.kind == "script" and nt.impl.source == ADDER
    assert issubclass(impl_cls, NodeImpl)
    impl = impl_cls()
    assert impl.doc()["summary"] == "两数相加,记录调用次数。"


def test_compile_declares_trigger_and_auto():
    nt, _ = compile_script(TRIGGER_NODE, "Triggered")
    assert [p.name for p in nt.trigger_in] == ["go"]
    assert nt.groups[0].policy == "on_data_and_trigger"
    nt2, _ = compile_script(AUTO_NODE, "Auto")
    assert nt2.auto


def test_compile_errors():
    with pytest.raises(ScriptError, match="语法错误"):
        compile_script("class Node:\n  def tick( self:", "Bad")
    with pytest.raises(ScriptError, match="必须定义一个名为 Node 的类"):
        compile_script("x = 1", "NoClass")


# ---------------------------------------------------------------------------
# 行为:tick 分发 / ctx / state / 信号
# ---------------------------------------------------------------------------

def test_tick_data_out_and_state():
    lib, registry = make_env()
    add_script(lib, ADDER, "Adder")
    g = Graph(name="g", nodes=[
        NodeInstance("in_a", "Input"), NodeInstance("in_b", "Input"),
        NodeInstance("n", "Adder"), NodeInstance("out", "Output"),
    ], wires=[
        Wire("in_a", "out", "n", "a"), Wire("in_b", "out", "n", "b"),
        Wire("n", "sum", "out", "msg"),
    ])
    w = World(lib, g, registry)
    w.run([Event("in_a", "in", 2), Event("in_b", "in", 3)])
    assert w._states["out"].state["lines"] == ["5"]
    assert w._states["n"].state["calls"] == 1


def test_tick_unknown_key_raises():
    lib, registry = make_env()
    add_script(lib, ADDER, "Adder")
    # 直接构造实现类验证返回键分发(绕过引擎,聚焦 _convert)
    _, impl_cls = compile_script(ADDER, "Adder")
    impl = impl_cls()
    ctx = TickContext(run_no=0, group="add", rng=Rng(0), data_in={"a": 1, "b": 2},
                      control_in={}, state={}, config={})
    with pytest.raises(ScriptError, match="未声明的键"):
        impl._convert({"typo": 1})


def test_tick_script_signal_node():
    lib, registry = make_env()
    add_script(lib, SIGNAL_NODE, "NotSignal")
    g = Graph(name="g", nodes=[NodeInstance("s", "NotSignal")], wires=[])
    w = World(lib, g, registry)
    w.run()
    assert w.control_out_levels[("s", "out")] == ACTIVE   # a 默认低(level) → 取反高
    w.run([Event("s", "a", ACTIVE, kind="control")])
    assert w.control_out_levels[("s", "out")] == INACTIVE
    w.run([Event("s", "a", INACTIVE, kind="control")])
    assert w.control_out_levels[("s", "out")] == ACTIVE


def test_tick_trigger_policy_group():
    """ON_DATA_AND_TRIGGER:数据齐 + 触发才执行(先到者等待)。"""
    lib, registry = make_env()
    add_script(lib, TRIGGER_NODE, "Triggered")
    g = Graph(name="g", nodes=[
        NodeInstance("in_v", "Input"), NodeInstance("in_go", "Input"),
        NodeInstance("n", "Triggered"), NodeInstance("out", "Output"),
    ], wires=[
        Wire("in_v", "out", "n", "value"), Wire("in_go", "out", "n", "go"),
        Wire("n", "out", "out", "msg"),
    ])
    w = World(lib, g, registry)
    w.run([Event("in_v", "in", 4)])           # 只有数据:等待触发
    assert w._states["out"].state["lines"] == []
    w.run([Event("in_go", "in", True)])       # 触发到达:执行
    assert w._states["out"].state["lines"] == ["8"]


def test_tick_init_method_and_extra_methods():
    """init 播种(init_in)+ tick 组方法组合。"""
    lib, registry = make_env()
    add_script(lib, INIT_NODE, "Seeded")
    g = Graph(name="g", nodes=[
        NodeInstance("in_seed", "Input"), NodeInstance("in_value", "Input"),
        NodeInstance("n", "Seeded"), NodeInstance("out", "Output"),
    ], wires=[
        Wire("in_seed", "out", "n", "seed"), Wire("in_value", "out", "n", "value"),
        Wire("n", "out", "out", "msg"),
    ])
    w = World(lib, g, registry)
    w.run([Event("in_seed", "in", 7), Event("in_value", "in", 1)])  # init → base=70;fire:70+1
    assert w._states["out"].state["lines"] == ["71"]


def test_tick_default_methods_when_missing():
    """脚本只声明端口不重载 tick → 空产出(不崩)。"""
    nt, impl_cls = compile_script("""
class Node:
    data_in = [DataIn("x")]
    data_out = [DataOut("y")]
    groups = [InputGroup("g", inputs=["x"], outputs=["y"])]
""", "Noop")
    impl = impl_cls()
    out = impl.tick(TickContext(run_no=0, group="g", rng=Rng(0), data_in={"x": 1},
                                control_in={}, state={}, config={}))
    assert out.data_out == {} and out.state == {}


def test_tick_auto_source_schedule():
    """auto 源节点每轮自走;schedule 重载返回周期(实时模式用)。"""
    lib, registry = make_env()
    add_script(lib, AUTO_NODE, "AutoTick")
    g = Graph(name="g", nodes=[NodeInstance("n", "AutoTick"),
                               NodeInstance("out", "Output")],
              wires=[Wire("n", "count", "out", "msg")])
    w = World(lib, g, registry)
    w.run()
    assert w._states["out"].state["lines"] == ["1"]
    w.run()
    assert w._states["out"].state["lines"] == ["1", "2"]
    from eidolon_graph.engine.protocol import ScheduleContext
    impl = w._impls["n"]
    assert impl.schedule(ScheduleContext(state={"count": 2}, config={})) == 0.5


# ---------------------------------------------------------------------------
# 引擎集成:多实例缓存 / 编辑 / 快照 / 序列化 / 校验
# ---------------------------------------------------------------------------

def test_world_shares_compiled_impl_for_multi_instances():
    lib, registry = make_env()
    add_script(lib, ADDER, "Adder")
    g = Graph(name="g", nodes=[
        NodeInstance("n1", "Adder"), NodeInstance("n2", "Adder"),
        NodeInstance("in_a", "Input"), NodeInstance("in_b", "Input"),
    ], wires=[
        Wire("in_a", "out", "n1", "a"), Wire("in_a", "out", "n2", "a"),
        Wire("in_b", "out", "n1", "b"), Wire("in_b", "out", "n2", "b"),
    ])
    w = World(lib, g, registry)
    assert len(w._script_cache) == 1           # 两实例共享一次编译
    assert type(w._impls["n1"]) is type(w._impls["n2"])


def test_edit_add_script_node():
    lib, registry = make_env()
    add_script(lib, ADDER, "Adder")
    g = Graph(name="g", nodes=[NodeInstance("in_a", "Input"),
                               NodeInstance("in_b", "Input")], wires=[])
    w = World(lib, g, registry)
    w.edit([AddNode(NodeInstance("n", "Adder")),
            AddEdge(Wire("in_a", "out", "n", "a")),
            AddEdge(Wire("in_b", "out", "n", "b"))])
    assert type(w._impls["n"]).__name__ == "Scripted_Adder"
    w.run([Event("in_a", "in", 2), Event("in_b", "in", 3)])
    assert w._states["n"].state["calls"] == 1


def test_snapshot_restore_script_state():
    lib, registry = make_env()
    add_script(lib, AUTO_NODE, "AutoTick")
    g = Graph(name="g", nodes=[NodeInstance("n", "AutoTick")], wires=[])
    runtime = __import__("eidolon_graph.engine.runtime", fromlist=["World"])
    w = runtime.World(lib, g, registry)
    w.run()
    snap = capture(w)
    w2 = runtime.World(lib, g, registry)
    restore_world(w2, snap)
    assert w2._states["n"].state["count"] == 1
    w2.run()
    assert w2._states["n"].state["count"] == 2


def test_library_serialize_roundtrip_source():
    lib, registry = make_env()
    add_script(lib, ADDER, "Adder")
    d = serialize.library_to_dict(lib)
    lib2 = serialize.library_from_dict(d)
    nt = lib2.node_types["Adder"]
    assert nt.impl.kind == "script" and nt.impl.source == ADDER
    assert [p.name for p in nt.data_out] == ["sum"]


def test_validate_script_ok_and_mismatch():
    lib, registry = make_env()
    add_script(lib, ADDER, "Adder")
    g = Graph(name="g", nodes=[NodeInstance("n", "Adder"),
                               NodeInstance("in_a", "Input"), NodeInstance("in_b", "Input")],
              wires=[Wire("in_a", "out", "n", "a"), Wire("in_b", "out", "n", "b")])
    assert validate(lib, g).ok
    # 资产声明与脚本不一致(手工改资产端口名)→ 校验拒绝
    lib.node_types["Adder"].data_out[0].name = "total"
    rep = validate(lib, g)
    assert not rep.ok and any("不一致" in e for e in rep.errors)


def test_validate_script_syntax_error():
    lib, registry = make_env()
    # 直接构造损坏资产:source 语法错误(绕过编译;校验器应报告编译失败)
    bad = NodeType(name="Broken", category=CATEGORY_CUSTOM, impl=ImplBinding(kind="script", source="def x(:"))
    lib.add_node_type(bad)
    g = Graph(name="g", nodes=[NodeInstance("n", "Broken")], wires=[])
    rep = validate(lib, g)
    assert not rep.ok and any("编译失败" in e for e in rep.errors)


def test_restricted_namespace_blocks_import():
    """轻防护:受限命名空间——脚本方法体内 __import__ 不可用(运行时 NameError)。"""
    _, impl_cls = compile_script("""
class Node:
    data_out = [DataOut("x")]
    def tick(self, ctx):
        return {"x": __import__("os")}
""", "Smuggle")
    impl = impl_cls()
    with pytest.raises(NameError, match="__import__"):
        impl.tick(TickContext(run_no=0, group="step", rng=Rng(0), data_in={},
                              control_in={}, state={}, config={}))
