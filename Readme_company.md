# 基于 Langflow 的 Agent 自动构建工作流功能（SOP）

---

## 1. 文档信息

| 项目     | 内容 |
|----------|------|
| 文档名称 | 基于 Langflow 引入 Agent 自动构建工作流 — 开发说明与传承文档 |
| 文档类型 | 技术 SOP（Standard Operating Procedure） |
| 适用系统 | Langflow 工作流平台（含前端与后端） |
| 主要功能 | Copilot Agent 根据用户自然语言需求自动生成并保存 Langflow 工作流 JSON |

---

## 2. 目的与适用范围

### 2.1 目的

- 规范基于 Langflow 的 Agent 自动构建工作流」功能的实现方式与维护方式。
- 概述自定义组件功能，包含通用型编辑组件、Agent构建RAG系统时的交互式选择功能等

### 2.2 适用范围

- 后端：`agent_workflow` 模块、Copilot API、`endpoints` 中与节点选择/保存相关的接口、自定义组件包 `aaa_guoyansong`、以及 `starter_projects` 参考。
- 前端：Copilot 聊天界面、与 Copilot 相关的 API 调用、首页/Header 中 Copilot 入口、以及流式数据展示组件与状态管理（如 flowStore）中与工作流编辑相关的部分。

---

## 3. 术语与定义

| 术语 | 定义 |
|------|------|
| Copilot Agent | 根据用户自然语言描述，调用大模型生成符合 Langflow 规范的 workflow JSON 的智能体。 |
| workflow / 工作流 | Langflow 中由 nodes（节点）与 edges（边）组成的图结构，对应前端画布与后端 `Flow.data`。 |
| nodes_auto.json | 供 Agent 使用的「完整节点模板」集合，用于补全 LLM 生成节点中缺失字段。 |
| nodes_auto_inject.json | 注入到 LLM 系统提示词中的「简化节点定义」，仅包含需编辑的 template/outputs 等关键字段。 |
| system_prompt2.md | Copilot 系统提示词模板，内含 `{{available_nodes}}` 占位符，运行时被 nodes_auto_inject 内容替换。 |
| flowStore | 前端 Zustand 状态库，管理当前流程的 nodes、edges 等图数据（与流程编辑直接相关）。 |
| node_selector | 后端「节点注入规则」配置：dict，key 为组件分类（category），value 为 `"all"` 或节点名列表；决定哪些组件写入 nodes_auto.json / nodes_auto_inject.json 并参与 Copilot 生成。 |
| category / 分类 | Langflow 组件在 all_types 中的一级分组名（如 input_output、models_and_agents、aaa_guoyansong），与左侧组件面板分类一致。 |

---

## 4. 系统架构概览

```
用户（前端）
    ↓ 自然语言描述
CopilotChat.tsx → useCopilotChat → POST /api/v1/copilot/chat
    ↓
API: copilot.py (router) → agent_workflow/copilot.py → generate_workflow_with_llm()
    ↓ 加载 system_prompt（含注入节点）、调用 LLM、校验/规范化/补全 JSON
返回 workflow_data
    ↓
前端：若存在 workflow_data → useGenerateWorkflow → POST /api/v1/copilot/generate-workflow
    ↓
API: 校验 → format_workflow_json → 写入 Flow 表 → 返回 flow id
    ↓
前端：刷新文件夹列表、跳转 /flow/{id}
```

节点能力来源：`/api/v1/all` 拉取 all_types → `save_selected_nodes` 按 node_selector 筛选并生成 `nodes_auto.json` 与 `nodes_auto_inject.json`，供 Agent 加载与注入 system prompt。

---

## 5. 后端实现说明

### 5.1 agent_workflow 模块（`src/backend/base/langflow/agent_workflow/`）

#### 5.1.1 目录与文件清单

| 文件/目录 | 说明 |
|-----------|------|
| `__init__.py` | 包初始化（可为空）。 |
| `copilot.py` | Copilot 核心逻辑：加载提示词、调用 LLM、校验/规范化/补全/布局工作流 JSON。 |
| `Files/system_prompt2.md` | 系统提示词模板，含角色、规则、组件模板占位符 `{{available_nodes}}`、Edges 规则及示例。 |
| `Files/nodes_auto_inject.json` | 注入到 system prompt 的简化节点列表（由 `save_selected_nodes_to_json` 生成）。 |
| `Files/nodes_auto.json` | 完整节点模板，用于 `enrich_workflow_nodes` 补全 LLM 输出中节点的缺失字段。 |
| `Files/all_types.json` | 可选；由 `/api/v1/all` 请求时写入的全局组件类型快照，供调试或离线使用。 |

#### 5.1.2 copilot.py 核心函数说明

- **load_system_prompt_auto_inject()**
  - 读取 `Files/nodes_auto_inject.json`（由 `save_selected_nodes_to_json` 生成的简化节点列表），将每个节点格式化为「## 序号. 节点类型 节点模板」的 Markdown 块，块内为 JSON 代码块。
  - 读取 `Files/system_prompt2.md`，将占位符 `{{available_nodes}}` 替换为上述节点块拼接结果。
  - 返回最终注入给 LLM 的系统提示词；LLM 仅能「看到」并生成这些节点类型，且需按提示中的 template/outputs 结构填写。

- **generate_node_id(prefix)**
  - 使用 `secrets.token_hex(3)` 生成 6 位十六进制后缀，格式：`{prefix}-{suffix}`，保证同一工作流内节点 ID 唯一。

- **validate_workflow_json(workflow_data)**
  - 若顶层有 `data` 则先取 `workflow_data = workflow_data["data"]`。
  - 校验存在 `nodes`、`edges` 且为 list；nodes 非空；每个 node 为 dict 且含 `id`、`data`，`data.node` 含 `template`；node id 不重复；每条 edge 含 `source`、`target` 且均在 node id 集合中。
  - 返回 `(True, None)` 或 `(False, error_message)`；校验失败时由调用方将 error_message 反馈给 LLM 重试。

