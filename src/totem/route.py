"""Routing for Totem OS captures.

Supports multiple routing engines:
- RuleRouter: deterministic keyword-based heuristics
- LLMRouter: LLM-based classification (fake or real)
- HybridRouter: combines rule + LLM with confidence thresholds
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .capture import generate_unique_filename
from .config import TotemConfig
from .ledger import LedgerWriter
from .llm.router import (
    BaseLLMRouter,
    FakeLLMRouter,
    LLM_ROUTER_VERSION,
    LLMRouterTrace,
    ROUTE_PROMPT_VERSION,
    get_llm_router,
    has_llm_api_key,
)
from .paths import VaultPaths
from .models.capture import CaptureMeta
from .models.routing import (
    HybridRouteMetadata,
    ReviewItem,
    RouteLabel,
    RouteResult,
    RoutedItem,
    SubRouteResult,
)

# Version constants - bump when keywords or confidence logic changes
RULE_ROUTER_VERSION = "1.0.0"
HYBRID_ROUTER_VERSION = "1.0.0"


class RuleRouter:
    """Deterministic keyword-based router.
    
    Uses simple keyword matching to classify captures into categories.
    No randomness, no LLM calls - same input always produces same output.
    """
    
    # Keyword mappings for each route category (lowercase for case-insensitive matching)
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
    
    def route(self, text: str, capture_id: str) -> RouteResult:
        """Route a capture based on keyword heuristics.
        
        Args:
            text: Raw capture text content
            capture_id: Capture identifier
            
        Returns:
            RouteResult with label, confidence, and extracted actions
        """
        # Handle empty or very short text
        if not text or len(text.strip()) < 5:
            return RouteResult(
                capture_id=capture_id,
                route_label=RouteLabel.UNKNOWN,
                confidence=0.1,
                next_actions=[],
                reasoning="Text too short or empty for classification"
            )
        
        text_lower = text.lower()
        
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
            # No keywords matched
            return RouteResult(
                capture_id=capture_id,
                route_label=RouteLabel.UNKNOWN,
                confidence=0.3,
                next_actions=[],
                reasoning="No recognizable keywords found"
            )
        
        # Find category with most matches
        best_label = max(match_counts, key=match_counts.get)
        best_count = match_counts[best_label]
        
        # Calculate confidence based on match count and ambiguity
        total_matches = sum(match_counts.values())
        
        # Check for ambiguity (multiple categories with similar counts)
        ambiguous = len([c for c in match_counts.values() if c >= best_count * 0.7]) > 1
        
        # Confidence calculation
        if best_count == 1 and not ambiguous:
            confidence = 0.6  # Single match, no ambiguity
        elif best_count == 1 and ambiguous:
            confidence = 0.5  # Single match, but ambiguous
        elif best_count == 2 and not ambiguous:
            confidence = 0.75  # Two matches, clear winner
        elif best_count == 2 and ambiguous:
            confidence = 0.65  # Two matches, but ambiguous
        elif best_count >= 3 and not ambiguous:
            confidence = 0.9  # Multiple matches, clear category
        elif best_count >= 3 and ambiguous:
            confidence = 0.75  # Multiple matches, but some ambiguity
        else:
            confidence = 0.7  # Default for edge cases
        
        # Build reasoning explanation
        matched_kw_str = ", ".join(matched_keywords[best_label][:3])
        if ambiguous:
            other_categories = [str(label.value) for label in match_counts.keys() if label != best_label]
            reasoning = (
                f"Matched {best_count} keyword(s) for {best_label.value} ({matched_kw_str}), "
                f"but also found matches in: {', '.join(other_categories)}"
            )
        else:
            reasoning = f"Matched {best_count} keyword(s) for {best_label.value} ({matched_kw_str})"
        
        # Extract next actions
        next_actions = self._extract_actions(text)
        
        return RouteResult(
            capture_id=capture_id,
            route_label=best_label,
            confidence=confidence,
            next_actions=next_actions,
            reasoning=reasoning
        )
    
    def _extract_actions(self, text: str) -> list[str]:
        """Extract action items from text.
        
        Args:
            text: Raw capture text
            
        Returns:
            List of extracted actions (max 3)
        """
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


class HybridRouter:
    """Hybrid router combining rule-based and LLM-based routing.
    
    Strategy:
    1. Run RuleRouter first
    2. If rule confidence >= high_conf_threshold, accept rule result
    3. Else call LLMRouter and use its result
    """
    
    def __init__(
        self,
        llm_router: BaseLLMRouter | None = None,
        high_conf_threshold: float = 0.85,
    ):
        """Initialize hybrid router.
        
        Args:
            llm_router: LLM router to use (defaults to auto-detected)
            high_conf_threshold: Confidence threshold for rule short-circuit
        """
        self.rule_router = RuleRouter()
        self.llm_router = llm_router or get_llm_router("auto")
        self.high_conf_threshold = high_conf_threshold
        self._last_metadata: HybridRouteMetadata | None = None
    
    def route(
        self, text: str, capture_id: str, force_llm: bool = False
    ) -> RouteResult:
        """Route a capture using hybrid strategy.
        
        Args:
            text: Raw capture text content
            capture_id: Capture identifier
            force_llm: If True, always call LLM even if rule confidence is high
                       (for A/B testing / --no-short-circuit mode)
            
        Returns:
            RouteResult with label, confidence, and extracted actions
        """
        # Step 1: Run rule-based router
        rule_result = self.rule_router.route(text, capture_id)
        
        # Step 2: Check if rule confidence is high enough to short-circuit
        # (unless force_llm is True)
        if not force_llm and rule_result.confidence >= self.high_conf_threshold:
            # Rule result is confident enough, use it
            self._last_metadata = HybridRouteMetadata(
                engine="hybrid",
                rule_result=SubRouteResult(
                    label=rule_result.route_label.value,
                    confidence=rule_result.confidence,
                ),
                llm_result=None,
                chosen_source="rule",
                provider_model=None,
            )
            return rule_result
        
        # Step 3: Rule confidence is low (or force_llm is True), call LLM router
        llm_result = self.llm_router.route(text, capture_id)
        
        # Store metadata for ledger
        self._last_metadata = HybridRouteMetadata(
            engine="hybrid",
            rule_result=SubRouteResult(
                label=rule_result.route_label.value,
                confidence=rule_result.confidence,
            ),
            llm_result=SubRouteResult(
                label=llm_result.route_label.value,
                confidence=llm_result.confidence,
            ),
            chosen_source="llm",
            provider_model=self.llm_router.provider_model,
        )
        
        return llm_result
    
    def get_last_metadata(self) -> HybridRouteMetadata | None:
        """Get metadata from the last routing decision.
        
        Returns:
            HybridRouteMetadata if route() was called, else None
        """
        return self._last_metadata


def get_router(
    engine: Literal["rule", "llm", "hybrid", "auto"],
    config: TotemConfig,
    llm_engine: str = "auto",
) -> RuleRouter | BaseLLMRouter | HybridRouter:
    """Get appropriate router based on engine setting.
    
    Args:
        engine: 'rule', 'llm', 'hybrid', or 'auto'
                'auto' uses hybrid if API key present, else rule
        config: Totem configuration with thresholds
        llm_engine: LLM engine for hybrid/llm ('fake', 'openai', 'anthropic', 'auto')
    
    Returns:
        Router instance
    """
    if engine == "rule":
        return RuleRouter()
    
    if engine == "llm":
        return get_llm_router(llm_engine)
    
    if engine == "hybrid":
        llm_router = get_llm_router(llm_engine)
        return HybridRouter(
            llm_router=llm_router,
            high_conf_threshold=config.router_high_confidence_threshold,
        )
    
    # Auto mode: hybrid if API key present, else rule
    if engine == "auto":
        if has_llm_api_key():
            llm_router = get_llm_router("auto")
            return HybridRouter(
                llm_router=llm_router,
                high_conf_threshold=config.router_high_confidence_threshold,
            )
        else:
            return RuleRouter()
    
    # Default to rule
    return RuleRouter()


def write_routing_trace(
    trace: LLMRouterTrace,
    vault_paths: VaultPaths,
    date_str: str,
    run_id: str,
) -> Path | None:
    """Write LLM routing trace to trace file for debugging.
    
    Trace files are written to 90_system/traces/routing/YYYY-MM-DD/<capture_id>_<run_id>.json
    
    Args:
        trace: LLMRouterTrace from the router
        vault_paths: VaultPaths instance
        date_str: Date string (YYYY-MM-DD)
        run_id: Run/session identifier from ledger writer
        
    Returns:
        Path to written trace file, or None if no trace
    """
    if trace is None:
        return None
    
    traces_dir = vault_paths.traces_routing_date_folder(date_str)
    traces_dir.mkdir(parents=True, exist_ok=True)
    
    # Use run_id in filename to avoid collisions on repeated routing
    trace_path = traces_dir / f"{trace.capture_id}_{run_id}.json"
    
    # Convert dataclass to dict for JSON
    trace_data = {
        "capture_id": trace.capture_id,
        "run_id": run_id,
        "ts": trace.ts,
        "model": trace.model,
        "prompt": trace.prompt,
        "raw_response": trace.raw_response,
        "parsed_result": trace.parsed_result,
        "parse_errors": trace.parse_errors,
        "prompt_version": ROUTE_PROMPT_VERSION,
        "router_version": LLM_ROUTER_VERSION,
    }
    
    trace_path.write_text(json.dumps(trace_data, indent=2), encoding="utf-8")
    return trace_path


def process_capture_routing(
    raw_file_path: Path,
    meta_file_path: Path,
    vault_root: Path,
    config: TotemConfig,
    ledger_writer: LedgerWriter,
    date_str: str,
    engine: Literal["rule", "llm", "hybrid", "auto"] = "rule",
    llm_engine: str = "auto",
    no_short_circuit: bool = False,
) -> tuple[Path, bool]:
    """Process routing for a single capture with bouncer logic.
    
    Args:
        raw_file_path: Path to raw capture file
        meta_file_path: Path to meta.json file
        vault_root: Vault root directory
        config: Totem configuration with confidence thresholds
        ledger_writer: Ledger writer for appending events
        date_str: Date string (YYYY-MM-DD) for output folder
        engine: Routing engine ('rule', 'llm', 'hybrid', 'auto')
        llm_engine: LLM engine for llm/hybrid ('fake', 'openai', 'anthropic', 'auto')
        no_short_circuit: If True, hybrid mode always calls LLM (for A/B testing)
        
    Returns:
        Tuple of (output_file_path, was_routed)
        where was_routed is True if confidence >= threshold, False if flagged for review
    """
    # Read raw capture text
    raw_text = raw_file_path.read_text(encoding="utf-8")
    
    # Read capture metadata
    meta_data = json.loads(meta_file_path.read_text(encoding="utf-8"))
    meta = CaptureMeta(**meta_data)
    
    # Get the appropriate router
    router = get_router(engine, config, llm_engine)
    
    # Route with force_llm if hybrid and no_short_circuit is True
    if isinstance(router, HybridRouter) and no_short_circuit:
        route_result = router.route(raw_text, meta.id, force_llm=True)
    else:
        route_result = router.route(raw_text, meta.id)
    
    # Write trace file for LLM/hybrid routing (not for pure rule routing)
    vault_paths = VaultPaths(vault_root)
    _write_router_trace(router, vault_paths, date_str, ledger_writer.run_id)
    
    # Build engine metadata for ledger
    engine_metadata = _build_engine_metadata(router, engine)
    
    # Bouncer: check confidence threshold
    threshold = config.route_confidence_min
    was_routed = route_result.confidence >= threshold
    
    # Prepare output paths
    if was_routed:
        output_dir = vault_root / "10_derived" / "routed" / date_str
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Create routed item
        item = RoutedItem(
            capture_id=meta.id,
            routed_at=datetime.now(timezone.utc),
            route_label=route_result.route_label,
            confidence=route_result.confidence,
            next_actions=route_result.next_actions,
            reasoning=route_result.reasoning,
            raw_file_path=str(raw_file_path.relative_to(vault_root)),
            meta_file_path=str(meta_file_path.relative_to(vault_root)),
        )
        
        # Write output with collision avoidance
        base_name = meta.id
        output_path = generate_unique_filename(output_dir, base_name, ".json")
        output_path.write_text(item.model_dump_json(indent=2), encoding="utf-8")
        
        # Log to ledger with engine metadata
        payload = {
            "route": route_result.route_label.value,
            "confidence": route_result.confidence,
            "routed_path": str(output_path.relative_to(vault_root)),
            "next_actions": route_result.next_actions,
            "reasoning": route_result.reasoning,
            **engine_metadata,
        }
        
        ledger_writer.append_event(
            event_type="CAPTURE_ROUTED",
            capture_id=meta.id,
            payload=payload,
        )
    else:
        output_dir = vault_root / "10_derived" / "review_queue" / date_str
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Create review item
        review_reason = (
            f"Confidence {route_result.confidence:.2f} below threshold {threshold:.2f}"
        )
        item = ReviewItem(
            capture_id=meta.id,
            flagged_at=datetime.now(timezone.utc),
            route_label=route_result.route_label,
            confidence=route_result.confidence,
            next_actions=route_result.next_actions,
            reasoning=route_result.reasoning,
            review_reason=review_reason,
            raw_file_path=str(raw_file_path.relative_to(vault_root)),
            meta_file_path=str(meta_file_path.relative_to(vault_root)),
        )
        
        # Write output with collision avoidance
        base_name = meta.id
        output_path = generate_unique_filename(output_dir, base_name, ".json")
        output_path.write_text(item.model_dump_json(indent=2), encoding="utf-8")
        
        # Log to ledger with engine metadata
        payload = {
            "route": route_result.route_label.value,
            "confidence": route_result.confidence,
            "review_path": str(output_path.relative_to(vault_root)),
            "next_actions": route_result.next_actions,
            "reasoning": route_result.reasoning,
            "flagged_for_review": True,
            "review_reason": review_reason,
            **engine_metadata,
        }
        
        ledger_writer.append_event(
            event_type="CAPTURE_ROUTED",
            capture_id=meta.id,
            payload=payload,
        )
    
    return output_path, was_routed


def _build_engine_metadata(
    router: RuleRouter | BaseLLMRouter | HybridRouter,
    engine: str,
) -> dict:
    """Build engine metadata for ledger payload.
    
    Args:
        router: The router instance used
        engine: Engine setting used
        
    Returns:
        Dictionary with engine metadata including version stamps
    """
    metadata: dict = {"engine": engine}
    
    if isinstance(router, HybridRouter):
        # For hybrid router, include detailed metadata
        metadata["router_version"] = f"hybrid@{HYBRID_ROUTER_VERSION}"
        hybrid_meta = router.get_last_metadata()
        if hybrid_meta:
            metadata["engine"] = hybrid_meta.engine
            if hybrid_meta.rule_result:
                metadata["rule_result"] = {
                    "label": hybrid_meta.rule_result.label,
                    "confidence": hybrid_meta.rule_result.confidence,
                }
            if hybrid_meta.llm_result:
                metadata["llm_result"] = {
                    "label": hybrid_meta.llm_result.label,
                    "confidence": hybrid_meta.llm_result.confidence,
                }
                # Include prompt_version when LLM was used
                metadata["prompt_version"] = ROUTE_PROMPT_VERSION
            if hybrid_meta.chosen_source:
                metadata["chosen_source"] = hybrid_meta.chosen_source
            if hybrid_meta.provider_model:
                metadata["provider_model"] = hybrid_meta.provider_model
    
    elif isinstance(router, BaseLLMRouter):
        # For LLM router, include provider info and versions
        metadata["engine"] = "llm"
        metadata["router_version"] = f"llm@{LLM_ROUTER_VERSION}"
        metadata["prompt_version"] = ROUTE_PROMPT_VERSION
        if router.provider_model:
            metadata["provider_model"] = router.provider_model
    
    else:
        # RuleRouter
        metadata["engine"] = "rule"
        metadata["router_version"] = f"rule@{RULE_ROUTER_VERSION}"
    
    return metadata


def _write_router_trace(
    router: RuleRouter | BaseLLMRouter | HybridRouter,
    vault_paths: VaultPaths,
    date_str: str,
    run_id: str,
) -> Path | None:
    """Write trace file if router supports it (LLM/hybrid only).
    
    Args:
        router: The router instance used
        vault_paths: VaultPaths instance
        date_str: Date string (YYYY-MM-DD)
        run_id: Run/session identifier from ledger writer
        
    Returns:
        Path to trace file, or None if no trace written
    """
    trace: LLMRouterTrace | None = None
    
    if isinstance(router, HybridRouter):
        # Get trace from the LLM router inside hybrid
        if hasattr(router.llm_router, 'get_last_trace'):
            trace = router.llm_router.get_last_trace()
    elif isinstance(router, BaseLLMRouter):
        # Direct LLM router
        if hasattr(router, 'get_last_trace'):
            trace = router.get_last_trace()
    # RuleRouter doesn't have traces
    
    if trace is not None:
        return write_routing_trace(trace, vault_paths, date_str, run_id)
    
    return None
