"""Copilot Agent for automatic workflow generation."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import re
import secrets
from copy import deepcopy
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from lfx.log.logger import logger

from langflow.services.deps import get_settings_service


def load_system_prompt_auto_inject() -> str:
    # 1. 加载 nodes_auto_inject.json
    nodes_path = Path(__file__).parent / "Files/nodes_auto_inject.json"
    with nodes_path.open("r", encoding="utf-8") as f:
        node_list = json.load(f)  # 这是一个 list of dicts

    # 2. 将每个节点对象转为格式化的 JSON 字符串，并用 ``` 包裹
    formatted_nodes = []
    for idx, node in enumerate(node_list, start=1):
        json_str = json.dumps(node, ensure_ascii=False, indent=2)
        formatted_nodes.append(
            f"\n\n## {idx}. {node.get('data').get('type') or node.get('id').split('-')[0]} 节点模板 \n\n```\n\n{json_str}\n\n```"
        )

    # 用两个换行符分隔每个节点块（你也可以用一个，根据需要调整）
    nodes_block = "\n\n".join(formatted_nodes)

    # 3. 读取 system_prompt2.md 并替换 {{available_nodes}}
    """Load the system prompt from markdown file."""
    prompt_path = Path(__file__).parent / "Files/system_prompt2.md"
    with prompt_path.open("r", encoding="utf-8") as f:
        prompt_template = f.read()
    # 替换占位符
    updated_prompt = prompt_template.replace("{{available_nodes}}", nodes_block)

    # 4. 可选：将更新后的提示词保存回文件（如果需要）
    # prompt_path2 = Path(__file__).parent / "Files/system_prompt_injected.md"
    # with prompt_path2.open("w", encoding="utf-8") as f:
    #     f.write(updated_prompt)

    # """Load the system prompt from markdown file."""
    # prompt_path = Path(__file__).parent / "system_prompt1.md"
    # with prompt_path.open("r", encoding="utf-8") as f:
    #     return f.read()

    return updated_prompt


def load_system_prompt() -> str:
    """Load the system prompt from markdown file."""
    prompt_path = Path(__file__).parent / "system_prompt1.md"
    with prompt_path.open("r", encoding="utf-8") as f:
        return f.read()


def generate_node_id(prefix: str) -> str:
    """Generate a unique node ID."""
    random_suffix = secrets.token_hex(3)
    return f"{prefix}-{random_suffix}"


def validate_workflow_json(workflow_data: dict[str, Any]) -> tuple[bool, str | None]:
    """Validate the generated workflow JSON structure.

    Args:
        workflow_data: The workflow JSON data to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        # 先检查是否有 "data" 字段
        if "data" in workflow_data:
            workflow_data = workflow_data["data"]

        # Check required top-level fields
        if "nodes" not in workflow_data:
            return False, "Missing 'nodes' field"
        if "edges" not in workflow_data:
            return False, "Missing 'edges' field"

        nodes = workflow_data.get("nodes", [])
        edges = workflow_data.get("edges", [])

        if not isinstance(nodes, list):
            return False, "'nodes' must be a list"
        if not isinstance(edges, list):
            return False, "'edges' must be a list"

        if len(nodes) == 0:
            return False, "Workflow must contain at least one node"

        # Validate nodes structure
        node_ids = set()
        for i, node in enumerate(nodes):
            if not isinstance(node, dict):
                return False, f"Node {i} must be a dictionary"

            if "id" not in node:
                return False, f"Node {i} missing 'id' field"
            node_id = node["id"]
            if node_id in node_ids:
                return False, f"Duplicate node ID: {node_id}"
            node_ids.add(node_id)

            if "data" not in node:
                return False, f"Node {i} missing 'data' field"

            node_data = node["data"]
            if not isinstance(node_data, dict):
                return False, f"Node {i} 'data' must be a dictionary"

            # Check for required node data fields
            if "id" not in node_data:
                return False, f"Node {i} data missing 'id' field"
            if "node" not in node_data:
                return False, f"Node {i} data missing 'node' field"

            node_inner = node_data.get("node", {})
            if "template" not in node_inner:
                return False, f"Node {i} missing 'template' field"

        # Validate edges structure
        for i, edge in enumerate(edges):
            if not isinstance(edge, dict):
                return False, f"Edge {i} must be a dictionary"

            if "source" not in edge:
                return False, f"Edge {i} missing 'source' field"
            if "target" not in edge:
                return False, f"Edge {i} missing 'target' field"

            source_id = edge["source"]
            target_id = edge["target"]

            if source_id not in node_ids:
                return False, f"Edge {i} references non-existent source node: {source_id}"
            if target_id not in node_ids:
                return False, f"Edge {i} references non-existent target node: {target_id}"

        return True, None

    except Exception as e:
        return False, f"Validation error: {e!s}"


