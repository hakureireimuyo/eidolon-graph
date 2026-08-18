"""Script 可编程节点:内嵌 Python 脚本定义节点(声明 + 实现),单片机类比。

脚本 = 一个 `Node` 类:
- 类属性 = 声明:data_in/data_out/trigger_in/control_in/control_out/state/
  config/groups/init_in/auto(直接使用内核模型类:DataIn/DataOut/TriggerIn/
  ControlIn/ControlOut/StateField/ConfigField/InputGroup/Annot 等);
- 类 docstring = 节点说明书(首行概要,其余为行为分节);
- 方法重载 = 实现:tick(ctx)/init(ctx)/schedule(ctx),缺省 = 基类默认;
- tick 返回 dict:键 ∈ data_out 端口名 → 数据输出,键 ∈ control_out 端口名
  → 信号电平,特殊键 "state" → 状态增量;未知键报错(防拼写错误);
- ctx 为 ScriptContext 包装:属性访问端口名(ctx.a = ctx.data_in["a"]),
  其余字段与 NodeImpl 上下文一致(state 只读,改走返回增量)。

安全边界(轻防护):脚本在受限命名空间执行——注入 DSL 符号,`__builtins__`
剔除 __import__/open/eval/exec/compile 等危险项。防误写不防恶意:本地
编辑器场景,脚本由用户自己编写并随图资产版本化(完整沙箱不在 V1 范围)。

V1 边界:脚本映射到现有 NodeImpl 全能力(init/tick/schedule/doc);文档提案
的 emit_trigger / request continuation 是引擎新机制,未实现(见语义审计
边界 7 重审建议)。
"""

from __future__ import annotations

import builtins as _builtins
from typing import Any

from ..model.node import ImplBinding, NodeType
from ..model.types import (ACTIVE, CATEGORY_CUSTOM, INACTIVE, ON_ALL_DATA_READY,
                           ON_ANY_DATA, ON_DATA_AND_TRIGGER, ON_TRIGGER, Annot,
                           ConfigField, ControlIn, ControlOut, DataIn, DataOut,
                           InputGroup, StateField, TriggerIn)
from .protocol import InitContext, NodeImpl, ScheduleContext, TickContext, TickOutput

__all__ = ["ScriptError", "compile_script", "compile_script_impl", "ScriptNodeImpl"]

# 受限内建:轻防护(防误写不防恶意)——剔除可直接触达宿主环境的入口
_BUILTIN_BLOCKLIST = frozenset({
    "__import__", "open", "eval", "exec", "compile", "globals", "locals",
    "vars", "getattr", "setattr", "delattr", "input", "breakpoint",
})

# 注入脚本命名空间的 DSL 符号:内核声明模型 + 信号/策略常量
_DSL_NAMES: dict[str, Any] = {
    "DataIn": DataIn, "DataOut": DataOut, "TriggerIn": TriggerIn,
    "ControlIn": ControlIn, "ControlOut": ControlOut,
    "StateField": StateField, "ConfigField": ConfigField, "InputGroup": InputGroup,
    "Annot": Annot,
    "ACTIVE": ACTIVE, "INACTIVE": INACTIVE,
    "ON_ALL_DATA_READY": ON_ALL_DATA_READY, "ON_ANY_DATA": ON_ANY_DATA,
    "ON_TRIGGER": ON_TRIGGER, "ON_DATA_AND_TRIGGER": ON_DATA_AND_TRIGGER,
}


class ScriptError(Exception):
    """脚本编译错误(语法/声明缺失等);校验器与宿主捕获后转校验报告。"""


def _script_namespace() -> dict[str, Any]:
    # 受限内建字典:显式放入 __builtins__,阻止 exec 自动注入完整内建
    # (exec 仅在键缺失时补全;方法体的全局名查找经 __builtins__ 兜底)
    safe = {k: v for k, v in _builtins.__dict__.items() if k not in _BUILTIN_BLOCKLIST}
    return {**safe, **{k: v for k, v in _DSL_NAMES.items()}, "__builtins__": safe}


def _doc_from_docstring(docstring: str | None) -> dict:
    """类 docstring → 结构化说明书:首行 = 概要,其余行 = 行为分节。"""
    lines = [l.strip() for l in (docstring or "").splitlines() if l.strip()]
    if not lines:
        return {"summary": "", "sections": []}
    return {"summary": lines[0],
            "sections": [{"title": "行为", "lines": lines[1:]}]}


