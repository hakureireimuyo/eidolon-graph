"""端口、连线与输入组的核心类型。

- 数据端口:输入绑定(常量/全局读取)与连线**正交**——带绑定的端口不参与触发,
  连线到达覆盖默认值;缓冲为空时读默认值。
- 每个数据端口自带信号(电平):active = 带电(参与传播与等待),inactive = 关闭
  (视为不存在)。输入信号来源:显式信号线(以信号线为准)或上游输出信号的自动
  传导;输出信号对数据节点只有一条自动传导(对应输入组全关 → 输出关闭)。
- 输入组 = 方法,输出组 = 方法的返回值:一个节点可以有多个输入组,每组一一
  对应一个输出组;组内全部有效输入有新值即触发、消费清零;组间参数不互传,
  状态(实例字段)共享。
- 类型注解仅编辑期静态检查(双方均声明才查),运行时零强制(鸭子类型)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# 类型注解哨兵:未声明 = Any(与"声明了 None"区分开)
TYPE_NOT_SET = object()

# 控制/端口信号电平(仅两个取值;字符串以便 JSON 直存)
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
    """数据输入端声明。每个数据端口自带信号(电平)。

    默认绑定与连线正交,绑定种类互斥(const_set 与 global_read 不可同时声明):
    - const_set:默认常量(缓冲为空时值为常量);
    - global_read:全局读取(执行时拉取全局最新值,不参与触发、不唤醒下游);
    - 带绑定的端口不参与触发(可入输入组作为值源,但触发只看未绑定的连线输入);
    - 无绑定且无连线 = 裸端口,编辑事务校验报错(强迫显式)。
    """

    name: str
    type_annot: Annot = field(default_factory=Annot)
    const_set: bool = False
    const: Any = None
    global_read: str | None = None

    def is_bound(self) -> bool:
        """带常量/全局读取绑定 → 不参与触发,值随读随用。"""
        return self.const_set or self.global_read is not None


@dataclass
class DataOut:
    """数据输出端声明:执行产出即投递,不产出即不投递;可绑定全局写入。"""

    name: str
    type_annot: Annot = field(default_factory=Annot)
    global_write: str | None = None


@dataclass
class ControlIn:
    """控制输入端声明:无载荷只承载电平;电平永远有定义(默认电平兜底),不参与触发。

    semantic:
    - enable:门控整节点(引擎级,节点实现之前被运行时拦截)——数据节点唯一可声明的
      控制端口;inactive = 不执行、输出信号关闭并传导;
    - level:纯电平输入,引擎不介入——信号节点(声明了控制输出端口的节点)的组合线。
    """

    name: str
    semantic: Literal["enable", "level"] = "enable"
    default_level: Level | None = None  # None → 按语义缺省:门控常开、电平 inactive

    def effective_default(self) -> Level:
        if self.default_level is not None:
            return self.default_level
        if self.semantic == "enable":
            return ACTIVE
        return INACTIVE


@dataclass
class ControlOut:
    """控制输出端声明:信号节点的输出,电平由实现显式写、未写保持原电平;
    可连其他控制端口(逻辑链),也可连数据端口的信号(显式屏蔽)。"""

    name: str
    default_level: Level = INACTIVE


@dataclass
class StateField:
    """可写状态字段:实例字段(方法间共享);必须有默认值;属世界事实,持久化。"""

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


@dataclass
class InputGroup:
    """输入组声明:一一对应一个输出组(方法/返回值)。

    - 组内全部有效输入收到新值 → 执行该组、该组输入消费清零、产出该组输出;
    - 组间参数不互传,节点状态(实例字段)共享;
    - 输出组为空 = void 方法(如参数调制组 [rate] → [])。
    """

    name: str
    inputs: list[str] = field(default_factory=list)   # 数据输入端口名(须连线,不可绑定)
    outputs: list[str] = field(default_factory=list)  # 数据输出端口名


@dataclass(frozen=True)
class Wire:
    """一条连线。

    dst_slot:"data" = 数据端口的数据槽;"signal" = 数据端口的信号槽或控制端口。
    数据流动方向 = 触发方向;扇出自由,扇入禁止(每个输入槽至多一条连线,
    多个来源的组合必须显式使用信号节点)。
    """

    src_node: str
    src_port: str
    dst_node: str
    dst_port: str
    dst_slot: str = "data"