def _parse_handle_value(val: Any) -> dict:
    """从 edge 的 sourceHandle/targetHandle 解析为字典。支持：dict、JSON 字符串（含 \" 或 œ）。"""
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        # Langflow 可能用 œ 替代 "，先统一为 "
        normalized = val.replace("œ", '"')
        try:
            return json.loads(normalized)
        except json.JSONDecodeError:
            return {}
    return {}


def _handle_to_langflow_str(obj: dict) -> str:
    """将 handle 对象转为 Langflow 规范字符串（用 œ 替代 \"，与 answer.json 一致）。"""
    s = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    return s.replace('"', "œ")


def normalize_workflow_edges(workflow_data: dict[str, Any]) -> dict[str, Any]:
    """校验并标准化 LLM 输出的 edges 为 answer.json 中的 edge 格式，并纠正内容。

    标准格式（与 answer.json 一致）：
    - animated: false
    - className: ""
    - data: { sourceHandle: {...}, targetHandle: {...} }  # 对象，以 data 为准
    - id: "reactflow__edge-{source}{sourceHandle_œ字符串}-{target}{targetHandle_œ字符串}"
    - selected: false
    - source, target: 由 data.sourceHandle.id、data.targetHandle.id 赋值，且须在 nodes 中存在
    - sourceHandle, targetHandle: 由 data 内的 sourceHandle/targetHandle 对象自动编码
      生成（œ 格式），不采用 LLM 输出的顶层 sourceHandle/targetHandle。

    流程：从 data.sourceHandle / data.targetHandle 得到对象 → source/target 取自其 id →
    校验 sourceHandle.id、targetHandle.id 是否在 workflow.data.nodes 中存在 →
    校验通过后补全、编码。
    """
    data = workflow_data.get("data", workflow_data)
    edges = data.get("edges") or []
    nodes = data.get("nodes") or []

    if not isinstance(nodes, list):
        msg = "workflow.data.nodes 必须为 list，当前为 %s" % type(nodes).__name__
        logger.error(msg)
        raise ValueError(msg)
    if not isinstance(edges, list):
        msg = "edges 必须为 list，当前为 %s" % type(edges).__name__
        logger.error(msg)
        raise ValueError(msg)

    node_ids = {n["id"] for n in nodes if isinstance(n, dict) and n.get("id")}
    normalized: list[dict[str, Any]] = []
    for i, raw in enumerate(edges):
        if not isinstance(raw, dict):
            continue
            msg = "edge[%d] 必须为 dict，当前为 %s" % (i, type(raw).__name__)
            logger.error(msg)
            raise ValueError(msg)

        edge_data = raw.get("data") or {}
        # 1) 以 data.sourceHandle / data.targetHandle 为主；若为字符串则解析，否则从顶层 fallback 解析
        sh = edge_data.get("sourceHandle")
        th = edge_data.get("targetHandle")
        if isinstance(sh, dict):
            pass
        elif isinstance(sh, str):
            sh = _parse_handle_value(sh)
        else:
            sh = _parse_handle_value(raw.get("sourceHandle") or "")
        if isinstance(th, dict):
            pass
        elif isinstance(th, str):
            th = _parse_handle_value(th)
        else:
            th = _parse_handle_value(raw.get("targetHandle") or "")

        # 2) source、target 仅从 data.sourceHandle.id、data.targetHandle.id 取值（不再从 raw.source/target）
        source = (sh.get("id") if isinstance(sh, dict) else None) or ""
        target = (th.get("id") if isinstance(th, dict) else None) or ""

        if not source or not target:
            continue
            msg = "edge[%d] 的 data.sourceHandle.id 或 data.targetHandle.id 为空；source=%r, target=%r" % (
                i,
                source,
                target,
            )
            logger.error(msg)
            raise ValueError(msg)

        # 3) 校验：sourceHandle.id、targetHandle.id 必须在 workflow.data.nodes 中存在
        if source not in node_ids:
            continue
            msg = "edge[%d] 的 data.sourceHandle.id=%r 在 workflow.data.nodes 中不存在；可选 node id: %s" % (
                i,
                source,
                sorted(node_ids)[:20],
            )
            logger.error(msg)
            raise ValueError(msg)
        if target not in node_ids:
            continue
            msg = "edge[%d] 的 data.targetHandle.id=%r 在 workflow.data.nodes 中不存在；可选 node id: %s" % (
                i,
                target,
                sorted(node_ids)[:20],
            )
            logger.error(msg)
            raise ValueError(msg)

        # 4) 补全并纠正 data.sourceHandle / data.targetHandle 对象
        if not isinstance(sh, dict):
            sh = {}
        sh["id"] = source
        # Ensure the dataType is derived from the source node TYPE (prefix before the first '-')
        # Some LLM outputs may erroneously set dataType to the node id; overwrite it to be safe.
        sh["dataType"] = source.split("-")[0] if "-" in source else "Component"
        sh.setdefault("name", "output")
        sh.setdefault("output_types", ["Data"])

        if not isinstance(th, dict):
            th = {}
        th["id"] = target
        th.setdefault("fieldName", "input_value")
        # Try to infer the field type from the target node's template if available, falling back to 'str'
        try:
            target_node = next((n for n in nodes if isinstance(n, dict) and n.get("id") == target), None)
            if (
                target_node
                and isinstance(target_node.get("data"), dict)
                and isinstance(target_node.get("data").get("node"), dict)
            ):
                template_field = target_node.get("data").get("node").get("template", {}).get(th.get("fieldName"))
                inferred_type = None
                if isinstance(template_field, dict):
                    inferred_type = template_field.get("type")
                if inferred_type:
                    th["type"] = inferred_type
                else:
                    th.setdefault("type", "str")
            else:
                th.setdefault("type", "str")
        except Exception:
            # Defensive fallback in case of unexpected node structure
            th.setdefault("type", "str")
        th.setdefault("inputTypes", ["Data"])

        data_obj: dict[str, Any] = {"sourceHandle": sh, "targetHandle": th}

        # 5) 根据 data 内的 sourceHandle / targetHandle 对象自动编码为 edge.sourceHandle / edge.targetHandle
        #    （不采用 LLM 的顶层 sourceHandle/targetHandle，由编码函数生成 œ 格式）
        sh_str = _handle_to_langflow_str(sh)
        th_str = _handle_to_langflow_str(th)
        eid = f"reactflow__edge-{source}{sh_str}-{target}{th_str}-{secrets.token_hex(4)}"

        norm: dict[str, Any] = {
            "id": eid,
            "source": source,
            "target": target,
            "sourceHandle": sh_str,
            "targetHandle": th_str,
            "data": data_obj,
            "selected": False,
            "animated": False,
            "className": "",
        }
        normalized.append(norm)

    data["edges"] = normalized
    return workflow_data


