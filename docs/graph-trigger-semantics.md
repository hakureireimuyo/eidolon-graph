# 触发语义:触发端口、触发策略与内核语义审计

> 本文档整理 2026-08-18 的设计讨论(原始对话:`ChatGPT-信号连接触发端口 !-20260818-1454.md`,仓库根目录)。起因是**数据端口同时承担"数据传递"与"事件触发"两个职责**——讨论先提出模型修正方案(Trigger Port 独立 + 节点触发策略),进而把触发提升为**内核一等语义**(函数调用层、因果追踪、可编程节点接口),最后形成一次针对现有实现的**语义职责审计**框架。
>
> 本文档是**设计决策记录**:已实现的行为与提案内容明确区分;提案部分不构成当前实现。
>
> 相关文档:[图运行时总纲](./graph-runtime-overview.md) · [执行模型](./graph-execution-model.md) · [端口、绑定与输入资格](./graph-ports-bindings.md) · [节点类型](./graph-node-types.md) · [内核设计理念](./kernel-design-philosophy.md)

## 1. 背景:数据端口同时回答两个问题

### 1.1 现状

当前模型:数据端口自带隐式触发语义——"数据到达 → 节点触发"。为了支持"数据到达但不触发"的场景,`DataIn` 增加了 `trigger=True` 声明(事件端口):

> 触发端口(事件端口):**主要语义是触发而非数据**——到达即触发组,载荷可用可忽略;引擎对触发端口零特殊处理——标记是语义声明(见 [端口、绑定与端口信号](./graph-ports-bindings.md) §2)。

### 1.2 问题:模型层面的类型混淆

讨论指出,这不是连接规则问题,而是**模型层面的类型混淆**:

- "数据到达" 与 "节点是否应该执行" 是两个不同层次的问题,却被压缩进了同一个端口对象:
  1. 数据到了什么?—— 属于 Data;
  2. 节点为什么执行?—— 属于 Trigger。
- 为了让 "Signal → Trigger" 在直觉上成立,而 Trigger 实际上仍只是带标记的数据端口,系统必须引入"某些情况下允许 Signal 接到 Data Port"的**隐藏语法规则**——端口的本体语义越来越难以解释;
- 症状:"触发"本是函数调用级(节点何时执行),却以参数级(端口属性)的形式存在;触发端口看起来"像信号",因为触发本质上是信号侧的语义。

> **结论:需要从本质上区分,而不是用隐藏的语法规则。** 独立出 Trigger Port,并给节点一个默认触发策略。

## 2. 模型修正:三个正交概念

重新整理为三个正交概念,职责完全分开:

| 概念 | 回答的问题 | 层级 |
|------|-----------|------|
| **Data** | 节点处理什么数据 | 参数级 |
| **Signal** | 哪些端口开放 / 控制什么 | 参数级 |
| **Trigger** | 节点何时执行 | **函数调用级** |

```
Node
│
├── Parameters           ← 参数环境
│   ├── Data Ports
│   └── Signal Ports
│
└── Function             ← 执行入口
    └── Trigger Ports
```

**Data 和 Signal 都属于函数参数环境;Trigger 是函数调用入口。** 节点近似于:

```text
function execute(data_a, data_b, signal_enable): ...
```

Data/Signal 是执行时使用的输入环境,Trigger 更接近 `execute(...)` 本身。

### 2.1 端口语义

- **Data Port**:只负责数据的接收 / 存储 / 暴露,不再承担"收到数据后顺便触发节点"的隐含职责。数据何时导致节点执行,由节点的 Trigger Policy 决定;
- **Signal / Control Port**:负责端口的启用 / 禁用 / 数据流选择;
- **Trigger Port**:负责请求节点激活(execution request)。

### 2.2 Trigger 是消费语义,不是数据类型

Trigger Port 可以接收 Signal 也可以接收 Data,但**不是类型兼容**——中间存在一次明确的语义转换(activation):

```text
Signal ──► Trigger            Data ──► Trigger
               ↑                             ↑
          activation                   activation
```

不是"Signal 可以连接 Data",而是:

> **Signal 和 Data 都可以产生 Trigger。**

