# 端口语义抽象收敛:事件、资格与节点组合

> 本文档整理 2026-08-19 端口语义抽象完整讨论(原始对话:
> `ChatGPT-重构端口语义抽象-20260819-1101.md`,同目录,14 轮演进)。这是
> **内核端口模型的一次语义重构决策记录**:从"每个数据端口默认附带信号"的
> 隐式假设,收敛到"事件是唯一传播事实、信号是输入资格、节点是原语组合"
> 的显式模型。
>
> **本阶段约束**:只读文档、不读代码。本文档的"现状"以既有文档表述为准;
> 新模型定义以本对话的收敛结论为准;未决问题明确标注为待审查,不自行发明
> 答案。
>
> 相关文档:[端口、绑定与资格](./graph-ports-bindings.md)(已按本模型重写) ·
> [执行模型](./graph-execution-model.md)(已按本模型重写) ·
> [触发语义](./graph-trigger-semantics.md) · [语义职责审计](./graph-semantic-audit.md) ·
> [节点类型](./graph-node-types.md) · [节点协议](./node-protocol.md) ·
> [图运行时总纲](./graph-runtime-overview.md) · [内核设计理念](./kernel-design-philosophy.md)

---

## 1. 背景与目的

旧模型有一个贯穿性假设:**每个数据输入/输出端口都自带一个信号(电平)**。
2026-08-19 的讨论证明这个假设需要推翻,而且推导出的新模型比"信号是否暴露"
更深一层——它重新定义了:

1. **事件的地位**:数据、信号、触发不是三套传播机制,而是同一种 Event 的
   载荷语义;
2. **端口的地位**:端口是 Event 与节点状态之间的语义边界(绑定模式、缓存
   策略、资格),不是简单的数据插槽;
3. **信号的地位**:信号是**输入资格(qualification)**,不是输出状态报告;
   输出侧不存在隐式信号;
4. **节点的地位**:节点不是内核的最小单位,是内核原语(Node Base + 声明式
   端口 + 重载)的组合。

本文档是这次收敛的完整决策记录,也是其他文档更新的依据。

---

## 2. 对话演进脉络(2026-08-19)

| 时刻 | 讨论内容 | 收敛结论 |
|------|---------|---------|
| 00:01 | Q1 重新定位:不是"哪些信号暴露",而是"节点需要什么端口组合" | 端口 = 正交能力组合;不存在默认成对 |
| 09:11 | 事件作为唯一底层事实? | **Event 是唯一传播事实**;Data/Signal 是载荷语义;"内核负责传播事实,节点负责解释事实" |
| 09:18 | 实际 bug:上游周期产出 Data + 翻转 Signal,下游永不输出 | 事件离散到达,"同时"不存在;Event → State → Readiness → Execution;**Event 不决定执行** |
| 09:21 | 顺序不同结果不同 | 事件需要**因果身份**(同一次 NodeTurn 产出共享因果组);但下游**不等全组**——等齐是端口语义,不是内核事实 |
| 09:45 | 四模型枚举:D1 S1 D2 S0... 推导出 D1,D2,D4 / D1,D3 顺序依赖 | **Data Arrival = Data State Update + Execution Attempt 是污染根源**;Dirty ≠ Execute |
| 09:55 | 选定状态模型 | 数据状态 / 信号状态 / **缓存策略**三分离;LOW 不拒数据、不清缓存,**有效值 = 默认属性**;缓存策略 Replace / Append |
| 10:01 | 想要 D1、D3 → 持续门控不够 | Signal 需要 **level(状态)+ pending(资格)** 两维;执行消费 pending,电平保持 |
| 10:03 | 修正:连接信号后不默认有效 | **未连接 Signal = 条件恒成立(结构属性,非事件);已连接 = 必须等实际 Signal Event** |
| 10:19 | 静态接口 / 动态接口 | **连接状态决定输入语义,而非只是输入来源**;Static = 节点自身属性,Dynamic = 外部事件驱动 |
| 10:22 | 2×2 资格矩阵 | Signal 关闭的是**参与资格**,不是接收能力;**Signal 是 Qualification 不是 Gate** |
| 10:29 | 触发条件 + Buffer 触发端口 + 输出语义 | Readiness ≠ Activation;Buffer 需显式 TriggerIn;输出 = Output Emission(Data/Signal Events) |
| 10:50 | **删除输出信号** | 无输出 = 没有事实发生,不是"LOW 事件";死等 = **拓扑诊断警告**,不是内核机制;Data→Signal 转换节点覆盖控制需求 |
| 10:55 | 节点不是最小单位 | Kernel Primitive → Node Base(默认行为 + 声明端口 + 重载)→ Concrete Node |
| 10:58 | 是否彻底收敛? | 尚未;已从"节点实现模型"收敛到"事件—端口—状态—激活—节点"语义模型;**下一步 = 冻结概念、寻找反例、不变量审查** |