def format_workflow_json(workflow_data: dict[str, Any]) -> dict[str, Any]:
    """Format and normalize the workflow JSON to match Langflow format.

    This function ensures each node contains the expected UI fields
    (dragging, measured, width, height, position, positionAbsolute, selected, type, id)
    and assigns deterministic, evenly-distributed positions using a grid
    layout with an MD5-based jitter so nodes do not cluster.

    Args:
        workflow_data: The raw workflow JSON data

    Returns:
        Formatted workflow JSON with proper structure
    """
    # Ensure viewport exists
    if "viewport" not in workflow_data:
        workflow_data["viewport"] = {"x": 0, "y": 0, "zoom": 1}

    # import pdb; pdb.set_trace()

    data = workflow_data.get("data")
    nodes = data.get("nodes", [])
    n = len(nodes)

    # import pdb; pdb.set_trace
    # Layout parameters: grid with spacing and small jitter to avoid exact overlap
    if n > 0:
        cols = max(1, math.ceil(math.sqrt(n)))
        spacing_x = 420
        spacing_y = 220
        base_x = 200
        base_y = 100
        jitter_range_x = spacing_x * 0.25
        jitter_range_y = spacing_y * 0.25

        for i, node in enumerate(nodes):
            # Ensure id exists
            node_id = node.get("id")
            if not node_id:
                prefix = (node.get("data", {}) or {}).get("type") or "node"
                node_id = generate_node_id(prefix=prefix)
                node["id"] = node_id

            # Ensure type exists
            node.setdefault("type", "genericNode")

            # Width/height defaults
            default_w = 320
            default_h = 234
            node.setdefault("width", default_w)
            node.setdefault("height", default_h)

            # Ensure measured reflects width/height
            if "measured" not in node or not isinstance(node.get("measured"), dict):
                node["measured"] = {"width": node["width"], "height": node["height"]}
            else:
                node["measured"].setdefault("width", node["width"])
                node["measured"].setdefault("height", node["height"])

            # dragging and selected
            node.setdefault("dragging", False)
            node.setdefault("selected", False)

            # Deterministic grid position + MD5 jitter
            col = i % cols
            row = i // cols
            h = int(hashlib.md5(node_id.encode("utf-8")).hexdigest()[:8], 16)
            jitter_x = ((h % 1000) / 1000.0 - 0.5) * 2 * jitter_range_x
            jitter_y = (((h >> 16) % 1000) / 1000.0 - 0.5) * 2 * jitter_range_y

            x = base_x + col * spacing_x + jitter_x
            y = base_y + row * spacing_y + jitter_y

            # Assign positions and ensure positionAbsolute mirrors position
            node["position"] = {"x": float(x), "y": float(y)}
            node["positionAbsolute"] = {"x": float(x), "y": float(y)}

            # Keep node.data.type in sync if present
            if "data" in node and isinstance(node["data"], dict):
                node_data = node["data"]
                node_data.setdefault("type", node.get("type") or "genericNode")

    # Ensure edges have proper structure (keep prior behavior)
    edges = workflow_data.get("edges", [])
    for edge in edges:
        if "selected" not in edge:
            edge["selected"] = False
        if "animated" not in edge:
            edge["animated"] = False

        # Ensure data field exists
        if "data" not in edge:
            edge["data"] = {}

    return workflow_data


