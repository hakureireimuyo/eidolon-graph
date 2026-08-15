"""执行引擎层。

职责:
- 同步轮次(tick):轮初读、轮末提交、采样保持;
- 节点调度与就绪规则(warm-up)、门控/屏蔽拦截、异常与熔断;
- 编辑事务与状态迁移(改连线→就绪重置、换实现→迁移函数);
- 世界快照/持久化(节点状态、端口 held 值、全局变量、轮次、RNG)。

不负责:图模型定义与资产格式 —— 见 eidolon_graph.model。
"""

from .edit import (AddEdge, AddNode, ChangeImpl, EditOp, EditResult, MigrationPlan,
                   ReimplementRecord, RemoveEdge, RemoveNode, SetConfig, apply_edits,
                   edit_transaction)
from .protocol import NodeImpl, TickContext, TickOutput
from .registry import NodeRegistry
from .rng import Rng
from .runtime import CompiledGraph, NodeState, World
from .signal import ACTIVE, INACTIVE, DataPacket
from .snapshot import NodeSnapshot, Snapshot, capture, restore_world
from .subgraph import SubgraphNodeImpl
from . import builtins

__all__ = [
    "ACTIVE", "INACTIVE", "DataPacket",
    "Rng",
    "NodeImpl", "TickContext", "TickOutput",
    "NodeRegistry",
    "CompiledGraph", "NodeState", "World",
    "NodeSnapshot", "Snapshot", "capture", "restore_world",
    "AddNode", "RemoveNode", "AddEdge", "RemoveEdge", "SetConfig", "ChangeImpl",
    "EditOp", "MigrationPlan", "ReimplementRecord", "EditResult",
    "apply_edits", "edit_transaction",
    "SubgraphNodeImpl",
    "builtins",
]
