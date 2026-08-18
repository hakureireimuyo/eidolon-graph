# 语义职责审计:内核实现现状逐项审查

> 依据 [graph-trigger-semantics.md §7](./graph-trigger-semantics.md) 的审查框架,对内核**现有实现**逐项审查——寻找"一个字段、一种端口或一个 API 同时解释两个不同层次的问题"的压缩语义。
>
> **审计范围**:`model/`(types.py 端口类型 · node.py 节点类型 · graph.py 图资产 · validate.py 校验器)、`engine/`(protocol.py 节点协议 · runtime.py 执行引擎 · snapshot.py 快照 · signal.py 信号)、`engine/builtins/`(Buffer / Latch / Timer / Join / MultiGate)、`nodes/llm/llm_node.py`(长任务节点)。
>
> **判定符号**:✅ 无混合(职责已分离)· ⚠️ 存在混合(需关注)· 🔶 有意识的设计选择(与提案相反但模型自洽)
>
> 相关文档:[触发语义设计(审查框架)](./graph-trigger-semantics.md) · [端口、绑定与端口信号](./graph-ports-bindings.md) · [执行模型](./graph-execution-model.md) · [内核设计理念](./kernel-design-philosophy.md)

## 1. 总览

| # | 边界 | 判定 | 摘要 |
|---|------|------|------|
| 1 | Data vs Availability | ✅ **已修复(1.0.0-0)** | TriggerIn 独立端口 + 组级触发策略落地;`DataIn.trigger` 标记与引擎硬编码判定移除;旧资产拒绝 |
| 2 | Signal vs Enable | ✅ | 事件(变化)与状态(电平)已分离;enable / level 已显式建模 |
| 3 | Port vs Connection | ✅ | 静态接口 / 拓扑关系 / 运行时状态三层清晰;Mark / NodeTurn / NodeState 三分是系统先例 |
| 4 | Data vs Buffer | ⚠️ | 存储策略硬编码"一格覆盖 + 消费清空";队列类策略靠节点状态模拟 |
| 5 | Node vs Scheduler | ✅ | 节点只能"请求"(产出 / schedule),引擎决定安排;缺口在无显式 emit_trigger API |
| 6 | State vs Event | ✅ | log / trace / 快照三层已分离;trace 缺执行生命周期与结果 |
| 7 | Execution vs Completion | 🔶 | 引擎保持无知:同步 tick + 节点让出 + 宿主回注;生命周期是节点包业务(与提案相反,自洽) |

## 2. 边界 1:Data vs Availability —— ⚠️ 部分分离,策略不可编程

**审查问题**:"这里有一份数据"与"这里的数据已就绪、可被消费"是否混合。

### 现状机制

**好消息:值与新鲜在实现上已经分开,是两个独立存储**:

- `NodeImpl._buffers`(protocol.py:88)—— 值存储;"键存在即有值";
- `NodeImpl._fresh`(protocol.py:89)—— 就绪标记;"未消费的新输入";
- 触发判定看 fresh(`all(p in impl.fresh for p in trigger)`,runtime.py:574),取值看 buffers(`_resolve_port`,runtime.py:821-831)。

**但"就绪"的产生规则被硬编码在引擎**:

- "到达即新鲜"是投递的固定语义:`receive()` 无条件 `_fresh.add(port)`(protocol.py:111-114),`_receive` 注释"新值即新鲜,与值是否相同无关"(runtime.py:472);
- 组触发条件 = **全部有效连线输入 fresh**(runtime.py:561-576)——即隐式 `ON_ALL_REQUIRED_DATA` 策略,写死在 `_node_turn` 里,**没有任何按节点声明/按端口声明覆盖的入口**;
- 节点无法表达"数据在,但此刻不可消费"。

### 压缩语义的具体实例

1. **`DataIn.trigger=True`(types.py:97)是声明层语义标记,引擎零特殊处理**——types.py:87-88 注释原文"触发判定本就基于新值到达(fresh),引擎对 trigger 端口零特殊处理——标记是语义声明"。这正是对话讨论的压缩形态:**触发(函数调用级)以端口属性(参数级)存在**,靠"新值到达"隐式兑现,没有任何机制层含义;
2. **校验器因此拒绝信号线连触发端口**:validate.py:297-300 "触发端口不接受信号线(事件端口:触发只认数据到达,信号屏蔽无意义)"——即 graph-trigger-semantics.md §2.3 提案中 `Signal → Trigger` 连接,在当前模型中**不可表达**;
3. **Buffer 初始放行问题的根**:Buffer 的"put 累积 / flush 取出"依赖**组声明序**(buffer.py:6-8 "同轮 put 与 flush 齐到:按组声明序先 put 后 flush"),"数据是否可被这次 flush 消费"由声明序隐式决定,无显式建模。