# 检测LLM输出是否达到max tokens被截断，并做拼接
async def invoke_with_continuation(
    llm,
    messages,
    *,
    max_rounds: int = 3,
    logger=None,
) -> str:
    """Invoke LLM with automatic continuation if finish_reason == 'length'.
    Returns the full concatenated response text.
    """
    full_text = ""
    current_messages = list(messages)

    for round_idx in range(1, max_rounds + 1):
        response = await llm.ainvoke(current_messages)
        chunk = response.content or ""
        full_text += chunk

        finish_reason = response.response_metadata.get("finish_reason")
        print(
            f"Continuation round {round_idx} /{max_rounds}, "
            f"chunk_len={len(chunk)}, full answer len={len(full_text)}, finish_reason={finish_reason}"
        )

        # 正常结束
        if finish_reason != "length":
            return full_text

        # 被截断 → 继续
        continuation_prompt = (
            "The previous response was truncated due to length limits.\n"
            "Continue EXACTLY from where it stopped.\n"
            "Do NOT repeat, rewrite, or explain.\n"
            "ONLY output the remaining content."
        )

        current_messages = (
            current_messages + [SystemMessage(content=continuation_prompt)] + [HumanMessage(content=full_text)]
        )

    raise RuntimeError("LLM response truncated after maximum continuation rounds")


