"""Tests for hybrid routing functionality (Milestone 5)."""

import json

from totem.config import TotemConfig
from totem.ledger import LedgerWriter
from totem.llm.router import (
    FakeLLMRouter,
    LLM_ROUTER_VERSION,
    ROUTE_PROMPT_VERSION,
    get_llm_router,
)
from totem.models.capture import CaptureMeta
from totem.models.routing import RouteLabel
from totem.route import (
    HYBRID_ROUTER_VERSION,
    RULE_ROUTER_VERSION,
    HybridRouter,
    RuleRouter,
    get_router,
    process_capture_routing,
)


class TestFakeLLMRouter:
    """Tests for FakeLLMRouter determinism and behavior."""
    
    def test_fake_llm_router_deterministic(self):
        """Test that FakeLLMRouter produces same output for same input."""
        router = FakeLLMRouter()
        
        text = "I need to finish the project today. Must complete the tasks."
        
        result1 = router.route(text, "test-capture-id")
        result2 = router.route(text, "test-capture-id")
        
        assert result1.route_label == result2.route_label
        assert result1.confidence == result2.confidence
        assert result1.next_actions == result2.next_actions
        assert result1.reasoning == result2.reasoning
    
    def test_fake_llm_router_extracts_actions(self):
        """Test that FakeLLMRouter extracts action items."""
        router = FakeLLMRouter()
        
        text = "I need to call John tomorrow. Must finish the report."
        result = router.route(text, "test-capture-id")
        
        assert len(result.next_actions) > 0
        assert any("call" in action.lower() for action in result.next_actions)
    
    def test_fake_llm_router_routes_task_keywords(self):
        """Test that FakeLLMRouter routes task keywords to TASK."""
        router = FakeLLMRouter()
        
        text = "I must complete this task by deadline. Need to finish the report."
        result = router.route(text, "test-capture-id")
        
        assert result.route_label == RouteLabel.TASK
        assert result.confidence > 0.5
        assert "FakeLLM" in result.reasoning
    
    def test_fake_llm_router_empty_text(self):
        """Test that empty text returns UNKNOWN with low confidence."""
        router = FakeLLMRouter()
        
        result = router.route("", "test-capture-id")
        
        assert result.route_label == RouteLabel.UNKNOWN
        assert result.confidence <= 0.3


class TestHybridRouter:
    """Tests for HybridRouter logic."""
    
    def test_hybrid_chooses_rule_when_high_confidence(self):
        """Test that HybridRouter uses rule result when confidence >= threshold."""
        # Create hybrid router with high threshold
        llm_router = FakeLLMRouter()
        hybrid = HybridRouter(llm_router=llm_router, high_conf_threshold=0.70)
        
        # Text that produces high confidence in rule router (multiple matches)
        text = "I must complete the task. Need to finish the deadline action."
        
        _ = hybrid.route(text, "test-capture-id")  # result not used, testing metadata
        metadata = hybrid.get_last_metadata()
        
        # Should choose rule (high confidence from multiple keywords)
        assert metadata is not None
        assert metadata.engine == "hybrid"
        assert metadata.chosen_source == "rule"
        assert metadata.rule_result is not None
        assert metadata.rule_result.confidence >= 0.70
        # LLM should not have been called (or be None)
        assert metadata.llm_result is None
    
    def test_hybrid_fallback_to_llm_when_low_confidence(self):
        """Test that HybridRouter falls back to LLM when rule confidence < threshold."""
        # Create hybrid router with very high threshold
        llm_router = FakeLLMRouter()
        hybrid = HybridRouter(llm_router=llm_router, high_conf_threshold=0.99)
        
        # Text that produces moderate confidence (single keyword)
        text = "I should consider this idea."
        
        _ = hybrid.route(text, "test-capture-id")  # result not used, testing metadata
        metadata = hybrid.get_last_metadata()
        
        # Should choose LLM (rule confidence below 0.99)
        assert metadata is not None
        assert metadata.engine == "hybrid"
        assert metadata.chosen_source == "llm"
        assert metadata.rule_result is not None
        assert metadata.llm_result is not None
        # Rule confidence should be below threshold
        assert metadata.rule_result.confidence < 0.99
    
    def test_hybrid_returns_route_result(self):
        """Test that HybridRouter returns proper RouteResult."""
        llm_router = FakeLLMRouter()
        hybrid = HybridRouter(llm_router=llm_router, high_conf_threshold=0.85)
        
        text = "I need to finish this task today."
        result = hybrid.route(text, "test-capture-id")
        
        assert result.capture_id == "test-capture-id"
        assert result.route_label in RouteLabel
        assert 0.0 <= result.confidence <= 1.0
        assert isinstance(result.next_actions, list)
        assert isinstance(result.reasoning, str)