- **_parse_handle_value / _handle_to_langflow_str**
  - Langflow 前端 edge 的 sourceHandle/targetHandle 有时为 JSON 字符串，且可能用 `œ` 替代双引号。
  - `_parse_handle_value`：若为 dict 直接返回，若为 str 则先 `replace("œ", '"')` 再 `json.loads`，解析失败返回 `{}`。
  - `_handle_to_langflow_str`：将 handle 对象 `json.dumps` 后再将 `"` 替换为 `œ`，与前端/持久化格式一致。

- **normalize_workflow_edges(workflow_data)**
  - 以每条 edge 的 `data.sourceHandle` / `data.targetHandle` 为准（缺失时从顶层 sourceHandle/targetHandle 解析）。
  - `source`、`target` 仅从 `data.sourceHandle.id`、`data.targetHandle.id` 取值，并校验二者均存在于 `workflow_data["data"]["nodes"]` 的 id 集合中；非法边会被跳过。
  - 补全 sourceHandle 的 dataType（由源节点 id 前缀推断）、name、output_types；补全 targetHandle 的 fieldName、type（可从目标节点 template 推断）、inputTypes。
  - 生成标准 edge：id（含 œ 编码的 handle 字符串）、source、target、sourceHandle/targetHandle 字符串、data、selected、animated、className。

- **format_workflow_json(workflow_data)**
  - 若无 viewport 则补 `{x:0,y:0,zoom:1}`。
  - 对每个 node：补 type、width、height、measured、dragging、selected；按网格布局（列数约 sqrt(n)）计算 position/positionAbsolute，并加 MD5(node_id) 抖动避免完全重叠；缺 id 时用 `generate_node_id` 生成。
  - 对 edges 补 selected、animated、data（若缺失）。

- **invoke_with_continuation(llm, messages, max_rounds)**
  - 调用 `llm.ainvoke(messages)`；若 `response.response_metadata.finish_reason == "length"` 表示被截断，则追加一条「请从截断处继续、不要重复」的 HumanMessage，将已生成内容也放入，再次调用；最多重复 `max_rounds` 次，拼接全部 content 返回。

- **generate_workflow_with_llm(user_message, conversation_history, max_iterations)**
  - 加载系统提示（`load_system_prompt_auto_inject`）；用环境变量初始化 ChatOpenAI（支持 max_completion_tokens=32768、base_url 等）；构建 messages：SystemMessage(system_prompt) + 历史消息 + HumanMessage(user_message)，若上一轮有错误则追加 HumanMessage(错误说明)。
  - 每轮：调用 `invoke_with_continuation` 取回复 → 用正则从回复中提取 JSON（优先 ```json ... ``` 块）→ `json.loads` → `normalize_workflow_edges` → `enrich_workflow_nodes` → `validate_workflow_json` → `format_workflow_json`；任一步失败则记录 last_error，下一轮将错误加入 messages 重试。
  - 成功则可选写入 `test2.json` 调试，并返回 workflow_data；若 max_iterations 轮仍失败则抛出 ValueError，包含 last_error 与 last_response_text 便于排查。

- **enrich_workflow_nodes(workflow_data)**
  - 从 `Files/nodes_auto.json` 读取 `nodes` 列表，构建「节点类型（id 前缀）→ 完整模板节点」的映射。
  - 遍历 `workflow_data["data"]["nodes"]`，按 node["id"] 的前缀（第一个 `-` 前）找到对应模板，用 `deep_merge_preserve_existing(llm_node, template_node)` 合并：模板为底，LLM 已有字段优先，递归处理嵌套 dict。
  - 保证 LLM 只输出部分字段时，节点仍具备 Langflow 运行所需的完整 template/outputs 等结构。

- **normalize_workflow_nodes(workflow_data, nodes_json_path)**
  - 与 enrich 逻辑类似，但使用指定路径的 nodes.json 做模板；当前主流程统一使用 `enrich_workflow_nodes` 与固定路径 `nodes_auto.json`。

#### 5.1.3 配置文件与环境变量

- LLM 调用依赖环境变量：`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`（未配置时可能回退到 OpenAI 默认配置）。
- 节点模板路径写死为 `src/backend/base/langflow/agent_workflow/Files/` 下 `nodes_auto.json`、`nodes_auto_inject.json`、`system_prompt2.md`。

---

### 5.2 Copilot API（`src/backend/base/langflow/api/v1/copilot.py`）

- **路由前缀**：`/copilot`，标签 `Copilot`；挂载在 v1 下后完整路径为 `/api/v1/copilot/...`。
- **依赖**：从 `langflow.agent_workflow.copilot` 引入 `format_workflow_json`、`generate_workflow_with_llm`、`validate_workflow_json`；使用 `CurrentActiveUser`（登录态）、`DbSession`（仅 generate-workflow 需要）。

#### 5.2.1 请求/响应模型

- **CopilotMessage**：`role`（"user" | "assistant"）、`content`（字符串）；用于多轮对话历史。
- **CopilotChatRequest**：`message`（必填，当前轮用户输入）、`conversation_history`（可选，此前轮次的 CopilotMessage 列表）。
- **CopilotChatResponse**：`message`（助手回复或错误说明）、`workflow_data`（可选，成功生成时含完整 workflow 的 dict，含 data.nodes/data.edges 等）、`flow_id`（可选，当前 chat 接口不写库，故通常为 None；保存由前端调 generate-workflow 完成）。
- **CopilotGenerateWorkflowRequest**：`workflow_data`（必填，来自 chat 返回）、`flow_name`（必填）、`description`（可选）。

#### 5.2.2 接口说明

