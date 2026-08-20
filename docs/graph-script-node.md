# Script 可编程节点:内嵌脚本定义节点(1.2)

> 实现状态:**已实现(1.2.0-0)**。设计构想见 [设计理念 §8 单片机类比](./kernel-design-philosophy.md)
> 与 [触发语义 §6 可编程节点接口](./graph-trigger-semantics.md);本文件是落地后的完整指南。

## 1. 定位

Script 节点 = **单片机**:介于门电路(基础节点)与专用芯片(LLM/记忆/世界模拟)之间的通用可编程节点。
一个脚本定义一个节点——**脚本是声明权威**:端口/组/状态/配置从脚本编译生成,资产声明是编译产物(缓存视图)。

```
Inputs
  ↓
┌─────────────┐
│ Script Node │
│ class Node  │   ← 声明(类属性)+ 行为(方法重载)
│ State       │
└──────┬──────┘
       ↓
    Outputs
```

实现形态:`impl.kind = "script"`,`impl.source` 存脚本正文,随节点类型资产版本化/打包分发(脚本不进快照,运行时状态照常经 `state` 字段入快照)。

## 2. 脚本形态:一个 `Node` 类

脚本命名空间注入内核声明模型(`DataIn/DataOut/TriggerIn/SignalIn/SignalOut/StateField/ConfigField/InputGroup/Annot`)、信号电平(`ACTIVE/INACTIVE`)与触发策略常量(`ON_*`)。脚本只需定义一个名为 `Node` 的类:

```python
class Node:
    """两数相加,记录调用次数。"""          # 类 docstring → 节点说明书(首行概要)
    data_in = [DataIn("a", Annot(int)), DataIn("b", Annot(int))]
    data_out = [DataOut("sum", Annot(int))]
    signal_out = [SignalOut("busy")]          # 信号节点:声明信号输出(仅信号节点)
    state = [StateField("calls", 0, Annot(int))]
    config = [ConfigField("offset", 0, Annot(int))]
    trigger_in = [TriggerIn("go")]            # 函数调用级触发入口(激活请求)
    groups = [InputGroup("add", inputs=["a", "b"], outputs=["sum"],
                         policy=ON_ALL_DATA_READY)]
    init_in = ["seed"]                        # 初始化输入(__init__ 参数,须 ∈ data_in)
    auto = False                              # 自走源节点

    def init(self, ctx):                      # 可选:初始化播种(init_in 就绪后执行一次)
        return {"calls": 0}

    def tick(self, ctx):                      # 必选语义:每个输入组一次(缺省 = 空产出)
        return {"sum": ctx.a + ctx.b + ctx.config["offset"],
                "state": {"calls": ctx.state.get("calls", 0) + 1},
                "busy": ACTIVE}

    def schedule(self, ctx):                  # 可选:实时模式发射周期(源节点)
        return None
```

**tick 返回 dict 约定**:

| 返回键 | 含义 |
|---|---|
| data_out 端口名 | 数据输出(本组 outputs 或任意已声明输出) |
| signal_out 端口名 | 信号电平(仅信号节点合法;未写保持原电平) |
| `"state"` | 状态增量(合并提交;与引擎深拷贝语义一致) |
| 其他 | 报错(防拼写错误) |

**ctx(ScriptContext)**:

- 属性访问端口名:`ctx.a` ≡ `ctx.data_in["a"]`(数据输入,含 TriggerIn 载荷);`ctx.control_in["enable"]` 电平;
- 标准字段:`ctx.group`(组名;源节点自走为 `"step"`)/ `ctx.state`(只读,改走返回增量)/ `ctx.config` / `ctx.rng` / `ctx.run_no`;
- 端口名与保留属性冲突时(如端口叫 `state`)用 `ctx.data_in["state"]` 显式访问。

**缺省行为**:未定义 `tick` → 空产出;未定义 `init`/`schedule` → 基类默认。脚本可定义任意辅助方法(如 `def _helper(self, ctx)`)。

## 3. 宿主注册(三种实现绑定之一)