这是一条健康的抽象收敛路径:每一次错误都不是补一个 `if`,而是发现
"两个我以为相同的概念,其实承担了两个不同的职责":

```
每个 Data 默认带 Signal      → 不是所有 Data 都需要控制
Signal 可选                  → Signal 不是 Gate,还涉及事件顺序
State 与 Occurrence 分离     → 无连接不能伪造隐式 Signal Event
Static / Dynamic 是不同语义   → Buffer 的数据接收与节点激活不同
Activation 与 Readiness 分离 → 没有输出不该编码成 LOW 事件
节点本身也不是原子            → 节点是原语组合
```

---

## 3. 收敛模型定义

### 3.1 Event 是唯一底层传播事实

> **在图之间的运行时交互层,唯一发生的传递事实是 Event;Data、Signal、
> Trigger 不是三套传播机制,而是同一种 Event 的载荷语义;端口声明规定
> 什么类型的事件可以通过这个端口。节点内部状态仍然是独立的状态,不是
> 事件。**

```text
Event
├── source / destination / port
├── payload(Data 值 或 Signal 电平)
└── causal_id(同一次 NodeTurn 产出共享因果身份)

DataIn   ← Event<Data>
SignalIn ← Event<Signal>
```

- "数据到达"与"信号到达"在内核层面不是两种不同事件,只是 `Event<Data>`
  与 `Event<Signal>`;数据不自己"流动",信号也不自己"流动",**流动的是
  事件**;
- **事件发生 ≠ 节点立即执行**:事件首先进入端口状态,节点按自己的执行
  契约决定是否产生一次 NodeTurn;
- 连接规则由端口类型决定:Data → Data、Signal → Signal、Data/Signal →
  TriggerIn 合法;Signal → Data 与 Data → Signal(数据线)在图验证阶段
  判定为类型不匹配;
- 边界:**不要事件溯源化**。`Node.state.counter = 18` 本身不需要成为事件;
  事件描述"发生了什么传递事实",状态描述"节点现在是什么状态";
- 事件可以异步传播,**事件的因果关系不能丢失**——但"同因果组"是内核
  事实,"要求哪些成员形成一次输入"是端口语义,二者区分,避免把运行时
  变成隐式同步批处理。

### 3.2 端口状态与资格

**Event 是事实,State 是事实的当前结果,Freshness(pending)是状态变化
是否尚未被当前执行消费的标记。**

```text
Data Port
├── value         缓存值(Replace 覆盖 / Append 累积)
├── pending       是否有尚未消费的 Data Event
└── cache policy  Replace(默认)/ Append(Buffer 类)

Signal Port
├── level         当前电平(HIGH / LOW),消费后保持
└── pending       是否有尚未消费的 Signal Event(资格)

Trigger Port
└── pending       是否有尚未消费的激活请求(occurrence)
```