1. **POST /copilot/chat**
   - 将请求体中的 `conversation_history` 转为 `[{"role": msg.role, "content": msg.content}, ...]` 传入 `generate_workflow_with_llm`。
   - **成功**：返回 200，body 为 `CopilotChatResponse(message="I've generated a workflow...", workflow_data=<完整 workflow dict>)`。
   - **ValueError**（如 LLM 多次重试仍无法产出合法 JSON）：捕获后仍返回 **200**，body 为 `CopilotChatResponse(message="I encountered an error: ...", workflow_data=None)`，便于前端统一按 200 处理并展示错误文案，无需区分 4xx/5xx。
   - **其他异常**：返回 500，detail 为异常信息；前端可据此弹出错误提示。

2. **POST /copilot/generate-workflow**
   - 先对 `request.workflow_data` 做 `validate_workflow_json`；若 `is_valid` 为 False，返回 **400**，detail 为 error_message。
   - 通过后执行 `format_workflow_json`；查询当前用户下名为默认文件夹（DEFAULT_FOLDER_NAME）的 Folder，若无则返回 **404**。
   - 使用 `FlowCreate(..., data=workflow_data["data"], ...)` 创建 Flow 并 commit；返回 201，body 为 `{ "id", "name", "data" }`（id 为新建 flow 的 UUID，前端用于跳转 `/flow/{id}`）。
   - 其他异常（如 DB 错误）返回 500。

---

### 5.3 endpoints.py 中与 Copilot/Agent 相关的部分（`src/backend/base/langflow/api/v1/endpoints.py`）

#### 5.3.1 GET /all

- 调用 `get_and_cache_all_types_dict(settings_service=...)` 获取全量组件类型（含所有已注册分类及分类下的节点定义）。
- 将结果序列化后写入 `src/backend/base/langflow/agent_workflow/Files/all_types.json`（便于调试或离线查看当前有哪些 category/节点）；写入失败仅打日志，不影响接口返回。
- 返回压缩后的 all_types 给前端（前端用于组件面板展示等）；**节点注入**不直接读此接口，而是读「经 save_selected_nodes 筛选后」生成的 nodes_auto / nodes_auto_inject 文件。

#### 5.3.2 节点注入规则（node_selector）详细说明

**作用**：决定「哪些组件」会被纳入 Copilot 自动构建工作流的能力集。只有被选中的节点才会写入 `nodes_auto.json` 与 `nodes_auto_inject.json`，进而被 LLM 在 system prompt 中看到并用于生成工作流。

**数据来源**：`all_types` 由 `get_and_cache_all_types_dict()` 提供，其顶层 key 为**组件分类（category）**，与 Langflow 左侧组件面板的分类一致，例如 `input_output`、`models_and_agents`、`aaa_guoyansong`、`chroma` 等；每个 category 下为「组件名 → 节点定义」的字典。

**node_selector 结构**：

```python
node_selector: dict[str, list[str] | str] = {
    "category_name": ["NodeA", "NodeB"],   # 仅注入该分类下的指定节点
    "another_category": "all",             # 注入该分类下的全部节点
}
```

- **Key**：必须与 `all_types` 中的分类名完全一致，否则该分类会被跳过（并打 warning 日志）。
- **Value 两种取值**：
  - **列表 `["NodeA", "NodeB"]`**：只注入该分类下名为 NodeA、NodeB 的节点；若某个名字在分类中不存在，会打 warning 并跳过该节点。
  - **字符串 `"all"`**：注入该分类下的**所有**节点（直接 `category_nodes.copy()`），适合「整类组件都开放给 Copilot」的场景。

**当前默认配置及含义**（`endpoints.py` 中 `save_selected_nodes` 内）：

| 分类 (category) | 取值 | 含义 |
|-----------------|------|------|
| `input_output` | `["ChatInput", "ChatOutput"]` | 仅注入聊天输入、聊天输出，保证基础对话流可被生成。 |
| `models_and_agents` | `"all"` | 注入该分类下全部节点（如 Prompt Template、LanguageModelComponent、EmbeddingModelComponent、各类 Agent 等），便于生成含模型与 Agent 的流。 |
| `aaa_guoyansong` | `["RagPrompt"]` | 自定义组件包中仅注入 RagPrompt，用于 RAG 类工作流；若需加入 Agent、Edit 等，可改为 `["RagPrompt", "Agent", "EditComponentTextOnly"]` 或 `"all"`。 |
| `chroma` | `"all"` | 注入 Chroma 相关全部组件（如向量存储、检索等），支持 RAG/检索类流。 |
| `files_and_knowledge` | `["File"]` | 仅注入 File 组件，用于文档输入。 |
| `processing` | `["SplitText", "parser"]` | 注入文本分割与解析器，用于预处理流水线。 |
| `openai` | `["OpenAIEmbeddings"]` | 注入 OpenAI 嵌入模型，用于向量化。 |
| `datastax` | `["AstraDB"]` | 注入 AstraDB 相关节点（如向量库等）。 |

**注入后的两条链路**：

1. **nodes_auto.json**：每个被选中的节点保存为**完整定义**（含 template、outputs、display_name 等全部字段），供 `agent_workflow/copilot.py` 中 `enrich_workflow_nodes()` 使用——LLM 生成的节点若缺少字段，会从此文件按「节点类型」做深度合并补全。
2. **nodes_auto_inject.json**：每个被选中的节点经 `extract_editable_fields_from_node()` 提取为**简化版**（仅保留 display_name、description、template 中非 advanced 的可编辑字段、outputs 等），再在 `load_system_prompt_auto_inject()` 中格式化为 Markdown 块，替换 `system_prompt2.md` 中的 `{{available_nodes}}`，供 LLM 阅读并生成符合规范的 nodes/edges。

**修改与扩展建议**：

- 新增「可被 Copilot 编排」的组件：在对应分类下增加组件名（或将该分类改为 `"all"`）；组件名必须与 `all_types[category]` 的 key 一致（一般为组件类的 `name` 或 display_name 的某种映射）。
- 减少 token 或聚焦场景：将 `"all"` 改为具体列表，仅保留需要的节点。
- 新增自定义分类：需保证该分类已在 Langflow 组件注册体系中存在并在 `all_types` 中出现，再在 `node_selector` 中增加对应 key。

