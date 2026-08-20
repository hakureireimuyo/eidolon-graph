# 图运行时内核:工程组织

> 本文档记录 eidolon-graph 仓库的工程组织决策:**编辑器与运行时共享同一个内核,而不是共享纯数据容器。**
> 相关文档:[图运行时总纲](./graph-runtime-overview.md) · [执行模型](./graph-execution-model.md) · [持久化与编辑](./graph-persistence-and-editing.md)

## 1. 核心命题

> **图资产不是纯数据:编辑它需要执行语义,验证它需要运行语义。因此生产方(图编辑服务)与消费方(eidolon-runtime)共同依赖本内核——编辑器内嵌引擎(Unity / Unreal 同构),而不是各基于一份 schema 实现。**编辑服务已 pin 本内核;eidolon-runtime 按演化路线接入(见 §6)。

对比既有模式:

```
角色资产(纯数据):
    asset-types/eidolon-character(schema)
        ├── eidolon-studio(静态校验即可编辑)
        └── eidolon-character-service(解释器)

图资产(可运行规则):
    eidolon-graph(内核:模型 + 引擎)
        ├── eidolon-graph-editor(编辑服务:headless 预览 + 编辑事务,由 eidolon-studio 调用)
        └── eidolon-runtime(消费:注册领域节点 + 组合)
```

## 2. 为什么不能只共享数据容器

- 校验是语义性的:绑定存在性、交叉连线、类型兼容、初始化输入、屏蔽语义——静态 schema 表达不了;
- 编辑事务需要**执行**状态迁移:改连线→缓冲重置、换实现→迁移函数(见 [持久化与编辑](./graph-persistence-and-editing.md) §4.3),迁移规则是内核语义,不是编辑器 UI 逻辑;
- 预览需要**真正运行**图(headless run),且必须是确定性可复现的(事件驱动因果序 + 每节点独立 RNG)→ 编辑器天然是调试器;
- 运行时加载时会"再校验一遍(防手工改资产绕过编辑器)"——共享内核意味着**同一个校验器**,编辑器与运行时不会语义漂移。

## 3. 内核纯度:节点由宿主注册

内核核心(`model/` + `engine/`)**零第三方依赖**,不含 LLM / 网络 / UI:

- 内核核心只实现:图模型、执行引擎、内置逻辑节点、校验器、编辑事务、快照;
- 内置节点白名单(17 个):Clock / Counter / Threshold / Comparator / AND / OR / NOT / Switch / Latch / Timer / Buffer / MultiGate / Random / Simulate / Join / Output / Input——领域节点一律不进内核核心(1.1 合并吸收:Pulse→Clock.sig、Delay→Timer 触发面、Printer→Output.echo);自定义可编程节点走 Script(1.2,宿主注册,见 [graph-script-node.md](./graph-script-node.md));
- 信号节点方向(2026-08-19 收敛,见 [端口语义抽象收敛](./graph-port-capability-composition.md) §3.6):数据 → 信号转换节点(DataToSignal / CompareToSignal / ThresholdToSignal / PredicateToSignal / Script→Signal)作为**普通信号节点类型资产**提供——控制逻辑全部显式存在于图中,内核零特殊处理;
- 节点实现由宿主注册:编辑器注入 stub 做预览,eidolon-runtime 注册 LLM 节点 / Context Compiler 节点 / 工具节点等真实实现;
- 预览不需要特殊"dry-run 模式"——宿主决定注册什么实现,节点协议是唯一边界(见 [节点类型](./graph-node-types.md) §7)。

内核仓内的**节点封装层**(`nodes/`,官方节点包)可引用独立能力库——能力库零图概念、
零协议依赖,封装层只做协议包装(模型调用逻辑全在能力库,见 [LLM 节点封装层](./llm-nodes.md))。
封装层与内核核心同仓,是因为它直接绑定节点协议,随内核版本同步演化。

## 4. 仓库内部三层

```
eidolon_graph/
├── model/   ← 图模型与资产格式:Graph / Node / Port / Asset 定义、(反)序列化、静态校验、内核版本标记
├── engine/  ← 执行引擎:事件驱动调度(注入 → 因果传播 → 静止)、快照/持久化、RNG、编辑事务与状态迁移;节点协议(protocol.py)
└── nodes/   ← 节点封装层(官方节点包,如 llm):引用独立能力库,只做协议包装
```

- **编辑事务 API 属于内核**:`edit_transaction(graph, edits) → {validation, migration_plan}`,编辑服务只是它的 UI;
- **图资产记录写入时的内核版本**,编辑/加载时比对(与 [持久化与编辑](./graph-persistence-and-editing.md) §3"快照 = 图资产版本引用"一致),不同内核版本间给出兼容性判断;
- **资产格式先于编辑器存在**:本仓库 model 层就是图资产格式的唯一来源。

## 5. 依赖方式

沿用既有仓库约定:**git 源 + pin rev**——宿主各自 pin 本内核,monorepo 与独立 clone 一致,无路径耦合。

```
eidolon-graph(内核核心零依赖;nodes/ 引用能力库)
    ├── eidolon-runtime(按演化路线接入:阶段一至三统一为"建图")→ 注册 LLM / Context Compiler / 工具等节点
    └── eidolon-graph-editor(已 pin,editor 侧,由 eidolon-studio 调用)→ headless 预览 + 编辑事务
```

## 6. 阶段零:最小验证闭环

内核的第一个交付物是最小验证闭环,**六个验收性质落地为 `tests/test_stage_zero.py`**:

```
Clock → Counter → Condition → Output → Feedback(回连)
```

1. 同一图、同一输入序列,执行结果确定可复现(数据流因果序 + 每节点独立随机流);
2. 反馈环跨 epoch 迭代、不递归展开(脏节点传播,每组每 epoch 至多一次);
3. 节点状态、输入缓冲、信号电平、RNG 保存后精确恢复(读档续跑);
4. 修改图资产后,已有世界状态按迁移规则继续运行(规则与事实分离);
5. LLM 节点被普通程序节点替换后,上层图零修改(节点协议是唯一边界);
6. 子图封装成节点后,上层 Runtime 不知道其内部结构。

六个性质已全部通过(落地为 `tests/test_stage_zero.py`);编辑服务(eidolon-graph-editor)已 pin 本内核。与 eidolon-runtime 的演化路线关系:阶段零在本仓内完成;后续阶段一至三(State/Event 骨架、多处理器协同、反向控制流)在 eidolon-runtime 内统一为"建图"(见 eidolon-runtime 仓库 `docs/evolution-roadmap.md`);领域能力拆分(人格/世界/记忆)仍按该路线图的阶段四执行。
