from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool

try:  # LangChain 0.3 exposes pydantic_v1; older installs only ship pydantic.Field
    from langchain_core.pydantic_v1 import Field
except ImportError:  # pragma: no cover - fallback for environments without the shim
    from pydantic import Field  # type: ignore

from .pett_websocket_client import PettWebSocketClient

logger = logging.getLogger(__name__)


class BackendChatModel(BaseChatModel):
    """LangChain-compatible chat model that proxies completions via Pett backend."""

    websocket_client: PettWebSocketClient
    default_model: Optional[str] = None
    timeout: int = 45
    request_metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True
        extra = "allow"

    def __init__(
        self,
        websocket_client: PettWebSocketClient,
        *,
        default_model: Optional[str] = None,
        timeout: int = 45,
        request_metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """Allow positional websocket_client injection while staying Pydantic-compatible."""
        super().__init__(
            websocket_client=websocket_client,
            default_model=default_model,
            timeout=timeout,
            request_metadata=request_metadata or {},
            **kwargs,
        )

    def bind_tools(
        self,
        tools: Sequence[Dict[str, Any] | type | Callable | BaseTool],
        *,
        tool_choice: Optional[str] = None,
        strict: Optional[bool] = None,
        parallel_tool_calls: Optional[bool] = None,
        **kwargs: Any,
    ) -> Runnable:
        """Bind LangChain tools by translating to OpenAI-style schemas."""
        if parallel_tool_calls is not None:
            kwargs["parallel_tool_calls"] = parallel_tool_calls

        formatted_tools = [
            convert_to_openai_tool(tool, strict=strict) for tool in tools
        ]

        if tool_choice:
            logger.debug(
                "[BackendChatModel] tool_choice=%s requested but not forwarded",
                tool_choice,
            )

        return super().bind(tool_schemas=formatted_tools, **kwargs)

    @property
    def _llm_type(self) -> str:
        return "pett-backend-llm-proxy"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Synchronous entrypoint proxies to async implementation."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            raise RuntimeError(
                "BackendChatModel does not support synchronous invocation while an event loop is running"
            )

        logger.debug("[BackendChatModel] _generate invoked; delegating to async version")
        return asyncio.run(self._agenerate(messages, stop, run_manager, **kwargs))

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        payload = self._build_proxy_payload(messages, stop=stop, **kwargs)

        response = await self.websocket_client.proxy_llm_completion(
            payload,
            timeout=self.timeout,
        )
        if response is None:
            raise RuntimeError("Backend LLM proxy returned no data")

        response_text = response.get("response", "") or ""
        tool_calls = response.get("toolCalls") or []

        additional_kwargs: Dict[str, Any] = {}
        if tool_calls:
            additional_kwargs["tool_calls"] = tool_calls

        ai_message = AIMessage(
            content=response_text,
            additional_kwargs=additional_kwargs,
            response_metadata=response.get("usage"),
        )

        generation_info = {
            "finish_reason": response.get("finishReason"),
            "tool_calls": tool_calls or None,
            "usage": response.get("usage"),
        }

        return ChatResult(
            generations=[ChatGeneration(message=ai_message, generation_info=generation_info)],
            llm_output={
                "finish_reason": response.get("finishReason"),
                "usage": response.get("usage"),
                "raw_message": response.get("rawMessage"),
            },
        )

    def _build_proxy_payload(
        self,
        messages: List[BaseMessage],
        *,
        stop: Optional[List[str]] = None,
        tool_schemas: Optional[List[Dict[str, Any]]] = None,
        max_output_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        model: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **extra_kwargs: Any,
    ) -> Dict[str, Any]:
        """Convert LangChain messages into the backend-friendly payload."""
        serialized_messages = [self._serialize_message(msg) for msg in messages]
        combined_metadata = dict(self.request_metadata)
        if metadata:
            combined_metadata.update(metadata)
        effective_max_tokens = max_output_tokens
        if effective_max_tokens is None and "max_tokens" in extra_kwargs:
            effective_max_tokens = extra_kwargs.get("max_tokens")
        effective_temperature = (
            temperature if temperature is not None else extra_kwargs.get("temperature")
        )
        return {
            "messages": serialized_messages,
            "toolSchemas": tool_schemas,
            "stop": stop,
            "temperature": effective_temperature,
            "model": model or self.default_model,
            "maxOutputTokens": effective_max_tokens,
            "metadata": combined_metadata or None,
        }

    def _serialize_message(self, message: BaseMessage) -> Dict[str, Any]:
        """Map LangChain BaseMessage into the backend's LangChainMessage shape."""
        additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls and "tool_calls" not in additional_kwargs:
            additional_kwargs["tool_calls"] = tool_calls

        data: Dict[str, Any] = {
            "content": message.content,
            "additional_kwargs": additional_kwargs or None,
            "response_metadata": getattr(message, "response_metadata", None),
        }
        optional_fields = (
            "id",
            "name",
            "tool_call_id",
        )
        for field in optional_fields:
            value = getattr(message, field, None)
            if value is not None:
                data[field] = value

        return {
            "type": message.type,
            "data": data,
        }

    async def ainvoke(
        self,
        input: Dict[str, Any],
        config: Optional[RunnableConfig] = None,
        **kwargs: Any,
    ) -> Any:
        return await super().ainvoke(input, config=config, **kwargs)
