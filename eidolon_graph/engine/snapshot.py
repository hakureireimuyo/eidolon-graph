"""世界快照:同步轮次的轮界是天然 checkpoint 点——每 tick 结束世界状态确定且自洽。

快照 = 图资产版本引用 + 节点状态表 + 端口 held 值表 + 全局变量表 + 轮次计数
+ RNG 状态(种子/计数器)+ 日志。读档 = 完整恢复运行中状态(含就绪/warm、
held 值、RNG),世界从断点精确续跑;子图快照递归内嵌。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from ..model import KERNEL_VERSION, compatible
from .signal import DataPacket


@dataclass
class NodeSnapshot:
    """单节点快照:状态字段表 + 各端口 held 值 + 熔断器状态 + 内嵌子图快照。"""

    state: dict[str, Any]
    data_in_held: dict[str, dict | None]     # 端口名 → 数据包 dict | None(包存在即 warm)
    control_in_held: dict[str, str]
    data_out_held: dict[str, dict | None]
    control_out_held: dict[str, str]
    fault_count: int
    circuit_open: bool
    circuit_cool: int
    inner: dict | None = None                # 子图内嵌快照(递归)

    def to_dict(self) -> dict:
        return {"state": self.state, "data_in_held": self.data_in_held,
                "control_in_held": self.control_in_held, "data_out_held": self.data_out_held,
                "control_out_held": self.control_out_held, "fault_count": self.fault_count,
                "circuit_open": self.circuit_open, "circuit_cool": self.circuit_cool,
                "inner": self.inner}

    @classmethod
    def from_dict(cls, d: dict) -> "NodeSnapshot":
        return cls(state=dict(d["state"]), data_in_held=dict(d["data_in_held"]),
                   control_in_held=dict(d["control_in_held"]),
                   data_out_held=dict(d["data_out_held"]),
                   control_out_held=dict(d["control_out_held"]),
                   fault_count=d["fault_count"], circuit_open=d["circuit_open"],
                   circuit_cool=d["circuit_cool"], inner=d.get("inner"))


@dataclass
class Snapshot:
    """世界快照:规则资产引用 + 世界事实 + 执行器状态。"""

    kernel_version: str
    graph_name: str
    graph_kernel_version: str  # 图资产版本引用(旧存档继续引用旧资产版本)
    tick: int
    rng: dict                 # {seed, counter}
    globals_: dict[str, Any]
    nodes: dict[str, NodeSnapshot]
    log: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"kernel_version": self.kernel_version, "graph_name": self.graph_name,
                "graph_kernel_version": self.graph_kernel_version, "tick": self.tick,
                "rng": self.rng, "globals": self.globals_,
                "nodes": {nid: ns.to_dict() for nid, ns in self.nodes.items()},
                "log": self.log}

    @classmethod
    def from_dict(cls, d: dict) -> "Snapshot":
        return cls(kernel_version=d["kernel_version"], graph_name=d["graph_name"],
                   graph_kernel_version=d["graph_kernel_version"], tick=d["tick"],
                   rng=dict(d["rng"]), globals_=dict(d["globals"]),
                   nodes={nid: NodeSnapshot.from_dict(x) for nid, x in d["nodes"].items()},
                   log=list(d.get("log", [])))


def capture(world: Any) -> Snapshot:
    """在轮界拍快照(宿主保证不在 tick 中途调用)。"""
    nodes: dict[str, NodeSnapshot] = {}
    for ni in world.graph.nodes:
        nid = ni.node_id
        st = world._states[nid]
        ns = NodeSnapshot(
            state=deepcopy(st.state),
            data_in_held={port: (pkt.to_dict() if pkt is not None else None)
                          for (n, port), pkt in world.data_in_held.items() if n == nid},
            control_in_held={port: lvl for (n, port), lvl in world.control_in_held.items() if n == nid},
            data_out_held={port: (pkt.to_dict() if pkt is not None else None)
                           for (n, port), pkt in world.data_out_held.items() if n == nid},
            control_out_held={port: lvl for (n, port), lvl in world.control_out_held.items() if n == nid},
            fault_count=st.fault_count,
            circuit_open=st.circuit_open,
            circuit_cool=st.circuit_cool,
            inner=capture(st.inner).to_dict() if st.inner is not None else None,
        )
        nodes[nid] = ns
    return Snapshot(kernel_version=KERNEL_VERSION, graph_name=world.graph.name,
                    graph_kernel_version=world.graph.kernel_version, tick=world.tick_no,
                    rng=world.rng.snapshot(), globals_=deepcopy(world.globals_),
                    nodes=nodes, log=deepcopy(world.log))


def restore_world(world: Any, snap: Snapshot) -> None:
    """读档:完整恢复运行中状态。要求图资产名与内核主版本匹配、节点集一致。"""
    if not compatible(snap.graph_kernel_version, KERNEL_VERSION):
        raise ValueError(f"快照记录的图资产内核版本 '{snap.graph_kernel_version}' 与当前 "
                         f"'{KERNEL_VERSION}' 主版本不兼容,拒绝读档")
    if snap.graph_name != world.graph.name:
        raise ValueError(f"快照图资产 '{snap.graph_name}' 与当前世界图 '{world.graph.name}' 不符")
    if set(snap.nodes) != set(world._states):
        raise ValueError("快照节点集与当前图不符:改图后的旧快照需走编辑事务迁移,不支持直接恢复")

    world.tick_no = snap.tick
    world.rng.restore(snap.rng)  # 就地恢复:内嵌世界共享同一 RNG 对象
    world.globals_ = deepcopy(snap.globals_)
    world.log = deepcopy(snap.log)
    world.data_in_held.clear()
    world.control_in_held.clear()
    world.data_out_held.clear()
    world.control_out_held.clear()
    for nid, ns in snap.nodes.items():
        st = world._states[nid]
        st.state = deepcopy(ns.state)
        st.fault_count = ns.fault_count
        st.circuit_open = ns.circuit_open
        st.circuit_cool = ns.circuit_cool
        for port, d in ns.data_in_held.items():
            if d is not None:
                world.data_in_held[(nid, port)] = DataPacket.from_dict(d)
        for port, lvl in ns.control_in_held.items():
            world.control_in_held[(nid, port)] = lvl
        for port, d in ns.data_out_held.items():
            if d is not None:
                world.data_out_held[(nid, port)] = DataPacket.from_dict(d)
        for port, lvl in ns.control_out_held.items():
            world.control_out_held[(nid, port)] = lvl
        if st.inner is not None:
            if ns.inner is None:
                raise ValueError(f"子图节点 [{nid}] 缺少内嵌快照")
            restore_world(st.inner, Snapshot.from_dict(ns.inner))
    # 控制端口默认电平兜底(从未收到电平的端口不出现在快照中)
    for nid in world._states:
        nt = world.compiled.types[nid]
        for c in nt.control_in:
            world.control_in_held.setdefault((nid, c.name), c.effective_default())
        for c in nt.control_out:
            world.control_out_held.setdefault((nid, c.name), c.default_level)
