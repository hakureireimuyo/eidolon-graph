# eidolon-graph

Eidolon **图运行时内核**。稳定核心:Node / Port / Signal / State / Graph / Tick / Asset / Snapshot——图编辑服务(editor 侧,由 eidolon-studio 调用)与 eidolon-runtime **共同依赖本内核**,而不是各自实现一遍图语义(编辑器内嵌引擎,Unity / Unreal 同构)。

## 结构

```
eidolon_graph/
├── model/   ← 图模型与资产格式:Graph / Node / Port / Asset 定义、(反)序列化、静态校验、内核版本标记
└── engine/  ← 执行引擎:同步轮次、调度、快照/持久化、RNG、编辑事务与状态迁移
```

- `docs/` — 内核设计文档:总纲 / 执行模型 / 端口绑定 / 节点类型 / 资产 / 持久化与编辑 / 工程组织
- `tests/test_stage_zero.py` — 阶段零最小验证闭环的六个验收性质(实现前立为清单)

## 原则

- **内核零依赖、零领域逻辑**:不依赖 LLM / 网络 / UI;节点由宿主注册(编辑器注入 stub、eidolon-runtime 注册真实实现),内核只认节点协议;
- **编辑器内嵌引擎**:编辑预览 = headless 运行同一个内核,校验器只有一份,编辑器与运行时不会语义漂移;
- **资产格式先于编辑器存在**:本仓库 model 层是图资产格式的唯一来源。

落地顺序见 `docs/graph-kernel-engineering.md`(阶段零最小验证闭环)。