class TestGetRouter:
    """Tests for router factory function."""
    
    def test_get_router_rule(self):
        """Test that get_router returns RuleRouter for 'rule' engine."""
        config = TotemConfig()
        router = get_router("rule", config)
        
        assert isinstance(router, RuleRouter)
    
    def test_get_router_llm(self):
        """Test that get_router returns LLM router for 'llm' engine."""
        config = TotemConfig()
        # Use fake to avoid API key requirement
        router = get_router("llm", config, llm_engine="fake")
        
        assert isinstance(router, FakeLLMRouter)
    
    def test_get_router_hybrid(self):
        """Test that get_router returns HybridRouter for 'hybrid' engine."""
        config = TotemConfig(router_high_confidence_threshold=0.85)
        router = get_router("hybrid", config, llm_engine="fake")
        
        assert isinstance(router, HybridRouter)
        assert router.high_conf_threshold == 0.85


class TestProcessCaptureRoutingWithEngine:
    """Tests for process_capture_routing with engine parameter."""
    
    def test_low_confidence_goes_to_review_queue(self, vault_paths):
        """Test that low confidence results are flagged for review."""
        date_str = "2026-01-11"
        date_folder = vault_paths.inbox / date_str
        date_folder.mkdir(parents=True, exist_ok=True)
        
        capture_id = "test-low-confidence"
        raw_file = date_folder / "test_capture.txt"
        meta_file = raw_file.with_suffix(raw_file.suffix + ".meta.json")
        
        # Text with no clear keywords (low confidence)
        raw_file.write_text("The quick brown fox jumps over the lazy dog.")
        
        meta = CaptureMeta(
            id=capture_id,
            created_at="2026-01-11T12:00:00Z",
            source="cli_text",
            type="text",
            files=[raw_file.name],
        )
        meta_file.write_text(meta.model_dump_json(indent=2))
        
        # High threshold to ensure review
        config = TotemConfig(vault_path=vault_paths.root, route_confidence_min=0.9)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        output_path, was_routed = process_capture_routing(
            raw_file_path=raw_file,
            meta_file_path=meta_file,
            vault_root=vault_paths.root,
            config=config,
            ledger_writer=ledger_writer,
            date_str=date_str,
            engine="hybrid",
            llm_engine="fake",
        )
        
        # Should be flagged for review
        assert not was_routed
        assert "review_queue" in str(output_path)
    
    def test_ledger_includes_engine_and_results(self, vault_paths):
        """Test that ledger payload includes engine metadata."""
        date_str = "2026-01-11"
        date_folder = vault_paths.inbox / date_str
        date_folder.mkdir(parents=True, exist_ok=True)
        
        capture_id = "test-ledger-engine"
        raw_file = date_folder / "test_capture.txt"
        meta_file = raw_file.with_suffix(raw_file.suffix + ".meta.json")
        
        # Text with task keywords
        raw_file.write_text("I need to complete this task urgently.")
        
        meta = CaptureMeta(
            id=capture_id,
            created_at="2026-01-11T12:00:00Z",
            source="cli_text",
            type="text",
            files=[raw_file.name],
        )
        meta_file.write_text(meta.model_dump_json(indent=2))
        
        config = TotemConfig(
            vault_path=vault_paths.root,
            route_confidence_min=0.5,
            router_high_confidence_threshold=0.85,
        )
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        # Use hybrid engine
        process_capture_routing(
            raw_file_path=raw_file,
            meta_file_path=meta_file,
            vault_root=vault_paths.root,
            config=config,
            ledger_writer=ledger_writer,
            date_str=date_str,
            engine="hybrid",
            llm_engine="fake",
        )
        
        # Read ledger
        ledger_lines = vault_paths.ledger_file.read_text().strip().split("\n")
        assert len(ledger_lines) >= 1
        
        # Check last event
        last_event = json.loads(ledger_lines[-1])
        assert last_event["event_type"] == "CAPTURE_ROUTED"
        assert last_event["capture_id"] == capture_id
        
        payload = last_event["payload"]
        assert "engine" in payload
        assert payload["engine"] == "hybrid"
        assert "route" in payload
        assert "confidence" in payload
        
        # Hybrid should include rule_result
        assert "rule_result" in payload
        assert "label" in payload["rule_result"]
        assert "confidence" in payload["rule_result"]
        
        # Should include chosen_source
        assert "chosen_source" in payload
        assert payload["chosen_source"] in ("rule", "llm")
    
    def test_rule_engine_ledger_payload(self, vault_paths):
        """Test that rule engine ledger payload includes engine field."""
        date_str = "2026-01-11"
        date_folder = vault_paths.inbox / date_str
        date_folder.mkdir(parents=True, exist_ok=True)
        
        capture_id = "test-rule-ledger"
        raw_file = date_folder / "test_capture.txt"
        meta_file = raw_file.with_suffix(raw_file.suffix + ".meta.json")
        
        raw_file.write_text("I must finish the project today.")
        
        meta = CaptureMeta(
            id=capture_id,
            created_at="2026-01-11T12:00:00Z",
            source="cli_text",
            type="text",
            files=[raw_file.name],
        )
        meta_file.write_text(meta.model_dump_json(indent=2))
        
        config = TotemConfig(vault_path=vault_paths.root, route_confidence_min=0.5)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        # Use rule engine
        process_capture_routing(
            raw_file_path=raw_file,
            meta_file_path=meta_file,
            vault_root=vault_paths.root,
            config=config,
            ledger_writer=ledger_writer,
            date_str=date_str,
            engine="rule",
        )
        
        # Read ledger
        ledger_lines = vault_paths.ledger_file.read_text().strip().split("\n")
        last_event = json.loads(ledger_lines[-1])
        
        payload = last_event["payload"]
        assert payload["engine"] == "rule"
        # Rule engine should not have llm_result or rule_result sub-objects
        assert "llm_result" not in payload
    
    def test_hybrid_high_confidence_short_circuits(self, vault_paths):
        """Test that hybrid short-circuits to rule when confidence is high."""
        date_str = "2026-01-11"
        date_folder = vault_paths.inbox / date_str
        date_folder.mkdir(parents=True, exist_ok=True)
        
        capture_id = "test-hybrid-shortcircuit"
        raw_file = date_folder / "test_capture.txt"
        meta_file = raw_file.with_suffix(raw_file.suffix + ".meta.json")
        
        # Text with many task keywords (high confidence)
        raw_file.write_text(
            "I must complete this task. Need to finish the action item. "
            "Should complete before the deadline."
        )
        
        meta = CaptureMeta(
            id=capture_id,
            created_at="2026-01-11T12:00:00Z",
            source="cli_text",
            type="text",
            files=[raw_file.name],
        )
        meta_file.write_text(meta.model_dump_json(indent=2))
        
        # Set low high_conf threshold to ensure rule is chosen
        config = TotemConfig(
            vault_path=vault_paths.root,
            route_confidence_min=0.5,
            router_high_confidence_threshold=0.70,  # Low threshold
        )
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        process_capture_routing(
            raw_file_path=raw_file,
            meta_file_path=meta_file,
            vault_root=vault_paths.root,
            config=config,
            ledger_writer=ledger_writer,
            date_str=date_str,
            engine="hybrid",
            llm_engine="fake",
        )
        
        # Read ledger
        ledger_lines = vault_paths.ledger_file.read_text().strip().split("\n")
        last_event = json.loads(ledger_lines[-1])
        
        payload = last_event["payload"]
        assert payload["engine"] == "hybrid"
        assert payload["chosen_source"] == "rule"
        # LLM should not have been called (llm_result should be None/absent)
        assert payload.get("llm_result") is None