#### 5.3.3 节点筛选与保存（供 Copilot 使用）— 函数说明

- **extract_selected_nodes(all_types, node_selector)**
  - 遍历 `node_selector` 的每个 (category, node_names)；若 category 不在 `all_types` 中则跳过并打 warning。
  - 若 `node_names == "all"`，则 `selected_nodes[category] = category_nodes.copy()`；若为 list，则只保留列表中且存在于 `category_nodes` 的节点名。
  - 返回与 `node_selector` 对应的 `all_types` 子集。

- **extract_editable_fields_from_node(node_data)**
  - 从节点定义中提取「可编辑」的 template 字段：排除 `code`、`_type`；若字段为 dict 且 `advanced is True` 则跳过，否则将 value 置为占位说明 `"<Need Edit!> (Edit or connected with other nodes' output)"`，保留 display_name、name、info、type、input_types、_input_type 等。
  - 同时提取 outputs 列表（name、display_name、types、method、selected）。
  - 用于生成注入 system prompt 的简化节点，便于 LLM 知道「哪些字段需要填写或连边」。

- **save_selected_nodes_to_json(all_types, node_selector, save_path, filename, file_inject)**
  - 使用 `extract_selected_nodes` 得到选中节点；
  - 写 `nodes_auto.json`：完整节点结构（id、data、node 等），供 `enrich_workflow_nodes` 使用。
  - 写 `file_inject`（默认 `nodes_auto_inject.json`）：对每个节点调用 `extract_editable_fields_from_node` 得到简化版并写入，供 `load_system_prompt_auto_inject` 注入。
  - 返回 `(output_file_path, node_inject_list)`。

#### 5.3.4 POST /save_selected_nodes

- 需要登录（CurrentActiveUser）。
- 接口内写死上述 `node_selector` 字典（见 5.3.2 表）；如需调整注入范围，直接修改该字典并重启或重新调用接口。
- 调用 `get_and_cache_all_types_dict` 与 `save_selected_nodes_to_json`，将生成文件写回 `agent_workflow/Files/`。
- 返回 message、file_path、nodes_count、categories、node_inject、inject_description 等。

说明：前端 Copilot 弹窗打开时可调用 POST `/api/v1/save_selected_nodes`（无需 body 或仅传 save_path/filename），以按当前后端 `node_selector` 重新生成 `nodes_auto.json` 与 `nodes_auto_inject.json`，保证本次会话使用的节点集与配置一致。

---

### 5.4 自定义组件包 aaa_guoyansong（`src/lfx/src/lfx/components/aaa_guoyansong/`）

本包为 Langflow 自定义组件集合，其中部分组件被纳入 Copilot 的 node_selector（如 RagPrompt），用于自动构建工作流。

| 文件 | 说明 |
|------|------|
| `__init__.py` | 导出 AgentComponent、ChromaVectorStoreComponent、ChromaVectorStoreComponentAgent、EditComponent、HelloComponent、ParserComponent2、RagPromptComponent 等；大模型/Agent 类懒加载。 |
| `agent.py` | AgentComponent：ToolCallingAgentComponent 子类，display_name 为 "Agent(gys)"，配置 LLM、记忆、工具等，供工作流中 Agent 节点使用。 |
| `chroma.py` | ChromaVectorStoreComponent：Chroma 向量存储。 |
| `chroma_agent.py` | ChromaVectorStoreComponentAgent：与 Chroma 结合的 Agent。 |
| `Edit.py` | EditComponent：文本编辑类组件。 |
| `hello.py` | HelloComponent：示例组件。 |
| `parser2.py` | ParserComponent2：解析器组件。 |
| `prompt.py` | RagPromptComponent：RAG 专用 Prompt，模板变量含 context、question，输出供下游 LLM 使用；在 node_selector 中配置为 `aaa_guoyansong: ["RagPrompt"]` 后，Copilot 可自动生成含该节点的工作流。 |

与 Copilot 直接相关：**RagPrompt** 作为可被自动编排的节点类型，需在 `endpoints.py` 的 `save_selected_nodes` 的 `node_selector` 中保留 `"aaa_guoyansong": ["RagPrompt"]`（或按需扩展其他组件名）。

---

### 5.5 starter_projects（`src/backend/base/langflow/initial_setup/starter_projects/`）

该目录存放 Langflow 内置的示例工作流（如 Basic Prompting、Document Q&A、Vector Store RAG 等）的 JSON 或 Python 定义。
与 Copilot 的关系：

- **参考价值**：`system_prompt2.md` 中的《模板示例》可参考这些示例的 nodes/edges 结构，保证 LLM 生成的 JSON 与现有流一致。
- **不直接调用**：Copilot 不在此目录读写文件，仅通过 `nodes_auto.json` / `nodes_auto_inject.json` 与 system prompt 约束生成结果。

若新增一类「标准 RAG 流」或「Agent 流」，可在此增加示例，并在 system_prompt2.md 的示例部分补充对应说明或片段，以提升生成质量。

---

## 6. 前端实现说明

### 6.1 Copilot 聊天组件（`src/frontend/src/components/copilot/CopilotChat.tsx`）