因此 Data 与 Signal 依然没有互通——它们只是在 Trigger 这个更高的"执行语义"层上汇合:

```text
Data
  │
  ├──► Data Port   → 数据状态
  │
  └──► Trigger     → 执行请求

Signal
  │
  ├──► Control     → 控制端口状态
  │
  └──► Trigger     → 执行请求
```

### 2.3 连接规则

| 连接 | 合法性 | 语义 |
|------|--------|------|
| Data → Data Port | ✓ | 参数绑定(数据传递) |
| Signal → Signal Port / 数据端口资格槽 | ✓ | 参数绑定(输入资格,更新 level + pending) |
| Data → Trigger Port | ✓ | 调用请求(Data 到达触发端口 → 一次 activation) |
| Signal → Trigger Port | ✓ | 调用请求(Signal Event 到达触发端口 → 一次 activation,每次事件 = 新请求) |
| Signal → 纯数据端口(未声明资格槽) | ✗ | 类型污染,非法 |

> 2026-08-19 更新:旧"Control Port"更名为信号端口(signal-in/signal-out);
> 数据端口的信号绑定收敛为**可选资格槽声明**(见 [端口、绑定与输入资格](./graph-ports-bindings.md) §2)。

> **连接线的类型决定"传递什么";端口的类型决定"节点如何消费它"。**

## 3. 触发到达后的行为

Trigger 到达时,节点**并不一定立即执行**——它只是满足/请求了触发条件,随后节点按自己的输入规则判断:

```text
Signal arrival
      ↓
Trigger satisfied
      ↓
Check required inputs
      ↓
Ready?
 ┌────┴────┐
Yes        No
 ↓          ↓
Execute    Wait / cache / reject
```

因此触发端口天然成为**activation gate**——例如"B 有多个数据输入,但不该因数据到达就执行,应由某条信号控制":

```text
A ───────► B
           ▲
           │
Trigger ───┘
```

## 4. 节点触发策略(Trigger Policy)

### 4.1 两种激活模式并存

| 模式 | 机制 | 示例 |
|------|------|------|
| 隐式激活(Implicit activation) | 输入满足条件 → 执行 | 组合节点:所有必要输入准备完毕即自动激活 |
| 显式激活(Explicit activation) | Trigger 信号 → 检查输入 → 执行 | 长任务节点:数据可到,但只在显式触发时执行 |

两者不冲突。允许节点没有显式 Trigger——纯数据反应节点(如 Join)走隐式激活。

### 4.2 策略从"端口隐藏属性"变成"节点的执行机制"

> **"数据到达 → 默认触发"不再写进 Data Port 的定义,而成为节点默认采用的策略(如 `ON_ANY_DATA`)。历史行为被保留,但隐式行为从 Port 移到了 Node。**

策略候选(策略描述**如何产生 activation**,不描述执行逻辑):

```text
ON_ANY_DATA        任何有效数据输入到达 → 触发(普通节点的默认行为)
ON_ALL_DATA_READY  全部所需数据就绪 → 触发(Join 类节点)
ON_TRIGGER         仅在 Trigger 端口收到激活时触发
ON_DATA_AND_TRIGGER 数据就绪 + 收到触发 → 触发
MANUAL / NEVER     由外部机制(如脚本)决定
```

将来 Buffer、Latch、Join、同步器等特殊节点,都可以通过改变 Trigger Policy 或显式使用 Trigger Port 表达,**不需要继续往 Data Port 上堆特殊属性**。

### 4.3 可编程策略

策略不应设计成一组写死的枚举:节点可以挂载用户脚本覆盖原始行为,由脚本自己决定"什么情况下自动产生 Trigger":

```text
on_data_changed(...)
on_signal_changed(...)
on_trigger(...)
```

## 5. 触发 = 内核一等语义

Trigger 不只是节点机制,而是 **Runtime/Kernel 的一等语义**——Eidolon 自己的极小执行语言:

```text
Data       → 参数
Signal     → 参数控制
Trigger    → 调用
State      → 持久环境
Node       → 函数 / 对象
Runtime    → 执行环境
```

### 5.1 触发产生与触发执行分离