class TestGetLLMRouter:
    """Tests for get_llm_router factory function."""
    
    def test_get_llm_router_fake(self):
        """Test that get_llm_router('fake') returns FakeLLMRouter."""
        router = get_llm_router("fake")
        
        assert isinstance(router, FakeLLMRouter)
        assert router.engine_name == "fake_llm"
    
    def test_fake_llm_router_engine_name(self):
        """Test FakeLLMRouter engine name."""
        router = FakeLLMRouter()
        
        assert router.engine_name == "fake_llm"
        assert router.provider_model is None


class TestNoShortCircuit:
    """Tests for --no-short-circuit functionality."""
    
    def test_no_short_circuit_always_calls_llm(self):
        """Test that force_llm=True always calls LLM even for high confidence rule."""
        llm_router = FakeLLMRouter()
        # Low threshold that would normally short-circuit
        hybrid = HybridRouter(llm_router=llm_router, high_conf_threshold=0.50)
        
        # Text with many keywords (high rule confidence)
        text = "I must complete the task. Need to finish the action item."
        
        # Without force_llm, should short-circuit to rule
        _ = hybrid.route(text, "test-1")
        metadata_normal = hybrid.get_last_metadata()
        assert metadata_normal.chosen_source == "rule"
        assert metadata_normal.llm_result is None
        
        # With force_llm=True, should always call LLM
        _ = hybrid.route(text, "test-2", force_llm=True)
        metadata_forced = hybrid.get_last_metadata()
        assert metadata_forced.chosen_source == "llm"
        assert metadata_forced.llm_result is not None
        assert metadata_forced.rule_result is not None
    
    def test_no_short_circuit_in_process_routing(self, vault_paths):
        """Test no_short_circuit parameter in process_capture_routing."""
        date_str = "2026-01-11"
        date_folder = vault_paths.inbox / date_str
        date_folder.mkdir(parents=True, exist_ok=True)
        
        capture_id = "test-no-short-circuit"
        raw_file = date_folder / "test_capture.txt"
        meta_file = raw_file.with_suffix(raw_file.suffix + ".meta.json")
        
        # Text with many keywords (normally would short-circuit)
        raw_file.write_text(
            "I must complete the task. Need to finish the action item. "
            "Should complete before the deadline."
        )
        
        meta = CaptureMeta(
            id=capture_id,
            created_at="2026-01-11T12:00:00Z",
            source="cli_text",
            type="text",
            files=[raw_file.name],
        )
        meta_file.write_text(meta.model_dump_json(indent=2))
        
        # Low threshold (would normally short-circuit)
        config = TotemConfig(
            vault_path=vault_paths.root,
            route_confidence_min=0.5,
            router_high_confidence_threshold=0.70,
        )
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        # With no_short_circuit=True
        process_capture_routing(
            raw_file_path=raw_file,
            meta_file_path=meta_file,
            vault_root=vault_paths.root,
            config=config,
            ledger_writer=ledger_writer,
            date_str=date_str,
            engine="hybrid",
            llm_engine="fake",
            no_short_circuit=True,
        )
        
        # Read ledger
        ledger_lines = vault_paths.ledger_file.read_text().strip().split("\n")
        last_event = json.loads(ledger_lines[-1])
        
        payload = last_event["payload"]
        assert payload["engine"] == "hybrid"
        # Should have called LLM despite high rule confidence
        assert payload["chosen_source"] == "llm"
        assert payload["llm_result"] is not None


