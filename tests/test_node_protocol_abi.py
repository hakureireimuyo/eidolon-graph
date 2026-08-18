"""节点协议 ABI 测试:Graph Kernel ↔ Node 扩展边界(docs/node-protocol.md)。

本文件模拟一个**外部节点包**:按协议实现"领域节点"并从零注册进内核。
任何破坏本测试的改动 = 破坏协议 = 破坏所有节点包,必须随协议版本演进。

覆盖协议的锁定承诺:
- §2 声明:类型资产声明 + 序列化往返;
- §3 执行:数据产出 / 信号产出(tick 契约);
- §4 异步:宿主完成注入——等待不产出、注入完成事件、因果 trace、确定性;
- §5 持久化:pending 凭证在 state 中,快照/读档续跑;
- §6 注册:宿主显式注册,重名/类型错误拒绝。
"""

from eidolon_graph.engine import Event, NodeRegistry, World
from eidolon_graph.engine.builtins import register_builtins
from eidolon_graph.engine.protocol import NodeImpl, TickContext, TickOutput
from eidolon_graph.model import (
    CATEGORY_CUSTOM,
    ON_TRIGGER, Annot, AssetLibrary, ControlOut, DataIn,
    DataOut, Graph, ImplBinding, InputGroup,
    NodeInstance, NodeType, StateField, TriggerIn, Wire,
    serialize
)
from eidolon_graph.engine.signal import ACTIVE, INACTIVE

# ---------------------------------------------------------------------------
# 外部节点包(模拟):AskLLM —— 领域节点,内核对其零特殊处理
# ---------------------------------------------------------------------------

ASK_LLM = NodeType(
    name="AskLLM",
    category=CATEGORY_CUSTOM,
    data_in=[DataIn("prompt"), DataIn("_result", optional=True)],  # 完成端口 = 可选参数
    data_out=[DataOut("response")],
    control_out=[ControlOut("answered", default_level=INACTIVE)],
    state=[StateField("pending", None), StateField("calls", 0, Annot(int))],
    groups=[
        InputGroup("call", inputs=["prompt"], outputs=[]),           # 发起外部调用
        InputGroup("complete", inputs=["_result"], outputs=["response"]),  # 完成注入
    ],
    impl=ImplBinding(kind="code", name="AskLLM"),
)


class AskLLMImpl(NodeImpl):
    """领域节点实现:异步 = 不产出即等待;结果经宿主完成注入重入。"""

    def tick(self, ctx: TickContext) -> TickOutput:
        if ctx.group == "call":
            prompt = ctx.data_in.get("prompt")
            seq = ctx.state.get("calls", 0) + 1
            # 发起外部调用:pending 凭证进 state(快照/读档续跑的基础)
            return TickOutput(state={"pending": {"prompt": prompt, "seq": seq},
                                     "calls": seq})
        # complete:外部结果到达(宿主注入 run([Event(node, "_result", 值)]))
        result = ctx.data_in.get("_result")
        pending = ctx.state.get("pending")
        if pending is None or result is None:
            return TickOutput()  # 无 pending:忽略(生命周期策略是节点包业务)
        return TickOutput(
            data_out={"response": f"{pending['prompt']}|{result}"},
            control_out={"answered": ACTIVE},
            state={"pending": None},
        )


def make_env():
    lib = AssetLibrary()
    registry = NodeRegistry()
    register_builtins(lib, registry)
    # 外部节点包登记:宿主 import 节点包 + 显式注册(协议 §6)
    lib.add_node_type(ASK_LLM)
    registry.register(ASK_LLM.name, AskLLMImpl)
    return lib, registry


def make_graph():
    """Input(注入提示词)→ AskLLM → Printer(回显)。"""
    return Graph(name="abi", nodes=[
        NodeInstance("i1", "Input"),
        NodeInstance("ask", "AskLLM"),
        NodeInstance("printer", "Output"),
    ], wires=[
        Wire("i1", "out", "ask", "prompt"),
        Wire("ask", "response", "printer", "msg"),
    ])


def make_world():
    lib, registry = make_env()
    return lib, registry, World(lib, make_graph(), registry, seed=0)


# ---------------------------------------------------------------------------
# §2 声明:类型资产序列化往返(编辑器/快照兼容的驱动面)
# ---------------------------------------------------------------------------

def test_type_declaration_roundtrip():
    d = serialize.node_type_to_dict(ASK_LLM)
    back = serialize.node_type_from_dict(d)
    assert back.name == "AskLLM"
    assert {p.name for p in back.data_in} == {"prompt", "_result"}
    assert back.data_in_map()["_result"].optional is True  # 完成端口 = 可选参数
    assert {p.name for p in back.data_out} == {"response"}
    assert {c.name for c in back.control_out} == {"answered"}
    assert {f.name for f in back.state} == {"pending", "calls"}
    assert [g.name for g in back.groups] == ["call", "complete"]
    assert back.impl.name == "AskLLM"


# ---------------------------------------------------------------------------
# §6 注册:宿主显式注册;重名 / 非法实现拒绝
# ---------------------------------------------------------------------------

def test_registry_contract():
    from eidolon_graph.engine import NodeRegistry

    r = NodeRegistry()
    r.register("AskLLM", AskLLMImpl)
    assert r.contains("AskLLM") and r.get("AskLLM") is AskLLMImpl
    try:
        r.register("AskLLM", AskLLMImpl)
    except ValueError:
        pass
    else:
        raise AssertionError("重名注册应被拒绝")
    try:
        r.register("Bad", object)
    except TypeError:
        pass
    else:
        raise AssertionError("非 NodeImpl 子类应被拒绝")


