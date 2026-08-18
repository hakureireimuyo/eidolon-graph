"""组触发策略(Trigger Policy)与 TriggerIn 运行时语义测试(1.0)。

覆盖:
- 策略四态:ON_ALL_DATA_READY / ON_ANY_DATA / ON_TRIGGER / ON_DATA_AND_TRIGGER;
- 信号线触发:电平变化触发、电平保持不重复、双沿各计一次;
- 载荷瞬态:数据线载荷触发后清空;信号线-only 无载荷;
- 空 wired 差异:ALL 真空真(每访问触发)vs ANY 永不触发;
- required_closed 交互:必需数据参数被信号关闭 → 组不执行;
- 门控保留:enable inactive 期间触发事件保留,重开即触发;
- 暂停/恢复:暂停期控制输出挂起,恢复冲刷后触发送达;
- 快照持久化:跨 run 等待的触发事件随快照保留;信号线电平重推导;
- 编辑事务:RemoveEdge 删触发来源 → 事件不再送达。
"""

from eidolon_graph.engine import (AddEdge, AddNode, Event, NodeImpl, NodeRegistry,
                                  RemoveEdge, TickContext, TickOutput, World)
from eidolon_graph.engine.builtins import register_builtins
from eidolon_graph.model import (
    CATEGORY_CUSTOM,
    ACTIVE, INACTIVE, ON_ALL_DATA_READY, ON_ANY_DATA,
    ON_DATA_AND_TRIGGER, ON_TRIGGER, AssetLibrary,
    ControlIn, ControlOut, DataIn, DataOut, Graph,
    ImplBinding, InputGroup, NodeInstance, NodeType,
    TriggerIn, Wire
)


def make_env():
    lib = AssetLibrary()
    registry = NodeRegistry()
    register_builtins(lib, registry)
    return lib, registry


class PolicyImpl(NodeImpl):
    """通用策略节点:out = [a, b, t](触发时看到的参数与载荷)。

    源节点自走(step)不产出——只有组触发才输出,保证空组策略测试
    测的是组触发判定而非源节点的 step 行为。
    """

    def tick(self, ctx: TickContext) -> TickOutput:
        if ctx.group == "step":
            return TickOutput()
        return TickOutput(data_out={"out": [ctx.data_in.get("a"),
                                            ctx.data_in.get("b"),
                                            ctx.data_in.get("t")]})


def make_policy_world(policy, inputs=("a", "b"), triggers=("t",), auto=False):
    # ALL/ANY 策略不使用触发输入(死声明校验);默认调用的 (("a","b"),("t",))
    # 只在 TRIGGER/AND 策略下保留触发输入
    if policy in (ON_ALL_DATA_READY, ON_ANY_DATA):
        triggers = ()
    lib, registry = make_env()
    nt = NodeType(
        name="Policy",
        category=CATEGORY_CUSTOM,
        data_in=[DataIn(n) for n in inputs],
        trigger_in=[TriggerIn(n) for n in triggers],
        data_out=[DataOut("out")],
        groups=[InputGroup("g", inputs=list(inputs), triggers=list(triggers),
                           outputs=["out"], policy=policy)],
        auto=auto,
        impl=ImplBinding(kind="code", name="Policy"),
    )
    lib.add_node_type(nt)
    registry.register("Policy", PolicyImpl)
    nodes = [NodeInstance(f"in_{p}", "Input") for p in inputs]
    nodes += [NodeInstance("n", "Policy"), NodeInstance("printer", "Output")]
    wires = [Wire(f"in_{p}", "out", "n", p) for p in inputs]
    wires.append(Wire("n", "out", "printer", "msg"))
    g = Graph(name="policy", nodes=nodes, wires=wires)
    return World(lib, g, registry)


# ---------------------------------------------------------------------------
# 策略四态
# ---------------------------------------------------------------------------

def test_on_all_data_ready_requires_all():
    w = make_policy_world(ON_ALL_DATA_READY)
    w.run([Event("in_a", "in", 1)])
    assert w._states["printer"].state["last_msg"] is None  # 单到不触发
    w.run([Event("in_b", "in", 2)])  # 齐到触发
    assert w._states["printer"].state["last_msg"] == [1, 2, None]


