"""LLM client implementations for distillation.

Provides an interface for distilling routed items into structured output.
Uses standard library http for API calls to avoid heavy dependencies.
"""

import hashlib
import json
import os
import ssl
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from ..models.distill import (
    DistillResult,
    EntityKind,
    EntityMention,
    Priority,
    TaskItem,
)


class LLMClient(ABC):
    """Abstract interface for LLM distillation clients.
    
    Implementations must provide a distill() method that takes a routed item
    and returns a structured DistillResult.
    """
    
    @abstractmethod
    def distill(self, routed_item: dict) -> DistillResult:
        """Distill a routed item into structured output.
        
        Args:
            routed_item: Dictionary containing routed capture data including:
                - capture_id: str
                - route_label: str
                - raw_file_path: str
                - raw_text: str (the actual capture content)
                
        Returns:
            DistillResult with extracted summary, tasks, entities, etc.
        """
        pass
    
    @property
    @abstractmethod
    def engine_name(self) -> str:
        """Return engine identifier (e.g., 'fake', 'openai', 'anthropic')."""
        pass
    
    @property
    def provider_model(self) -> str | None:
        """Return provider/model string for real clients, None for fake."""
        return None


class FakeLLMClient(LLMClient):
    """Deterministic fake LLM client for testing.
    
    Produces predictable output based on input text using simple heuristics.
    Same input always produces same output (deterministic).
    """
    
    @property
    def engine_name(self) -> str:
        return "fake"
    
    def distill(self, routed_item: dict) -> DistillResult:
        """Generate deterministic distillation from routed item.
        
        Uses simple text analysis to produce predictable output.
        """
        capture_id = routed_item.get("capture_id", "unknown")
        route_label = routed_item.get("route_label", "UNKNOWN")
        raw_text = routed_item.get("raw_text", "")
        
        # Generate deterministic confidence from text hash
        text_hash = hashlib.md5(raw_text.encode()).hexdigest()
        # Use first 4 hex chars to generate confidence between 0.7 and 0.95
        confidence = 0.7 + (int(text_hash[:4], 16) / 65535) * 0.25
        
        # Extract summary (first 100 chars, cleaned)
        summary = self._extract_summary(raw_text)
        
        # Extract key points (deterministic based on sentences)
        key_points = self._extract_key_points(raw_text)
        
        # Extract tasks from action-like phrases
        tasks = self._extract_tasks(raw_text)
        
        # Extract entities (simple noun phrase extraction)
        entities = self._extract_entities(raw_text)
        
        # Build reasoning
        reasoning = f"FakeLLM: extracted from {len(raw_text)} chars, route={route_label}"
        
        return DistillResult(
            capture_id=capture_id,
            distilled_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            route_label=route_label,
            summary=summary,
            key_points=key_points,
            tasks=tasks,
            entities=entities,
            confidence=round(confidence, 2),
            reasoning=reasoning[:200],
        )
    
    def _extract_summary(self, text: str) -> str:
        """Extract summary from text (first sentence or truncated)."""
        text = text.strip()
        if not text:
            return "Empty capture."
        
        # Try to find first sentence
        for end in [".", "!", "?"]:
            idx = text.find(end)
            if 0 < idx < 200:
                return text[:idx + 1]
        
        # Fallback: truncate to 150 chars
        if len(text) > 150:
            return text[:147] + "..."
        return text
    
    def _extract_key_points(self, text: str) -> list[str]:
        """Extract key points from text (max 5)."""
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        key_points = []
        
        for line in lines[:5]:
            if len(line) > 10:
                point = line[:100] if len(line) > 100 else line
                key_points.append(point)
        
        return key_points[:5]
    
    def _extract_tasks(self, text: str) -> list[TaskItem]:
        """Extract tasks from action phrases (max 7)."""
        tasks = []
        text_lower = text.lower()
        
        # Simple action patterns
        action_triggers = ["need to", "must", "should", "todo:", "action:"]
        
        for line in text.split("\n"):
            line_lower = line.lower().strip()
            for trigger in action_triggers:
                if trigger in line_lower:
                    # Extract task text after trigger
                    idx = line_lower.find(trigger)
                    task_text = line[idx + len(trigger):].strip()
                    # Clean up ending punctuation
                    task_text = task_text.rstrip(".,;:")
                    if task_text and len(task_text) > 3:
                        # Determine priority based on keywords
                        priority = Priority.MED
                        if "urgent" in line_lower or "asap" in line_lower:
                            priority = Priority.HIGH
                        elif "eventually" in line_lower or "someday" in line_lower:
                            priority = Priority.LOW
                        
                        tasks.append(TaskItem(
                            text=task_text[:200],
                            priority=priority,
                            due_date=None,
                        ))
                        break
            
            if len(tasks) >= 7:
                break
        
        return tasks
    
    def _extract_entities(self, text: str) -> list[EntityMention]:
        """Extract entities from text using simple heuristics (max 7)."""
        entities = []
        words = text.split()
        
        # Look for capitalized words as potential entities
        seen_names = set()
        for i, word in enumerate(words):
            # Skip very short words
            if len(word) < 3:
                continue
            
            # Check if word starts with capital (potential proper noun)
            clean_word = word.strip(".,;:!?\"'()[]")
            if clean_word and clean_word[0].isupper() and clean_word.lower() not in seen_names:
                # Skip common words
                common = {"the", "this", "that", "what", "when", "where", "i", "a", "an"}
                if clean_word.lower() in common:
                    continue
                
                # Determine kind based on context
                kind = EntityKind.TOPIC  # default
                context_before = words[max(0, i-2):i]
                context_str = " ".join(context_before).lower()
                
                if any(w in context_str for w in ["met", "call", "talk", "email", "@"]):
                    kind = EntityKind.PERSON
                elif any(w in context_str for w in ["project", "build", "working on"]):
                    kind = EntityKind.PROJECT
                elif any(w in context_str for w in ["using", "tool", "app", "software"]):
                    kind = EntityKind.TOOL
                
                entities.append(EntityMention(
                    name=clean_word,
                    kind=kind,
                    note=None,
                ))
                seen_names.add(clean_word.lower())
                
                if len(entities) >= 7:
                    break
        
        return entities


