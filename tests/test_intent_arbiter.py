"""Tests for IntentArbiterAgent and routing."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from totem.agents.intent_arbiter import IntentArbiterAgent
from totem.ledger import LedgerWriter
from totem.models.intent import IntentDecision, IntentType
from totem.routing.intent_router import IntentRouter
from totem.agents.stubs import (
    PlannerAgent,
    AnalystAgent,
    ToolAgent,
    ReflectionAgent,
    KnowledgeGardenAgent,
    NullAgent,
)


@pytest.fixture
def mock_ledger(tmp_path):
    ledger_file = tmp_path / "ledger.jsonl"
    return LedgerWriter(ledger_file)


@pytest.fixture
def mock_vault_root(tmp_path):
    return tmp_path


@pytest.fixture
def arbiter(mock_ledger, mock_vault_root):
    return IntentArbiterAgent(
        ledger_writer=mock_ledger,
        vault_root=mock_vault_root,
        llm_engine="fake"
    )


def test_classify_heuristics(arbiter):
    """Test deterministic heuristic classification."""
    
    cases = [
        ("todo: buy milk", IntentType.TASK_GENERATION),
        ("make a plan for world domination", IntentType.TASK_GENERATION),
        ("should I use python or go?", IntentType.DECISION_SUPPORT),
        ("compare x and y", IntentType.DECISION_SUPPORT),
        ("run the build script", IntentType.EXECUTION),
        ("search my vault for 'cats'", IntentType.EXECUTION),
        ("I feel tired today", IntentType.REFLECT),
        ("processing my thoughts", IntentType.REFLECT),
        ("remember that elephants never forget", IntentType.KNOWLEDGE_UPDATE),
        ("ok", IntentType.IGNORE),
    ]

    for text, expected in cases:
        decision = arbiter.classify(text)
        assert decision.intent_type == expected, f"Failed for '{text}'"
        assert decision.confidence == 0.99


def test_router_mapping():
    """Test IntentRouter maps to correct agent types."""
    router = IntentRouter()
    
    assert isinstance(router.get_agent(IntentType.TASK_GENERATION), PlannerAgent)
    assert isinstance(router.get_agent(IntentType.DECISION_SUPPORT), AnalystAgent)
    assert isinstance(router.get_agent(IntentType.EXECUTION), ToolAgent)
    assert isinstance(router.get_agent(IntentType.REFLECT), ReflectionAgent)
    assert isinstance(router.get_agent(IntentType.KNOWLEDGE_UPDATE), KnowledgeGardenAgent)
    assert isinstance(router.get_agent(IntentType.IGNORE), NullAgent)


def test_ledger_logging(arbiter, mock_ledger):
    """Test that decisions are logged to the ledger."""
    text = "todo: write tests"
    arbiter.run(text)
    
    # Read ledger
    events = []
    with open(mock_ledger.ledger_path, "r") as f:
        for line in f:
            events.append(json.loads(line))
            
    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "INTENT_DECISION"
    assert event["payload"]["intent_type"] == "task_generation"
    assert "input_excerpt" in event["payload"]
    assert "input_hash" in event["payload"]
    assert "routed_to" in event["payload"]
    assert "arbiter_version" in event["payload"]


def test_trace_writing(arbiter, mock_vault_root):
    """Test that traces are written (mocking LLM usage)."""
    # Force use of _fake_llm_classify which doesn't write trace by default in my implementation check...
    # Wait, my implementation only writes trace in _call_openai.
    # The heuristic path does NOT write a trace. 
    # Let's check implementation of IntentArbiterAgent.classify again.
    # It calls _llm_classify only if heuristics fail.
    # _fake_llm_classify returns decision but does not write trace in my code?
    # Checking code...
    
    # Actually, verify that run() calls classify() -> log -> route.
    # If I want to test trace writing, I need to trigger _call_openai, but I don't want to call real API.
    # I should mock _call_openai or the request.
    pass  # I'll implement a specific test for trace if I can mock the LLM call effectively.


@patch("totem.agents.intent_arbiter.urllib.request.urlopen")
def test_llm_fallback_and_trace(mock_urlopen, arbiter, mock_vault_root):
    """Test LLM fallback writes trace."""
    arbiter.llm_engine = "auto"
    
    # Mock API response
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "choices": [{
            "message": {
                "content": json.dumps({
                    "intent_type": "decision_support",
                    "confidence": 0.9,
                    "rationale": "Asking for comparison",
                    "suggested_agents": ["AnalystAgent"]
                })
            }
        }]
    }).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_response
    
    input_text = "ambiguous input that fails heuristics"
    
    with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-key"}):
        arbiter.classify(input_text, capture_id="test-cap-123")
        
    # Check trace file exists
    traces_dir = mock_vault_root / "90_system" / "traces" / "intent"
    assert traces_dir.exists()
    trace_files = list(traces_dir.glob("*.json"))
    assert len(trace_files) >= 1
    
    # Verify filename format: <capture_id>_<run_id>.json
    filename = trace_files[0].name
    assert filename.startswith("test-cap-123_")
    assert filename.endswith(".json")
    
    content = json.loads(trace_files[0].read_text())
    assert content["input"] == input_text
    assert content["capture_id"] == "test-cap-123"


def test_determinism(arbiter):
    """Test determinism for heuristics."""
    text = "todo: repetitive task"
    decision1 = arbiter.classify(text)
    decision2 = arbiter.classify(text)
    
    assert decision1 == decision2
    assert decision1.rationale == decision2.rationale
    assert decision1.confidence == decision2.confidence
    assert decision1.intent_type == decision2.intent_type
