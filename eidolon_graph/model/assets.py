"""资产系统:一切可被图引用的东西都是资产;必须声明才能使用,引用即校验。

V1 八类:全局变量(状态,持久化)/ 常量 / 节点类型 / 图 / 服务 / 知识 / 数据 / 媒体。
kind 开放:知识/数据/媒体在阶段零只做"声明存在 + 可被引用"的存在性校验,声明内容
内核不解释(由宿主/编辑器消费)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .graph import Graph
from .node import NodeType
from .types import Annot


@dataclass
class GlobalVar:
    """全局变量:命名状态槽;必须声明(名字 + 类型 + 默认值)才能被引用;属世界快照。"""

    name: str
    default: Any  # 必需——声明即带默认值
    type_annot: Annot = field(default_factory=Annot)


@dataclass
class ConstAsset:
    """常量:永远是声明值,不持久化于快照。"""

    name: str
    value: Any
    type_annot: Annot = field(default_factory=Annot)


@dataclass
class ServiceAsset:
    """服务/连接:声明持久化(DSN、凭证引用、配置);连接本身是运行时资源。"""

    name: str
    declaration: dict = field(default_factory=dict)


@dataclass
class GenericAsset:
    """kind 开放的资产(知识/数据/媒体/未来扩展)。"""

    kind: str
    name: str
    declaration: dict = field(default_factory=dict)


class AssetLibrary:
    """资产注册表:先声明后引用;各表保序(全局变量初始值声明序)。"""

    def __init__(self) -> None:
        self.globals_: dict[str, GlobalVar] = {}
        self.consts: dict[str, ConstAsset] = {}
        self.node_types: dict[str, NodeType] = {}
        self.graphs: dict[str, Graph] = {}
        self.services: dict[str, ServiceAsset] = {}
        self.generic: dict[str, GenericAsset] = {}

    # -- 注册(重名 → ValueError) --
    @staticmethod
    def _add(table: dict, obj: Any, kind: str) -> None:
        if obj.name in table:
            raise ValueError(f"{kind} '{obj.name}' 已声明(重名)")
        table[obj.name] = obj

    def add_global(self, g: GlobalVar) -> None:
        self._add(self.globals_, g, "全局变量")

    def add_const(self, c: ConstAsset) -> None:
        self._add(self.consts, c, "常量")

    def add_node_type(self, nt: NodeType) -> None:
        self._add(self.node_types, nt, "节点类型")

    def add_graph(self, g: Graph) -> None:
        self._add(self.graphs, g, "图")

    def add_service(self, s: ServiceAsset) -> None:
        self._add(self.services, s, "服务")

    def add_generic(self, a: GenericAsset) -> None:
        self._add(self.generic, a, "资产")

    # -- 引用查询(未声明 → KeyError,校验器转报错) --
    @staticmethod
    def _require(table: dict, name: str, kind: str) -> Any:
        try:
            return table[name]
        except KeyError:
            raise KeyError(f"未声明的{kind} '{name}'(资产必须声明后才能引用)") from None

    def require_global(self, name: str) -> GlobalVar:
        return self._require(self.globals_, name, "全局变量")

    def require_const(self, name: str) -> ConstAsset:
        return self._require(self.consts, name, "常量")

    def require_node_type(self, name: str) -> NodeType:
        return self._require(self.node_types, name, "节点类型")

    def require_graph(self, name: str) -> Graph:
        return self._require(self.graphs, name, "图")

    def require_service(self, name: str) -> ServiceAsset:
        return self._require(self.services, name, "服务")

    def has_asset(self, name: str) -> bool:
        """资源类资产(服务/知识/数据/媒体)是否已声明——配置字段 asset_ref 的存在性校验用。"""
        return name in self.services or name in self.generic