class RealLLMClient(LLMClient):
    """Real LLM client using OpenAI or Anthropic API.
    
    Uses standard library urllib.request to make API calls.
    Requires OPENAI_API_KEY or ANTHROPIC_API_KEY environment variable.
    """
    
    def __init__(self, provider: str = "openai", model: str | None = None):
        """Initialize real LLM client.
        
        Args:
            provider: 'openai' or 'anthropic'
            model: Model name (defaults based on provider)
        """
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
    
    def distill(self, routed_item: dict) -> DistillResult:
        """Distill using real LLM API call."""
        capture_id = routed_item.get("capture_id", "unknown")
        route_label = routed_item.get("route_label", "UNKNOWN")
        raw_text = routed_item.get("raw_text", "")
        
        # Build prompt
        prompt = self._build_prompt(raw_text, route_label)
        
        # Make API call
        response_json = self._call_api(prompt)
        
        # Parse response
        return self._parse_response(response_json, capture_id, route_label)
    
    def _build_prompt(self, raw_text: str, route_label: str) -> str:
        """Build the distillation prompt."""
        return f"""Analyze the following text and extract structured information.
The text was categorized as: {route_label}

Text to analyze:
---
{raw_text[:2000]}
---

Return a JSON object with exactly these fields:
{{
  "summary": "Brief summary under 500 chars",
  "key_points": ["point1", "point2", ...],  // max 5 points
  "tasks": [
    {{"text": "task description", "priority": "low|med|high", "due_date": null}}
  ],  // max 7 tasks, only actionable items
  "entities": [
    {{"name": "EntityName", "kind": "person|project|tool|topic", "note": null}}
  ],  // max 7 entities
  "confidence": 0.85,  // 0-1 confidence in extraction quality
  "reasoning": "Brief explanation of signals used"  // max 200 chars
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
                {"role": "system", "content": "You are a precise JSON extraction assistant."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 1500,
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
            "max_tokens": 1500,
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
    
    def _parse_response(self, response: dict, capture_id: str, route_label: str) -> DistillResult:
        """Parse API response into DistillResult."""
        # Extract content based on provider
        if self.provider == "openai":
            content = response["choices"][0]["message"]["content"]
        else:  # anthropic
            content = response["content"][0]["text"]
        
        # Parse JSON from content
        # Try to extract JSON if wrapped in markdown code blocks
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # Fallback if JSON parsing fails
            return DistillResult(
                capture_id=capture_id,
                distilled_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                route_label=route_label,
                summary="Failed to parse LLM response",
                key_points=[],
                tasks=[],
                entities=[],
                confidence=0.3,
                reasoning="JSON parse error from LLM response",
            )
        
        # Build tasks
        tasks = []
        for t in data.get("tasks", [])[:7]:
            tasks.append(TaskItem(
                text=str(t.get("text", ""))[:200],
                priority=Priority(t.get("priority", "med")),
                due_date=t.get("due_date"),
            ))
        
        # Build entities
        entities = []
        for e in data.get("entities", [])[:7]:
            try:
                entities.append(EntityMention(
                    name=str(e.get("name", ""))[:100],
                    kind=EntityKind(e.get("kind", "topic")),
                    note=e.get("note"),
                ))
            except ValueError:
                # Skip invalid entity kinds
                pass
        
        return DistillResult(
            capture_id=capture_id,
            distilled_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            route_label=route_label,
            summary=str(data.get("summary", ""))[:500],
            key_points=[str(p)[:200] for p in data.get("key_points", [])[:5]],
            tasks=tasks,
            entities=entities,
            confidence=float(data.get("confidence", 0.7)),
            reasoning=str(data.get("reasoning", ""))[:200],
        )


def get_llm_client(engine: str = "auto") -> LLMClient:
    """Get appropriate LLM client based on engine setting and available API keys.
    
    Args:
        engine: 'fake', 'real', 'openai', 'anthropic', or 'auto'
                'auto' uses real client if API key available, else fake
    
    Returns:
        LLMClient implementation
    """
    if engine == "fake":
        return FakeLLMClient()
    
    if engine in ("openai", "real"):
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            return RealLLMClient(provider="openai")
        elif engine == "openai":
            raise ValueError("OPENAI_API_KEY not set")
        # Fall through to anthropic or fake
    
    if engine in ("anthropic", "real"):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            return RealLLMClient(provider="anthropic")
        elif engine == "anthropic":
            raise ValueError("ANTHROPIC_API_KEY not set")
        # Fall through to fake
    
    if engine == "auto":
        # Try OpenAI first, then Anthropic, then fake
        if os.environ.get("OPENAI_API_KEY"):
            return RealLLMClient(provider="openai")
        elif os.environ.get("ANTHROPIC_API_KEY"):
            return RealLLMClient(provider="anthropic")
        else:
            return FakeLLMClient()
    
    # Default to fake
    return FakeLLMClient()