def test_on_any_data_fires_on_first_arrival():
    w = make_policy_world(ON_ANY_DATA)
    w.run([Event("in_a", "in", 1)])
    assert w._states["printer"].state["last_msg"] == [1, None, None]
    w.run([Event("in_b", "in", 2)])  # 第二个到达再次触发
    assert w._states["printer"].state["last_msg"] == [None, 2, None]


def test_on_trigger_fires_on_trigger_event():
    w = make_policy_world(ON_TRIGGER, inputs=(), triggers=("t",))
    w.run([Event("n", "t", "go")])  # 数据线直注入:载荷 + 激活
    assert w._states["printer"].state["last_msg"] == [None, None, "go"]
    w.run([])  # 无事件不触发(与 ALL 的真空真不同:ON_TRIGGER 只看事件)
    assert w._states["printer"].state["last_msg"] == [None, None, "go"]


def test_on_data_and_trigger_requires_both():
    w = make_policy_world(ON_DATA_AND_TRIGGER, inputs=("a",))
    w.run([Event("in_a", "in", 1)])  # 数据先到:等待
    assert w._states["printer"].state["last_msg"] is None
    w.run([Event("n", "t", "go")])  # 触发事件到达:数据齐 + 事件 → 触发
    assert w._states["printer"].state["last_msg"] == [1, None, "go"]


def test_on_data_and_trigger_data_after_trigger():
    w = make_policy_world(ON_DATA_AND_TRIGGER, inputs=("a",))
    w.run([Event("n", "t", "go")])  # 触发先到:事件保留等待数据(跨 run)
    assert w._states["printer"].state["last_msg"] is None
    w.run([Event("in_a", "in", 1)])  # 数据到达:触发
    assert w._states["printer"].state["last_msg"] == [1, None, "go"]


# ---------------------------------------------------------------------------
# 信号线触发(Latch → Buffer.flush):电平变化触发、保持不重复、双沿
# ---------------------------------------------------------------------------

def make_latch_buffer_world():
    lib, registry = make_env()
    g = Graph(name="latch-buffer", nodes=[
        NodeInstance("in", "Input"),
        NodeInstance("latch", "Latch"),
        NodeInstance("buffer", "Buffer"),
        NodeInstance("printer", "Output"),
    ], wires=[
        Wire("in", "out", "buffer", "put"),
        Wire("latch", "q", "buffer", "flush", dst_slot="signal"),  # 信号线 → 触发入口
        Wire("buffer", "items", "printer", "msg"),
    ])
    return World(lib, g, registry)


def test_signal_wire_fires_on_level_change_only():
    w = make_latch_buffer_world()
    w.run([Event("in", "in", "v1")])  # 装填
    # set 高 → q 电平变化 → flush 触发 → 输出累积数据并清空
    w.run([Event("latch", "set", ACTIVE, kind="control")])
    assert w._states["printer"].state["last_msg"] == ["v1"]
    assert w._states["buffer"].state["items"] == []
    # 电平保持不重复触发:再发 set(电平未变)不产生新事件
    w.run([Event("latch", "set", ACTIVE, kind="control")])
    assert w._states["printer"].state["last_msg"] == ["v1"]
    # set 拉低(q 保持,状态保留)+ reset 拉高:q 电平变低(第二沿)→ flush 触发
    # (Latch 是 set 优先:reset 前必须先拉低 set)
    w.run([Event("in", "in", "v2")])
    w.run([Event("latch", "set", INACTIVE, kind="control")])
    w.run([Event("latch", "reset", ACTIVE, kind="control")])
    assert w._states["printer"].state["last_msg"] == ["v2"]
    # 第三沿:reset 拉低 + set 拉高 → 再次触发
    w.run([Event("in", "in", "v3")])
    w.run([Event("latch", "reset", INACTIVE, kind="control")])
    w.run([Event("latch", "set", ACTIVE, kind="control")])
    assert w._states["printer"].state["last_msg"] == ["v3"]