- **Signal 的 level 与 occurrence 必须独立**:连续收到两个相同电平
  `S1 → S1` 是两次独立的 Signal Event、两次资格;不能用
  `old_level != new_level` 判断"是否更新";
- 执行(消费)后:`pending = false`,但 `level` 与 `value` 保持——信号状态
  不会因被消费而消失,缓存值也不会因参与过一次执行而清空;
- **低电平不是清空缓存,也不是拒绝数据**:

  ```text
  Signal = LOW 时,Data Event 照常进入、照常缓存
  cached_value = D4      ← 保留
  effective_value = default  ← 参与计算时表现为默认属性
  ```

  用 `stored / effective`(或 `cached / active`)而非"数据无效"来描述——
  缓存与参与资格是两个维度;
- 缓存策略是**端口属性**,不是节点类型:普通节点 = Replace;Buffer 节点 =
  Append。"高频事件在低频节点上传播导致旧值被覆盖"是 Replace 策略的
  正常结果,不是内核一致性错误(需要时由用户显式插入 Buffer)。

### 3.3 静态 / 动态接口:连接状态决定输入语义

> **接口的连接状态决定其输入语义,而不只是输入来源。** 未连接 = 节点自身
> 拥有的属性(静态);已连接 = 外部事件驱动(动态)。`connect(port)` 发生
> 一次语义转换,不是 `port.source = edge`。

```text
Static(未连接)
    → 使用默认属性 / 配置值 / 常量绑定
    → 条件恒满足(不等待事件)

Dynamic(已连接)
    → 等待实际事件
    → 初始状态不是默认值,而是"尚未收到事件"——Signal 端口是 ?(非
      HIGH 非 LOW),Data 端口无动态值
```

- 未连接 Signal:条件恒成立——**是结构属性,不是一个隐式初始事件**;
  已连接 Signal:隐式条件消失,必须等实际 Signal Event(否则"默认有效"
  会错误地让 D2 在 D1 S1 之后输出);
- 节点实现不需要写 `if connected:`——连接状态被内核吸收进 Port Readiness
  计算:
  - 静态接口 → requirement 从默认状态满足;
  - 动态接口 → requirement 必须等 Event;
- 静态与动态是**同一端口的两种运行模式**,不是两种端口类型
  (`Port = {declaration} + {binding: static | dynamic}`),避免类型爆炸;
- 编辑器中可自然体现:静态 = 参数/属性输入,动态 = 图连接输入——编辑器
  渲染内核声明的组合,不做"隐藏/暴露"决定。

### 3.4 资格矩阵:Signal 是 Qualification,不是 Gate

Data Port 的两个正交维度:

```text
维度一:数据来源    Static / Dynamic
维度二:控制绑定    Uncontrolled / Signal-Controlled
```

| Data Port | Signal 绑定 | 数据来源 | 当前有效性 |
|---|---|---|---|
| 无连接 | 无 | 默认属性 | 始终有效 |
| 有数据连接 | 无 | 动态事件 | 始终有效 |
| 无数据连接 | 有 Signal 连接 | 默认属性 | 由 Signal 控制 |
| 有数据连接 | 有 Signal 连接 | 动态事件 | 动态数据持续接收,有效性受 Signal 控制 |

- **Signal 关闭的是端口 → 节点执行的资格,不是 Source → Port 的接收**;
- 典型组合(节点声明的端口):

  ```text
  Static + Replace + 无资格     = 普通参数(Counter.increment 未接线)
  Dynamic + Replace + 无资格    = 普通动态输入(Random.num)
  Dynamic + Replace + Signal 资格 = 受控数据流(Join.a)
  Static + Replace + Signal 资格 = 受控默认参数
  Dynamic + Append + 无资格     = Buffer.data
  Dynamic + Append + Signal 资格 = 受控积累(低电平期间持续收集,资格放开才释放)
  ```

### 3.5 触发:Readiness 与 Activation 分离