- **作用**：Copilot 对话 UI，用户输入自然语言 → 调用 Copilot 接口生成工作流 → 若返回 workflow_data 则自动保存并跳转到新 flow。
- **Props**：`open: boolean`（是否显示弹窗）、`setOpen: (open: boolean) => void`（由父组件传入，用于关闭弹窗）。
- **状态**：`messages`（对话列表，用于展示与作为 conversation_history 上报）、`inputMessage`（当前输入框内容）、`isGenerating`（是否正在请求，用于禁用发送与显示 loading）。
- **关键逻辑**：
  - **发送消息**：`handleSendMessage` 将当前输入加入 `messages`、清空输入、设 `isGenerating=true`，然后 `chatMutate({ message: inputMessage, conversation_history: [...messages, userMessage] })`；请求路径为 `getURL("COPILOT")/chat`，即 `/api/v1/copilot/chat`。
  - **chat 成功**：`onSuccess` 中先 `setIsGenerating(false)`；若 `data.workflow_data` 存在，则追加一条助手消息（data.message），并立即调用 `handleSaveWorkflow(data.workflow_data)` 走保存流程；若无 workflow_data 则只追加助手消息（例如仅回复说明而无生成）。
  - **handleSaveWorkflow**：构造 `flow_name` 为 `"Copilot Generated Flow " + 当前时间戳字符串`，`description` 为 `"Generated by Copilot Agent"`，调用 `generateWorkflow({ workflow_data, flow_name, description })`；保存成功时 `onSuccess` 内会 `setErrorData({ title: "Success", ... })`、`setOpen(false)`、`navigate(\`/flow/${data.id}\`)`。
  - **弹窗打开时**：`useEffect` 依赖 `open`，当 `open===true` 时执行 `handleSaveSelectedNodes()`。该方法 POST `/api/v1/save_selected_nodes`（当前实现未传 body 或仅传 save_path/filename），触发后端按 `node_selector` 重新生成 `nodes_auto.json` 与 `nodes_auto_inject.json`，保证本次对话使用的「可编排节点集」与后端配置一致；若请求失败仅 `console.error`，不打断用户操作。
  - **欢迎语**：另一 `useEffect` 在 `open && messages.length === 0` 时设置一条 assistant 欢迎消息，内容为介绍 Copilot 能力与示例（如 "Create a chatbot that answers questions about AI"）。
- **错误处理**：`useCopilotChat` 的 `onError` 会 `setIsGenerating(false)` 并 `setErrorData({ title: "Error", list: [error.message] })`；`useGenerateWorkflow` 的 `onError` 同样通过 `setErrorData` 展示保存失败原因。
- **UI**：固定全屏遮罩（bg-black/50）、居中卡片（max-w-4xl、h-80vh）、标题栏「Copilot Agent」+ Close 按钮、可滚动消息区（用户气泡右对齐、助手左对齐）、生成中时显示 "Generating workflow..." 与 Loader、底部 Textarea + 发送按钮（Enter 发送、Shift+Enter 换行）。

---

### 6.2 API 封装（`src/frontend/src/controllers/API/queries/copilot/`）

#### 6.2.1 use-copilot-chat.ts

- **用途**：Copilot 对话请求的 mutation。
- **类型**：`CopilotMessage`、`CopilotChatRequest`（message、conversation_history）、`CopilotChatResponse`（message、workflow_data?、flow_id?）。
- **实现**：通过 `UseRequestProcessor` 的 `mutate` 封装 `api.post(\`${getURL("COPILOT")}/chat\`, payload)`，返回 `UseMutationResult<CopilotChatResponse, any, CopilotChatRequest>`。
- **getURL("COPILOT")**：在 `controllers/API/helpers/constants.ts` 中对应为 `"copilot"`，最终请求为 `/api/v1/copilot/chat`（与后端路由前缀一致）。

#### 6.2.2 use-generate-workflow.ts

- **用途**：将已生成的 workflow_data 保存为 Flow 的 mutation。
- **请求**：`GenerateWorkflowRequest`：workflow_data、flow_name、description?。
- **实现**：`api.post(\`${getURL("COPILOT")}/generate-workflow\`, payload)`；`onSuccess` 中 refetch `useGetFolders`、`useGetFolder`，再调用外部传入的 `options?.onSuccess`（在 CopilotChat 中用于提示成功、关闭弹窗、跳转 `/flow/${data.id}`）。

---

### 6.3 首页与 Header 中的 Copilot 入口

#### 6.3.1 Header（`src/frontend/src/pages/MainPage/components/header/index.tsx`）

- **Props**：包含可选 `setCopilotOpen?: (open: boolean) => void`。
- **表现**：当 `setCopilotOpen` 存在时，渲染「Copilot」按钮（MessageSquare 图标 + 文案 "Copilot"），`onClick` 调用 `setCopilotOpen(true)`。
- **测试标识**：`id="copilot-btn"`、`data-testid="copilot-btn"`。
- **Tooltip**：`ShadTooltip content="Copilot Agent"`。

#### 6.3.2 首页（`src/frontend/src/pages/MainPage/pages/homePage/index.tsx`）

- **状态**：`const [copilotOpen, setCopilotOpen] = useState(false)`。
- **使用**：将 `setCopilotOpen` 传给 Header；在页面底部渲染 `<CopilotChat open={copilotOpen} setOpen={setCopilotOpen} />`。
- 用户点击 Header 的 Copilot 按钮 → `copilotOpen === true` → 显示 CopilotChat 弹窗。

---

### 6.4 selectableDataOutputComponent（`src/frontend/src/components/core/selectableDataOutputComponent/index.tsx`）

- **作用**：在流程运行/编辑场景下，对某节点的表格类输出进行「多选」，并将选中行的 id 写回节点的 `selected_result_ids`（通过 `useHandleOnNewValue`）。
- **与 Copilot 的关系**：不直接调用 Copilot API；属于工作流中「可选数据输出」节点的通用 UI。Copilot 生成的工作流若包含此类节点，在编辑/运行时会用到该组件。
- **依赖**：`useFlowStore` 取 `nodes`，根据 `nodeId` 找到对应 node；`useHandleOnNewValue` 更新 `selected_result_ids`。

---

### 6.5 flowStore（`src/frontend/src/stores/flowStore.ts`）