async def generate_workflow_with_llm(
    user_message: str,
    conversation_history: list[dict[str, str]] | None = None,
    max_iterations: int = 2,
) -> dict[str, Any]:
    """Generate workflow JSON using LLM based on user requirements.

    This function will attempt up to `max_iterations` times. On each failure it will
    wrap the error/exception into a HumanMessage and re-invoke the LLM (preserving
    previous conversation history) so the LLM can attempt to correct its output.
    """
    settings_service = get_settings_service()

    # Get OpenAI API key from settings
    # api_key = settings_service.settings.openai_api_key
    api_key = "sk-6f8e6f1012494df29bff153af1606271"
    base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    if not api_key:
        raise ValueError("OpenAI API key not configured. Please set OPENAI_API_KEY environment variable.")

    # Load system prompt
    system_prompt = load_system_prompt_auto_inject()

    # ==================== 1. Initialize LLM (try primary first, fallback to OpenAI)
    try:
        await logger.ainfo("Initializing LLM with Deepseek API...")
        llm = ChatOpenAI(
            model="qwen-plus",
            openai_api_key=api_key,  # 你的 API Key
            temperature=0,
            max_completion_tokens=32768,
            max_tokens=32768,
            base_url=base_url,
        )
        await logger.ainfo("ModelScope LLM initialized successfully")
    except Exception as e:
        await logger.awarning(f"Failed to initialize ModelScope LLM: {e}, falling back to OpenAI")
        if not api_key:
            raise ValueError("OpenAI API key not configured. Please set OPENAI_API_KEY environment variable.")
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.3,
            openai_api_key=api_key,
        )

    # 2. Helper to build base messages (preserve original conversation history)
    def build_base_messages() -> list:
        msgs: list = [SystemMessage(content=system_prompt)]
        if conversation_history:
            for msg in conversation_history:
                if msg.get("role") == "user":
                    msgs.append(HumanMessage(content=msg.get("content", "")))
                elif msg.get("role") == "assistant":
                    msgs.append(SystemMessage(content=msg.get("content", "")))
        return msgs

    last_error: str | None = None
    last_response_text: str = ""

    for attempt in range(1, max_iterations + 1):
        print(f"====== LLM generation attempt {attempt}/{max_iterations}")

        messages = build_base_messages()
        # Always include the user's original requirement each attempt
        messages.append(HumanMessage(content=user_message))
        # If there was an error in previous attempt, provide details to LLM for correction
        if last_error:
            feedback = (
                f"Previous attempt failed with error: {last_error}\n"
                f"Please correct the output and return only a valid workflow JSON object."
            )
            # Also include last response text for context if available
            if last_response_text:
                feedback += f"\nPrevious LLM response was:\n{last_response_text}"
            messages.append(HumanMessage(content=feedback))

        # =========================== 3. Invoke LLM
        try:
            # response = await llm.ainvoke(messages)
            # # import pdb; pdb.set_trace()
            # response_text = response.content or ""

            try:
                response_text = await invoke_with_continuation(
                    llm,
                    messages,
                    max_rounds=15,  # 一般 2~3 就够
                    logger=logger,
                )
                last_response_text = response_text

                if not response_text:
                    last_error = "Empty response from LLM"
                    await logger.awarning(last_error)
                    await asyncio.sleep(attempt * 0.5)
                    continue

                await logger.adebug(f"LLM full response length: {len(response_text)}")

            except Exception as e:
                last_error = f"LLM invocation (with continuation) failed: {e!s}"
                await logger.aerror(last_error)
                await logger.aexception("LLM continuation error")
                await asyncio.sleep(attempt * 0.5)
                continue

            if not response_text:
                last_error = "Empty response from LLM"
                await logger.awarning(last_error)
                # small backoff
                await asyncio.sleep(attempt * 0.5)
                continue
            print(f"------ LLM response length: {len(response_text)}")
        except Exception as e:
            last_error = f"LLM invocation failed: {e!s}"
            await logger.aerror(last_error)
            await logger.aexception("LLM call error")
            await asyncio.sleep(attempt * 0.5)
            continue

        # =================== 4. Try to extract JSON from LLM response
        json_str = None
        try:
            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_match = re.search(r"(\{.*\})", response_text, re.DOTALL)
                if json_match:
                    json_str = json_match.group(1)
                else:
                    last_error = "No JSON found in LLM response"
                    await logger.awarning(last_error)
                    await asyncio.sleep(attempt * 0.5)
                    continue
        except Exception as e:
            last_error = f"Error extracting JSON from response: {e!s}"
            await logger.aerror(last_error)
            await asyncio.sleep(attempt * 0.5)
            continue

        # Parse JSON
        try:
            workflow_data = json.loads(json_str)
        except json.JSONDecodeError as e:
            last_error = f"Invalid JSON in LLM response: {e}"
            await logger.aerror(last_error)
            await logger.adebug(f"Response text: {response_text}")
            await asyncio.sleep(attempt * 0.5)
            continue

        # Normalize edges (may raise) — catch and feed back to LLM
        try:
            workflow_data = normalize_workflow_edges(workflow_data)
        except Exception as e:
            last_error = f"normalize_workflow_edges failed: {e!s}"
            await logger.awarning(last_error)
            await asyncio.sleep(attempt * 0.5)
            continue

        # Enrich nodes (may raise)
        try:
            workflow_data = enrich_workflow_nodes(workflow_data)
        except Exception as e:
            last_error = f"enrich_workflow_nodes failed: {e!s}"
            await logger.awarning(last_error)
            await asyncio.sleep(attempt * 0.5)
            continue

        # Validate workflow
        is_valid, error_msg = validate_workflow_json(workflow_data)
        if not is_valid:
            last_error = f"Validation failed: {error_msg}"
            await logger.awarning(last_error)
            await asyncio.sleep(attempt * 0.5)
            continue

        # Format workflow
        try:
            # import pdb; pdb.set_trace()
            workflow_data = format_workflow_json(workflow_data)
            print("successfully format_workflow_json")
        except Exception as e:
            last_error = f"format_workflow_json failed: {e!s}"
            await logger.awarning(last_error)
            await asyncio.sleep(attempt * 0.5)
            continue

        # Success — optionally write debug file and return
        try:
            import os

            output_dir = "/home/gys/catl/langflow/src/backend/base/langflow/agent_workflow"
            file_path = os.path.join(output_dir, "test2.json")
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(workflow_data, f, ensure_ascii=False, indent=2)
                print("successfully write file to test2.json")
        except Exception:
            # Non-fatal — continue to return the result
            await logger.awarning("Failed to write debug output file, but generation succeeded")

        await logger.ainfo("Successfully generated workflow from LLM")
        await logger.adebug(f"================ {workflow_data.keys()} =================")
        return workflow_data

    # If we fall out of the loop, raise with last error and last LLM response for debugging
    err = f"Failed to generate valid workflow after {max_iterations} attempts. Last error: {last_error}. Last LLM response: {last_response_text}"
    await logger.aerror(err)
    raise ValueError(err)