```text
Event
  ↓
Port State 更新(value/level + pending)
  ↓
节点变 Dirty(任何相关输入变化)
  ↓
调度 → 检查 Readiness
  ↓
Ready → NodeTurn;Not Ready → 等待
```

- **Dirty ≠ Execute**。Data 与 Signal 在调度层面完全对称:都不拥有
  "触发权",都只是改变状态、使节点 Dirty、重新评估 Readiness;
- **Readiness 是节点语义**(从完整端口状态推导):

  ```text
  未连接 Signal:       信号条件恒成立
  已连接 Signal:       Data.pending AND Signal.pending AND Signal.level == HIGH
  节点级资格(enable):  同上(节点级 Signal)
  ```

  执行后消费本轮 pending;
- **Activation 与 Readiness 分离**:基类携带默认触发策略(隐式激活——
  输入状态变化 → Dirty → 自动检查 Readiness);声明显式 TriggerIn 端口
  改变激活策略(显式激活——Buffer 的 flush:Trigger Event → Activation →
  检查 Buffer 是否可输出)。普通节点可以没有显式 Trigger;
- 三种输入职责:

  ```text
  Data    → 改变数据状态
  Signal  → 改变控制状态 / qualification
  Trigger → 请求执行(Activation)
  ```

### 3.6 输出侧:无隐式输出信号

**删除输出信号自动传导。** 旧模型用"输出信号 LOW"告诉下游"不要再等"——
这是"无事件事件",把拓扑设计问题转化成了运行时语义问题:

> **没有 Output Event 就意味着本轮没有事实传播,而不是产生一个特殊的
> LOW / No Output Event。**

- 节点因拓扑原因永远无法产生 Event → **拓扑诊断给出警告**(静态可判定:
  上游无输入、不能自行产生 Event,下游又在等待该输入 → 潜在死锁拓扑),
  而不是让内核制造 LOW 信号维持错误拓扑;
- "没有人会试图通过让节点陷入无法输出的状态从而控制数据的流动"——
  控制流应该显式构造:

  ```text
  Data → Compare/DataToSignal → Signal → 受控输入
  ```

  内置数据转信号节点(DataToSignal / ValueToSignal / CompareToSignal /
  PredicateToSignal / ThresholdToSignal)+ 通用 Script → Signal 节点,
  覆盖全部控制需求;控制逻辑从内核提升到图本身;
- **Signal 的职责严格限定**:信号是**输入控制事件**,改变下游输入的资格;
  不是普通节点输出是否成功的状态码;
- 输出 = **Output Emission**:一次 NodeTurn 产生的事件集合。普通数据节点
  只产 Data Event;**Signal Event 仅当节点显式声明信号输出(信号节点)时
  才存在**——Signal 是图中一种可显式生成、传播和消费的事件类型,不是
  每个节点生命周期的副产品;
- 数据节点 = 不声明 SignalOut 的节点;信号节点 = 声明 SignalOut 的节点
  (可接受数据作为条件,AND/OR/NOT/Latch/比较器/DataToSignal 都是信号节点)。

### 3.7 节点不是最小单位:原语 → 基类 → 具体节点

```text
Kernel Primitive    Event / Port / State / Binding / Readiness /
                    Activation / NodeTurn / Output Emission
        ↓ 组合
Node Base           默认触发策略 + 默认执行流程 + 状态管理 + 声明式端口
        ↓ 重载
Concrete Node       Counter / Join / Buffer / Switch / Timer / Random /
                    DataToSignal / Script ...
```

- 大量 Concrete Node 不需要任何内核特权:

  ```text
  Buffer       = Base + Append 数据端口 + 显式 TriggerIn + 重载激活行为
  Counter      = Base + Replace 输入 + 默认激活 + 自增行为
  DataToSignal = Base + Data 输入 + Signal 输出 + 转换行为
  ```