- **作用**：Zustand 全局状态，管理当前打开的工作流图数据（nodes、edges 等）及画布相关状态。
- **与 Copilot 的关系**：Copilot 不直接读写 flowStore 中的 workflow。流程为：Copilot 获取 `workflow_data` → 调用 `generate-workflow` 保存到服务端 → 前端跳转到 `/flow/{id}`；在 flow 编辑页由加载 flow 的逻辑从服务端拉取 data 并写入 flowStore。因此 flowStore 是「当前编辑的 flow」的单一数据源，Copilot 通过「创建新 flow + 跳转」间接影响其内容。

---

## 7. 数据流与调用顺序（SOP 流程）

1. **用户打开 Copilot**
   - 首页 Header 点击「Copilot」→ 父组件 `setCopilotOpen(true)` → 首页 re-render，`copilotOpen===true`，渲染 `<CopilotChat open={true} setOpen={setCopilotOpen} />`。
   - CopilotChat 挂载后，`useEffect` 因 `open===true` 执行 `handleSaveSelectedNodes()`：POST `/api/v1/save_selected_nodes`（无需 body）。后端按 `endpoints.py` 内写死的 `node_selector` 从 `all_types` 筛选节点，写回 `agent_workflow/Files/nodes_auto.json` 与 `nodes_auto_inject.json`。这样后续同一次会话内，`/copilot/chat` 使用的 system prompt 中的《组件模板》与当前配置一致。
   - 若 `messages.length===0`，另一 `useEffect` 会设置欢迎语，用户看到助手首条消息。

2. **用户发送自然语言**
   - 用户输入并点击发送或按 Enter → `handleSendMessage`：当前内容加入 `messages`，清空输入框，`setIsGenerating(true)`，调用 `chatMutate({ message: inputMessage, conversation_history: [...messages, userMessage] })`。
   - 请求：POST `/api/v1/copilot/chat`，body 为 `{ message, conversation_history }`。
   - 后端：`copilot_chat` 将 history 转为 list[dict]，调用 `generate_workflow_with_llm`；内部加载 `load_system_prompt_auto_inject()`（即用刚更新过的 nodes_auto_inject 替换 system_prompt2.md 的占位符）、调用 LLM、从回复中提取 JSON、normalize_workflow_edges → enrich_workflow_nodes → validate_workflow_json → format_workflow_json；任一步失败则在 max_iterations 内将错误反馈给 LLM 重试；成功则返回 `CopilotChatResponse(message=..., workflow_data=...)`。

3. **前端收到 workflow_data**
   - `useCopilotChat` 的 `onSuccess` 被调用：`setIsGenerating(false)`，追加助手消息，若 `data.workflow_data` 存在则调用 `handleSaveWorkflow(data.workflow_data)`。
   - `handleSaveWorkflow` 内部调用 `generateWorkflow({ workflow_data, flow_name, description })` → POST `/api/v1/copilot/generate-workflow`，body 为 `{ workflow_data, flow_name, description }`。
   - 后端：`validate_workflow_json` 通过后 `format_workflow_json`，查当前用户默认文件夹，`FlowCreate(..., data=workflow_data["data"])` 写入 DB，返回 `{ id, name, data }`。
   - 前端：`useGenerateWorkflow` 的 `onSuccess` 会 refetch `useGetFolders`、`useGetFolder`，然后执行传入的 onSuccess：`setErrorData({ title: "Success", ... })`、`setOpen(false)`、`navigate(\`/flow/${data.id}\`)`。用户被带到新 flow 的编辑页，该页加载 flow 详情后将 data 写入 flowStore，画布展示刚生成的工作流。

4. **异常与重试**
   - **Chat 返回业务错误**：后端捕获 ValueError 后仍返回 200，body 中 `workflow_data=null`，`message` 为错误说明；前端 onSuccess 仍会执行，因无 workflow_data 不会调 handleSaveWorkflow，仅展示助手消息（错误文案）。
   - **Chat 请求异常**：网络或 500 等会走 onError，前端 `setErrorData` 展示错误，`setIsGenerating(false)`。
   - **保存接口**：校验失败 400、无默认文件夹 404、其它 500；前端 onError 展示对应提示。
   - **后端 LLM 重试**：`generate_workflow_with_llm` 内解析/校验失败时，将 last_error 作为新 HumanMessage 加入下一轮，最多重试 max_iterations 次；全部失败后抛出 ValueError，被 copilot_chat 捕获后以 200 + message 形式返回。

---

## 8. 配置与运维要点

- **环境变量（后端）**
  - `LLM_API_KEY`：大模型 API 密钥（必填，否则会报错或回退到 OpenAI 默认）。
  - `LLM_BASE_URL`：OpenAI 兼容接口的 base_url（如自建或第三方代理）。
  - `LLM_MODEL`：模型名（如 `qwen-plus`、`gpt-4o-mini` 等）。
  - 未配置时部分逻辑会回退到 OpenAI 默认；建议在部署环境显式配置，避免行为不一致。

- **节点注入范围（node_selector）**
  - 在 `endpoints.py` 的 `save_selected_nodes` 接口内维护 `node_selector` 字典；详见本文档 **5.3.2 节点注入规则**。
  - 新增可被 Copilot 编排的组件：在对应 `all_types` 分类下加入组件名（或将该分类设为 `"all"`）；组件名需与注册时的 name/display_name 映射一致。
  - 修改后需触发一次「保存选中节点」：前端打开 Copilot 时会自动调 `/api/v1/save_selected_nodes`，或手动调用该接口，才会更新 `nodes_auto.json` 与 `nodes_auto_inject.json`。

- **系统提示词**
  - `agent_workflow/Files/system_prompt2.md` 中占位符 `{{available_nodes}}` 必须保留，运行时由 `load_system_prompt_auto_inject()` 替换为 nodes_auto_inject 内容。
  - 若在提示词中增改《组件模板》外的规则或示例，需与当前注入的节点结构（template、outputs、input_types 等）一致，否则 LLM 易生成不合法节点或边。