class TestVersionStamps:
    """Tests for version stamps in ledger payloads."""
    
    def test_ledger_includes_router_version_rule(self, vault_paths):
        """Test that rule engine includes router_version in ledger."""
        date_str = "2026-01-11"
        date_folder = vault_paths.inbox / date_str
        date_folder.mkdir(parents=True, exist_ok=True)
        
        capture_id = "test-version-rule"
        raw_file = date_folder / "test_capture.txt"
        meta_file = raw_file.with_suffix(raw_file.suffix + ".meta.json")
        
        raw_file.write_text("I must finish the project today.")
        
        meta = CaptureMeta(
            id=capture_id,
            created_at="2026-01-11T12:00:00Z",
            source="cli_text",
            type="text",
            files=[raw_file.name],
        )
        meta_file.write_text(meta.model_dump_json(indent=2))
        
        config = TotemConfig(vault_path=vault_paths.root, route_confidence_min=0.5)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        process_capture_routing(
            raw_file_path=raw_file,
            meta_file_path=meta_file,
            vault_root=vault_paths.root,
            config=config,
            ledger_writer=ledger_writer,
            date_str=date_str,
            engine="rule",
        )
        
        # Read ledger
        ledger_lines = vault_paths.ledger_file.read_text().strip().split("\n")
        last_event = json.loads(ledger_lines[-1])
        
        payload = last_event["payload"]
        assert "router_version" in payload
        assert payload["router_version"] == f"rule@{RULE_ROUTER_VERSION}"
        # Rule engine should not have prompt_version
        assert "prompt_version" not in payload
    
    def test_ledger_includes_version_stamps_hybrid(self, vault_paths):
        """Test that hybrid engine includes router_version and prompt_version."""
        date_str = "2026-01-11"
        date_folder = vault_paths.inbox / date_str
        date_folder.mkdir(parents=True, exist_ok=True)
        
        capture_id = "test-version-hybrid"
        raw_file = date_folder / "test_capture.txt"
        meta_file = raw_file.with_suffix(raw_file.suffix + ".meta.json")
        
        # Text that will trigger LLM (low rule confidence)
        raw_file.write_text("This is a simple note.")
        
        meta = CaptureMeta(
            id=capture_id,
            created_at="2026-01-11T12:00:00Z",
            source="cli_text",
            type="text",
            files=[raw_file.name],
        )
        meta_file.write_text(meta.model_dump_json(indent=2))
        
        config = TotemConfig(
            vault_path=vault_paths.root,
            route_confidence_min=0.3,
            router_high_confidence_threshold=0.99,  # High to force LLM
        )
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        process_capture_routing(
            raw_file_path=raw_file,
            meta_file_path=meta_file,
            vault_root=vault_paths.root,
            config=config,
            ledger_writer=ledger_writer,
            date_str=date_str,
            engine="hybrid",
            llm_engine="fake",
        )
        
        # Read ledger
        ledger_lines = vault_paths.ledger_file.read_text().strip().split("\n")
        last_event = json.loads(ledger_lines[-1])
        
        payload = last_event["payload"]
        assert "router_version" in payload
        assert payload["router_version"] == f"hybrid@{HYBRID_ROUTER_VERSION}"
        # Hybrid with LLM should have prompt_version
        assert "prompt_version" in payload
        assert payload["prompt_version"] == ROUTE_PROMPT_VERSION


