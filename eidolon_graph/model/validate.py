"""校验器:编辑事务提交前 / 运行时加载时共用的同一份语义校验。

清单(见 docs/graph-persistence-and-editing.md §5):
1. 端口绑定合法性(裸数据端口报错;可显式声明默认 None);
2. 连线 kind 匹配:data→data、信号→信号(信号源 = 控制输出或数据输出的信号
   端口),交叉连线报错;
   唯一例外:控制输出 → 数据端口信号(显式屏蔽)合法;
3. 扇入禁止:每个输入端口至多一条数据线、至多一条信号线(组合必须显式信号节点);
4. 引用存在性(未声明的全局变量/常量/服务/图 → 报错);
5. 连线类型兼容(数据通道,双方均声明时检查);
6. 输入组:组名唯一、端口归属唯一;连线数据输入必须属于某组或初始化输入;
   有组的节点其数据输出必须属于某组;
7. 初始化输入:必须连线或绑定(裸 init 端口报错);
8. 控制端口:数据节点(无控制输出)只可声明 enable;level 属于信号节点;
9. 同一节点多输出写同一全局 → 报错;
10. 全局变量声明带默认值(声明构造时强制,反序列化时复查);
11. 静态提示(非错误):重复连线、无源环、全函数节点环、多写者。
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
    in_edges: dict[tuple[str, str, str], Wire] = {}
    for w in graph.wires:
        key = (w.dst_node, w.dst_port, w.dst_slot)
        if key in in_edges:
            rep.error(f"扇入禁止:端口 '{w.dst_node}.{w.dst_port}({w.dst_slot})' "
                      f"已有来源 '{in_edges[key].src_node}.{in_edges[key].src_port}',"
                      f"不能再接 '{w.src_node}.{w.src_port}'(多个来源的组合必须显式"
                      f"使用信号节点)")
        else:
            in_edges[key] = w

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
                   nt: NodeType, in_edges: dict[tuple[str, str, str], Wire]) -> None:
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

    # 数据输入:绑定互斥 / 引用存在性 / 裸端口(可选参数端口豁免——函数默认参数)
    for p in nt.data_in:
        if p.const_set and p.global_read is not None:
            rep.error(f"节点 [{nid}] 数据输入 '{p.name}' 同时声明了常量与全局读取绑定(互斥)")
        if p.global_read is not None and p.global_read not in lib.globals_:
            rep.error(f"节点 [{nid}] 数据输入 '{p.name}' 引用了未声明的全局变量 '{p.global_read}'")
        wired = (nid, p.name, "data") in in_edges
        if not p.is_bound() and not wired and not p.optional:
            rep.error(f"节点 [{nid}] 数据输入 '{p.name}' 是裸端口:无连线、无默认、无引用"
                      f"(强迫显式;可显式声明默认 None 或可选参数)")

    # 数据输出:全局写入目标存在 / 同节点多输出同目标
    node_writes: dict[str, str] = {}
    for p in nt.data_out:
        if p.global_write is not None:
            if p.global_write not in lib.globals_:
                rep.error(f"节点 [{nid}] 数据输出 '{p.name}' 写入了未声明的全局变量 '{p.global_write}'")
            elif p.global_write in node_writes:
                rep.error(f"节点 [{nid}] 多个数据输出写同一全局变量 '{p.global_write}'")
            node_writes[p.global_write] = p.name

    # 输入组:名称唯一、端口归属唯一、绑定端口不入组、连线输入必须入组、输出必须入组
    _validate_groups(rep, nid, nt, in_edges)

    # 初始化输入:必须连线或绑定
    init_ports = set(nt.init_in)
    for name in init_ports:
        if name not in nt.data_in_map():
            rep.error(f"节点 [{nid}] 初始化输入 '{name}' 不是类型 '{nt.name}' 的数据输入")
        elif not (name in nt.data_in_map() and nt.data_in_map()[name].is_bound()) \
                and (nid, name, "data") not in in_edges:
            rep.error(f"节点 [{nid}] 初始化输入 '{name}' 是裸端口:无连线、无绑定")

    # 控制端口:数据节点只可声明 enable;level 属于信号节点
    if not nt.is_signal_node():
        for c in nt.control_in:
            if c.semantic != "enable":
                rep.error(f"节点 [{nid}] 是数据节点(未声明控制输出),控制输入 '{c.name}' "
                          f"只能使用门控语义(enable),不能使用 '{c.semantic}'")

    # 子图实现绑定
    if nt.impl.kind == "subgraph":
        _validate_subgraph_binding(rep, lib, nid, nt)


def _validate_groups(rep: ValidationReport, nid: str, nt: NodeType,
                     in_edges: dict[tuple[str, str, str], Wire]) -> None:
    seen_names: set[str] = set()
    in_port_group: dict[str, str] = {}
    out_port_group: dict[str, str] = {}
    for g in nt.groups:
        if g.name in seen_names:
            rep.error(f"节点 [{nid}] 输入组名 '{g.name}' 重复")
        seen_names.add(g.name)
        for p in g.inputs:
            if p not in nt.data_in_map():
                rep.error(f"节点 [{nid}] 输入组 '{g.name}' 引用了未声明的数据输入 '{p}'")
                continue
            # 绑定端口可入组作为值源,但不参与触发(触发只看未绑定的连线输入)
            if p in in_port_group:
                rep.error(f"节点 [{nid}] 数据输入 '{p}' 同时属于输入组 "
                          f"'{in_port_group[p]}' 与 '{g.name}'(每组至多一个)")
            if p in nt.init_in:
                rep.error(f"节点 [{nid}] 数据输入 '{p}' 同时是初始化输入与输入组成员")
            in_port_group[p] = g.name
        for p in g.outputs:
            if p not in nt.data_out_map():
                rep.error(f"节点 [{nid}] 输入组 '{g.name}' 引用了未声明的数据输出 '{p}'")
                continue
            if p in out_port_group:
                rep.error(f"节点 [{nid}] 数据输出 '{p}' 同时属于输入组 "
                          f"'{out_port_group[p]}' 与 '{g.name}'(每组至多一个)")
            out_port_group[p] = g.name
    # 连线数据输入必须属于某组(或初始化输入)
    for p in nt.data_in:
        if p.is_bound() or p.name in nt.init_in:
            continue
        if (nid, p.name, "data") in in_edges and p.name not in in_port_group:
            rep.error(f"节点 [{nid}] 连线数据输入 '{p.name}' 不属于任何输入组:"
                      f"该端口永远不会触发执行")
    # 有组的节点:数据输出必须属于某组(自走节点的源输出豁免——每轮运行产出)
    if nt.groups:
        for p in nt.data_out:
            if p.name not in out_port_group and not nt.auto:
                rep.error(f"节点 [{nid}] 数据输出 '{p.name}' 不属于任何输出组"
                          f"(有输入组的节点其输出必须归组;自走源输出除外)")


def _validate_subgraph_binding(rep: ValidationReport, lib: AssetLibrary, nid: str,
                               nt: NodeType) -> None:
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
    seen: set[tuple[str, str, str, str, str]] = set()
    for w in graph.wires:
        key = (w.src_node, w.src_port, w.dst_node, w.dst_port, w.dst_slot)
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
        if src_data:
            if w.dst_slot == "signal":
                # 数据输出的信号端口:电平由自动传导决定(实现永不写信号),但可
                # 显式拉线到任何信号接收端(控制输入 / 数据输入的信号槽)——不改变
                # 电平,只是显式路由;不拉线则沿数据线自动传导。
                continue
            # 数据线(dst_slot='data')→ 数据输入
            if not dst_data:
                rep.error(f"交叉连线:数据输出 '{w.src_node}.{w.src_port}' 不能连控制输入 '{w.dst_node}.{w.dst_port}'")
                continue
            src_annot = snt.data_out_map()[w.src_port].type_annot
            dst_annot = dnt.data_in_map()[w.dst_port].type_annot
            if not src_annot.compatible_with(dst_annot):
                rep.error(f"连线类型不兼容:'{w.src_node}.{w.src_port}' 的类型不能流入 "
                          f"'{w.dst_node}.{w.dst_port}'")
        else:
            # 控制输出 → 控制输入,或 → 数据端口信号(显式屏蔽)
            if w.dst_slot != "signal":
                rep.error(f"控制输出 '{w.src_node}.{w.src_port}' 只能连信号槽 "
                          f"(dst_slot='signal')")
                continue
            if dst_ctrl:
                continue
            # 数据端口信号:合法(跨通道信号连线)
            if not dst_data:
                rep.error(f"连线目标端口 '{w.dst_node}.{w.dst_port}' 不是输入端口")


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
            rep.warning(f"全局变量 '{g}' 有多个写者 {ports}:按声明序 last-write-wins,建议避免")


def _cycle_hints(rep: ValidationReport, lib: AssetLibrary, graph: Graph,
                 in_edges: dict[tuple[str, str, str], Wire]) -> None:
    """环静态提示(启发式):无源环永不自发启动、全函数节点环不收敛。提示非禁止。"""
    adj: dict[str, set[str]] = {}
    has_source: dict[str, bool] = {}
    wait: dict[str, set[str]] = {}
    for ni in graph.nodes:
        adj.setdefault(ni.node_id, set())
        nt = lib.node_types.get(ni.type_name)
        if nt is None:
            continue
        has_source[ni.node_id] = nt.is_source()
        wait[ni.node_id] = {p.name for p in nt.data_in
                            if not p.is_bound() and p.name not in nt.init_in
                            and (ni.node_id, p.name, "data") in in_edges}
    for w in graph.wires:
        if w.dst_slot == "data":
            adj.setdefault(w.src_node, set()).add(w.dst_node)

    for scc in _tarjan(adj):
        if len(scc) == 1:
            (only,) = scc
            if only not in adj[only]:
                continue  # 无自环
        # 无源:环内没有任何源节点,也没有从环外喂入等待端口的边
        has_src = any(has_source.get(n, False) for n in scc)
        externally_fed = any(
            any(w.src_node not in scc for w in [in_edges.get((n, p, "data"))] if w is not None)
            for n in scc
            for p in wait.get(n, set())
        )
        if not (has_src or externally_fed):
            rep.warning(f"无源环 {sorted(scc)}:环内节点没有外部输入来源,永不自发启动(提示非禁止)")
        # 全函数节点环(无同步组截断):每组单端口 → 级联不收敛,仅提示
        node_map = graph.node_map()
        all_single = all(
            lib.node_types.get(node_map.get(n).type_name) is None
            or all(len(g.inputs) <= 1 for g in lib.node_types[
                node_map[n].type_name].groups)
            for n in scc if node_map.get(n) is not None
        )
        if all_single:
            rep.warning(f"全单例组环 {sorted(scc)}:环内无多输入同步组截断,级联不收敛(提示非禁止)")


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