- **前端 API 基路径**
  - `src/frontend/src/controllers/API/helpers/constants.ts` 中 `COPILOT: "copilot"`，与后端路由前缀一致；完整路径为 `/api/v1/copilot/chat`、`/api/v1/copilot/generate-workflow`。
  - 若后端挂载前缀变更，需同步修改前端 base URL 或 getURL 的拼接方式。

- **常见问题**
  - **生成的流缺少某类节点**：检查 `node_selector` 是否包含对应 category 及组件名；确认 `/save_selected_nodes` 已被调用，且 `Files/nodes_auto_inject.json` 中已有该节点。
  - **LLM 一直报错或生成非法 JSON**：可查看后端日志中 last_error、last_response_text；检查 system_prompt2.md 与 nodes_auto_inject 的格式是否被破坏；适当增加 max_iterations 或优化提示词示例。
  - **保存失败 400**：多为 `validate_workflow_json` 不通过（缺 nodes/edges、node 缺 id/data/template、edge 的 source/target 不在 nodes 中），可根据 detail 中的 error_message 排查。
  - **保存失败 404**：当前用户没有默认文件夹，需保证用户初始化时已创建默认文件夹（DEFAULT_FOLDER_NAME）。

---

## 9. 相关文件索引

| 层级 | 路径 | 说明 |
|------|------|------|
| 后端-核心 | `src/backend/base/langflow/agent_workflow/copilot.py` | LLM 调用、校验、规范化、补全、布局 |
| 后端-核心 | `src/backend/base/langflow/agent_workflow/Files/system_prompt2.md` | 系统提示词模板 |
| 后端-核心 | `src/backend/base/langflow/agent_workflow/Files/nodes_auto_inject.json` | 注入 system prompt 的节点 |
| 后端-核心 | `src/backend/base/langflow/agent_workflow/Files/nodes_auto.json` | 完整节点模板（enrich） |
| 后端-API | `src/backend/base/langflow/api/v1/copilot.py` | /copilot/chat、/copilot/generate-workflow |
| 后端-API | `src/backend/base/langflow/api/v1/endpoints.py` | /all、/save_selected_nodes、节点筛选与保存 |
| 后端-组件 | `src/lfx/src/lfx/components/aaa_guoyansong/*` | RagPrompt、Agent 等可编排组件 |
| 后端-参考 | `src/backend/base/langflow/initial_setup/starter_projects/` | 示例工作流（可作 prompt 示例参考） |
| 前端-UI | `src/frontend/src/components/copilot/CopilotChat.tsx` | Copilot 对话与自动保存 |
| 前端-API | `src/frontend/src/controllers/API/queries/copilot/use-copilot-chat.ts` | 对话 mutation |
| 前端-API | `src/frontend/src/controllers/API/queries/copilot/use-generate-workflow.ts` | 保存工作流 mutation |
| 前端-入口 | `src/frontend/src/pages/MainPage/components/header/index.tsx` | Copilot 按钮与 setCopilotOpen |
| 前端-入口 | `src/frontend/src/pages/MainPage/pages/homePage/index.tsx` | copilotOpen 状态与 CopilotChat 挂载 |
| 前端-状态 | `src/frontend/src/stores/flowStore.ts` | 当前 flow 图数据（编辑页使用） |
| 前端-组件 | `src/frontend/src/components/core/selectableDataOutputComponent/index.tsx` | 表格输出多选（工作流内节点用） |

---

## 10. 用户操作指南（使用者视角：基于 Agent 自动构建工作流）

本章从**使用者**角度说明如何通过 Copilot Agent 用自然语言自动生成并保存工作流，无需手拖节点与连线。

### 10.1 前置条件

- 已登录 Langflow，能正常进入首页（工作流列表 / 项目列表）。
- 环境已配置大模型 API（`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`），否则 Copilot 可能报错或无法生成。
- 当前用户存在「默认文件夹」（系统会在此文件夹下创建新工作流）；若无，保存时会提示 404，需联系管理员或先创建默认文件夹。

### 10.2 操作步骤

**第一步：打开 Copilot**

- 在首页顶部导航栏找到 **「Copilot」** 按钮（一般为消息图标 + “Copilot” 文案）。
- 点击后弹出 Copilot 对话窗口；若为首次打开，会看到一条欢迎语，介绍 Copilot 能力并给出示例描述（如「创建一个能回答 AI 相关问题的聊天机器人」）。

**第二步：描述你想要的工作流**

- 在底部输入框中用**自然语言**描述要生成的流程，例如：
  - 「做一个简单的对话流：用户输入 → 大模型回答 → 在聊天窗口显示」
  - 「生成一个 RAG 流程：用户提问、检索知识库、用检索结果和问题拼 prompt 再调用大模型回答」
  - 「构建一个带 Prompt 模板的 GenAI 专家问答流」
- 描述尽量包含：**输入是什么、中间有哪些步骤（如检索、模板、模型）、最终输出是什么**；越具体，生成结果越贴近预期。
- 输入完成后点击发送按钮或按 **Enter**（Shift+Enter 换行）。

**第三步：等待生成**

- 界面会显示「Generating workflow...」等加载状态；后台会调用大模型生成符合 Langflow 规范的工作流 JSON，并做校验、补全与布局。
- 若生成成功，助手会回复成功提示，并**自动将工作流保存到你的默认文件夹**，名称一般为「Copilot Generated Flow + 当前时间」。
- 若生成失败（如描述超出当前支持的组件、或大模型多次重试仍无法产出合法结构），助手会返回错误说明，此时可**修改描述后再次发送**重试。

**第四步：进入编辑页查看与修改**

- 保存成功后，弹窗会自动关闭，并**跳转到新创建的工作流编辑页**。
- 在编辑页可以看到自动生成的节点与连线；可像普通 flow 一样**拖拽调整位置、增删节点、改参数、运行测试**。
- 若对结果不满意，可返回首页再次打开 Copilot，用更精确的描述重新生成，或基于现有流在编辑页手动微调。

### 10.3 使用建议

