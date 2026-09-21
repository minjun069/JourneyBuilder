"""Typed JSON responses without provider conversion of arbitrary JSON values."""

from typing import Any

from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel

from core.config import settings


def json_response_chain(model: Any, schema: type[BaseModel]) -> Any:
    """Request JSON once and validate it locally; invoke through secure_ainvoke.

    Callers describe the small output shape in their prompt. Sending a provider
    function schema here would coerce arbitrary JSON values to one scalar type
    in the installed Gemini adapter.
    """
    options = (
        {"response_mime_type": "application/json"}
        if settings.llm_provider == "gemini"
        else {"response_format": {"type": "json_object"}}
    )
    return model.bind(**options) | PydanticOutputParser(pydantic_object=schema)
