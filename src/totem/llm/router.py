"""LLM router implementations for Totem OS.

Provides LLM-based routing as an alternative to rule-based routing.
Uses standard library http for API calls to avoid heavy dependencies.
"""

import hashlib
import json
import os
import re
import ssl
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..models.routing import RouteLabel, RouteResult

# Version constants - bump when prompt or parsing logic changes
LLM_ROUTER_VERSION = "1.0.0"
ROUTE_PROMPT_VERSION = "v1"


@dataclass
class LLMRouterTrace:
    """Trace data for LLM routing calls (for debugging)."""
    capture_id: str
    ts: str
    model: str
    prompt: str
    raw_response: str
    parsed_result: dict
    parse_errors: str | None = None


class BaseLLMRouter(ABC):
    """Abstract interface for LLM-based routers.
    
    Implementations must provide a route() method that takes text and capture_id
    and returns a RouteResult.
    """
    
    def __init__(self):
        self._last_trace: LLMRouterTrace | None = None
    
    @abstractmethod
    def route(self, text: str, capture_id: str) -> RouteResult:
        """Route a capture using LLM analysis.
        
        Args:
            text: Raw capture text content
            capture_id: Capture identifier
            
        Returns:
            RouteResult with label, confidence, reasoning, and next_actions
        """
        pass
    
    @property
    @abstractmethod
    def engine_name(self) -> str:
        """Return engine identifier (e.g., 'fake_llm', 'openai', 'anthropic')."""
        pass
    
    @property
    def provider_model(self) -> str | None:
        """Return provider/model string for real clients, None for fake."""
        return None
    
    def get_last_trace(self) -> LLMRouterTrace | None:
        """Get trace data from the last routing call.
        
        Returns:
            LLMRouterTrace if route() was called, else None
        """
        return self._last_trace


