"""端口、连线与字段声明的核心类型。

- 数据端口:输入默认绑定(常量/全局读取/状态写入)与连线**正交**——带常量或全局
  读取绑定的输入端口即时满足(不参与就绪等待),连线数据包到达后覆盖默认值(采样
  保持)。这是反馈环可自发启动的一致读法,见 docs/graph-ports-bindings.md §2。
- 控制端口:无载荷只承载电平;semantic ∈ {enable, mask, level}:
  - enable = 门控整节点(引擎在节点实现之前拦截);
  - mask   = 屏蔽目标数据端口(输入旁路/输出不发值);
  - level  = 纯电平输入,引擎不介入——AND/OR/NOT/Latch/Timer 等逻辑元件白名单
    (docs/graph-ports-bindings.md §4.5)的组合线;语义由声明决定(§4.1),V1 不引入
    pause/cancel/reset 等事件式语义(§4.8)。
- 类型注解仅编辑期静态检查(双方均声明才查),运行时零强制(鸭子类型)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# 类型注解哨兵:未声明 = Any(与"声明了 None"区分开)
TYPE_NOT_SET = object()

# 控制电平(仅两个取值;字符串以便 JSON 直存)
ACTIVE = "active"
INACTIVE = "inactive"
Level = Literal["active", "inactive"]

# 常见注解的字符串注册表(序列化时类型存为字符串名;未知名字按 Any 放行)
_TYPE_REGISTRY: dict[str, Any] = {
    "int": int,
    "float": float,
    "bool": bool,
    "str": str,
    "list": list,
    "dict": dict,
    "Any": Any,
}


def resolve_type(t: Any) -> Any:
    """注解 → Python 类型:未声明 → None;未知字符串 → Any(放行);类型对象原样返回。"""
    if t is TYPE_NOT_SET:
        return None
    if isinstance(t, str):
        return _TYPE_REGISTRY.get(t, Any)
    return t


@dataclass
class Annot:
    """可选类型注解:未声明 = Any;仅编辑期静态检查,运行时零强制。"""

    ty: Any = TYPE_NOT_SET

    def declared(self) -> bool:
        return self.ty is not TYPE_NOT_SET

    def compatible_with(self, other: "Annot") -> bool:
        """self 侧的值能否流入 other(目标)侧:双方均声明才查,任一端未声明放行。"""
        src, dst = resolve_type(self.ty), resolve_type(other.ty)
        if src is None or dst is None or src is Any or dst is Any:
            return True
        try:
            return issubclass(src, dst)
        except TypeError:
            return src == dst


@dataclass
class DataIn:
    """数据输入端声明。

    默认绑定与连线正交,绑定种类互斥(const_set 与 global_read 不可同时声明):
    - const_set:默认常量(即时满足,值为常量;显式 const=None 也是合法默认);
    - global_read:全局读取(即时满足,开火时拉取全局最新值,不唤醒下游);
    - state_write:输入信号写入的可写状态字段(参数可被信号调制 = 普通连线,
      docs/graph-node-types.md §3);
    - 无绑定且无连线 = 裸端口,编辑事务校验报错(强迫显式)。
    """

    name: str
    type_annot: Annot = field(default_factory=Annot)
    const_set: bool = False
    const: Any = None
    global_read: str | None = None
    state_write: str | None = None

    def is_immediate(self) -> bool:
        """带常量/全局读取绑定 → 即时满足,不参与就绪等待。"""
        return self.const_set or self.global_read is not None


@dataclass
class DataOut:
    """数据输出端声明:未屏蔽时每轮必发一个值(可 None);可绑定全局写入(开火时轮末提交)。"""

    name: str
    type_annot: Annot = field(default_factory=Annot)
    global_write: str | None = None


@dataclass
class ControlIn:
    """控制输入端声明:无载荷只承载电平;电平永远有定义(默认电平兜底),不参与就绪等待。

    semantic(由声明决定,见 docs/graph-ports-bindings.md §4):
    - enable:门控整节点,active=门开;inactive 在节点实现之前被运行时拦截;
    - mask:屏蔽目标数据端口,active=该端口本轮被忽略(输入不参与就绪与计算、
      输出不发值、下游冻结旧值);
    - level:纯电平输入,引擎不介入(逻辑元件组合线)。
    """

    name: str
    semantic: Literal["enable", "mask", "level"] = "enable"
    target: str | None = None           # mask:目标数据端口名
    default_level: Level | None = None  # None → 按语义缺省:门控常开、屏蔽常闭、电平 inactive

    def effective_default(self) -> Level:
        if self.default_level is not None:
            return self.default_level
        if self.semantic == "enable":
            return ACTIVE
        return INACTIVE


@dataclass
class ControlOut:
    """控制输出端声明:按轮保持电平;声明其含义(如时钟的方波、阈值的 under 极性)。"""

    name: str
    default_level: Level = INACTIVE


@dataclass
class StateField:
    """可写状态字段:运行时被转移函数更新或被输入信号写入;必须有默认值;属世界事实,持久化。"""

    name: str
    default: Any  # 必需——声明即带默认值(默认值本身可以是 None)
    type_annot: Annot = field(default_factory=Annot)


@dataclass
class ConfigField:
    """只读配置字段:编辑期设定、运行时只读;属图资产,版本化。

    asset_ref:该字段的值是对资产的引用(如数据库连接、人格矩阵)——值本身存资产名,
    实现经宿主解析器按名取资产;引用即校验(编辑事务提交前检查已声明)。
    可选给期望的种类提示:"service" / "data" / "knowledge" / "media";None = 不限。
    """

    name: str
    default: Any
    type_annot: Annot = field(default_factory=Annot)
    asset_ref: str | None = None


@dataclass(frozen=True)
class Wire:
    """一条连线:数据流动方向 = 触发方向;kind 由两端端口种类决定(交叉连线校验报错)。"""

    src_node: str
    src_port: str
    dst_node: str
    dst_port: str