```
Data change
    ↓
Trigger Policy          ← 节点/脚本可覆盖:什么情况下产生触发
    ↓
Trigger generated
    ↓
Runtime scheduler       ← 内核管理:排队、暂停、恢复、传播
    ↓
execute()
```

用户脚本可以覆盖 Trigger Policy,但**不应绕过 Runtime scheduler 直接调用另一个节点**——否则会把事件传播系统打穿。脚本只能通过受控 API 请求:

```text
node_b.execute()        ✗  直接调用(禁止)
context.emit_trigger(node_b)   ✓  重新进入内核的正常传播机制
```

> 用户脚本是**节点行为的扩展机制**,不是第二套 Runtime。脚本必须参与现有的"事件注入 → 传播 → 节点激活 → 执行 → 继续传播 → 静止"模型,而不是创造一个新模型。

### 5.2 Trigger Context

一次 Trigger 天然对应一个执行上下文:

```text
TriggerContext
├── trigger source       ← 触发来源
├── input snapshot       ← 输入快照
├── active parameters    ← 生效参数
├── node state           ← 节点状态
├── execution metadata   ← 执行元数据
└── runtime API          ← context.trigger / read / write / signal / state / emit_trigger
```

脚本不应窥探 Runtime 内部数据结构,而应通过 Kernel API 获取当前 Trigger Context——**用户脚本看到的是稳定的语言接口,而不是 Python 实现细节**。

### 5.3 因果链追踪(Execution Trace)

Trigger 成为一等语义后,一次节点运行存在明确的因果对象:

```text
Trigger
├── source
├── target
├── cause
├── parameters
├── execution context
└── result
```

生命周期可观测:

```text
trigger.created → trigger.dispatched → node.enter → node.execute → node.exit → trigger.completed
```

于是编辑器可以呈现真实的运行时因果链,而不只是"这条线刚发光":

```text
[A]
 │
 │ Trigger #1024
 ▼
[B]
 │
 │ Trigger #1025
 ▼
[C]

Trigger #1025
└── caused by Trigger #1024
    └── caused by Trigger #1018
        └── external injection
```

上层由此获得稳定的基础,无需修改 Runtime 核心执行逻辑:

- **调试器**:回答"这个节点为什么执行 / 为什么没执行 / 这次执行使用了哪些输入";
- **编辑器**:实时显示传播路径;
- **性能分析**:统计一次 Trigger 从源传播到静止经过的节点与耗时;
- **Replay**:把一次完整事件传播保存成可回放数据。

## 6. 可编程节点接口(Node API / ABI)

### 6.1 设计原则

> **Kernel 不负责告诉上层"应该如何实现节点";Kernel 只定义"节点如何被调用、如何观察环境、如何产生新的执行请求"。**

内核只保证几个基本事实:

```text
参数发生变化 → 节点状态更新
Trigger 到达 → 创建一次执行上下文 → 调用节点 execute()
```

最小的 Node Runtime Interface:

```text
Node
├── parameters        data + signals
├── triggers
├── state
└── execute(context)
```

### 6.2 两类节点

| 类别 | 能力来源 | 示例 |
|------|---------|------|
| 内核原语节点 | 随内核分发的内置节点(17 个,全部为普通节点类型资产,引擎零特殊处理) | Buffer / Latch / Join / Timer / Clock / Output / Input(完整清单见 [工程组织](./graph-kernel-engineering.md) §3 白名单) |

> Split / Source / Trigger 为早期提案,未实现;1.1 合并同构节点:Clock 吸收 Pulse(周期源双输出面)、Timer 吸收 Delay(倒计时器双装填面)、Output 吸收 Printer(日志回显)。
| 可编程节点 | 只依赖标准 Node API(**已实现 1.2**:Script 节点,见 [graph-script-node.md](./graph-script-node.md)) | read parameter / write parameter / read state / write state / emit trigger / schedule work |

```
             Eidolon Runtime
                    │
          ┌─────────┴─────────┐
          │                   │
     Kernel Primitive     Script Node
          │                   │
     Buffer/Latch/...     User Behavior
          │                   │
          └─────────┬─────────┘
                    │
              Common Node API
```