class TestLLMTraces:
    """Tests for LLM routing trace files."""
    
    def test_fake_llm_router_captures_trace(self):
        """Test that FakeLLMRouter captures trace data."""
        router = FakeLLMRouter()
        
        text = "I need to finish the project today."
        result = router.route(text, "test-capture-id")
        
        trace = router.get_last_trace()
        assert trace is not None
        assert trace.capture_id == "test-capture-id"
        assert trace.model == "fake_llm/deterministic"
        assert trace.prompt is not None
        assert trace.raw_response is not None
        assert trace.parsed_result is not None
        assert trace.parsed_result["route_label"] == result.route_label.value
        assert trace.parse_errors is None
    
    def test_trace_file_created_for_llm_routing(self, vault_paths):
        """Test that trace file is created for LLM routing."""
        date_str = "2026-01-11"
        date_folder = vault_paths.inbox / date_str
        date_folder.mkdir(parents=True, exist_ok=True)
        
        capture_id = "test-trace-llm"
        raw_file = date_folder / "test_capture.txt"
        meta_file = raw_file.with_suffix(raw_file.suffix + ".meta.json")
        
        raw_file.write_text("I need to finish this task.")
        
        meta = CaptureMeta(
            id=capture_id,
            created_at="2026-01-11T12:00:00Z",
            source="cli_text",
            type="text",
            files=[raw_file.name],
        )
        meta_file.write_text(meta.model_dump_json(indent=2))
        
        config = TotemConfig(vault_path=vault_paths.root, route_confidence_min=0.5)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        # Use LLM engine
        process_capture_routing(
            raw_file_path=raw_file,
            meta_file_path=meta_file,
            vault_root=vault_paths.root,
            config=config,
            ledger_writer=ledger_writer,
            date_str=date_str,
            engine="llm",
            llm_engine="fake",
        )
        
        # Check trace file was created (filename includes run_id)
        trace_folder = vault_paths.traces_routing / date_str
        trace_files = list(trace_folder.glob(f"{capture_id}_*.json"))
        assert len(trace_files) == 1
        trace_path = trace_files[0]
        
        # Verify trace content
        trace_data = json.loads(trace_path.read_text())
        assert trace_data["capture_id"] == capture_id
        assert trace_data["run_id"] == ledger_writer.run_id
        assert trace_data["model"] == "fake_llm/deterministic"
        assert "prompt" in trace_data
        assert "raw_response" in trace_data
        assert "parsed_result" in trace_data
        assert trace_data["prompt_version"] == ROUTE_PROMPT_VERSION
        assert trace_data["router_version"] == LLM_ROUTER_VERSION
    
    def test_trace_file_not_created_for_rule_only(self, vault_paths):
        """Test that no trace file is created for rule-only routing."""
        date_str = "2026-01-11"
        date_folder = vault_paths.inbox / date_str
        date_folder.mkdir(parents=True, exist_ok=True)
        
        capture_id = "test-no-trace-rule"
        raw_file = date_folder / "test_capture.txt"
        meta_file = raw_file.with_suffix(raw_file.suffix + ".meta.json")
        
        raw_file.write_text("I need to finish this task.")
        
        meta = CaptureMeta(
            id=capture_id,
            created_at="2026-01-11T12:00:00Z",
            source="cli_text",
            type="text",
            files=[raw_file.name],
        )
        meta_file.write_text(meta.model_dump_json(indent=2))
        
        config = TotemConfig(vault_path=vault_paths.root, route_confidence_min=0.5)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        # Use rule engine
        process_capture_routing(
            raw_file_path=raw_file,
            meta_file_path=meta_file,
            vault_root=vault_paths.root,
            config=config,
            ledger_writer=ledger_writer,
            date_str=date_str,
            engine="rule",
        )
        
        # Check that no trace file was created
        trace_folder = vault_paths.traces_routing / date_str
        if trace_folder.exists():
            trace_files = list(trace_folder.glob(f"{capture_id}*.json"))
            assert len(trace_files) == 0
    
    def test_trace_file_created_for_hybrid_when_llm_called(self, vault_paths):
        """Test that trace file is created for hybrid routing when LLM is called."""
        date_str = "2026-01-11"
        date_folder = vault_paths.inbox / date_str
        date_folder.mkdir(parents=True, exist_ok=True)
        
        capture_id = "test-trace-hybrid"
        raw_file = date_folder / "test_capture.txt"
        meta_file = raw_file.with_suffix(raw_file.suffix + ".meta.json")
        
        # Low confidence text to trigger LLM
        raw_file.write_text("This is a simple note without keywords.")
        
        meta = CaptureMeta(
            id=capture_id,
            created_at="2026-01-11T12:00:00Z",
            source="cli_text",
            type="text",
            files=[raw_file.name],
        )
        meta_file.write_text(meta.model_dump_json(indent=2))
        
        config = TotemConfig(
            vault_path=vault_paths.root,
            route_confidence_min=0.3,
            router_high_confidence_threshold=0.99,  # High to force LLM
        )
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        process_capture_routing(
            raw_file_path=raw_file,
            meta_file_path=meta_file,
            vault_root=vault_paths.root,
            config=config,
            ledger_writer=ledger_writer,
            date_str=date_str,
            engine="hybrid",
            llm_engine="fake",
        )
        
        # Check trace file was created (filename includes run_id)
        trace_folder = vault_paths.traces_routing / date_str
        trace_files = list(trace_folder.glob(f"{capture_id}_*.json"))
        assert len(trace_files) == 1


class TestConfigThreshold:
    """Tests for configuration threshold changes."""
    
    def test_default_high_confidence_threshold_is_090(self):
        """Test that default router_high_confidence_threshold is 0.90."""
        config = TotemConfig()
        assert config.router_high_confidence_threshold == 0.90
