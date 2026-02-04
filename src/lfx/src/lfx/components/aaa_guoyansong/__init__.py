from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

from .agent import AgentComponent
from .chroma import ChromaVectorStoreComponent
from .chroma_agent import ChromaVectorStoreComponentAgent
from .Edit import EditComponent
from .hello import HelloComponent
from .parser2 import ParserComponent2
from .prompt import RagPromptComponent

_dynamic_imports = {
    "ChromaVectorStoreComponent": "chroma",
    "ChromaVectorStoreComponentAgent": "chroma_agent",
    "AgentComponent": "agent",
}

__all__ = [
    "AgentComponent",
    "ChromaVectorStoreComponent",
    "ChromaVectorStoreComponentAgent",
    "EditComponent",
    "HelloComponent",
    "ParserComponent1",
    "ParserComponent2",
    "RagPromptComponent",
]


def __getattr__(attr_name: str) -> Any:
    """Lazily import model and agent components on attribute access."""
    if attr_name not in _dynamic_imports:
        msg = f"module '{__name__}' has no attribute '{attr_name}'"
        raise AttributeError(msg)
    try:
        result = import_mod(attr_name, _dynamic_imports[attr_name], __spec__.parent)
    except (ModuleNotFoundError, ImportError, AttributeError) as e:
        msg = f"Could not import '{attr_name}' from '{__name__}': {e}"
        raise AttributeError(msg) from e
    globals()[attr_name] = result
    return result


def __dir__() -> list[str]:
    return list(__all__)
