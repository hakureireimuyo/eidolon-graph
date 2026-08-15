"""图模型与资产格式层。

职责:
- Graph / Node / Port / Asset 的数据定义(声明顺序承载全局写序语义);
- 图资产(反)序列化:资产格式先于编辑器存在,本层是格式的唯一来源;
- 编辑事务提交前的静态校验(端口绑定、连线 kind、引用存在性、类型兼容);
- 内核版本标记:图资产记录写入时的内核版本,加载时比对。

不负责:tick 执行、调度、快照、RNG —— 见 eidolon_graph.engine。
"""

from .assets import AssetLibrary, ConstAsset, GenericAsset, GlobalVar, ServiceAsset
from .graph import Graph, NodeInstance
from .node import ImplBinding, NodeType
from .types import (ACTIVE, INACTIVE, TYPE_NOT_SET, Annot, ConfigField, ControlIn,
                    ControlOut, DataIn, DataOut, InputGroup, StateField, Wire)
from .validate import ValidationError, ValidationReport, validate
from .version import KERNEL_VERSION, compatible
from . import serialize

__all__ = [
    "KERNEL_VERSION", "compatible",
    "ACTIVE", "INACTIVE", "TYPE_NOT_SET",
    "Annot", "DataIn", "DataOut", "ControlIn", "ControlOut",
    "InputGroup", "StateField", "ConfigField", "Wire",
    "ImplBinding", "NodeType", "NodeInstance", "Graph",
    "GlobalVar", "ConstAsset", "ServiceAsset", "GenericAsset", "AssetLibrary",
    "ValidationReport", "ValidationError", "validate",
    "serialize",
]
