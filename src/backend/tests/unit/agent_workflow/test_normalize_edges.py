from langflow.agent_workflow.copilot import normalize_workflow_edges


def make_node(node_id: str, type_name: str, field_name: str = None, field_type: str = None):
    node = {"id": node_id, "data": {"id": node_id, "type": type_name, "node": {"template": {}}}}
    if field_name:
        node["data"]["node"]["template"][field_name] = {"type": field_type}
    return node


def test_normalize_source_datatype_overwrites_bad_value():
    nodes = [make_node("Prompt-def56", "Prompt")]
    # LLM produced wrong dataType equal to id
    edges = [
        {
            "data": {
                "sourceHandle": {
                    "dataType": "Prompt-def56",
                    "id": "Prompt-def56",
                    "name": "prompt",
                    "output_types": ["Message"],
                },
                "targetHandle": {
                    "fieldName": "system_message",
                    "id": "LanguageModelComponent-xyz34",
                    "inputTypes": ["Message"],
                    "type": "str",
                },
            }
        }
    ]
    wf = {"data": {"nodes": nodes, "edges": edges}}

    normalized = normalize_workflow_edges(wf)
    normalized_edges = normalized.get("data", {}).get("edges", [])
    assert len(normalized_edges) == 1
    source_handle = normalized_edges[0]["data"]["sourceHandle"]
    assert source_handle["dataType"] == "Prompt"


def test_infer_target_field_type_from_template():
    nodes = [
        make_node(
            "LanguageModelComponent-xyz34", "LanguageModelComponent", field_name="system_message", field_type="str"
        )
    ]
    edges = [
        {
            "data": {
                "sourceHandle": {
                    "dataType": "Prompt",
                    "id": "Prompt-def56",
                    "name": "prompt",
                    "output_types": ["Message"],
                },
                # LLM left out the 'type' or set to wrong value
                "targetHandle": {
                    "fieldName": "system_message",
                    "id": "LanguageModelComponent-xyz34",
                    "inputTypes": ["Message"],
                },
            }
        }
    ]
    wf = {"data": {"nodes": nodes, "edges": edges}}

    normalized = normalize_workflow_edges(wf)
    normalized_edges = normalized.get("data", {}).get("edges", [])
    assert len(normalized_edges) == 1
    target_handle = normalized_edges[0]["data"]["targetHandle"]
    assert target_handle["type"] == "str"
