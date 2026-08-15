"""图资产(反)序列化:资产格式先于编辑器存在,本层是格式的唯一来源。

格式 = 保序 JSON(Python dict 天然保序,列表用数组保序;声明顺序承载全局写序
语义,必须保留)。类型注解存字符串名,经注册表还原;未知名字按 Any 放行
(运行时零强制)。载荷/状态/默认值需为 JSON 原生类型——非原生对象在序列化时
抛 SerializationError(V1 明示约束)。
"""

from __future__ import annotations

import json
from typing import Any

from .assets import (AssetLibrary, ConstAsset, GenericAsset, GlobalVar, ServiceAsset)
from .graph import Graph, NodeInstance
from .node import ImplBinding, NodeType
from .types import (TYPE_NOT_SET, Annot, ConfigField, ControlIn, ControlOut, DataIn,
                    DataOut, InputGroup, StateField, Wire)
from .version import KERNEL_VERSION, compatible


class SerializationError(ValueError):
    """序列化失败:通常因载荷/状态含非 JSON 原生值。"""


# ---------------------------------------------------------------------------
# 类型注解
# ---------------------------------------------------------------------------

def annot_to_dict(a: Annot) -> str | None:
    if not a.declared():
        return None
    t = a.ty
    if isinstance(t, str):
        return t
    module = getattr(t, "__module__", "builtins")
    qualname = getattr(t, "__qualname__", str(t))
    return qualname if module == "builtins" else f"{module}.{qualname}"


def annot_from_dict(v: Any) -> Annot:
    return Annot() if v is None else Annot(v)


# ---------------------------------------------------------------------------
# 端口 / 字段声明
# ---------------------------------------------------------------------------

def data_in_to_dict(p: DataIn) -> dict:
    return {"name": p.name, "type": annot_to_dict(p.type_annot),
            "const_set": p.const_set, "const": p.const,
            "global_read": p.global_read, "optional": p.optional}


def data_in_from_dict(d: dict) -> DataIn:
    return DataIn(name=d["name"], type_annot=annot_from_dict(d.get("type")),
                  const_set=d.get("const_set", False), const=d.get("const"),
                  global_read=d.get("global_read"), optional=d.get("optional", False))


def data_out_to_dict(p: DataOut) -> dict:
    return {"name": p.name, "type": annot_to_dict(p.type_annot), "global_write": p.global_write}


def data_out_from_dict(d: dict) -> DataOut:
    return DataOut(name=d["name"], type_annot=annot_from_dict(d.get("type")),
                   global_write=d.get("global_write"))


def control_in_to_dict(c: ControlIn) -> dict:
    return {"name": c.name, "semantic": c.semantic, "default_level": c.default_level}


def control_in_from_dict(d: dict) -> ControlIn:
    return ControlIn(name=d["name"], semantic=d.get("semantic", "enable"),
                     default_level=d.get("default_level"))


def input_group_to_dict(g: InputGroup) -> dict:
    return {"name": g.name, "inputs": list(g.inputs), "outputs": list(g.outputs)}


def input_group_from_dict(d: dict) -> InputGroup:
    return InputGroup(name=d["name"], inputs=list(d.get("inputs", [])),
                      outputs=list(d.get("outputs", [])))


def control_out_to_dict(c: ControlOut) -> dict:
    return {"name": c.name, "default_level": c.default_level}


def control_out_from_dict(d: dict) -> ControlOut:
    return ControlOut(name=d["name"], default_level=d.get("default_level", "inactive"))


def state_field_to_dict(f: StateField) -> dict:
    return {"name": f.name, "type": annot_to_dict(f.type_annot), "default": f.default}


def state_field_from_dict(d: dict) -> StateField:
    if "default" not in d:
        raise SerializationError(f"状态字段 '{d.get('name')}' 声明缺少默认值(默认值必需)")
    return StateField(name=d["name"], type_annot=annot_from_dict(d.get("type")),
                      default=d["default"])


def config_field_to_dict(f: ConfigField) -> dict:
    return {"name": f.name, "type": annot_to_dict(f.type_annot), "default": f.default,
            "asset_ref": f.asset_ref}


def config_field_from_dict(d: dict) -> ConfigField:
    if "default" not in d:
        raise SerializationError(f"配置字段 '{d.get('name')}' 声明缺少默认值")
    return ConfigField(name=d["name"], type_annot=annot_from_dict(d.get("type")),
                       default=d["default"], asset_ref=d.get("asset_ref"))


def wire_to_dict(w: Wire) -> dict:
    return {"src_node": w.src_node, "src_port": w.src_port,
            "dst_node": w.dst_node, "dst_port": w.dst_port,
            "dst_slot": w.dst_slot}


def wire_from_dict(d: dict) -> Wire:
    return Wire(src_node=d["src_node"], src_port=d["src_port"],
                dst_node=d["dst_node"], dst_port=d["dst_port"],
                dst_slot=d.get("dst_slot", "data"))


# ---------------------------------------------------------------------------
# 节点类型 / 图
# ---------------------------------------------------------------------------

def impl_binding_to_dict(b: ImplBinding) -> dict:
    return {"kind": b.kind, "name": b.name, "graph": b.graph,
            "port_map": {k: list(v) for k, v in b.port_map.items()}}


