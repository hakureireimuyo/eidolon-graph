"""校验器:编辑事务提交前 / 运行时加载时共用的同一份语义校验。

清单(见 docs/graph-persistence-and-editing.md §5):
1. 端口绑定合法性(裸数据输入报错;可显式声明默认 None);
2. 连线 kind 匹配:data→data、control→control,交叉连线报错;
3. 引用存在性(未声明的全局变量/常量/服务/图 → 报错);
4. 连线类型兼容(数据通道,双方均声明时检查);
5. 同一节点多输出写同一全局 → 报错;
6. 全局变量声明带默认值(声明构造时强制,反序列化时复查);
7. 静态提示(非错误):重复连线、无源环、无门控环、多写者。
环检测为保守启发式(不模拟屏蔽动态),提示不是禁止。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .assets import AssetLibrary
from .graph import Graph, NodeInstance
from .node import NodeType
from .types import Wire


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)     # 阻塞提交/加载
    warnings: list[str] = field(default_factory=list)   # 静态提示(非错误)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def to_dict(self) -> dict:
        return {"errors": list(self.errors), "warnings": list(self.warnings)}

    def __str__(self) -> str:
        lines = [f"校验结果:{len(self.errors)} 个错误,{len(self.warnings)} 个提示"]
        lines += [f"  [E] {e}" for e in self.errors]
        lines += [f"  [W] {w}" for w in self.warnings]
        return "\n".join(lines)


class ValidationError(Exception):
    """校验不通过:携带完整报告(编辑事务提交 / 运行时加载时抛出)。"""

    def __init__(self, report: ValidationReport):
        super().__init__(
            f"图校验失败({len(report.errors)} 个错误):\n"
            + "\n".join(f"- {e}" for e in report.errors)
        )
        self.report = report


def validate(lib: AssetLibrary, graph: Graph) -> ValidationReport:
    rep = ValidationReport()
    in_edges: dict[tuple[str, str], list[Wire]] = {}
    for w in graph.wires:
        in_edges.setdefault((w.dst_node, w.dst_port), []).append(w)

    seen_ids: set[str] = set()
    for ni in graph.nodes:
        if ni.node_id in seen_ids:
            rep.error(f"节点 id '{ni.node_id}' 重复")
            continue
        seen_ids.add(ni.node_id)
        nt = lib.node_types.get(ni.type_name)
        if nt is None:
            rep.error(f"节点 [{ni.node_id}] 引用了未声明的节点类型 '{ni.type_name}'")
            continue
        _validate_node(rep, lib, ni, nt, in_edges)

    _validate_wires(rep, lib, graph)
    _validate_global_writers(rep, lib, graph)
    _cycle_hints(rep, lib, graph, in_edges)
    return rep


def _asset_ref_ok(lib: AssetLibrary, kind_hint: str | None, name: str) -> bool:
    """资产引用存在性:名字已声明,且(给出种类提示时)种类匹配。"""
    if kind_hint == "service":
        return name in lib.services
    if kind_hint in ("data", "knowledge", "media"):
        return name in lib.generic and lib.generic[name].kind == kind_hint
    return lib.has_asset(name)


def _validate_node(rep: ValidationReport, lib: AssetLibrary, ni: NodeInstance,
                   nt: NodeType, in_edges: dict[tuple[str, str], list[Wire]]) -> None:
    nid = ni.node_id

    # 配置覆盖:键必须是已声明的配置字段
    for key in ni.config:
        if key not in nt.config_map():
            rep.error(f"节点 [{nid}] 配置字段 '{key}' 未在类型 '{nt.name}' 中声明")
    # 配置字段的资产引用(asset_ref):默认值与实例覆盖值都必须指向已声明的资产(引用即校验)
    for f in nt.config:
        if not f.asset_ref:
            continue
        for ref in (f.default, ni.config.get(f.name)):
            if ref is not None and not _asset_ref_ok(lib, f.asset_ref, ref):
                rep.error(f"节点 [{nid}] 配置字段 '{f.name}' 引用了未声明的资产 '{ref}'")

    # 数据输入:绑定互斥 / 引用存在性 / 状态写入目标 / 裸端口
    written: set[str] = set()
    for p in nt.data_in:
        if p.const_set and p.global_read is not None:
            rep.error(f"节点 [{nid}] 数据输入 '{p.name}' 同时声明了常量与全局读取绑定(互斥)")
        if p.global_read is not None and p.global_read not in lib.globals_:
            rep.error(f"节点 [{nid}] 数据输入 '{p.name}' 引用了未声明的全局变量 '{p.global_read}'")
        if p.state_write is not None:
            if p.state_write not in nt.state_map():
                rep.error(f"节点 [{nid}] 数据输入 '{p.name}' 的 state_write 目标 "
                          f"'{p.state_write}' 不是类型 '{nt.name}' 的状态字段")
            elif p.state_write in written:
                rep.error(f"节点 [{nid}] 多个数据输入写同一状态字段 '{p.state_write}'")
            written.add(p.state_write)
        if not p.is_immediate() and (nid, p.name) not in in_edges:
            rep.error(f"节点 [{nid}] 数据输入 '{p.name}' 是裸端口:无连线、无默认、无引用"
                      f"(强迫显式;可显式声明默认 None)")

    # 数据输出:全局写入目标存在 / 同节点多输出同目标
    node_writes: dict[str, str] = {}
    for p in nt.data_out:
        if p.global_write is not None:
            if p.global_write not in lib.globals_:
                rep.error(f"节点 [{nid}] 数据输出 '{p.name}' 写入了未声明的全局变量 '{p.global_write}'")
            elif p.global_write in node_writes:
                rep.error(f"节点 [{nid}] 多个数据输出写同一全局变量 '{p.global_write}'")
            node_writes[p.global_write] = p.name

    # 控制输入:屏蔽目标必须是本节点数据端口
    data_ports = set(nt.data_in_map()) | set(nt.data_out_map())
    for c in nt.control_in:
        if c.semantic == "mask" and (c.target is None or c.target not in data_ports):
            rep.error(f"节点 [{nid}] 控制输入 '{c.name}' 的屏蔽目标 '{c.target}' 不是本节点的数据端口")

    # 子图实现绑定
    if nt.impl.kind == "subgraph":
        _validate_subgraph_binding(rep, lib, nid, nt)


def _validate_subgraph_binding(rep: ValidationReport, lib: AssetLibrary, nid: str, nt: NodeType) -> None:
    inner_graph = lib.graphs.get(nt.impl.graph)
    if inner_graph is None:
        rep.error(f"节点 [{nid}] 子图实现引用了未声明的图资产 '{nt.impl.graph}'")
        return
    if nt.state:
        rep.error(f"节点 [{nid}] 子图类型 '{nt.name}' 声明了状态字段:子图节点状态全部位于内部世界(V1 约束)")
    if nt.config:
        rep.error(f"节点 [{nid}] 子图类型 '{nt.name}' 声明了配置字段:子图节点配置无法传递进内部世界(V1 约束)")

    pm = nt.impl.port_map
    port_names = (set(nt.data_in_map()) | set(nt.data_out_map())
                  | set(nt.control_in_map()) | set(nt.control_out_map()))
    inner_nodes = inner_graph.node_map()
    inner_types: dict[str, NodeType] = {}
    for inid, inni in inner_nodes.items():
        inner_types[inid] = lib.node_types.get(inni.type_name)  # type: ignore[assignment]

    seen_targets: list[tuple[str, str]] = []
    for outer, (inner_node, inner_port) in pm.items():
        if outer not in port_names:
            rep.error(f"节点 [{nid}] 端口映射引用了未声明的外部端口 '{outer}'")
            continue
        if inner_node not in inner_nodes:
            rep.error(f"节点 [{nid}] 端口映射 '{outer}' 指向不存在的内部节点 '{inner_node}'")
            continue
        inner_nt = inner_types.get(inner_node)
        if inner_nt is None:
            continue  # 内部节点类型缺失的错误已由该图自身校验报告
        # 方向匹配:data→data、control→control
        if outer in nt.data_in_map():
            ok = inner_port in inner_nt.data_in_map()
        elif outer in nt.data_out_map():
            ok = inner_port in inner_nt.data_out_map()
        elif outer in nt.control_in_map():
            ok = inner_port in inner_nt.control_in_map()
        else:
            ok = inner_port in inner_nt.control_out_map()
        if not ok:
            rep.error(f"节点 [{nid}] 端口映射 '{outer} → {inner_node}.{inner_port}' 端口种类不匹配")
        seen_targets.append((inner_node, inner_port))
    # 同一内部端口被多个外部端口映射 → 报错
    for t in set(seen_targets):
        if seen_targets.count(t) > 1:
            rep.error(f"节点 [{nid}] 多个外部端口映射到同一内部端口 {t}")
    # 数据端口必须全映射(显式原则);控制端口未映射仅提示
    for p in list(nt.data_in) + list(nt.data_out):
        if p.name not in pm:
            rep.error(f"节点 [{nid}] 子图数据端口 '{p.name}' 未映射(强迫显式)")
    for c in list(nt.control_in) + list(nt.control_out):
        if c.name not in pm:
            rep.warning(f"节点 [{nid}] 子图控制端口 '{c.name}' 未映射:仅父层语义/不导出")


def _validate_wires(rep: ValidationReport, lib: AssetLibrary, graph: Graph) -> None:
    nodes = graph.node_map()
    seen: set[tuple[str, str, str, str]] = set()
    for w in graph.wires:
        key = (w.src_node, w.src_port, w.dst_node, w.dst_port)
        if key in seen:
            rep.warning(f"重复连线 {w.src_node}.{w.src_port} → {w.dst_node}.{w.dst_port}(已去重)")
            continue
        seen.add(key)
        sn, dn = nodes.get(w.src_node), nodes.get(w.dst_node)
        if sn is None:
            rep.error(f"连线源节点 '{w.src_node}' 不存在")
            continue
        if dn is None:
            rep.error(f"连线目标节点 '{w.dst_node}' 不存在")
            continue
        snt, dnt = lib.node_types.get(sn.type_name), lib.node_types.get(dn.type_name)
        if snt is None or dnt is None:
            continue  # 类型缺失已报告
        src_data = w.src_port in snt.data_out_map()
        src_ctrl = w.src_port in snt.control_out_map()
        dst_data = w.dst_port in dnt.data_in_map()
        dst_ctrl = w.dst_port in dnt.control_in_map()
        if not (src_data or src_ctrl):
            rep.error(f"连线源端口 '{w.src_node}.{w.src_port}' 不是输出端口")
            continue
        if not (dst_data or dst_ctrl):
            rep.error(f"连线目标端口 '{w.dst_node}.{w.dst_port}' 不是输入端口")
            continue
        if src_data and dst_ctrl:
            rep.error(f"交叉连线:数据输出 '{w.src_node}.{w.src_port}' 不能连控制输入 '{w.dst_node}.{w.dst_port}'")
        elif src_ctrl and dst_data:
            rep.error(f"交叉连线:控制输出 '{w.src_node}.{w.src_port}' 不能连数据输入 '{w.dst_node}.{w.dst_port}'")
        elif src_data:
            src_annot = snt.data_out_map()[w.src_port].type_annot
            dst_annot = dnt.data_in_map()[w.dst_port].type_annot
            if not src_annot.compatible_with(dst_annot):
                rep.error(f"连线类型不兼容:'{w.src_node}.{w.src_port}' 的类型不能流入 "
                          f"'{w.dst_node}.{w.dst_port}'")


def _validate_global_writers(rep: ValidationReport, lib: AssetLibrary, graph: Graph) -> None:
    writers: dict[str, list[str]] = {}
    for ni in graph.nodes:
        nt = lib.node_types.get(ni.type_name)
        if nt is None:
            continue
        for p in nt.data_out:
            if p.global_write:
                writers.setdefault(p.global_write, []).append(f"{ni.node_id}.{p.name}")
    for g, ports in writers.items():
        if len(ports) > 1:
            rep.warning(f"全局变量 '{g}' 有多个写者 {ports}:轮末按节点声明顺序 last-write-wins,建议避免")


def _cycle_hints(rep: ValidationReport, lib: AssetLibrary, graph: Graph,
                 in_edges: dict[tuple[str, str], list[Wire]]) -> None:
    """环静态提示(启发式,不模拟屏蔽动态):无源环永不自发触发、无门控环每轮空转。"""
    adj: dict[str, set[str]] = {}
    wait: dict[str, set[str]] = {}
    gated: set[str] = set()
    for ni in graph.nodes:
        adj.setdefault(ni.node_id, set())
        nt = lib.node_types.get(ni.type_name)
        if nt is None:
            continue
        wait[ni.node_id] = {p.name for p in nt.data_in
                            if not p.is_immediate() and (ni.node_id, p.name) in in_edges}
        if any(c.semantic == "enable" for c in nt.control_in):
            gated.add(ni.node_id)
    for w in graph.wires:
        adj.setdefault(w.src_node, set()).add(w.dst_node)

    for scc in _tarjan(adj):
        if len(scc) == 1:
            (only,) = scc
            if only not in adj[only]:
                continue  # 无自环
        # 无源:环内没有任何自发源(无线输入等待的节点),也没有从环外喂入等待端口的边
        has_source = any(not wait.get(n, set()) for n in scc)
        externally_fed = any(
            any(w.src_node not in scc for w in in_edges.get((n, p), []))
            for n in scc
            for p in wait.get(n, set())
        )
        if not (has_source or externally_fed):
            rep.warning(f"无源环 {sorted(scc)}:环内节点没有外部输入来源,永不自发触发(提示非禁止)")
        if not any(n in gated for n in scc):
            rep.warning(f"无门控环 {sorted(scc)}:环内节点无 enable 门控,就绪后每轮空转(提示非禁止)")


def _tarjan(adj: dict[str, set[str]]) -> list[set[str]]:
    """Tarjan 强连通分量分解。"""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[set[str]] = []
    counter = [0]

    def visit(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in adj.get(v, ()):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            scc: set[str] = set()
            while True:
                w = stack.pop()
                on_stack.discard(w)
                scc.add(w)
                if w == v:
                    break
            result.append(scc)

    for v in adj:
        if v not in index:
            visit(v)
    return result