### 判定与修复(1.0.0-0)

- fresh(就绪)与 value(数据)存储层已分离 ✅;**策略层混合已在 1.0 修复**:
  - `DataIn.trigger` 字段移除,独立 `TriggerIn` 端口 + `InputGroup.policy`(ON_ALL_DATA_READY / ON_ANY_DATA / ON_TRIGGER / ON_DATA_AND_TRIGGER)落地;
  - 引擎组触发判定(runtime.py)按策略执行,`trigger=True` 声明层标记与"触发端口不接受信号线"校验删除(信号线 → TriggerIn 现为合法激活源);
  - 旧 0.x 资产(含 trigger 标记)主版本不兼容,加载直接拒绝(version bump 1.0.0-0);
- 触发事件与数据 fresh 同构:组触发后消费清空、未触发保留(等齐语义)、信号关闭一并失效;快照 / 编辑事务 / 暂停恢复均已适配。

## 3. 边界 2:Signal vs Enable —— ✅ 已分离

**审查问题**:"Signal 是一次控制事件,还是持续存在的控制状态?"

### 现状机制

- **事件与状态已分开**:电平是**持久状态**(`output_signals` / `control_in_levels` / `control_out_levels` 三张表,runtime.py:176-178);控制变化是**事件**——`_set_ctrl` 仅电平真变化才唤醒(runtime.py:497 "电平未变直接返回",Mark 注释"电平真变了才 K_CTRL",runtime.py:88-90);
- **enable 与 level 已显式建模**(types.py:113-132):`semantic="enable"` = 引擎级门控(`_enabled`,runtime.py:727-730,"inactive = 不执行、输出信号关闭并传导");`semantic="level"` = 纯电平输入,引擎不介入(逻辑元件组合线)。这正是"Signal 的两种职责"的显式分化;
- 数据端口的 fresh 是"一次事件"(触发后消费清零,protocol.py:116-121),控制电平是"持续状态"——**两套语义各自清晰,未混用**。

### 注意点(非混合,记录)

- 数据输入的信号电平是**访问时现算、无持久存储**(runtime.py:525 "输入信号无持久存储、访问时现算"),控制输入电平有持久存储——不对称,但各自语义自洽(输入信号 = 推导值,控制电平 = 存储值);
- `clear_input`(protocol.py:123-130)实现"信号关闭 → 缓冲失效"——是信号对数据的**联动清理**,不属于混合。

### 判定

边界 2 无混合。审查框架 §7.2 的"Signal ON vs ONCE"担忧,现有模型通过"电平状态 + 变化事件"已解决。

## 4. 边界 3:Port vs Connection —— ✅ 分层清晰

**审查问题**:Port 是否保存 connected / enabled / pending / buffered / triggered 等连接运行时状态。

### 现状机制

三层在实现中明确分离:

| 层 | 位置 | 内容 |
|----|------|------|
| 静态接口定义 | `model/types.py`(DataIn/DataOut/ControlIn/ControlOut,均为纯声明 dataclass) | 无任何运行时状态字段 |
| 图拓扑关系 | `model/types.py` Wire(types.py:185-198)+ `CompiledGraph` 边索引(runtime.py:57-82) | 扇入/扇出索引 |
| 运行时状态 | `World` 映射表(runtime.py:176-186)+ `NodeImpl._buffers/_fresh`(protocol.py:87-89)+ `NodeState`(runtime.py:117-129) | 信号电平、控制电平、输出信号、缓冲、熔断器 |

**系统先例:Mark / NodeTurn / NodeState 三分**(runtime.py:102-129)——注释原文:

> "Mark = 为什么访问这个节点;NodeTurn = 本轮已经做过什么;节点状态 = 跨轮保存什么。三个职责分开"

### 判定

边界 3 无混合。审查框架 §7.2 的担忧(端口保存 enabled/pending 等)不成立——运行时状态全部外置,端口声明保持静态。**这也是"拆语义职责 ≠ 拆类"原则的正面实例**:没有把状态字段移进端口对象,而是全部放 World 表与基类缓冲。

## 5. 边界 4:Data vs Buffer —— ⚠️ 存储策略硬编码

**审查问题**:Data Port 是否同时承担"数据接口"与"数据存储策略"。

### 现状机制