def impl_binding_from_dict(d: dict) -> ImplBinding:
    return ImplBinding(kind=d.get("kind", "code"), name=d.get("name"), graph=d.get("graph"),
                       port_map={k: tuple(v) for k, v in d.get("port_map", {}).items()})


def node_type_to_dict(nt: NodeType) -> dict:
    return {"name": nt.name,
            "data_in": [data_in_to_dict(p) for p in nt.data_in],
            "data_out": [data_out_to_dict(p) for p in nt.data_out],
            "control_in": [control_in_to_dict(c) for c in nt.control_in],
            "control_out": [control_out_to_dict(c) for c in nt.control_out],
            "state": [state_field_to_dict(f) for f in nt.state],
            "config": [config_field_to_dict(f) for f in nt.config],
            "groups": [input_group_to_dict(g) for g in nt.groups],
            "init_in": list(nt.init_in),
            "auto": nt.auto,
            "impl": impl_binding_to_dict(nt.impl)}


def node_type_from_dict(d: dict) -> NodeType:
    return NodeType(name=d["name"],
                    data_in=[data_in_from_dict(x) for x in d.get("data_in", [])],
                    data_out=[data_out_from_dict(x) for x in d.get("data_out", [])],
                    control_in=[control_in_from_dict(x) for x in d.get("control_in", [])],
                    control_out=[control_out_from_dict(x) for x in d.get("control_out", [])],
                    state=[state_field_from_dict(x) for x in d.get("state", [])],
                    config=[config_field_from_dict(x) for x in d.get("config", [])],
                    groups=[input_group_from_dict(x) for x in d.get("groups", [])],
                    init_in=list(d.get("init_in", [])),
                    auto=bool(d.get("auto", False)),
                    impl=impl_binding_from_dict(d.get("impl", {})))


def node_instance_to_dict(ni: NodeInstance) -> dict:
    return {"node_id": ni.node_id, "type_name": ni.type_name, "config": ni.config}


def node_instance_from_dict(d: dict) -> NodeInstance:
    return NodeInstance(node_id=d["node_id"], type_name=d["type_name"],
                        config=dict(d.get("config", {})))


def graph_to_dict(g: Graph) -> dict:
    return {"name": g.name, "kernel_version": g.kernel_version,
            "nodes": [node_instance_to_dict(n) for n in g.nodes],
            "wires": [wire_to_dict(w) for w in g.wires]}


def graph_from_dict(d: dict, current_version: str = KERNEL_VERSION) -> Graph:
    recorded = d.get("kernel_version", "0")
    if not compatible(recorded, current_version):
        raise SerializationError(
            f"图资产 '{d.get('name')}' 记录的内核版本 '{recorded}' 与当前版本 "
            f"'{current_version}' 主版本不兼容,拒绝加载")
    return Graph(name=d["name"], kernel_version=recorded,
                 nodes=[node_instance_from_dict(x) for x in d.get("nodes", [])],
                 wires=[wire_from_dict(x) for x in d.get("wires", [])])


# ---------------------------------------------------------------------------
# 资产库
# ---------------------------------------------------------------------------

def library_to_dict(lib: AssetLibrary) -> dict:
    return {"kernel_version": KERNEL_VERSION,
            "globals": [{"name": g.name, "type": annot_to_dict(g.type_annot),
                         "default": g.default} for g in lib.globals_.values()],
            "consts": [{"name": c.name, "type": annot_to_dict(c.type_annot),
                        "value": c.value} for c in lib.consts.values()],
            "node_types": [node_type_to_dict(nt) for nt in lib.node_types.values()],
            "graphs": [graph_to_dict(g) for g in lib.graphs.values()],
            "services": [{"name": s.name, "declaration": s.declaration}
                         for s in lib.services.values()],
            "generic": [{"kind": a.kind, "name": a.name, "declaration": a.declaration}
                        for a in lib.generic.values()]}


def library_from_dict(d: dict) -> AssetLibrary:
    lib = AssetLibrary()
    for x in d.get("globals", []):
        if "default" not in x:
            raise SerializationError(f"全局变量 '{x.get('name')}' 声明缺少默认值(默认值必需)")
        lib.add_global(GlobalVar(name=x["name"], type_annot=annot_from_dict(x.get("type")),
                                 default=x["default"]))
    for x in d.get("consts", []):
        lib.add_const(ConstAsset(name=x["name"], type_annot=annot_from_dict(x.get("type")),
                                 value=x.get("value")))
    for x in d.get("node_types", []):
        lib.add_node_type(node_type_from_dict(x))
    for x in d.get("graphs", []):
        lib.add_graph(graph_from_dict(x))
    for x in d.get("services", []):
        lib.add_service(ServiceAsset(name=x["name"], declaration=x.get("declaration", {})))
    for x in d.get("generic", []):
        lib.add_generic(GenericAsset(kind=x["kind"], name=x["name"],
                                     declaration=x.get("declaration", {})))
    return lib


# ---------------------------------------------------------------------------
# JSON 包装
# ---------------------------------------------------------------------------

def dumps(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except (TypeError, ValueError) as e:
        raise SerializationError(
            f"包含非 JSON 原生值(载荷/状态/默认值需为 JSON 原生类型):{e}") from e


def loads(s: str) -> Any:
    return json.loads(s)