Native 节点、Python 脚本节点、LLM 节点共享同一套执行语义——对 Kernel 来说,它们都只是"接受一次 Trigger、读取参数和状态、产生结果,并可能产生新的 Trigger":

```text
                    Kernel
                      │
        ┌─────────────┼─────────────┐
        │             │             │
      Native        Script          LLM
       Node          Node           Node
        │             │             │
        └─────────────┼─────────────┘
                      │
                Node API / ABI
                      │
                 Trigger Context
```

## 7. 语义职责审计:审查现有实现

> 本次修正的起因——"数据端口同时承担数据承载与触发执行"——本质上说明早期模型为简洁,把两个不同层次的概念压缩进了同一个对象。趁模型尚未固化,把类似的混合全部找出来。

### 7.1 审计原则

> **当一个概念同时回答两个不同层次的问题时,优先检查是否存在被压缩的语义。**

但注意:**拆的是语义职责,不一定是类或 API**。实现层完全可以让两个语义暂时共享一个对象,只要在 Kernel 的概念模型中独立、以后可以独立演化。例如"数据到了,所以节点执行"实际回答了两个问题——"数据到了什么"(Data)与"节点为什么执行"(Trigger),后者正是本次从前者中解放出来的。

### 7.2 七个审查边界

| # | 边界 | 被压缩的职责 | 审查要点 | 现有实现对照(整理时观察) |
|---|------|-------------|---------|------------------------|
| 1 | Data vs Availability | "有一份数据" vs "数据已就绪、可被消费" | 数据存在 ≠ 可参与本次执行;避免"有数据 = 可执行"的隐含语义 | `DataIn.trigger` 标记即此类压缩的实例;输入缓冲"一格、新值覆盖"的消费条件语义待确认 |
| 2 | Signal vs Enable | 控制事件 vs 持续控制状态 | "Signal ON" ≠ "Signal ONCE";"信号到达" ≠ "端口从现在起永久启用" | 端口信号(电平)是状态语义;Signal 事件与电平状态的边界需确认(见 [端口、绑定与端口信号](./graph-ports-bindings.md) §4) |
| 3 | Port vs Connection | 静态接口定义 vs 图拓扑关系 vs 运行时状态 | Port 不应保存 connected / enabled / pending / buffered / triggered 等连接运行时状态 | 三层分离方向与现有 Graph / Node / Connection 分层一致,需审计运行时状态归属 |
| 4 | Data vs Buffer | 数据接口 vs 数据存储策略 | latest / queue / overwrite / pending 是存储策略,不应内建于 Data Port | Buffer 初始放行问题已暴露此边界;输入缓冲策略需显式建模(Latest / Queue / Buffer / Accumulator / Custom) |
| 5 | Node vs Scheduler | 节点逻辑 vs 执行安排 | 节点只应"提出执行请求"(request trigger / continuation / scheduling),由 Runtime 决定排队、暂停、恢复、传播 | LLM 长任务节点的挂起/恢复是首个实例;节点直接决定调度顺序 = 第二套调度系统 |
| 6 | State vs Event | 当前状态 vs 变化因果 | 只存最终 State 会丢失传播信息;全部 Event 变成 State 会污染节点模型 | Trigger Trace(§5.3)正是此问题的解:State = 当前世界状态,Trigger/Event = 状态变化的因果过程,两者关联但不互相替代 |
| 7 | Execution vs Completion | 开始 vs 结束 vs 挂起 | Trigger 到达 ≠ 节点完成;需要显式建模 Activation / Execution / Completion / Suspend / Resume | LLM、网络请求、脚本任务等长时执行将依赖此区分 |

### 7.3 审查检查表

| 当前对象 | 它实际承担的职责 | 是否应拆分 |
|---------|-----------------|-----------|
| Data Port | 数据承载 + 默认触发 | **已发现,应拆分**(本次修正) |
| Signal | 控制事件 + 控制状态 | 需要确认 |
| Data Port | 数据接口 + 缓存策略 | 值得检查 |
| Port | 接口定义 + 运行时状态 | 值得检查 |
| Node | 节点逻辑 + 调度 | **建议严格分离** |
| State | 当前状态 + 历史事件 | **建议严格分离** |
| Trigger | 执行请求 + 执行过程 | **建议区分生命周期** |
| Connection | 拓扑关系 + 传播状态 | 值得检查 |
| Execution | 开始 + 完成 + 挂起 | **建议显式建模** |

