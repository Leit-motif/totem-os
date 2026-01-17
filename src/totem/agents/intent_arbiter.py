"""Intent Arbiter Agent for classifying and routing user input."""

import hashlib
import json
import os
import re
import ssl
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from rich.console import Console

from ..ledger import LedgerWriter
from ..models.intent import IntentDecision, IntentType
from ..routing.intent_router import IntentRouter

console = Console()

ARBITER_VERSION = "1.0.0"

class IntentArbiterAgent:
    """Classifies user input into intents and routes to downstream agents."""

    # Heuristic rules
    HEURISTICS = [
        (r"(?i)^(todo:|make a plan|steps|roadmap)", IntentType.TASK_GENERATION),
        (r"(?i)(should I|compare|pros and cons|which is better)", IntentType.DECISION_SUPPORT),
        (r"(?i)(run|execute|call|search my vault|create file|invoke tool)", IntentType.EXECUTION),
        (r"(?i)(I feel|I'm thinking about|processing)", IntentType.REFLECT),
        (r"(?i)(remember that|save this|add to vault|update note)", IntentType.KNOWLEDGE_UPDATE),
    ]

    def __init__(
        self, 
        ledger_writer: LedgerWriter, 
        vault_root: Path,
        llm_engine: str = "auto"
    ):
        self.ledger_writer = ledger_writer
        self.vault_root = vault_root
        self.router = IntentRouter()
        self.llm_engine = llm_engine
        self.run_id = ledger_writer.run_id

    def run(self, input_text: str, capture_id: Optional[str] = None) -> None:
        """Main entry point: Classify, Log, Route.
        
        Args:
            input_text: The user input string.
            capture_id: Optional capture ID for correlation.
        """
        # Calculate input hash for correlation
        input_hash = hashlib.md5(input_text.encode("utf-8")).hexdigest()
        
        # 1. Classify
        decision = self.classify(input_text, capture_id)
        
        # 2. Log to Ledger
        self._log_decision(input_text, decision, input_hash, capture_id)
        
        # 3. Route
        agent = self.router.get_agent(decision.intent_type)
        console.print(f"[bold]Routing to:[/bold] {agent.__class__.__name__}")
        agent.run(input_text)

    def classify(self, text: str, capture_id: Optional[str] = None) -> IntentDecision:
        """Classify input text into an IntentDecision."""
        # 1. Try Heuristics
        for pattern, intent_type in self.HEURISTICS:
            if re.search(pattern, text):
                return IntentDecision(
                    intent_type=intent_type,
                    confidence=0.99,  # "Hard match" heuristic
                    rationale=f"Matched heuristic pattern: {pattern}",
                    suggested_agents=[self.router.get_agent(intent_type).__class__.__name__]
                )
        
        # Check for ignore (short/filler)
        if len(text.strip()) < 10 and text.strip().lower() in ["ok", "lol", "nice", "thanks"]:
             return IntentDecision(
                intent_type=IntentType.IGNORE,
                confidence=0.99,
                rationale="Input too short/filler",
                suggested_agents=["NullAgent"]
            )

        # 2. LLM Fallback
        return self._llm_classify(text, capture_id)

    def _llm_classify(self, text: str, capture_id: Optional[str] = None) -> IntentDecision:
        """Fallback to LLM for classification."""
        if self.llm_engine == "fake":
            return self._fake_llm_classify(text)
        
        # Check API keys for auto/real
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
             if self.llm_engine == "auto":
                 console.print("[yellow]No API key found for IntentArbiter, falling back to Fake LLM[/yellow]")
                 return self._fake_llm_classify(text)
             else:
                 raise ValueError("OPENAI_API_KEY not set for IntentArbiter")

        # Call OpenAI (defaulting to OpenAI for now as per minimal implementation)
        try:
            return self._call_openai(text, api_key, capture_id)
        except Exception as e:
            console.print(f"[red]LLM Call failed: {e}. Falling back to Fake.[/red]")
            return self._fake_llm_classify(text)

    def _fake_llm_classify(self, text: str) -> IntentDecision:
        """Deterministic fake classifier."""
        # Simple deterministic hash-based fallback or default to reflect
        return IntentDecision(
            intent_type=IntentType.REFLECT,
            confidence=0.5,
            rationale="Ambiguous input (FakeLLM fallback), defaulting to Reflection.",
            suggested_agents=["ReflectionAgent"]
        )

    def _call_openai(self, text: str, api_key: str, capture_id: Optional[str] = None) -> IntentDecision:
        """Call OpenAI to classify intent."""
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        
        prompt = f"""You are an intent classifier. Return only JSON. Choose exactly one intent_type.
Choices: {', '.join([t.value for t in IntentType])}

Input: "{text[:1000]}"

JSON Schema:
{{
  "intent_type": "string",
  "confidence": float,
  "rationale": "string",
  "suggested_agents": ["string"]
}}
"""
        data = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "You are a deterministic intent classifier. Output valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
             "response_format": { "type": "json_object" }
        }

        req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST")
        context = ssl.create_default_context()
        
        with urllib.request.urlopen(req, context=context, timeout=10) as response:
            resp_data = json.loads(response.read().decode("utf-8"))
            content = resp_data["choices"][0]["message"]["content"]
            
            # Save trace
            self._write_trace(text, prompt, content, capture_id)

            parsed = json.loads(content)
            return IntentDecision(**parsed)

    def _log_decision(self, text: str, decision: IntentDecision, input_hash: str, capture_id: Optional[str]):
        """Log decision to ledger."""
        payload = {
            "input_excerpt": text[:100],
            "input_hash": input_hash,
            "intent_type": decision.intent_type.value,
            "confidence": decision.confidence,
            "rationale": decision.rationale,
            "suggested_agents": decision.suggested_agents,
            "routed_to": decision.suggested_agents[0] if decision.suggested_agents else "Unknown",
            "arbiter_version": ARBITER_VERSION,
            "run_id": self.run_id
        }
        if capture_id:
            payload["capture_id"] = capture_id
            
        self.ledger_writer.append_event("INTENT_DECISION", payload=payload, capture_id=capture_id)

    def _write_trace(self, input_text: str, prompt: str, raw_response: str, capture_id: Optional[str] = None):
        """Write trace file."""
        traces_dir = self.vault_root / "90_system" / "traces" / "intent"
        traces_dir.mkdir(parents=True, exist_ok=True)
        
        # Construct filename: <capture_id>_<run_id>.json
        # If capture_id is missing, fallback to input hash
        cid = capture_id if capture_id else hashlib.md5(input_text.encode("utf-8")).hexdigest()[:8]
        filename = f"{cid}_{self.run_id}.json"
        
        trace_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "input": input_text,
            "capture_id": capture_id,
            "run_id": self.run_id,
            "prompt": prompt,
            "raw_response": raw_response
        }
        
        (traces_dir / filename).write_text(json.dumps(trace_data, indent=2))