- **存储策略内建于节点基类,不可选**:一格最新覆盖 + 新鲜标记 + 触发后消费清空(protocol.py:86-130 `receive` / `consume_inputs` / `clear_input`)——即唯一策略 "Latest + 瞬态消费";
- **数据生命周期(瞬态 vs 持久)由绑定种类表达**:连线输入 = 瞬态事件(触发后拿走,protocol.py:117-121),常量/全局读取绑定 = 持久输入(`is_bound()`,types.py:99-101,"不参与触发、不消费")。这已是一种策略分化,但**不可按端口声明扩展**;
- **队列类策略由节点状态模拟**:Buffer 节点用 `state.items` 累积(engine/builtins/buffer.py:48-54)、Timer 用 `state.pending` 装填(engine/builtins/timer.py:arm 组)——存储策略被实现为**节点逻辑**,不是端口属性。对话提案的 `Latest / Queue / Buffer / Accumulator / Custom` 策略分层在实现中不存在。

### 判定与建议

- "数据是什么"与"数据怎么保存"在**语义文档**层面已分开(graph-ports-bindings.md §2 "瞬态与持久"),在**实现**层面由"绑定 vs 连线"承载——这是**有意识的压缩**(没有为策略引入机制);
- 建议:当前形态对现有节点够用;若脚本系统 / 节点 ABI 落地后出现"同一端口多存储策略"需求,应把存储策略提升为输入端口声明字段或缓冲策略对象,而不是继续用节点状态模拟。

## 6. 边界 5:Node vs Scheduler —— ✅ 分离良好,缺口在显式请求 API

**审查问题**:Node 是否自行决定 sleep / run_other_node / wait_until(第二套调度系统)。

### 现状机制

- **执行模型本身就是"节点请求、引擎调度"**:节点 tick 是同步纯函数调用(protocol.py:136-139),产出经引擎投递、引擎入队(runtime.py:693-721)——**协议层没有任何"节点调用其他节点"的通道**;设计哲学 §6 "动态性来自节点,而不是全局调度器"在实现中成立;
- 节点对引擎的**请求通道**共两种:
  1. **产出请求**:数据 / 控制输出 → 引擎投递(runtime.py:693-721)——唯一的传播请求;
  2. **周期请求**:`schedule(ctx)` 返回发射周期(protocol.py:141-147,runtime.py:404-426 `_source_due` / `_reschedule`)——源节点自定节奏,引擎只负责按时唤醒;
- 执行安排权在引擎:`_turns` 预算(NodeTurn,runtime.py:102-115)——每组每轮至多一次、信号重算至多两次、熔断冷却每轮一次——"节点请求、引擎决定次数"。

### 缺口(非混合,提案方向)

- 对话提案的 `context.emit_trigger(node_b)` / `request continuation` 显式 API 不存在——当前"请求"只能通过产出隐式表达,**没有"请求唤醒指定下游"或"请求自身延续"的独立动词**;
- LLM 长任务的现状解法(runtime 不回注时见边界 7):tick 写 pending 凭证不产出 → 宿主经 `run([Event(node, "_result", ...)])` 回注(llm_node.py:44-81)——**请求延续 = 节点让出 + 宿主回注**,没有节点侧的 "suspend / request resume"。

### 判定

边界 5 无调度混合,节点逻辑与执行安排分离良好;提案的显式触发请求 API 属于**新增能力**(引擎级扩展),不是修复现有混合。

## 7. 边界 6:State vs Event —— ✅ 三层已分离,结构深度待补

**审查问题**:Runtime 是否只保存最终状态、丢失事件传播信息;或把事件全变成状态污染节点模型。

### 现状机制

**三层已显式分离**(runtime.py:187-191,注释原文):

| 层 | 位置 | 回答 |
|----|------|------|
| log(事件日志) | runtime.py:187,672 | "程序当时打印了什么"——只追加、可截断 |
| **trace(因果时间线)** | runtime.py:190,448-450,636-638 | "世界为什么变成这个状态"——`run+seq` 确定性因果传播序号,**独立于 log,不进快照** |
| 快照(世界状态) | snapshot.py | "世界是什么状态"——图资产版本 + 状态表 + 缓冲 + 电平 + RNG |

trace 条目形态(runtime.py:448-450):

```python
{"run": run_no, "seq": seq, "kind": mark.kind, "dst": nid, "port": port,
 "src": src, "src_port": src_port, "src_slot": src_slot}   # 访问/唤醒事件
{"run": ..., "seq": ..., "kind": "fire", "dst": nid, "group": g, ...}   # 执行事件
```

**这已经回答"为什么访问"**:Mark 的 src 字段(上游节点/端口/槽)承载因果来源(runtime.py:95-99),run+seq 是确定性时间线——graph-trigger-semantics.md §5.3 提案的因果链在**访问粒度**上已实现。

### 差距(与提案 §5.2 / §5.3)