- **重载分两个层级**:策略重载(activation = explicit_trigger 等声明)与
  生命周期/执行行为重载(`on_input / on_activate / on_execute / on_output`)。
  后者必须谨慎——越往底层开放 Override,节点越容易重新实现一套自己的
  执行模型;Base Node 应提供稳定生命周期钩子,不让节点绕过内核的事件与
  Readiness 机制;
- 内核的语义规模因此不随节点数量增长:"内核提供制造节点的物理规律,Node
  是按照这些规律构造出来的器件"。

### 3.8 六条稳定原则

1. **Event 是底层动态事实**:数据传播、信号传播、显式 Trigger 都是 Event;
   Event 改变 Port State,Readiness/Activation 决定是否产生 NodeTurn;
2. **Port 是语义边界,不是数据插槽**:Data / Signal / Trigger 语义 ×
   Static/Dynamic 绑定 × Replace/Append 缓存 × 资格,正交组合;Signal 不
   阻止 Data Event 到达,只影响资格;
3. **Static/Dynamic 是输入绑定模式,不是数据类型**:未连接用默认属性;
   连接后由外部事件驱动;Signal 连接后不存在隐式默认事件;
4. **Activation 与 Readiness 分离**:基类默认隐式激活,显式 TriggerIn 改变
   激活策略;"数据到达"与"要求节点执行"是独立概念;
5. **输出不通过隐式 Signal 报告"本次没有输出"**:没有 Event 就是没有事实
   发生;死等由拓扑分析警告;控制流用 Data → Signal 显式构造;
6. **Node 不是内核的最小语义单位**:只有真正改变执行模型的东西才进入内核。

**一句话概括**:

> **Event 是唯一传播事实;Data 决定"数据是什么",Signal 决定"已到达的数据
> 何时具备参与执行的资格",Trigger 决定"何时请求一次执行";Signal 永远不
> 改变 Data Port 的接收属性;没有 Event 不会产生隐式 Event。**

---

## 4. 案例:D1/S1 事件配对(本模型的核心测试用例)

上游每周期产生一个数据和一个翻转电平:

```text
D1 S1  D2 S0  D3 S1  D4 S0
```

内核不保证同一轮内部的传播顺序,实际可能是:

```text
序列 A: D1 S1 D2 S0 D3 S1 D4 S0
序列 B: S1 D1 S0 D2 S1 D3 S0 D4
```

下游节点:Data 端口(Replace)+ 已连接 Signal 资格端口。

**旧模型(Data 到达即检查执行条件)**:序列 A 输出 D1,D2,D4;序列 B 输出
D1,D3——顺序进入可观察语义,结果不确定。根因:Data Arrival 同时承担
"数据状态更新"与"执行尝试"两个动作。

**新模型**:

```text
执行条件 = Data.pending AND Signal.pending AND Signal.level == HIGH
执行后:  Data.pending = false;Signal.pending = false(Signal.level 保持)
```

序列 A:

```text
D1  → Data=D1 pending;Signal 无 pending → 不执行
S1  → Signal=HIGH pending;条件全满足 → 输出 D1,消费双 pending
D2  → Data=D2 pending;Signal 无 pending → 不执行
S0  → Signal=LOW pending;level 不满足 → 不输出(资格被 S0 自身消费为
      控制状态更新;LOW 不产生有效组合)
D3  → Data=D3 pending;Signal 无 pending → 不执行
S1  → Signal=HIGH pending;条件全满足 → 输出 D3,消费双 pending
D4/S0 → 同理不输出
```

序列 B 同样得到 **D1, D3**——交换 Data/Signal 到达顺序不改变语义。

关键语义:

- Signal 的 pending **不是"最近发生过变化"**,而是"这个 Signal Event 尚未
  与一个 Data Event 形成一次执行";执行 D1 时已消费那一轮 Signal,D2 不能
  继承前一个 S1 的执行资格;
- 未连接 Signal = 条件恒成立(D1 → 输出,D2 → 输出……普通数据节点的自然
  行为),**且不存在隐式初始事件**(否则序列 A 的 D1 会在 S1 之前错误输出,
  或 D2 在 S1 之后错误输出)。

