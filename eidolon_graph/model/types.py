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

# 组触发策略(描述"如何产生 activation",不描述执行逻辑;字符串以便 JSON 直存)
ON_ALL_DATA_READY = "on_all_data_ready"       # 默认:全部有效连线数据输入有新值 → 触发(现状行为)
ON_ANY_DATA = "on_any_data"                   # 任一有效连线数据输入有新值 → 触发
ON_TRIGGER = "on_trigger"                     # 任一 TriggerIn 收到激活(信号电平变化 / 数据到达)→ 触发
ON_DATA_AND_TRIGGER = "on_data_and_trigger"   # 数据齐(全部有效连线输入有新值)+ 触发事件 → 触发
TRIGGER_POLICIES = (ON_ALL_DATA_READY, ON_ANY_DATA, ON_TRIGGER, ON_DATA_AND_TRIGGER)
TriggerPolicy = Literal["on_all_data_ready", "on_any_data", "on_trigger",
                        "on_data_and_trigger"]

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

    输入组 = 函数:端口 = 参数,端口信号 = 该参数是否使用默认值。
    optional 参数端口(函数默认参数):可不连线(裸端口校验豁免),不接线时
    不在 data_in 中出现(实现回退配置默认值);接线即参数,参与触发;
    端口被信号禁用时同样回退默认值。全部参数被禁用 → 对应输出端口
    自动禁用(自动传导)。

    数据端口**不再承担触发职责**(1.0 边界 1 修正):"何时执行"由组触发策略
    (InputGroup.policy)与独立 TriggerIn 端口表达,见 TriggerIn / ON_* 常量。
    """

    name: str
    type_annot: Annot = field(default_factory=Annot)
    const_set: bool = False
    const: Any = None
    global_read: str | None = None
    optional: bool = False

    def is_bound(self) -> bool:
        """带常量/全局读取绑定 → 不参与触发,值随读随用。"""
        return self.const_set or self.global_read is not None


@dataclass
class TriggerIn:
    """触发输入端声明:**函数调用级**——到达即请求一次 activation(组触发机会)。

    Trigger 是消费语义,不是数据类型:数据线(载荷可用可忽略)与信号线
    (电平双沿变化)都可以产生触发事件,但 Data 与 Signal 依然不互通——
    它们在 Trigger 这个执行语义层汇合(见 docs/graph-trigger-semantics.md §2)。

    - 无绑定 / 无 optional / 无信号槽概念(激活请求不需要"带电"维度);
    - 未连线合法(裸端口豁免):没有显式触发源 = 仅依赖数据策略激活;
    - 不进 init_in(初始化输入是数据语义);
    - 触发事件 = 新激活请求标记(与数据端口的 fresh 同构),组触发后消费清空,
      未触发时保留(等齐语义);端口信号关闭时事件一并失效。
    """

    name: str
    type_annot: Annot = field(default_factory=Annot)  # 载荷类型注解(数据线投递时静态检查)


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

    普通字段 = 可序列化的默认值/覆盖值(JSON 原生类型,存于图资产);
    asset_ref 字段 = 对资产的**引用**(如数据库连接、人格矩阵)——值本身存资产名
    (可序列化),World 构造时经宿主注入的 runtime_assets(资产名 → 运行时对象)
    按名解析成对象,**初始化后冻结不变**(连接等对象不落图资产、不深拷贝)。
    引用即校验(编辑事务提交前检查已声明;构造时检查有运行时绑定,失败要早)。
    可选给期望的种类提示:"service" / "data" / "knowledge" / "media";None = 不限。
    """

    name: str
    default: Any
    type_annot: Annot = field(default_factory=Annot)
    asset_ref: str | None = None


@dataclass
class InputGroup:
    """输入组声明:一一对应一个输出组(方法/返回值)。

    - 组 = 函数:inputs = 参数(数据输入端口,须连线、不可绑定),triggers = 调用
      入口(TriggerIn 端口,激活请求);组触发 = 按 policy 判定(见 ON_* 常量);
    - 组触发后该组输入消费清零(瞬态),组间参数不互传,节点状态(实例字段)共享;
    - 输出组为空 = void 方法(如参数调制组 [rate] → [])。
    """

    name: str
    inputs: list[str] = field(default_factory=list)      # 数据输入端口名(须连线,不可绑定)
    outputs: list[str] = field(default_factory=list)     # 数据输出端口名
    triggers: list[str] = field(default_factory=list)    # TriggerIn 端口名(激活入口)
    policy: TriggerPolicy = ON_ALL_DATA_READY            # 触发策略(默认 = 现状行为)


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