def test_signal_wire_trigger_has_no_payload():
    w = make_latch_buffer_world()
    w.run([Event("in", "in", "x")])
    w.run([Event("latch", "set", ACTIVE, kind="control")])  # flush 触发但无载荷
    assert w._states["buffer"].state["items"] == []  # 已清空
    assert "flush" not in w._impls["buffer"].buffers  # 信号线不产生缓冲


# ---------------------------------------------------------------------------
# 载荷瞬态:数据线载荷触发后清空
# ---------------------------------------------------------------------------

def test_trigger_payload_consumed_after_fire():
    w = make_policy_world(ON_TRIGGER, inputs=(), triggers=("t",))
    w.run([Event("n", "t", "go")])
    assert w._states["printer"].state["last_msg"] == [None, None, "go"]
    assert "t" not in w._impls["n"].buffers  # 触发后缓冲清空(瞬态)
    w.run([])  # 再次访问不触发(无新事件)
    assert w._states["printer"].state["last_msg"] == [None, None, "go"]


# ---------------------------------------------------------------------------
# 空 wired 差异:ALL 真空真 vs ANY 永不触发(auto 节点每轮唤醒)
# ---------------------------------------------------------------------------

def test_all_data_ready_vacuous_truth_with_empty_wired():
    w = make_policy_world(ON_ALL_DATA_READY, inputs=(), triggers=(), auto=True)
    w.run([])  # 源节点每轮播种 → 空组真空真触发
    assert w._states["printer"].state["last_msg"] == [None, None, None]


def test_any_data_never_fires_with_empty_wired():
    w = make_policy_world(ON_ANY_DATA, inputs=(), triggers=(), auto=True)
    w.run([])
    assert w._states["printer"].state["last_msg"] is None  # 永不触发


# ---------------------------------------------------------------------------
# required_closed 交互:必需数据参数被信号关闭 → 组不执行(即便事件已到)
# ---------------------------------------------------------------------------

def test_required_closed_blocks_group_even_with_trigger():
    lib, registry = make_env()
    nt = NodeType(
        name="Policy",
        category=CATEGORY_CUSTOM,
        data_in=[DataIn("a")],
        trigger_in=[TriggerIn("t")],
        data_out=[DataOut("out")],
        groups=[InputGroup("g", inputs=["a"], triggers=["t"], outputs=["out"],
                           policy=ON_DATA_AND_TRIGGER)],
        impl=ImplBinding(kind="code", name="Policy"),
    )
    lib.add_node_type(nt)
    registry.register("Policy", PolicyImpl)
    g = Graph(name="g", nodes=[
        NodeInstance("in_a", "Input"),
        NodeInstance("latch", "Latch"),
        NodeInstance("n", "Policy"),
        NodeInstance("printer", "Output"),
    ], wires=[
        Wire("in_a", "out", "n", "a"),
        Wire("latch", "q", "n", "a", dst_slot="signal"),  # 显式信号线屏蔽 a
        Wire("n", "out", "printer", "msg"),
    ])
    w = World(lib, g, registry)
    # Latch 默认 q = inactive → a 信号关闭:数据丢弃、事件保留,组不执行
    w.run([Event("in_a", "in", 1), Event("n", "t", "go")])
    assert w._states["printer"].state["last_msg"] is None
    # q 拉高 → a 恢复带电;重新提供数据(关闭期数据已失效)→ 触发
    w.run([Event("latch", "set", ACTIVE, kind="control")])
    w.run([Event("in_a", "in", 2)])
    assert w._states["printer"].state["last_msg"] == [2, None, "go"]


# ---------------------------------------------------------------------------
# 门控保留:enable inactive 期间触发事件保留,重开即触发
# ---------------------------------------------------------------------------