---

## 5. 与现状文档的差异对照

以下差异已同步到本文档 §3 引用的各设计文档(状态见 §7)。

| # | 旧定义(现状文档) | 新定义(收敛模型) | 影响 |
|---|-----------------|-----------------|------|
| 1 | 每个数据输入/输出端口自带信号,信号数量 = 数据端口数量 | Signal 是可选声明(输入资格槽 / 独立 SignalIn);输出无隐式信号 | 声明模型、序列化、快照、校验、编辑器渲染 |
| 2 | 输出信号自动传导:对应输入组全关 → 输出关闭;门控 → 输出信号输出关闭并传播 | 删除。没有输出 = 没有事实发生;控制流用 Data→Signal 显式构造 | 执行引擎、信号节点定义、结构级门控表述 |
| 3 | 输入信号 inactive → 端口视为不存在、缓冲失效、旧值清除 | LOW → 数据照常接收缓存,effective = 默认属性;缓存保留 | 缓冲语义、clear_input、快照 |
| 4 | 信号三来源:显式信号线 / 自动传播 / 默认 active;以信号线为准 | 资格槽未连接 = 条件恒成立(结构属性);已连接 = 等实际 Signal Event | 输入资格计算 |
| 5 | 门控 enable:inactive = 不执行 → 输出信号输出关闭并传导 | enable = 节点级 Signal 资格(level + pending);不执行但数据照常缓存 | 门控语义、结构级门控 |
| 6 | 信号线 → 数据端口信号槽是唯一合法交叉通道 | SignalOut → SignalIn / 数据端口的资格槽合法;Signal → 纯数据端口非法 | 连线校验 |
| 7 | 触发 = 数据到达(隐式)/ TriggerIn(1.0)双轨;策略四枚举 | Readiness 与 Activation 分离:基类默认隐式激活,TriggerIn 显式覆盖;策略 = pending 聚合规则 | 触发语义文档 |
| 8 | 数据节点唯一信号逻辑 = 自动传导 | 数据节点无信号逻辑;SignalOut 仅信号节点显式声明 | 节点分类 |
| 9 | 死等靠输出信号传导避免 | 死等 = 拓扑诊断警告(静态可判定) | 编辑器静态提示扩展 |
| 10 | 缓存策略硬编码"一格覆盖 + 消费清空"(审计边界 4 的压缩) | 缓存策略是端口声明属性:Replace(默认)/ Append | 审计边界 4 缓解 |
| 11 | Buffer 靠组声明序区分 put/flush | Buffer = Append + 显式 TriggerIn,不再依赖声明序 | Buffer 语义 |
| 12 | 快照含端口信号电平表 | 快照含端口状态表(value/level/pending) | 持久化 |

---

## 6. 未决问题与不变量审查(尚未收敛的部分)

对话结尾明确:**模型尚未"彻底收敛",下一步不是继续抽象,而是冻结概念、
寻找反例、审查不变量**。以下问题在实现前必须逐一裁定(沿用对话原文的
尖锐提问):

### 6.1 十问(Event / Port / State / Turn 的精确关系)

```text
1.  一个 Event 是否只能被消费一次?
2.  一个 NodeTurn 是否可以消费多个 Event?
3.  两个 Event 同时让节点 Ready 时,是否一定只产生一次 Turn?
4.  一个 NodeTurn 产生多个 Output Event 时,它们是否属于同一个原子传播单元?
5.  NodeTurn 执行过程中产生的 Event 能否重新进入当前节点?
6.  一个 Event 到达多个下游时,是一个 Event 的多个传播实例,还是一个 Event 的多个引用?
7.  Dynamic Port 刚建立连接时,是否继承原来的 Static State?
8.  断开 Dynamic Port 后,缓存数据是否重新成为 Static State?
9.  Signal LOW 时,Data Pending 是否继续存在?
10. Signal Event 被消费以后,Signal State 是否永久保持?
11. NodeTurn 没有输出时,是否仍然构成一次有效的 Turn?
```

