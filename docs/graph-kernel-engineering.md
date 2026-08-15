# 图运行时内核:工程组织

> 本文档记录 eidolon-graph 仓库的工程组织决策:**编辑器与运行时共享同一个内核,而不是共享纯数据容器。**
> 相关文档:[图运行时总纲](./graph-runtime-overview.md) · [执行模型](./graph-execution-model.md) · [持久化与编辑](./graph-persistence-and-editing.md)

## 1. 核心命题

> **图资产不是纯数据:编辑它需要执行语义,验证它需要运行语义。因此生产方(图编辑服务)与消费方(eidolon-runtime)共同依赖本内核——编辑器内嵌引擎(Unity / Unreal 同构),而不是各基于一份 schema 实现。**

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

- 校验是语义性的:绑定存在性、交叉连线、类型兼容、就绪(warm-up)、屏蔽语义——静态 schema 表达不了;
- 编辑事务需要**执行**状态迁移:改连线→就绪重置、换实现→迁移函数(见 [持久化与编辑](./graph-persistence-and-editing.md) §4.3),迁移规则是内核语义,不是编辑器 UI 逻辑;
- 预览需要**真正运行**图(headless tick),且必须是确定性可复现的(同步轮次 + RNG seed)→ 编辑器天然是调试器;
- 运行时加载时会"再校验一遍(防手工改资产绕过编辑器)"——共享内核意味着**同一个校验器**,编辑器与运行时不会语义漂移。

## 3. 内核纯度:节点由宿主注册

内核**零第三方依赖**,不含 LLM / 网络 / UI:

- 内核只实现:图模型、执行引擎、内置逻辑节点、校验器、编辑事务、快照;
- 内置节点白名单:Clock / Counter / Comparator / AND / OR / NOT / Switch / Latch / Timer / Threshold 等逻辑元件——领域节点一律不进内核;
- 节点实现由宿主注册:编辑器注入 stub 做预览,eidolon-runtime 注册 LLM 节点 / Context Compiler 节点 / 工具节点等真实实现;
- 预览不需要特殊"dry-run 模式"——宿主决定注册什么实现,节点协议是唯一边界(见 [节点类型](./graph-node-types.md) §7)。

## 4. 仓库内部两层

```
eidolon_graph/
├── model/   ← 图模型与资产格式:Graph / Node / Port / Asset 定义、(反)序列化、静态校验、内核版本标记
└── engine/  ← 执行引擎:同步轮次、调度、快照/持久化、RNG、编辑事务与状态迁移
```

- **编辑事务 API 属于内核**:`edit_transaction(graph, edits) → {validation, migration_plan}`,编辑服务只是它的 UI;
- **图资产记录写入时的内核版本**,编辑/加载时比对(与 [持久化与编辑](./graph-persistence-and-editing.md) §3"快照 = 图资产版本引用"一致),不同内核版本间给出兼容性判断;
- **资产格式先于编辑器存在**:本仓库 model 层就是图资产格式的唯一来源。

## 5. 依赖方式

沿用既有仓库约定:**git 源 + pin rev**——eidolon-runtime 与图编辑服务各自 pin 本内核,monorepo 与独立 clone 一致,无路径耦合。

```
eidolon-graph(零依赖)
    ├── eidolon-runtime ────────────→ 注册 LLM / Context Compiler / 工具等节点
    └── eidolon-graph-editor(editor 侧,由 eidolon-studio 调用)→ headless 预览 + 编辑事务
```

## 6. 阶段零:最小验证闭环

内核的第一个交付物是最小验证闭环,**六个验收性质落地为 `tests/test_stage_zero.py`**:

```
Clock → Counter → Condition → Printer → Feedback(回连)
```

1. 节点执行顺序改变,结果完全一致(同步轮次的确定性);
2. 反馈环严格产生 tick 延迟,不递归执行;
3. 节点状态、端口 held 值、RNG 保存后精确恢复(读档续跑);
4. 修改图资产后,已有世界状态按迁移规则继续运行(规则与事实分离);
5. LLM 节点被普通程序节点替换后,上层图零修改(节点协议是唯一边界);
6. 子图封装成节点后,上层 Runtime 不知道其内部结构。

全部通过后,内核方可被 eidolon-runtime 与编辑服务 pin 依赖。与 eidolon-runtime 的演化路线关系:阶段零在本仓内完成;后续阶段一至三(State/Event 骨架、多处理器协同、反向控制流)在 eidolon-runtime 内统一为"建图"(见 eidolon-runtime 仓库 `docs/evolution-roadmap.md`);领域能力拆分(人格/世界/记忆)仍按该路线图的阶段四执行。