- **无执行生命周期事件**:没有 node.enter / node.execute / node.exit / trigger.completed——fire 条目只有 group 名,**无执行结果、无输入快照、无 TriggerContext**;
- **无因果对象关联**:trace 是扁平序列,靠 run+seq 推断因果,没有"Trigger #1025 caused by #1024"的结构化对象;
- **无消费接口**:trace 只追加、可截断(runtime.py:190),但没有订阅 API(编辑器无法实时订阅),截断策略由宿主自行处理;
- **快照不含 trace**(设计如此)——但编辑事务 `World.edit` 是否保持 trace 一致性未审计(edit.py 未纳入本次范围)。

### 判定

State vs Event 的**分离本身已达标**(甚至早于触发语义讨论),差距在 trace 的**结构深度与消费接口**,属于图 §5.3 的增量增强。

## 8. 边界 7:Execution vs Completion —— 🔶 有意识的设计选择

**审查问题**:Trigger 到达是否隐含完成;Activation / Execution / Completion / Suspend / Resume 是否显式建模。

### 现状机制

- **引擎层没有显式生命周期**:`_fire`(runtime.py:632-721)一次调用完成 激活 → 执行 → 消费 → 投递 全过程——tick 是**同步阻塞调用**,引擎不区分"开始"与"完成";
- **长任务 = 节点让出 + 宿主回注**(llm_node.py:44-81):"call" 组触发 → tick 写 pending 凭证不产出(让出)→ 宿主异步调模型 → 注入 `_result` 事件 → "complete" 组(可选参数 + 触发端口,`DataIn("_result", optional=True, trigger=True)`)触发 → 产出 response——**执行过程的中间态在节点状态字段(pending)里,引擎不可见**;
- llm_node.py:66 注释原文:**"生命周期策略是节点包业务"**——这是一个明确的架构决定:引擎保持无知,长任务语义由节点实现;
- 引擎级的"挂起/恢复"是**投递闸门**(pause/resume,runtime.py:319-366:节点照常运行、输出挂起、恢复冲刷),不是执行挂起——与对话提案的 Suspend/Resume(执行生命周期)不同层。

### 判定

- 边界 7 不是"未建模的混合",而是**有意识的反向决策**:对话提案主张显式建模执行生命周期(Activation / Execution / Completion / Suspend / Resume),现状选择"引擎无知 + 节点包业务";
- 代价:**长任务对引擎不透明**——暂停/恢复语义、超时、取消都无法由引擎统一管理(依赖宿主桥的纪律),trace 中两次触发(call / complete)看起来是独立执行;
- 建议:若引入脚本节点 ABI(对话 §6),执行生命周期必须显式化——脚本节点无法像 LlmCall 一样靠宿主桥回注,`request continuation` 会成为硬需求;届时应重审此决策。

## 9. 与提案的差距总览

| graph-trigger-semantics.md 提案 | 现状 | 差距等级 |
|--------------------------------|------|---------|
| 独立 Trigger Port(函数调用级) | **已实现(1.0.0-0)**:TriggerIn 端口,数据线/信号线均合法,旧标记移除、旧资产拒绝 | 已落地 |
| 节点触发策略(可编程) | **已实现(1.0.0-0)**:组级 `InputGroup.policy` 四策略;策略可编程(脚本挂载)待脚本系统 | 部分落地 |
| Trigger 一等语义 + Context | trace 已有访问粒度因果时间线;无 Trigger 对象 / Context / 生命周期事件 | 增量增强 |
| 脚本节点 Node API(emit_trigger 等) | 无脚本系统;节点请求通道 = 产出 + schedule | 新能力 |
| 语义职责审计(本文档) | 边界 2 / 3 / 5 / 6 达标;1 / 4 部分压缩;7 是有意识的反向决策 | 已完成 |

## 10. 结论

1. **框架验证**:审查框架的担忧并非普遍成立——七个边界中 2 / 3 / 5 / 6 在现有实现中已分离良好,其中 Mark / NodeTurn / NodeState 三分(runtime.py:102-129)与 log / trace / 快照三层(runtime.py:187-191)是**先于讨论就存在的正确先例**;
2. **真正需要修的是边界 1**:触发语义(函数调用级)仍以端口属性 + 引擎硬编码形式存在——`trigger=True` 标记、fresh 判定、Signal→Trigger 被拒,三处共同构成对话讨论的"压缩语义",是触发模型修正的实际落点;
3. **边界 4 与 7 是设计权衡而非缺陷**:存储策略硬编码与"引擎无知"决策目前自洽,但**在脚本系统 / 节点 ABI 落地前必须重审**(边界 7 会成为硬约束);
4. 建议实施顺序:边界 1(触发端口 + 触发策略)→ 边界 7 重审(执行生命周期,配合脚本 ABI)→ 边界 6 增量(trace 结构化)→ 边界 4 按需(存储策略声明)。