def enrich_workflow_nodes(workflow_data: dict[str, Any]) -> dict[str, Any]:
    """补全 workflow_data 中 nodes 的缺失字段，使用本地 nodes.json 中的完整模板。

    规则：
    - 保留 workflow_data 中已有的字段值（LLM 输出优先）。
    - 仅从模板中补充缺失的字段。
    - 不修改 edges 或其他顶层字段。

    Args:
        workflow_data (dict): LLM 输出的工作流数据（含部分节点信息）

    Returns:
        dict: 补全后的 workflow_data
    """
    NODES_JSON_PATH = "/home/gys/catl/langflow/src/backend/base/langflow/agent_workflow/Files/nodes_auto.json"

    # 加载完整的节点模板
    nodes_json_path = Path(NODES_JSON_PATH)
    if not nodes_json_path.exists():
        raise FileNotFoundError(f"nodes.json not found at {NODES_JSON_PATH}")
    with open(nodes_json_path, encoding="utf-8") as f:
        nodes_schema = json.load(f)  # 假设是 { "ChatInput": {...}, ... }
    node_templates = {}
    for template_node in nodes_schema.get("nodes", []):
        node_type = template_node.get("id").split("-")[0]
        if node_type:
            node_templates[node_type] = template_node

    enriched_workflow = deepcopy(workflow_data)
    nodes = enriched_workflow["data"]["nodes"]

    for i, node in enumerate(nodes):
        node_type = node.get("id").split("-")[0]
        if not node_type:
            continue  # 跳过无 type 的节点

        # 从模板中获取完整定义
        template_node = node_templates.get(node_type)
        if not template_node:
            continue  # 模板中无此类型，跳过

        # 合并：已有字段保留，缺失字段从模板补充
        def deep_merge_preserve_existing(existing: dict, template: dict) -> dict:
            merged = deepcopy(template)  # 先复制模板
            for k, v in existing.items():
                if isinstance(v, dict) and isinstance(merged.get(k), dict):
                    merged[k] = deep_merge_preserve_existing(v, merged[k])
                else:
                    merged[k] = v  # LLM 的值优先
            return merged

        # 执行合并
        merged_node = deep_merge_preserve_existing(node, template_node)

        # 写回
        nodes[i] = merged_node

    return enriched_workflow


