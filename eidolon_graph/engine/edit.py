"""编辑事务与状态迁移:编辑 = 停止世界的事务,校验后原子提交。

编辑事务 API 属于内核:edit_transaction(world, edits) → {validation, migration_plan}。
tick 由宿主同步调用,事务天然位于轮界(没有"边改边迁"的中间态)。

原则:规则与事实分离——改图不动状态,除非显式删节点/换实现。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

from ..model import Graph, NodeInstance, NodeType, ValidationReport, Wire, validate
from .runtime import CompiledGraph, NodeState, World
from .subgraph import SubgraphNodeImpl

# ---------------------------------------------------------------------------
# 编辑操作
# ---------------------------------------------------------------------------


@dataclass
class AddNode:
    node: NodeInstance


@dataclass
class RemoveNode:
    node_id: str


@dataclass
class AddEdge:
    wire: Wire


@dataclass
class RemoveEdge:
    wire: Wire


@dataclass
class SetConfig:
    node_id: str
    config: dict[str, Any]  # 增量合并


@dataclass
class ChangeImpl:
    node_id: str
    new_type_name: str  # 换实现 = 换类型资产(同协议则状态保留)


EditOp = AddNode | RemoveNode | AddEdge | RemoveEdge | SetConfig | ChangeImpl


def apply_edits(graph: Graph, ops: list[EditOp]) -> Graph:
    """纯图层面应用编辑操作(编辑器草稿用):返回新图,不改原图。

    结构错误(节点/连线不存在、节点 id 重复)抛 ValueError;语义问题留给校验器。
    """
    draft = deepcopy(graph)
    nodes = draft.node_map()
    for op in ops:
        if isinstance(op, AddNode):
            if op.node.node_id in nodes:
                raise ValueError(f"节点 id '{op.node.node_id}' 已存在")
            ni = deepcopy(op.node)
            draft.nodes.append(ni)
            nodes[ni.node_id] = ni
        elif isinstance(op, RemoveNode):
            if op.node_id not in nodes:
                raise ValueError(f"节点 '{op.node_id}' 不存在")
            del nodes[op.node_id]
            draft.nodes = [n for n in draft.nodes if n.node_id != op.node_id]
            draft.wires = [w for w in draft.wires
                           if w.src_node != op.node_id and w.dst_node != op.node_id]
        elif isinstance(op, AddEdge):
            if op.wire.src_node not in nodes or op.wire.dst_node not in nodes:
                raise ValueError(f"连线端点节点不存在:{op.wire}")
            if op.wire not in draft.wires:  # 重复连线去重
                draft.wires.append(op.wire)
        elif isinstance(op, RemoveEdge):
            if op.wire not in draft.wires:
                raise ValueError(f"连线不存在:{op.wire}")
            draft.wires = [w for w in draft.wires if w != op.wire]
        elif isinstance(op, SetConfig):
            if op.node_id not in nodes:
                raise ValueError(f"节点 '{op.node_id}' 不存在")
            nodes[op.node_id].config.update(deepcopy(op.config))
        elif isinstance(op, ChangeImpl):
            if op.node_id not in nodes:
                raise ValueError(f"节点 '{op.node_id}' 不存在")
            nodes[op.node_id].type_name = op.new_type_name
    return draft


# ---------------------------------------------------------------------------
# 迁移计划与结果
# ---------------------------------------------------------------------------


@dataclass
class ReimplementRecord:
    node_id: str
    old_type: str
    new_type: str
    action: Literal["kept", "migrated", "reset"]


@dataclass
class MigrationPlan:
    kept: list[str] = field(default_factory=list)              # 状态保留的节点
    added: list[str] = field(default_factory=list)             # 新节点(初始状态)
    removed: list[str] = field(default_factory=list)           # 状态销毁的节点
    rewarmed: list[tuple[str, str]] = field(default_factory=list)
    # 失去值来源的端口:数据端口重归未就绪(重新 warm-up),控制端口回默认电平
    reimplemented: list[ReimplementRecord] = field(default_factory=list)
    edges_removed: list[Wire] = field(default_factory=list)    # RemoveNode 级联删除的连线


@dataclass
class EditResult:
    ok: bool
    validation: ValidationReport
    migration_plan: MigrationPlan | None = None


def _protocol_compatible(old: NodeType, new: NodeType) -> bool:
    """同协议 = 端口集合与状态字段(名/序)一致。配置不参与(属资产侧)。"""
    return (
        [p.name for p in old.data_in] == [p.name for p in new.data_in]
        and [p.name for p in old.data_out] == [p.name for p in new.data_out]
        and [(c.name, c.semantic, c.target) for c in old.control_in]
        == [(c.name, c.semantic, c.target) for c in new.control_in]
        and [c.name for c in old.control_out] == [c.name for c in new.control_out]
        and [f.name for f in old.state] == [f.name for f in new.state]
    )


def edit_transaction(world: World, ops: list[EditOp]) -> EditResult:
    """编辑事务:图草稿上应用 ops → 校验 → 原子提交(状态迁移一次完成)。"""
    draft = apply_edits(world.graph, ops)
    report = validate(world.lib, draft)
    if not report.ok:
        return EditResult(ok=False, validation=report)
    plan = _compute_plan(world, draft, ops)
    _apply_migration(world, draft, plan, len(ops))
    return EditResult(ok=True, validation=report, migration_plan=plan)


def _compute_plan(world: World, draft: Graph, ops: list[EditOp]) -> MigrationPlan:
    plan = MigrationPlan()
    old_ids = set(world._node_map)
    # 连线集合发生变化的端口(增/删/级联):失去旧来源 → 就绪重置(重新 warm-up)。
    # 换源在一个事务内完成([RemoveEdge, AddEdge])同样重置——旧源的 held 值不能混入新源。
    touched: set[tuple[str, str]] = set()
    for op in ops:
        if isinstance(op, AddNode):
            plan.added.append(op.node.node_id)
        elif isinstance(op, RemoveNode):
            plan.removed.append(op.node_id)
            for w in world.graph.wires:
                if w.src_node == op.node_id or w.dst_node == op.node_id:
                    plan.edges_removed.append(w)
            # 级联断线同样触发就绪重置(目标节点存活的端口)
            for w in world.graph.wires:
                if w.src_node != op.node_id or w.dst_node == op.node_id:
                    continue
                touched.add((w.dst_node, w.dst_port))
        elif isinstance(op, RemoveEdge):
            touched.add((op.wire.dst_node, op.wire.dst_port))
        elif isinstance(op, AddEdge):
            touched.add((op.wire.dst_node, op.wire.dst_port))
        elif isinstance(op, ChangeImpl):
            nid = op.node_id
            old_nt = world.compiled.types[nid]
            new_nt = world.lib.node_types[op.new_type_name]  # 校验已保证存在
            if _protocol_compatible(old_nt, new_nt):
                action = "kept"
            elif op.new_type_name in world.impl_migrations:
                action = "migrated"
            else:
                action = "reset"
            plan.reimplemented.append(
                ReimplementRecord(nid, old_nt.name, op.new_type_name, action))
    surviving = old_ids - set(plan.removed)
    for (nid, port) in touched:
        if nid in surviving:
            plan.rewarmed.append((nid, port))
    plan.kept = sorted(old_ids - set(plan.removed))
    return plan


def _apply_migration(world: World, draft: Graph, plan: MigrationPlan, op_count: int) -> None:
    # 1) 换图 + 重编译 + 重建节点运行时(状态按迁移规则)
    world.graph = draft
    world.compiled = CompiledGraph.build(world.lib, draft)
    world._node_map = draft.node_map()
    removed = set(plan.removed)
    reimpl = {r.node_id: r for r in plan.reimplemented}
    old_states = world._states
    new_states: dict[str, NodeState] = {}
    new_impls: dict[str, Any] = {}
    stack = (draft.name,)
    for ni in draft.nodes:
        nid = ni.node_id
        nt = world.compiled.types[nid]
        st: NodeState
        if nid in old_states and nid not in removed:
            st = old_states[nid]
            if nid in reimpl:
                rec = reimpl[nid]
                if rec.action == "kept":
                    pass  # 同协议:状态保留
                elif rec.action == "migrated":
                    st.state = world.impl_migrations[nt.name](deepcopy(st.state), nt)
                else:
                    st.state = nt.default_state()
                st.inner = None  # 换实现后旧内嵌世界作废(V1:子图状态不迁移)
            if nt.impl.kind == "subgraph" and st.inner is None:
                st.inner = world._build_inner(nt, stack)
        else:
            st = NodeState(state=nt.default_state())
            if nt.impl.kind == "subgraph":
                st.inner = world._build_inner(nt, stack)
        new_states[nid] = st
        new_impls[nid] = (SubgraphNodeImpl(nt) if nt.impl.kind == "subgraph"
                          else world.registry.get(nt.impl.name or nt.name)())
    world._states = new_states
    world._impls = new_impls

    # 2) held 表迁移:剪除消失的节点/端口;失去来源的端口就绪重置
    rewarm_set = set(plan.rewarmed)
    new_dih: dict[tuple[str, str], Any] = {}
    new_cih: dict[tuple[str, str], str] = {}
    new_doh: dict[tuple[str, str], Any] = {}
    new_coh: dict[tuple[str, str], str] = {}
    for (nid, port), pkt in world.data_in_held.items():
        if nid in new_states and port in world.compiled.types[nid].data_in_map() \
                and (nid, port) not in rewarm_set:
            new_dih[(nid, port)] = pkt
    for (nid, port), pkt in world.data_out_held.items():
        if nid in new_states and port in world.compiled.types[nid].data_out_map():
            new_doh[(nid, port)] = pkt
    for (nid, port), lvl in world.control_in_held.items():
        if nid in new_states and port in world.compiled.types[nid].control_in_map() \
                and (nid, port) not in rewarm_set:
            new_cih[(nid, port)] = lvl
    for (nid, port), lvl in world.control_out_held.items():
        if nid in new_states and port in world.compiled.types[nid].control_out_map():
            new_coh[(nid, port)] = lvl
    for nid in new_states:
        nt = world.compiled.types[nid]
        for c in nt.control_in:
            new_cih.setdefault((nid, c.name), c.effective_default())
        for c in nt.control_out:
            new_coh.setdefault((nid, c.name), c.default_level)
    world.data_in_held = new_dih
    world.control_in_held = new_cih
    world.data_out_held = new_doh
    world.control_out_held = new_coh

    # 3) 日志记录(只追加的历史)
    world.log.append({"tick": world.tick_no, "level": "edit",
                      "message": f"编辑提交:{op_count} 个操作,"
                                 f"删除 {len(plan.removed)} 节点,"
                                 f"重置 {len(plan.rewarmed)} 端口"})
