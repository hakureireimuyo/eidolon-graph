"""eidolon-graph:图运行时内核。

稳定核心:Node / Port / Signal / State / Graph / Tick / Asset / Snapshot。
分两层:

- eidolon_graph.model  —— 图模型与资产格式(定义、序列化、静态校验、版本标记)
- eidolon_graph.engine —— 执行引擎(同步轮次、调度、快照、RNG、编辑事务)

内核零第三方依赖;节点实现由宿主注册,内核只认节点协议。
设计文档见 docs/。
"""

from eidolon_graph.engine import World, NodeRegistry, NodeImpl, TickContext, TickOutput, Rng
from eidolon_graph.model import (ACTIVE, INACTIVE, Annot, AssetLibrary, ConstAsset,
                                 ConfigField, ControlIn, ControlOut, DataIn, DataOut,
                                 GenericAsset, GlobalVar, Graph, ImplBinding,
                                 KERNEL_VERSION, NodeInstance, NodeType, ServiceAsset,
                                 StateField, ValidationError, ValidationReport, Wire,
                                 validate)

__version__ = "0.1.0"

__all__ = [
    "KERNEL_VERSION",
    "Annot", "DataIn", "DataOut", "ControlIn", "ControlOut",
    "StateField", "ConfigField", "Wire",
    "ImplBinding", "NodeType", "NodeInstance", "Graph",
    "GlobalVar", "ConstAsset", "ServiceAsset", "GenericAsset", "AssetLibrary",
    "ValidationReport", "ValidationError", "validate",
    "ACTIVE", "INACTIVE",
    "World", "NodeRegistry", "NodeImpl", "TickContext", "TickOutput", "Rng",
]