class FakeLLMRouter(BaseLLMRouter):
    """Deterministic fake LLM router for testing.
    
    Produces predictable output based on input text using simple heuristics.
    Same input always produces same output (deterministic).
    """
    
    def __init__(self):
        super().__init__()
    
    # Keyword mappings (same as RuleRouter for consistency)
    KEYWORDS = {
        RouteLabel.TASK: [
            "todo", "task", "need to", "must", "should", "action", 
            "deadline", "reminder", "complete", "finish", "do this"
        ],
        RouteLabel.IDEA: [
            "idea", "maybe", "consider", "thought", "concept", 
            "brainstorm", "what if", "could try", "potential"
        ],
        RouteLabel.JOURNAL: [
            "today", "feeling", "experienced", "noticed", "realized", 
            "grateful", "yesterday", "this morning", "reflection"
        ],
        RouteLabel.PEOPLE: [
            "met with", "call with", "talked to", "email from", 
            "discussion with", "spoke to", "conversation with", "meeting"
        ],
        RouteLabel.ADMIN: [
            "expense", "receipt", "invoice", "appointment", 
            "scheduled", "booking", "payment", "bill"
        ],
    }
    
    # Action extraction patterns
    ACTION_PATTERNS = [
        r"need to (.+?)(?:\.|$|\n)",
        r"must (.+?)(?:\.|$|\n)",
        r"should (.+?)(?:\.|$|\n)",
        r"todo:?\s*(.+?)(?:\.|$|\n)",
        r"action:?\s*(.+?)(?:\.|$|\n)",
    ]
    
    @property
    def engine_name(self) -> str:
        return "fake_llm"
    
    def route(self, text: str, capture_id: str) -> RouteResult:
        """Generate deterministic routing from text.
        
        Uses text hash for confidence and keyword analysis for label.
        """
        # Simulated prompt for tracing
        fake_prompt = f"[FakeLLM] Classify text ({len(text)} chars)"
        
        # Handle empty or very short text
        if not text or len(text.strip()) < 5:
            result = RouteResult(
                capture_id=capture_id,
                route_label=RouteLabel.UNKNOWN,
                confidence=0.2,
                next_actions=[],
                reasoning="FakeLLM: Text too short or empty for classification"
            )
            self._save_trace(capture_id, fake_prompt, result)
            return result
        
        text_lower = text.lower()
        
        # Generate deterministic confidence from text hash
        # Use MD5 for determinism (not for security)
        text_hash = hashlib.md5(text.encode()).hexdigest()
        # Use first 4 hex chars to generate confidence between 0.6 and 0.95
        base_confidence = 0.6 + (int(text_hash[:4], 16) / 65535) * 0.35
        
        # Count keyword matches per category
        match_counts = {}
        matched_keywords = {}
        
        for label, keywords in self.KEYWORDS.items():
            count = 0
            matches = []
            for keyword in keywords:
                if keyword in text_lower:
                    count += 1
                    matches.append(keyword)
            if count > 0:
                match_counts[label] = count
                matched_keywords[label] = matches
        
        # Determine best route
        if not match_counts:
            result = RouteResult(
                capture_id=capture_id,
                route_label=RouteLabel.UNKNOWN,
                confidence=round(base_confidence * 0.5, 2),  # Lower for unknown
                next_actions=[],
                reasoning="FakeLLM: No recognizable keywords found"
            )
            self._save_trace(capture_id, fake_prompt, result)
            return result
        
        # Find category with most matches
        best_label = max(match_counts, key=match_counts.get)
        best_count = match_counts[best_label]
        
        # Adjust confidence based on match count
        if best_count >= 3:
            confidence = min(0.95, base_confidence + 0.15)
        elif best_count == 2:
            confidence = base_confidence + 0.05
        else:
            confidence = base_confidence
        
        confidence = round(confidence, 2)
        
        # Build reasoning
        matched_kw_str = ", ".join(matched_keywords[best_label][:3])
        reasoning = f"FakeLLM: Matched {best_count} keyword(s) for {best_label.value} ({matched_kw_str})"
        
        # Extract next actions
        next_actions = self._extract_actions(text)
        
        result = RouteResult(
            capture_id=capture_id,
            route_label=best_label,
            confidence=confidence,
            next_actions=next_actions,
            reasoning=reasoning
        )
        self._save_trace(capture_id, fake_prompt, result)
        return result
    
    def _save_trace(self, capture_id: str, prompt: str, result: RouteResult) -> None:
        """Save trace data for this routing call."""
        fake_response = json.dumps({
            "route_label": result.route_label.value,
            "confidence": result.confidence,
            "reasoning": result.reasoning,
            "next_actions": result.next_actions,
        })
        self._last_trace = LLMRouterTrace(
            capture_id=capture_id,
            ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            model="fake_llm/deterministic",
            prompt=prompt,
            raw_response=fake_response,
            parsed_result={
                "route_label": result.route_label.value,
                "confidence": result.confidence,
                "reasoning": result.reasoning,
                "next_actions": result.next_actions,
            },
            parse_errors=None,
        )
    
    def _extract_actions(self, text: str) -> list[str]:
        """Extract action items from text (max 3)."""
        actions = []
        
        for pattern in self.ACTION_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                action = match.strip()
                if action and action not in actions:
                    actions.append(action)
                    if len(actions) >= 3:
                        return actions
        
        return actions