(对话提出 10 个,补充第 11 条——事件因果闭包问题的直接落点。)

### 6.2 内核不变量草案

```text
Event 是唯一动态传播事实。
Port State 可以存在而没有 Pending Event。
Dynamic Port 在没有收到 Event 时不能凭空获得新的动态输入资格。
Signal State 与 Signal Event Occurrence 必须独立。
Data Event 不因为 Signal LOW 而消失。
Signal 不负责报告 Node 是否产生 Data Output。
NodeTurn 只能由明确的 Activation 条件产生。
没有 Event 不会产生隐式 Event。
```

### 6.3 极端组合测试矩阵(不变量审查的输入)

```text
静态 Data / 动态 Data / 静态 Signal / 动态 Signal
Data → Signal 转换 / Signal → Data 拒绝
D1 S1 与 S1 D1(顺序交换)
D1 S1 D2 S0 D3 S1(配对)
D1 D2 D3 后 S1(堆积)
S1 S1 S0 S0(同电平重复)
多个 Data 同时 Ready / 多个 Event 连续到达
Replace / Append 缓存
显式 Trigger / 隐式 Trigger
Trigger + Signal / Trigger + Data / Trigger + Data + Signal
断开连接 / 重新连接 / 运行中修改拓扑
节点没有任何输出 / 节点永久无法输出(→ 拓扑警告)
节点输出多个 Event / 一个输出连接多个下游
```

审查方法:每个场景记录内核不变量是否成立,而不是"程序是否符合预期"。

---

## 7. 文档同步状态

| 文档 | 状态 |
|------|------|
| graph-ports-bindings.md | **已按新模型重写**(端口、绑定与资格) |
| graph-execution-model.md | **已按新模型重写**(端口状态与资格、无隐式输出信号) |
| graph-node-types.md | 声明骨架更新(端口行、资格、缓存策略) |
| node-protocol.md | 声明表与执行协议更新 |
| graph-runtime-overview.md | 概念表更新("数据到达 = 触发"旧表述修正) |
| graph-trigger-semantics.md | 连接规则与演进记录更新 |
| graph-semantic-audit.md | 增补 2026-08-19 复审章节(历史审计保留) |
| graph-persistence-and-editing.md | 快照结构更新(端口状态表) |
| graph-script-node.md | 示例与端口声明更新 |
| graph-kernel-engineering.md | 白名单注释(DataToSignal 系列方向) |
| llm-nodes.md | failed 信号输出 → 显式 SignalOut 表述 |
| editor docs/node-open-questions.md | Q1/Q2 定案记录 |
| kernel-design-philosophy.md / graph-assets.md | 概念兼容,未改动 |

---

## 8. 综合评估

- **收敛状态**:内核已从"节点实现模型"进入"事件—端口—状态—激活—节点"
  语义模型。前一个阶段的问题是"这个节点应该怎么实现";现在的问题是
  "任何节点,无论如何组合,都必须遵守什么世界规则"——后者才是真正的
  Kernel Design;
- **本次重构与 1.0 触发重构的关系**:同构延续——1.0 把 Trigger(函数调用级)
  从 Data 端口拆出;本次把 Signal(输入资格)从 Data 端口拆出,并删除了
  输出侧隐式信号。两次都是"拆语义职责"原则的应用,而且都发生在节点 ABI
  固化之前(成本最低窗口);
- **对实现者的提示**:现有实现中"到达即 fresh""组触发判定 = 全部有效输入
  fresh""clear_input 信号关闭即失效""输出信号自动传导"等机制与旧文档
  同源,与新模型冲突;但本阶段不触碰代码,实现对照与迁移留待后续阶段
  (以本文档 + 两个重写文档为基准)。