- **描述尽量具体**：说明「输入 → 中间步骤（模型/检索/模板等）→ 输出」，比只说「做一个聊天机器人」更容易得到可用流。
- **仅使用已支持的组件**：当前 Copilot 只能生成「节点注入规则」中已配置的组件（如 ChatInput、ChatOutput、RagPrompt、各类模型与 Agent、Chroma、File 等）；若描述中涉及未注入的组件，可能被忽略或替换为相近节点。
- **失败时可重试**：网络或大模型偶发错误时，可直接再次发送相同或略改的描述；多轮对话历史会一并传给后端，有助于上下文一致。

### 10.4 常见问题（用户侧）

- **点击 Copilot 没反应**：确认已登录且首页已加载完成；若仍无反应，可检查浏览器控制台是否有报错。
- **一直提示生成失败**：可尝试简化描述（如先只要「用户输入 → 模型 → 聊天输出」），或联系管理员检查 LLM 配置与节点注入配置。
- **保存后找不到新工作流**：新流会保存在「默认文件夹」下，请在首页对应文件夹中查找；若提示保存失败，多为默认文件夹不存在，需联系管理员。

---

## 11. 未来展望与改进方向

本章从**功能演进**与**健壮性**两方面，对当前「基于 Agent 自动构建工作流」的方式提出可改进点，供后续迭代参考。

### 11.1 功能与体验改进

| 方向 | 现状简述 | 改进建议 |
|------|----------|----------|
| **可编排节点范围** | 由后端 `node_selector` 写死，用户无法在前端选择「本次对话可用哪些组件」。 | 支持前端配置或预设多套「场景」（如仅基础对话、含 RAG、含 Agent 工具等），用户打开 Copilot 时选择场景，或由管理员在配置中心维护 node_selector，避免改代码。 |
| **多轮对话与迭代** | 当前虽传 conversation_history，但每次请求均为「根据本轮描述生成一个完整新流」，不支持「在上一版流基础上增删节点」的增量式生成。 | 引入「基于现有 flow 的改写」模式：将当前 flow 的 nodes/edges 摘要或部分 JSON 传入 LLM，让用户用自然语言描述「加一个 XX 节点」「把 A 连到 B」，输出为 diff 或增量指令，再在后端合并到现有流。 |
| **生成前预览与确认** | 生成后直接保存并跳转，用户无法在保存前预览或拒绝。 | 增加「预览步」：生成成功后先展示节点列表或缩略图，用户确认后再调用 generate-workflow 保存；可选「另存为」或「覆盖当前 flow」。 |
| **提示词与示例维护** | system_prompt2.md 与示例写死在仓库，调整需改代码并重启。 | 将系统提示词、示例模板、甚至 node_selector 纳入配置中心或 DB，支持热更新；可做 A/B 测试不同提示词对生成质量的影响。 |
| **结构化描述引导** | 用户完全自由输入，容易描述不清或超出能力范围。 | 提供简短「描述模板」或表单（如：输入类型、是否检索、是否多轮、输出类型），前端拼成结构化描述再发给 Copilot，降低歧义并提高一次生成成功率。 |

### 11.2 健壮性提升

| 方向 | 现状简述 | 改进建议 |
|------|----------|----------|
| **LLM 输出解析** | 依赖正则从回复中抽取 JSON，若 LLM 输出格式多变（多段代码块、混合说明与 JSON）可能抽错或漏抽。 | 优先使用「结构化输出」（如 JSON mode、function calling 或指定 schema），让模型直接返回单一 JSON 对象；解析层只做 schema 校验与类型转换，减少正则与容错逻辑。 |
| **校验与错误反馈** | 校验失败时把错误文案反馈给 LLM 重试，若错误信息过于技术化，LLM 可能难以理解并修正。 | 将校验错误归纳为「用户可读 + 机器可读」的简短码（如 NODE_MISSING_ID、EDGE_UNKNOWN_SOURCE），提示词中提供「错误码与修正示例」的对照表，便于 LLM 自纠错；同时对常见错误做自动修复（如缺 id 自动生成）。 |
| **超长输出与截断** | 依赖 invoke_with_continuation 多轮续写，轮次与 token 上限固定，极端情况下仍可能截断或超时。 | 引导 LLM 先输出「骨架」（少量关键节点与边），再对子图或单节点分步生成；或对超大流做分片保存与合并；同时监控 finish_reason 与 token 使用，超限时提前返回「流过大，请简化描述」等友好提示。 |
| **节点模板与注入一致性** | nodes_auto_inject 与 nodes_auto 由同一 node_selector 生成，若 all_types 在运行中变更（如热加载组件），可能短暂不一致。 | 在 save_selected_nodes 或 chat 入口处做版本或哈希校验，避免使用「未同步」的注入文件；或改为每次 chat 前按当前 all_types 实时生成注入内容（需权衡延迟与一致性）。 |
| **接口与依赖可用性** | LLM 服务不可用或超时时，仅返回通用异常，用户难以区分「描述问题」与「服务问题」。 | 对 LLM 调用做超时与重试（含退避），区分超时、鉴权失败、限流等，返回明确错误码与提示；可选降级策略（如缓存最近一次成功结果、或返回「请稍后重试」的引导）。 |
| **并发与限流** | 多用户同时使用 Copilot 时，可能集中打满 LLM 或 DB。 | 对 /copilot/chat 做按用户或 IP 的限流与队列；对 generate-workflow 做写库限流或异步落库，避免长耗时阻塞；记录生成耗时与成功率，便于容量规划。 |

### 11.3 小结

当前方案已实现「自然语言 → 工作流图」的端到端闭环，适合作为 MVP 快速搭建可用的自动构建能力。后续可优先在**结构化输出与解析**、**校验错误标准化与自修复**、**用户侧预览与确认**上投入，再逐步扩展**可配置节点集**、**增量式生成**与**提示词/配置可运维化**，在体验与健壮性上持续提升。