# ---------------------------------------------------------------------------
# §3+§4 执行与异步:等待不产出 → 宿主完成注入 → 因果传播 + trace 记录
# ---------------------------------------------------------------------------

def test_async_host_completion_contract():
    lib, registry, w = make_world()

    # 1) 注入提示词:组 "call" 触发,发起外部调用,等待(不产出)
    w.run([Event("i1", "in", "你好")])
    assert w._states["ask"].state["pending"] == {"prompt": "你好", "seq": 1}
    assert w._states["printer"].state["last_msg"] is None  # 下游无产出 = 等待
    assert w.control_out_levels[("ask", "answered")] == "inactive"
    assert w.run_outputs.get(("ask", "response")) is None

    # 2) 外部结果到达:宿主注入完成事件(与 Input 注入同构)
    w.run([Event("ask", "_result", "世界")])
    assert w._states["printer"].state["last_msg"] == "你好|世界"
    assert w._states["ask"].state["pending"] is None
    assert w.control_out_levels[("ask", "answered")] == "active"

    # 3) 因果 trace 记录两个 epoch 的注入与触发(run+seq 确定性时间线)
    runs = {e["run"] for e in w.trace}
    assert runs == {1, 2}
    data_marks = [e for e in w.trace if e["kind"] == "data" and e["dst"] == "ask"]
    assert {(e["port"], e["src"]) for e in data_marks} == {("prompt", "i1"), ("_result", None)}
    assert any(e["kind"] == "fire" and e["dst"] == "ask" for e in w.trace)


def test_async_determinism_same_input_same_trace():
    """同一图、同一输入序列 → 同一结果与因果 trace(确定性承诺)。"""
    lib, registry = make_env()
    w1 = World(lib, make_graph(), registry, seed=0)
    w2 = World(lib, make_graph(), registry, seed=0)
    for w in (w1, w2):
        w.run([Event("i1", "in", "你好")])
        w.run([Event("ask", "_result", "世界")])
    assert w1._states["printer"].state["last_msg"] == w2._states["printer"].state["last_msg"]
    assert w1.trace == w2.trace


# ---------------------------------------------------------------------------
# §5 持久化:pending 凭证在 state 中 —— 快照/读档续跑
# ---------------------------------------------------------------------------

def test_async_pending_survives_snapshot_restore():
    lib, registry, w = make_world()
    w.run([Event("i1", "in", "你好")])
    assert w._states["ask"].state["pending"] is not None  # 等待中拍快照
    snap = w.snapshot()

    w2 = World(lib, make_graph(), registry, seed=0)
    w2.restore(snap)
    assert w2._states["ask"].state["pending"] == {"prompt": "你好", "seq": 1}
    # 恢复后完成注入:结果与不恢复的世界一致
    w2.run([Event("ask", "_result", "世界")])
    assert w2._states["printer"].state["last_msg"] == "你好|世界"
    assert w2._states["ask"].state["pending"] is None


def test_async_orphan_completion_ignored_by_impl():
    """无 pending 的完成注入:实现自行决定语义(此处忽略)——策略是节点包业务。"""
    lib, registry, w = make_world()
    w.run([Event("ask", "_result", "孤儿")])
    assert w._states["printer"].state["last_msg"] is None
    assert w._states["ask"].state["calls"] == 0


# ---------------------------------------------------------------------------
# §2/§3 触发端口协议面:TriggerIn + 组触发策略(1.0)
# ---------------------------------------------------------------------------

TRIG_GATE = NodeType(
    name="TrigGate",
    category=CATEGORY_CUSTOM,
    trigger_in=[TriggerIn("go")],          # 触发入口:数据线(载荷)或信号线(电平)
    data_out=[DataOut("out")],
    groups=[InputGroup("g", triggers=["go"], outputs=["out"], policy=ON_TRIGGER)],
    impl=ImplBinding(kind="code", name="TrigGate"),
)


class TrigGateImpl(NodeImpl):
    """外部节点包:仅触发入口激活时产出(载荷回显)。"""

    def tick(self, ctx: TickContext) -> TickOutput:
        return TickOutput(data_out={"out": ctx.data_in.get("go")})


def test_trigger_port_protocol_roundtrip():
    """协议锁定:TriggerIn 声明与组策略必须随协议序列化往返。"""
    d = serialize.node_type_to_dict(TRIG_GATE)
    nt = serialize.node_type_from_dict(d)
    assert [t.name for t in nt.trigger_in] == ["go"]
    assert nt.groups[0].triggers == ["go"]
    assert nt.groups[0].policy == ON_TRIGGER


def test_trigger_port_fires_on_injected_payload():
    """协议锁定:宿主注入 TriggerIn 数据 → 激活(载荷进 data_in)。"""
    lib, registry, _ = make_world()
    lib.add_node_type(TRIG_GATE)
    registry.register("TrigGate", TrigGateImpl)
    g = Graph(name="tg", nodes=[
        NodeInstance("tg", "TrigGate"),
        NodeInstance("printer", "Output"),
    ], wires=[Wire("tg", "out", "printer", "msg")])
    w = World(lib, g, registry)
    w.run([Event("tg", "go", "你好")])
    assert w._states["printer"].state["last_msg"] == "你好"