class RealLLMRouter(BaseLLMRouter):
    """Real LLM router using OpenAI or Anthropic API.
    
    Uses standard library urllib.request to make API calls.
    Requires OPENAI_API_KEY or ANTHROPIC_API_KEY environment variable.
    """
    
    def __init__(self, provider: str = "openai", model: str | None = None):
        """Initialize real LLM router.
        
        Args:
            provider: 'openai' or 'anthropic'
            model: Model name (defaults based on provider)
        """
        super().__init__()
        self.provider = provider.lower()
        
        if self.provider == "openai":
            self.api_key = os.environ.get("OPENAI_API_KEY")
            self.model = model or "gpt-4o-mini"
            self.api_url = "https://api.openai.com/v1/chat/completions"
        elif self.provider == "anthropic":
            self.api_key = os.environ.get("ANTHROPIC_API_KEY")
            self.model = model or "claude-3-5-sonnet-20241022"
            self.api_url = "https://api.anthropic.com/v1/messages"
        else:
            raise ValueError(f"Unsupported provider: {provider}")
        
        if not self.api_key:
            raise ValueError(f"Missing API key: set {provider.upper()}_API_KEY environment variable")
    
    @property
    def engine_name(self) -> str:
        return self.provider
    
    @property
    def provider_model(self) -> str:
        return f"{self.provider}/{self.model}"
    
    def route(self, text: str, capture_id: str) -> RouteResult:
        """Route using real LLM API call."""
        # Handle empty or very short text
        if not text or len(text.strip()) < 5:
            result = RouteResult(
                capture_id=capture_id,
                route_label=RouteLabel.UNKNOWN,
                confidence=0.1,
                next_actions=[],
                reasoning="Text too short or empty for classification"
            )
            # Save minimal trace for short text
            self._last_trace = LLMRouterTrace(
                capture_id=capture_id,
                ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                model=self.provider_model,
                prompt="[skipped - text too short]",
                raw_response="",
                parsed_result={
                    "route_label": result.route_label.value,
                    "confidence": result.confidence,
                },
                parse_errors=None,
            )
            return result
        
        # Build prompt
        prompt = self._build_prompt(text)
        
        # Make API call
        response_json = self._call_api(prompt)
        
        # Parse response and capture trace
        return self._parse_response(response_json, capture_id, prompt)
    
    def _build_prompt(self, text: str) -> str:
        """Build the routing prompt."""
        return f"""Analyze the following text and classify it into exactly one category.

Text to analyze:
---
{text[:2000]}
---

Categories:
- TASK: actionable items, todos, things that need to be done
- IDEA: thoughts, concepts, brainstorms, possibilities to explore
- JOURNAL: personal reflections, feelings, daily experiences
- PEOPLE: interactions with others, meetings, conversations
- ADMIN: administrative items like expenses, invoices, appointments
- UNKNOWN: if text doesn't clearly fit any category

Return a JSON object with exactly these fields:
{{
  "route_label": "TASK|IDEA|JOURNAL|PEOPLE|ADMIN|UNKNOWN",
  "confidence": 0.85,  // 0-1 confidence in classification
  "reasoning": "Brief explanation of signals used",  // max 200 chars
  "next_actions": ["action1", "action2"]  // max 3 actionable items extracted, empty if none
}}

Return ONLY valid JSON, no markdown formatting or explanation."""
    
    def _call_api(self, prompt: str) -> dict[str, Any]:
        """Make API call using urllib.request."""
        if self.provider == "openai":
            return self._call_openai(prompt)
        else:
            return self._call_anthropic(prompt)
    
    def _call_openai(self, prompt: str) -> dict[str, Any]:
        """Call OpenAI API."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        
        data = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a precise JSON classification assistant."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 500,
        }
        
        return self._make_request(self.api_url, headers, data)
    
    def _call_anthropic(self, prompt: str) -> dict[str, Any]:
        """Call Anthropic API."""
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        
        data = {
            "model": self.model,
            "max_tokens": 500,
            "messages": [
                {"role": "user", "content": prompt},
            ],
        }
        
        return self._make_request(self.api_url, headers, data)
    
    def _make_request(self, url: str, headers: dict, data: dict) -> dict[str, Any]:
        """Make HTTP request using urllib."""
        json_data = json.dumps(data).encode("utf-8")
        
        req = urllib.request.Request(url, data=json_data, headers=headers, method="POST")
        
        # Create SSL context
        context = ssl.create_default_context()
        
        try:
            with urllib.request.urlopen(req, context=context, timeout=60) as response:
                response_data = response.read().decode("utf-8")
                return json.loads(response_data)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else "No error body"
            raise RuntimeError(f"API error {e.code}: {error_body}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Network error: {e.reason}")
    
    def _parse_response(
        self, response: dict, capture_id: str, prompt: str
    ) -> RouteResult:
        """Parse API response into RouteResult and save trace."""
        # Extract content based on provider
        if self.provider == "openai":
            content = response["choices"][0]["message"]["content"]
        else:  # anthropic
            content = response["content"][0]["text"]
        
        raw_content = content  # Save for trace
        
        # Parse JSON from content
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        
        parse_error: str | None = None
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            # Fallback if JSON parsing fails
            parse_error = f"JSON parse error: {e}"
            result = RouteResult(
                capture_id=capture_id,
                route_label=RouteLabel.UNKNOWN,
                confidence=0.3,
                next_actions=[],
                reasoning="JSON parse error from LLM response"
            )
            # Save trace with error
            self._last_trace = LLMRouterTrace(
                capture_id=capture_id,
                ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                model=self.provider_model,
                prompt=prompt,
                raw_response=raw_content,
                parsed_result={
                    "route_label": result.route_label.value,
                    "confidence": result.confidence,
                },
                parse_errors=parse_error,
            )
            return result
        
        # Parse route label
        label_str = str(data.get("route_label", "UNKNOWN")).upper()
        try:
            route_label = RouteLabel(label_str)
        except ValueError:
            route_label = RouteLabel.UNKNOWN
        
        # Parse confidence
        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))  # Clamp to [0, 1]
        
        # Parse reasoning
        reasoning = str(data.get("reasoning", ""))[:200]
        
        # Parse next_actions
        raw_actions = data.get("next_actions", [])
        if not isinstance(raw_actions, list):
            raw_actions = []
        next_actions = [str(a)[:200] for a in raw_actions[:3]]
        
        result = RouteResult(
            capture_id=capture_id,
            route_label=route_label,
            confidence=round(confidence, 2),
            next_actions=next_actions,
            reasoning=reasoning
        )
        
        # Save trace
        self._last_trace = LLMRouterTrace(
            capture_id=capture_id,
            ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            model=self.provider_model,
            prompt=prompt,
            raw_response=raw_content,
            parsed_result={
                "route_label": result.route_label.value,
                "confidence": result.confidence,
                "reasoning": result.reasoning,
                "next_actions": result.next_actions,
            },
            parse_errors=None,
        )
        
        return result


def get_llm_router(engine: str = "auto") -> BaseLLMRouter:
    """Get appropriate LLM router based on engine setting and available API keys.
    
    Args:
        engine: 'fake', 'openai', 'anthropic', or 'auto'
                'auto' uses real router if API key available, else fake
    
    Returns:
        BaseLLMRouter implementation
    """
    if engine == "fake":
        return FakeLLMRouter()
    
    if engine == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            return RealLLMRouter(provider="openai")
        raise ValueError("OPENAI_API_KEY not set")
    
    if engine == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            return RealLLMRouter(provider="anthropic")
        raise ValueError("ANTHROPIC_API_KEY not set")
    
    if engine == "auto":
        # Try OpenAI first, then Anthropic, then fake
        if os.environ.get("OPENAI_API_KEY"):
            return RealLLMRouter(provider="openai")
        elif os.environ.get("ANTHROPIC_API_KEY"):
            return RealLLMRouter(provider="anthropic")
        else:
            return FakeLLMRouter()
    
    # Default to fake
    return FakeLLMRouter()


def has_llm_api_key() -> bool:
    """Check if any LLM API key is available.
    
    Returns:
        True if OPENAI_API_KEY or ANTHROPIC_API_KEY is set
    """
    return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))
