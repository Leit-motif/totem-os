"""Deterministic routing for Totem OS captures.

No LLM calls, no embeddings - only keyword-based heuristics.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .capture import generate_unique_filename
from .config import TotemConfig
from .ledger import LedgerWriter
from .models.capture import CaptureMeta
from .models.routing import ReviewItem, RouteLabel, RouteResult, RoutedItem


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


def process_capture_routing(
    raw_file_path: Path,
    meta_file_path: Path,
    vault_root: Path,
    config: TotemConfig,
    ledger_writer: LedgerWriter,
    date_str: str,
) -> tuple[Path, bool]:
    """Process routing for a single capture with bouncer logic.
    
    Args:
        raw_file_path: Path to raw capture file
        meta_file_path: Path to meta.json file
        vault_root: Vault root directory
        config: Totem configuration with confidence thresholds
        ledger_writer: Ledger writer for appending events
        date_str: Date string (YYYY-MM-DD) for output folder
        
    Returns:
        Tuple of (output_file_path, was_routed)
        where was_routed is True if confidence >= threshold, False if flagged for review
    """
    # Read raw capture text
    raw_text = raw_file_path.read_text(encoding="utf-8")
    
    # Read capture metadata
    meta_data = json.loads(meta_file_path.read_text(encoding="utf-8"))
    meta = CaptureMeta(**meta_data)
    
    # Route the capture
    router = RuleRouter()
    route_result = router.route(raw_text, meta.id)
    
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
        
        # Log to ledger
        ledger_writer.append_event(
            event_type="CAPTURE_ROUTED",
            capture_id=meta.id,
            payload={
                "route": route_result.route_label.value,
                "confidence": route_result.confidence,
                "routed_path": str(output_path.relative_to(vault_root)),
                "next_actions": route_result.next_actions,
                "reasoning": route_result.reasoning,
            }
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
        
        # Log to ledger
        ledger_writer.append_event(
            event_type="CAPTURE_ROUTED",
            capture_id=meta.id,
            payload={
                "route": route_result.route_label.value,
                "confidence": route_result.confidence,
                "review_path": str(output_path.relative_to(vault_root)),
                "next_actions": route_result.next_actions,
                "reasoning": route_result.reasoning,
                "flagged_for_review": True,
                "review_reason": review_reason,
            }
        )
    
    return output_path, was_routed