def normalize_workflow_nodes(
    workflow_data: dict,
    nodes_json_path: str = "/home/gys/catl/langflow/src/backend/base/langflow/agent_workflow/nodes.json",
) -> dict:
    """使用 nodes.json 作为权威模板，修复 / 补全 LLM 生成的 workflow JSON"""
    # ---------- 1. 加载 nodes.json ----------
    with open(nodes_json_path, encoding="utf-8") as f:
        nodes_schema = json.load(f)

    # ---------- 2. 构建 type -> 模板 映射 ----------
    template_map = {}
    for template_node in nodes_schema.get("nodes", []):
        node_type = template_node.get("id").split("-")[0]
        if node_type:
            template_map[node_type] = template_node

    # ---------- 3. 深度合并函数（只补缺失，不覆盖） ----------
    def deep_merge(llm_node: dict, template_node: dict) -> dict:
        """template_node 作为兜底，llm_node 优先"""
        result = copy.deepcopy(template_node)

        for key, value in llm_node.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = deep_merge(value, result[key])
            else:
                result[key] = value

        return result

    # ---------- 4. 遍历并修复 workflow 中的 nodes ----------
    normalized_nodes = []
    # import pdb; pdb.set_trace()
    for llm_node in workflow_data.get("nodes", []):
        node_type = llm_node.get("id").split("-")[0]

        # 未知节点类型：原样保留（防止系统被 LLM 输出阻断）
        if node_type not in template_map:
            normalized_nodes.append(llm_node)
            continue

        template_node = template_map[node_type]

        # 基于模板补全字段
        fixed_node = deep_merge(llm_node, template_node)
        normalized_nodes.append(fixed_node)

    workflow_data["nodes"] = normalized_nodes
    return workflow_data
