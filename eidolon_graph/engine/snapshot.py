"""世界快照:两次运行之间世界静止,是天然 checkpoint 点。

快照 = 图资产版本引用 + 节点状态表 + 输入缓冲表(含新鲜标记)+ 端口信号电平表
+ 全局变量表 + 运行序号 + 每节点 RNG 状态(种子/计数器)+ 日志。读档 = 完整恢复
运行中状态(含缓冲半满、信号电平、RNG),世界从断点精确续跑;子图快照递归内嵌。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from ..model import KERNEL_VERSION, compatible
from .rng import Rng


@dataclass
class NodeSnapshot:
    """单节点快照:状态字段表 + 输入缓冲/新鲜标记 + 触发事件 + 信号电平 + 熔断器 + 内嵌子图。"""

    state: dict[str, Any]
    buffers: dict[str, Any]          # 端口 → 最近值(键存在即有值)
    fresh: list[str]                 # 新鲜端口(触发后清零)
    trigger_fresh: list[str]         # 未消费的触发事件(TriggerIn 激活请求)
    trigger_in_levels: dict[str, str]  # 信号线触发输入的上一电平(变化检测)
    control_in_levels: dict[str, str]
    output_signals: dict[str, str]
    control_out_levels: dict[str, str]
    initialized: bool
    fault_count: int
    circuit_open: bool
    circuit_cool: int
    inner: dict | None = None        # 子图内嵌快照(递归)

    def to_dict(self) -> dict:
        return {"state": self.state, "buffers": self.buffers,
                "fresh": list(self.fresh),
                "trigger_fresh": list(self.trigger_fresh),
                "trigger_in_levels": self.trigger_in_levels,
                "control_in_levels": self.control_in_levels,
                "output_signals": self.output_signals,
                "control_out_levels": self.control_out_levels,
                "initialized": self.initialized,
                "fault_count": self.fault_count, "circuit_open": self.circuit_open,
                "circuit_cool": self.circuit_cool, "inner": self.inner}

    @classmethod
    def from_dict(cls, d: dict) -> "NodeSnapshot":
        return cls(state=dict(d["state"]), buffers=dict(d["buffers"]),
                   fresh=list(d.get("fresh", [])),
                   trigger_fresh=list(d.get("trigger_fresh", [])),
                   trigger_in_levels=dict(d.get("trigger_in_levels", {})),
                   control_in_levels=dict(d["control_in_levels"]),
                   output_signals=dict(d["output_signals"]),
                   control_out_levels=dict(d["control_out_levels"]),
                   initialized=d.get("initialized", True),
                   fault_count=d["fault_count"], circuit_open=d["circuit_open"],
                   circuit_cool=d["circuit_cool"], inner=d.get("inner"))


@dataclass
class Snapshot:
    """世界快照:规则资产引用 + 世界事实 + 执行器状态。"""

    kernel_version: str
    graph_name: str
    graph_kernel_version: str  # 图资产版本引用(旧存档继续引用旧资产版本)
    run_no: int
    seed: int
    rngs: dict[str, dict]      # 节点 id → {seed, counter}(每节点独立流)
    globals_: dict[str, Any]
    nodes: dict[str, NodeSnapshot]
    log: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"kernel_version": self.kernel_version, "graph_name": self.graph_name,
                "graph_kernel_version": self.graph_kernel_version, "run_no": self.run_no,
                "seed": self.seed, "rngs": dict(self.rngs),
                "globals": self.globals_,
                "nodes": {nid: ns.to_dict() for nid, ns in self.nodes.items()},
                "log": self.log}

    @classmethod
    def from_dict(cls, d: dict) -> "Snapshot":
        return cls(kernel_version=d["kernel_version"], graph_name=d["graph_name"],
                   graph_kernel_version=d["graph_kernel_version"], run_no=d["run_no"],
                   seed=d.get("seed", 0), rngs={k: dict(v) for k, v in d.get("rngs", {}).items()},
                   globals_=dict(d["globals"]),
                   nodes={nid: NodeSnapshot.from_dict(x) for nid, x in d["nodes"].items()},
                   log=list(d.get("log", [])))


def capture(world: Any) -> Snapshot:
    """在两次运行之间拍快照(宿主保证不在 run 中途调用)。"""
    nodes: dict[str, NodeSnapshot] = {}
    for ni in world.graph.nodes:
        nid = ni.node_id
        st = world._states[nid]
        impl = world._impls[nid]
        ns = NodeSnapshot(
            state=deepcopy(st.state),
            buffers={port: deepcopy(v) for port, v in impl.buffers.items()},
            fresh=sorted(p for p in impl.fresh),
            trigger_fresh=sorted(p for p in impl.trigger_fresh),
            trigger_in_levels={port: lvl for (n, port), lvl in world.trigger_in_levels.items()
                               if n == nid},
            control_in_levels={port: lvl for (n, port), lvl in world.control_in_levels.items()
                               if n == nid},
            output_signals={port: lvl for (n, port), lvl in world.output_signals.items()
                            if n == nid},
            control_out_levels={port: lvl for (n, port), lvl in world.control_out_levels.items()
                                if n == nid},
            initialized=st.initialized,
            fault_count=st.fault_count,
            circuit_open=st.circuit_open,
            circuit_cool=st.circuit_cool,
            inner=capture(st.inner).to_dict() if st.inner is not None else None,
        )
        nodes[nid] = ns
    return Snapshot(kernel_version=KERNEL_VERSION, graph_name=world.graph.name,
                    graph_kernel_version=world.graph.kernel_version, run_no=world.run_no,
                    seed=world.seed,
                    rngs={nid: rng.snapshot() for nid, rng in world.rngs.items()},
                    globals_=deepcopy(world.globals_), nodes=nodes,
                    log=deepcopy(world.log))


def restore_world(world: Any, snap: Snapshot) -> None:
    """读档:完整恢复运行中状态。要求图资产名与内核主版本匹配、节点集一致。"""
    if not compatible(snap.graph_kernel_version, KERNEL_VERSION):
        raise ValueError(f"快照记录的图资产内核版本 '{snap.graph_kernel_version}' 与当前 "
                         f"'{KERNEL_VERSION}' 主版本不兼容,拒绝读档")
    if snap.graph_name != world.graph.name:
        raise ValueError(f"快照图资产 '{snap.graph_name}' 与当前世界图 '{world.graph.name}' 不符")
    if set(snap.nodes) != set(world._states):
        raise ValueError("快照节点集与当前图不符:改图后的旧快照需走编辑事务迁移,不支持直接恢复")

    world.run_no = snap.run_no
    world.seed = snap.seed
    for nid, st in snap.rngs.items():
        world.rngs[nid].restore(st)
    world.globals_ = deepcopy(snap.globals_)
    world.log = deepcopy(snap.log)
    world.output_signals.clear()
    world.control_in_levels.clear()
    world.control_out_levels.clear()
    world.trigger_in_levels.clear()
    for nid, ns in snap.nodes.items():
        st = world._states[nid]
        impl = world._impls[nid]
        st.state = deepcopy(ns.state)
        impl._buffers = {port: deepcopy(v) for port, v in ns.buffers.items()}
        impl._fresh = set(ns.fresh)
        impl._trigger_fresh = set(ns.trigger_fresh)
        st.initialized = ns.initialized
        st.fault_count = ns.fault_count
        st.circuit_open = ns.circuit_open
        st.circuit_cool = ns.circuit_cool
        for port, lvl in ns.control_in_levels.items():
            world.control_in_levels[(nid, port)] = lvl
        for port, lvl in ns.output_signals.items():
            world.output_signals[(nid, port)] = lvl
        for port, lvl in ns.control_out_levels.items():
            world.control_out_levels[(nid, port)] = lvl
        for port, lvl in ns.trigger_in_levels.items():
            world.trigger_in_levels[(nid, port)] = lvl
        if st.inner is not None:
            if ns.inner is None:
                raise ValueError(f"子图节点 [{nid}] 缺少内嵌快照")
            restore_world(st.inner, Snapshot.from_dict(ns.inner))
    # 信号/控制电平默认兜底(从未收到的端口不出现在快照中);
    # 信号线触发输入:电平记录按当前推导重算(防恢复后第一次投递被误判为"变化")
    for nid in world._states:
        nt = world.compiled.types[nid]
        for c in nt.control_in:
            world.control_in_levels.setdefault((nid, c.name), c.effective_default())
        for c in nt.control_out:
            world.control_out_levels.setdefault((nid, c.name), c.default_level)
        for p in nt.data_out:
            world.output_signals.setdefault((nid, p.name), "active")
        for t in nt.trigger_in:
            if (nid, t.name, "signal") in world.compiled.in_edge:
                world.trigger_in_levels.setdefault((nid, t.name),
                                                   world._input_signal(nid, t.name))