def test_trigger_event_kept_while_gated():
    lib, registry = make_env()
    nt = NodeType(
        name="Policy",
        category=CATEGORY_CUSTOM,
        trigger_in=[TriggerIn("t")],
        control_in=[ControlIn("enable", semantic="enable")],
        data_out=[DataOut("out")],
        groups=[InputGroup("g", triggers=["t"], outputs=["out"], policy=ON_TRIGGER)],
        impl=ImplBinding(kind="code", name="Policy"),
    )
    lib.add_node_type(nt)
    registry.register("Policy", PolicyImpl)
    g = Graph(name="g", nodes=[
        NodeInstance("n", "Policy"),
        NodeInstance("printer", "Output"),
    ], wires=[Wire("n", "out", "printer", "msg")])
    w = World(lib, g, registry)
    w.run([Event("n", "enable", INACTIVE, kind="control")])  # 关门
    w.run([Event("n", "t", "go")])  # 门内事件到达:保留(组被门控)
    assert w._states["printer"].state["last_msg"] is None
    w.run([Event("n", "enable", ACTIVE, kind="control")])  # 开门 → 事件生效
    assert w._states["printer"].state["last_msg"] == [None, None, "go"]


# ---------------------------------------------------------------------------
# 暂停/恢复:暂停期控制输出挂起,恢复冲刷后触发送达
# ---------------------------------------------------------------------------

def test_pause_resume_flushes_trigger_delivery():
    w = make_latch_buffer_world()
    w.run([Event("in", "in", "x")])
    w.pause()
    w.run([Event("latch", "set", ACTIVE, kind="control")])  # 暂停期:q 电平挂起
    assert w._states["printer"].state["last_msg"] is None  # flush 未收到
    w.resume()  # 恢复冲刷:电平投递 → flush 触发
    assert w._states["printer"].state["last_msg"] == ["x"]


# ---------------------------------------------------------------------------
# 快照持久化:触发事件跨快照保留;信号线电平重推导
# ---------------------------------------------------------------------------

def test_snapshot_keeps_pending_trigger_event():
    w = make_policy_world(ON_DATA_AND_TRIGGER, inputs=("a",))
    w.run([Event("n", "t", "go")])  # 触发事件已到,数据未到
    snap = w.snapshot()
    w2 = make_policy_world(ON_DATA_AND_TRIGGER, inputs=("a",))
    w2.restore(snap)
    w2.run([Event("in_a", "in", 1)])  # 数据到达 → 触发(事件随快照保留)
    assert w2._states["printer"].state["last_msg"] == [1, None, "go"]


def test_snapshot_rederives_trigger_level():
    w = make_latch_buffer_world()
    w.run([Event("in", "in", "x")])
    w.run([Event("latch", "set", ACTIVE, kind="control")])  # q 高:flush 触发,输出 x
    assert w._states["printer"].state["last_msg"] == ["x"]
    snap = w.snapshot()
    w2 = make_latch_buffer_world()
    w2.restore(snap)
    # 恢复后 q 电平已重推导:同电平再投递不产生虚假触发
    w2.run([Event("latch", "set", ACTIVE, kind="control")])
    assert w2._states["printer"].state["last_msg"] == ["x"]
    # 真变化才触发:set 拉低 + reset 拉高 → flush(空缓冲不产出,msg 保持)
    w2.run([Event("latch", "set", INACTIVE, kind="control")])
    w2.run([Event("latch", "reset", ACTIVE, kind="control")])
    assert w2._states["printer"].state["last_msg"] == ["x"]
    # 重新装填后触发正常
    w2.run([Event("in", "in", "y")])
    w2.run([Event("latch", "reset", INACTIVE, kind="control")])
    w2.run([Event("latch", "set", ACTIVE, kind="control")])
    assert w2._states["printer"].state["last_msg"] == ["y"]


# ---------------------------------------------------------------------------
# 编辑事务:RemoveEdge 删触发来源 → 事件不再送达
# ---------------------------------------------------------------------------

def test_edit_remove_trigger_edge_stops_delivery():
    w = make_policy_world(ON_TRIGGER, inputs=(), triggers=("t",))
    res = w.edit([AddNode(NodeInstance("in_a", "Input")),
                  AddEdge(Wire("in_a", "out", "n", "t"))])  # 加输入源 + 数据线
    assert res.ok
    w.run([Event("in_a", "in", "go")])  # 经连线送达 → 触发
    assert w._states["printer"].state["last_msg"] == [None, None, "go"]
    res = w.edit([RemoveEdge(Wire("in_a", "out", "n", "t"))])
    assert res.ok
    w.run([Event("in_a", "in", "again")])  # 线已删:不再送达
    assert w._states["printer"].state["last_msg"] == [None, None, "go"]