def compile_script(source: str, type_name: str) -> tuple[NodeType, type[NodeImpl]]:
    """编译脚本 → (NodeType 声明, NodeImpl 实现类)。

    - NodeType:从脚本类的声明属性读取(脚本是声明权威;资产/校验/编辑器
      都以此为准,运行期与资产比对防漂移);
    - 实现类:包装脚本实例,把 tick/init/schedule/doc 转发到 NodeImpl 协议。
    """
    ns = _script_namespace()
    try:
        exec(compile(source, f"<script:{type_name}>", "exec"), ns)  # noqa: S102 轻防护见模块头注
    except SyntaxError as e:
        raise ScriptError(f"脚本语法错误(第 {e.lineno} 行):{e.msg}") from e
    cls = ns.get("Node")
    if cls is None or not isinstance(cls, type):
        raise ScriptError("脚本必须定义一个名为 Node 的类(端口声明 + 方法重载)")
    attrs = cls.__dict__  # 只取本类定义,不混入继承链

    def _list(key: str, default: Any = None) -> list:
        v = attrs.get(key, [])
        return list(v) if v is not None else default

    nt = NodeType(
        name=type_name,
        category=CATEGORY_CUSTOM,  # 脚本节点 = 用户自定义类型,域恒为 custom
        data_in=_list("data_in"),
        data_out=_list("data_out"),
        trigger_in=_list("trigger_in"),
        control_in=_list("control_in"),
        control_out=_list("control_out"),
        state=_list("state"),
        config=_list("config"),
        groups=_list("groups"),
        init_in=_list("init_in", []),
        auto=bool(attrs.get("auto", False)),
        impl=ImplBinding(kind="script", source=source, name=type_name),
    )
    impl_cls = type(f"Scripted_{type_name}", (ScriptNodeImpl,), {
        "_script_cls": cls,
        "_nt": nt,
        "_doc": _doc_from_docstring(cls.__doc__),
    })
    return nt, impl_cls


def compile_script_impl(source: str, type_name: str) -> type[NodeImpl]:
    """运行期轻量编译:仅取实现类(World/编辑事务构建脚本节点时用)。"""
    return compile_script(source, type_name)[1]


class ScriptContext:
    """脚本看到的 ctx:属性访问端口名 + 标准字段(兼容 tick/init/schedule 三种上下文)。

    - 属性访问:数据输入端口名(含 TriggerIn 载荷)→ 值,其次控制输入端口名
      → 电平;端口名与保留属性冲突时用 ctx.data_in["..."] 显式访问;
    - state 只读:修改必须走 tick 返回值的 "state" 增量(深拷贝语义一致);
    - 字段不在当前上下文(如 init 没有 group/rng)时返回 None。
    """

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    @property
    def group(self) -> str | None:
        return getattr(self._ctx, "group", None)

    @property
    def run_no(self) -> int | None:
        return getattr(self._ctx, "run_no", None)

    @property
    def rng(self) -> Any:
        return getattr(self._ctx, "rng", None)

    @property
    def data_in(self) -> dict[str, Any]:
        return getattr(self._ctx, "data_in", None) or {}

    @property
    def control_in(self) -> dict[str, Any]:
        return getattr(self._ctx, "control_in", None) or {}

    @property
    def state(self) -> dict[str, Any]:
        return getattr(self._ctx, "state", None) or {}

    @property
    def config(self) -> dict[str, Any]:
        return getattr(self._ctx, "config", None) or {}

    @property
    def closed_in(self) -> frozenset[str]:
        return getattr(self._ctx, "closed_in", frozenset())

    def __getattr__(self, name: str) -> Any:
        di = getattr(self._ctx, "data_in", None)
        if isinstance(di, dict) and name in di:
            return di[name]
        ci = getattr(self._ctx, "control_in", None)
        if isinstance(ci, dict) and name in ci:
            return ci[name]
        raise AttributeError(name)


class ScriptNodeImpl(NodeImpl):
    """包装脚本实例:把脚本方法转发到 NodeImpl 协议(基类 final 行为保留)。

    子类通过类属性注入:_script_cls(脚本类)/_nt(编译出的 NodeType)/
    _doc(从 docstring 生成的说明书)。
    """

    _script_cls: type
    _nt: NodeType
    _doc: dict

    def __init__(self) -> None:
        super().__init__()
        self._script = self._script_cls()
        self._data_out_names = {p.name for p in self._nt.data_out}
        self._control_out_names = {p.name for p in self._nt.control_out}

    def doc(self) -> dict:
        return self._doc

    def init(self, ctx: InitContext) -> dict[str, Any] | None:
        if not hasattr(self._script, "init"):
            return None
        return self._script.init(ScriptContext(ctx))

    def tick(self, ctx: TickContext) -> TickOutput:
        if not hasattr(self._script, "tick"):
            return TickOutput()
        raw = self._script.tick(ScriptContext(ctx))
        return self._convert(raw)

    def schedule(self, ctx: ScheduleContext) -> float | None:
        if not hasattr(self._script, "schedule"):
            return None
        return self._script.schedule(ScriptContext(ctx))

    def _convert(self, raw: Any) -> TickOutput:
        """脚本返回 dict 分发:输出端口名 → data_out/control_out,"state" → 增量。"""
        if raw is None:
            return TickOutput()
        if not isinstance(raw, dict):
            raise ScriptError(f"脚本 tick 必须返回 dict(输出端口名 → 值,键 'state' → 状态增量),"
                              f"实际是 {type(raw).__name__}")
        data_out: dict[str, Any] = {}
        control_out: dict[str, Any] = {}
        state: dict[str, Any] = {}
        for k, v in raw.items():
            if k == "state":
                state = v
            elif k in self._control_out_names:
                control_out[k] = v
            elif k in self._data_out_names:
                data_out[k] = v
            else:
                raise ScriptError(f"脚本 tick 返回了未声明的键 '{k}'"
                                  f"(键必须 ∈ data_out/control_out 端口名,或 'state')")
        return TickOutput(data_out=data_out, control_out=control_out, state=state)
