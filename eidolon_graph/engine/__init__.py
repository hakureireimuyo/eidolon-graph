"""执行引擎层。

职责:
- 运行(run):注入事件 → 按节点声明序单遍执行 → 静止;
- 节点协议:初始化输入(__init__)、输入组(方法)、信号自动传导、门控/熔断;
- 编辑事务与状态迁移(改连线→缓冲重置、换实现→迁移函数);
- 世界快照/持久化(节点状态、输入缓冲、端口信号电平、全局变量、运行序号、每节点 RNG)。

不负责:图模型定义与资产格式 —— 见 eidolon_graph.model。
"""

from .edit import (AddEdge, AddNode, ChangeImpl, EditOp, EditResult, MigrationPlan,
                   ReimplementRecord, RemoveEdge, RemoveNode, SetConfig, apply_edits,
                   edit_transaction)
from .protocol import InitContext, NodeImpl, ScheduleContext, TickContext, TickOutput
from .registry import NodeRegistry
from .rng import Rng, derive_seed
from .runtime import CompiledGraph, Event, NodeState, World
from .signal import ACTIVE, INACTIVE
from .snapshot import NodeSnapshot, Snapshot, capture, restore_world
from .subgraph import SubgraphNodeImpl
from . import builtins

__all__ = [
    "ACTIVE", "INACTIVE",
    "Rng", "derive_seed",
    "NodeImpl", "TickContext", "TickOutput", "InitContext", "ScheduleContext",
    "NodeRegistry",
    "CompiledGraph", "NodeState", "World", "Event",
    "NodeSnapshot", "Snapshot", "capture", "restore_world",
    "AddNode", "RemoveNode", "AddEdge", "RemoveEdge", "SetConfig", "ChangeImpl",
    "EditOp", "MigrationPlan", "ReimplementRecord", "EditResult",
    "apply_edits", "edit_transaction",
    "SubgraphNodeImpl",
    "builtins",
]