> **时机**:现在拆分的成本很低;等脚本系统、节点 ABI 和资源系统建立之后再拆,成本会高很多。

## 8. 讨论演进记录

第一轮观点曾被后续讨论修正,最终模型以本文档为准:

| 早期观点 | 最终决定 |
|---------|---------|
| Trigger 是 Signal 的一种"消费端口",Signal → Trigger 合法、Signal → Data 非法 | Trigger 是独立概念(执行请求),**Data 与 Signal 都可以产生 Trigger**;Trigger Port 是消费语义而非数据类型 |
| Trigger 仍可作为数据端口的特殊声明 | 独立出 Trigger Port(函数调用级);数据端口的 `trigger=True` 标记让位于端口类型与节点策略 |
| "数据到达 → 触发"写在 Data Port 上 | 移到节点层:Node Trigger Policy 决定参数变化如何转化为执行请求 |

## 9. 落地建议(实施状态)

1. **端口模型**:**已实现(1.0.0-0)**——独立 TriggerIn 端口(函数调用级)替代 `DataIn.trigger=True` 标记;连接规则(§2.3:数据线/信号线 → TriggerIn 均合法)与校验落地;旧 0.x 资产(含 trigger 标记)直接拒绝加载;
2. **触发策略**:**已实现(1.0.0-0)**——组级策略 `InputGroup.policy`(ON_ALL_DATA_READY 默认保现状 / ON_ANY_DATA / ON_TRIGGER / ON_DATA_AND_TRIGGER);策略可编程(挂载用户脚本)待脚本系统;
3. **一等语义**:Trigger 生命周期、Trigger Context、因果链数据输出(供编辑器/调试器消费)——待实施(trace 已有访问粒度因果时间线,见 [语义职责审计](./graph-semantic-audit.md) 边界 6);
4. **节点接口**:标准 Node API(parameters / triggers / state / execute(context)),脚本只能通过 `context.emit_trigger()` 产生新触发,不得绕过调度器——待脚本系统落地;
5. **语义审计**:**已完成**,见 [语义职责审计:内核实现现状逐项审查](./graph-semantic-audit.md)(结论:边界 1 触发语义是实际修正落点,边界 2/3/5/6 已分离良好,边界 7 为有意识的反向决策)。

## 10. 后续演进(2026-08-19):触发与资格的统一

2026-08-19 端口语义抽象讨论(见 [端口语义抽象收敛](./graph-port-capability-composition.md))
进一步收敛了触发与信号的关系,本文档的模型在上层保持一致,以下是增量:

1. **触发判定改为 Readiness 检查**:组触发 = 端口状态推导(动态输入
   `pending` 聚合 + 资格条件叠加),而非"数据到达即触发";**Dirty ≠
   Execute**——任何相关输入状态变化(Data 或 Signal)都使节点 Dirty、
   重新评估 Readiness,Data 与 Signal 在调度层面完全对称;
2. **Activation 与 Readiness 分离**:基类默认触发策略 = 隐式激活(状态
   变化 → 自动检查 Readiness);显式 TriggerIn = 覆盖激活策略(本文档
   §4.1 的两种模式即为此);Trigger 的"请求执行"职责不变;
3. **Signal 收敛为输入资格**:Signal Event 更新 `level + pending`;执行
   条件 = `Data.pending AND Signal.pending AND Signal.level == HIGH`;
   未连接资格槽 = 条件恒成立(结构属性,非隐式事件)——消除了数据/信号
   到达顺序对结果的影响(D1/S1 配对案例见 [端口语义抽象收敛](./graph-port-capability-composition.md) §4);
4. **输出侧无隐式信号**:Signal 不承担"节点是否产生输出"的状态报告;
   死等 = 拓扑诊断警告;数据 → 信号转换节点(DataToSignal 等)显式构造
   控制流;
5. **触发端口与信号的连接规则保持不变**(§2.3):Data/Signal → TriggerIn
   均合法,每次 Signal Event(含同电平重复)都是一次新的激活请求。
