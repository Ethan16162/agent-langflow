from textwrap import dedent

from lfx.base.prompts.api_utils import process_prompt_template
from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import DefaultPromptField
from lfx.io import HandleInput, MessageTextInput, Output, PromptInput
from lfx.schema.message import Message
from lfx.template.utils import update_template_values

# RAG 默认模板：与 starter_projects/vector_store_rag.py 中包装后的 prompt 一致
# 模板变量 {context} 接检索结果，{question} 接用户问题
RAG_DEFAULT_TEMPLATE = dedent("""\
    Given the following context, answer the question.
    Context:{context}

    Question: {question}
    Answer:""")


class RagPromptComponent(Component):
    display_name: str = "RAG Prompt"
    description: str = "RAG 专用：基于检索上下文与用户问题生成回答的 prompt 模板（context + question -> answer）。"
    documentation: str = "https://docs.langflow.org/components-prompts"
    icon = "braces"
    trace_type = "prompt"
    name = "RagPrompt"
    priority = 0  # Set priority to 0 to make it appear first

    inputs = [
        PromptInput(
            name="template",
            display_name="Template",
            value=RAG_DEFAULT_TEMPLATE,
        ),
        MessageTextInput(
            name="context",
            display_name="Context",
            input_types=["Message"],
            required=True,
            info="检索得到的上下文，通常接 Parser 或 Vector Store 的输出。",
        ),
        MessageTextInput(
            name="question",
            display_name="Question",
            input_types=["Message"],
            required=True,
            info="用户问题，通常接 Chat Input 的 message_response。",
        ),
        MessageTextInput(
            name="tool_placeholder",
            display_name="Tool Placeholder",
            tool_mode=True,
            advanced=True,
            info="A placeholder input for tool mode.",
        ),
    ]

    outputs = [
        Output(display_name="Prompt", name="prompt", method="build_prompt"),
    ]

    async def build_prompt(self) -> Message:
        template = self._attributes.get("template") or RAG_DEFAULT_TEMPLATE
        variables = {}
        for key, val in self._attributes.items():
            if key == "template":
                continue
            if isinstance(val, Message):
                variables[key] = val.text
            elif val is None:
                variables[key] = ""
            else:
                variables[key] = val
        prompt = Message.from_template(template=template, **variables)
        self.status = prompt.text
        return prompt

    def _update_template(self, frontend_node: dict):
        prompt_template = frontend_node["template"]["template"]["value"]
        custom_fields = frontend_node["custom_fields"]
        frontend_node_template = frontend_node["template"]
        _ = process_prompt_template(
            template=prompt_template,
            name="template",
            custom_fields=custom_fields,
            frontend_node_template=frontend_node_template,
        )
        return frontend_node

    async def update_frontend_node(self, new_frontend_node: dict, current_frontend_node: dict):
        """This function is called after the code validation is done."""
        frontend_node = await super().update_frontend_node(new_frontend_node, current_frontend_node)
        template = frontend_node["template"]["template"]["value"]
        # Kept it duplicated for backwards compatibility
        _ = process_prompt_template(
            template=template,
            name="template",
            custom_fields=frontend_node["custom_fields"],
            frontend_node_template=frontend_node["template"],
        )
        # Now that template is updated, we need to grab any values that were set in the current_frontend_node
        # and update the frontend_node with those values
        update_template_values(new_template=frontend_node, previous_template=current_frontend_node["template"])
        return frontend_node

    def _get_fallback_input(self, **kwargs):
        return DefaultPromptField(**kwargs)