```python
from eidolon_graph.engine.script import compile_script
from eidolon_graph.model import AssetLibrary
from eidolon_graph.engine import NodeRegistry

lib = AssetLibrary()
registry = NodeRegistry()

nt, _ = compile_script(source, type_name)   # 编译:声明(资产)+ 实现
lib.add_node_type(nt)                        # 类型资产(端口/组/状态/配置 + impl.source)
# 注意:kind="script" 的运行时实现由内核直接从 source 编译,**不走 registry**
```

- 与内置/能力库节点一样,类型资产进 `lib.node_types`,编辑器调色板经 node-types API 自动可见;
- 与 `kind="code"` 的差别:实现不注册进 `NodeRegistry`,运行时由内核按 `impl.source` 编译(World 级按类型缓存,多实例共享一次编译);
- 脚本随 `library_to_dict` 序列化往返(资产内容),随卡带打包分发。

## 4. 方法重载 ↔ 节点协议映射

| 脚本方法 | 节点协议 | 说明 |
|---|---|---|
| `tick(ctx)` | `NodeImpl.tick` | 每输入组一次;返回 dict(见 §2 约定) |
| `init(ctx)` | `NodeImpl.init` | 初始化输入就绪后执行一次;返回状态增量 |
| `schedule(ctx)` | `NodeImpl.schedule` | 实时模式发射周期(None = 每轮) |
| 类 docstring | `NodeImpl.doc` | 首行概要,其余行为分节 |
| — | 基类 final 方法 | 输入缓冲/资格计算(Readiness 判定、pending 消费)/状态提交不可重载(与代码节点相同) |

**可重载面与代码节点完全一致**:只重载各组处理逻辑与初始化逻辑;资格计算(Readiness 判定、pending 消费)、输出投递、组缓冲、状态提交是引擎基类 final。

## 5. 校验与安全

**校验器**(`validate`):`kind="script"` 专项检查——

1. `impl.source` 非空;
2. 脚本编译成功(语法/声明错误带行号报告);
3. **声明一致性**:编译产出的声明(端口/组/状态/配置/init_in/auto)与资产声明逐项比对,不一致报错——脚本是权威,资产是缓存,防漂移。

**安全边界(轻防护)**:脚本在受限命名空间执行——注入 DSL 符号,`__builtins__` 剔除 `__import__/open/eval/exec/compile/globals/locals` 等入口。防误写不防恶意:本地编辑器场景,脚本由用户自己编写并随图资产版本化;完整沙箱不在 V1 范围。

## 6. V1 边界(未实现)

- **`emit_trigger` / `request continuation`**(显式请求唤醒指定下游/自身延续):文档提案能力,涉及执行生命周期显式化(语义审计边界 7 重审),未实现——脚本请求下游沿用现有"产出 + schedule"隐式通道;
- 编辑器脚本编辑 UI(脚本经 node-types API 可见,编辑界面为后续工作);
- 多节点单脚本、子图内嵌脚本等组合形态。

## 7. 典型用法

```python
# 触发装填 + 到期输出(等价内置 Timer 触发面,演示策略与 TriggerIn)
class Node:
    """触发后 delay 值经过 time 次扣减输出。"""
    data_in = [DataIn("delay", Annot(int))]
    trigger_in = [TriggerIn("fire")]
    data_out = [DataOut("out", Annot(int))]
    state = [StateField("remaining", 0, Annot(int)), StateField("pending", None)]
    groups = [InputGroup("arm", inputs=["delay"], triggers=["fire"],
                         outputs=[], policy=ON_DATA_AND_TRIGGER)]
    auto = True

    def tick(self, ctx):
        if ctx.group == "arm":
            return {"state": {"remaining": ctx.delay, "pending": ctx.fire}}
        remaining = ctx.state.get("remaining", 0)
        if remaining <= 0 or ctx.state.get("pending") is None:
            return {"state": {"remaining": 0, "pending": None}}
        remaining -= 1
        if remaining == 0:
            return {"out": ctx.state["pending"], "state": {"pending": None}}
        return {"state": {"remaining": remaining}}
```
