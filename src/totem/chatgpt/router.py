"""Deterministic routing heuristics for ChatGPT conversations."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import ChatGptConversation
from ..config import ChatGptRoutingConfig


TITLE_PREFIXES = ("how", "fix", "error", "bug", "help")

STACKTRACE_PATTERNS = [
    re.compile(r"traceback \(most recent call last\)", re.IGNORECASE),
    re.compile(r"\bexception:\b", re.IGNORECASE),
    re.compile(r"\berror:\b", re.IGNORECASE),
    re.compile(r"\bstack trace\b", re.IGNORECASE),
    re.compile(r'file ".*", line \d+', re.IGNORECASE),
]


@dataclass
class RoutingDecision:
    """Routing decision plus computed signals."""

    destination: str
    code_fence_count: int
    code_ratio: float
    keyword_hit: bool
    stacktrace_hit: bool
    title_prefix_hit: bool
    short_iterative: bool


def classify_conversation(
    conversation: ChatGptConversation,
    routing_config: ChatGptRoutingConfig,
    routing_mode: str = "heuristic",
) -> RoutingDecision:
    """Classify a conversation into daemon or tooling vault."""
    if routing_mode == "force-daemon":
        return RoutingDecision(
            destination="daemon",
            code_fence_count=0,
            code_ratio=0.0,
            keyword_hit=False,
            stacktrace_hit=False,
            title_prefix_hit=False,
            short_iterative=False,
        )
    if routing_mode == "force-tooling":
        return RoutingDecision(
            destination="tooling",
            code_fence_count=0,
            code_ratio=0.0,
            keyword_hit=False,
            stacktrace_hit=False,
            title_prefix_hit=False,
            short_iterative=False,
        )

    title_text = conversation.title or ""
    message_texts = [msg.content or "" for msg in conversation.messages]
    full_text = "\n".join([title_text] + message_texts)

    code_fence_count, code_chars, total_chars = _compute_code_stats(full_text)
    code_ratio = (code_chars / total_chars) if total_chars else 0.0

    lower_text = full_text.lower()
    keyword_hit = any(keyword in lower_text for keyword in routing_config.keywords_any)
    stacktrace_hit = routing_config.enable_stacktrace_detection and _stacktrace_hit(lower_text)

    title_prefix_hit = title_text.strip().lower().startswith(TITLE_PREFIXES)
    short_iterative = _short_iterative(conversation)

    tooling_signal = (
        code_fence_count >= routing_config.code_fence_min
        or code_ratio >= routing_config.code_ratio_min
        or stacktrace_hit
        or (keyword_hit and (title_prefix_hit or short_iterative))
    )

    destination = "tooling" if tooling_signal else "daemon"
    return RoutingDecision(
        destination=destination,
        code_fence_count=code_fence_count,
        code_ratio=code_ratio,
        keyword_hit=keyword_hit,
        stacktrace_hit=stacktrace_hit,
        title_prefix_hit=title_prefix_hit,
        short_iterative=short_iterative,
    )


def _compute_code_stats(text: str) -> tuple[int, int, int]:
    """Return (fence_count, code_chars, total_chars) for triple-backtick blocks."""
    if "```" not in text:
        return 0, 0, len(text)

    parts = text.split("```")
    fence_count = len(parts) // 2
    code_chars = sum(len(parts[i]) for i in range(1, len(parts), 2))
    total_chars = len(text)
    return fence_count, code_chars, total_chars


def _stacktrace_hit(text: str) -> bool:
    return any(pattern.search(text) for pattern in STACKTRACE_PATTERNS)


def _short_iterative(conversation: ChatGptConversation) -> bool:
    messages = conversation.messages
    if len(messages) < 6:
        return False
    lengths = [len((msg.content or "").strip()) for msg in messages]
    avg_len = sum(lengths) / max(len(lengths), 1)
    return avg_len <= 120
