"""Conversation metadata generation for ChatGPT transcripts."""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..config import ChatGptSummaryConfig
from ..ledger import LedgerWriter

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-lite"

TOTEM_KEYS_ORDER = [
    "totem_signpost",
    "totem_summary",
    "totem_themes",
    "totem_open_questions",
    "totem_summary_confidence",
    "totem_input_chars_used",
    "totem_input_chars_total",
    "totem_input_coverage_ratio",
    "totem_input_selection_strategy",
    "totem_meta_provider",
    "totem_meta_model",
    "totem_meta_version",
    "totem_meta_created_at",
]

TOTEM_KEYS = set(TOTEM_KEYS_ORDER)

SALIENT_SUBSTRINGS = [
    "i realized",
    "the point is",
    "what i'm saying",
    "decision",
    "so i'm going to",
    "next",
    "action",
    "summary",
]


@dataclass
class MetadataResult:
    status: str  # generated | skipped | failed
    reason: str | None = None
    provider: str | None = None
    model: str | None = None
    coverage_ratio: float | None = None


def ensure_conversation_metadata(
    note_path: Path,
    summary_config: ChatGptSummaryConfig,
    ledger_writer: LedgerWriter,
    dry_run: bool = False,
) -> MetadataResult:
    """Ensure metadata exists for a conversation note, generating if needed."""
    try:
        if not summary_config.enabled:
            return _log_skip(
                ledger_writer,
                note_path,
                reason="disabled",
            )

        try:
            note_text = note_path.read_text(encoding="utf-8")
        except OSError as exc:
            return _log_failed(ledger_writer, note_path, f"read_failed: {exc}")

        frontmatter_lines, body_text, has_frontmatter = split_frontmatter(note_text)
        frontmatter_data = parse_frontmatter(frontmatter_lines)

        if _is_metadata_up_to_date(frontmatter_data, summary_config.version):
            return _log_skip(ledger_writer, note_path, reason="up_to_date")

        if dry_run:
            return _log_skip(ledger_writer, note_path, reason="dry_run")

        provider_info = select_provider(summary_config)
        if provider_info is None:
            logger.warning("ChatGPT metadata skipped: no API key available")
            return _log_skip(ledger_writer, note_path, reason="no_api_key")

        provider, model, api_key = provider_info

        transcript_text = extract_transcript_text(body_text)
        total_chars = len(transcript_text)
        if total_chars == 0:
            return _log_skip(ledger_writer, note_path, reason="empty_transcript")

        input_text, used_chars, confidence = build_salience_input(
            transcript_text,
            summary_config.max_input_chars,
        )
        coverage_ratio = round((used_chars / total_chars), 4) if total_chars else 0.0

        prompt = build_metadata_prompt(
            transcript_input=input_text,
            total_chars=total_chars,
            input_chars_used=used_chars,
            coverage_ratio=coverage_ratio,
            confidence=confidence,
        )

        try:
            raw_response = _call_with_retries(
                provider=provider,
                model=model,
                api_key=api_key,
                prompt=prompt,
                temperature=summary_config.temperature,
                timeout_seconds=summary_config.timeout_seconds,
            )
        except Exception as exc:
            return _log_failed(ledger_writer, note_path, f"llm_call_failed: {exc}")

        parsed = parse_llm_json(raw_response)
        if parsed is None:
            preview = _make_preview(raw_response)
            return _log_failed(ledger_writer, note_path, f"json_parse_failed: {preview}")

        metadata_payload = normalize_metadata_payload(parsed)
        if metadata_payload is None:
            return _log_failed(ledger_writer, note_path, "json_validation_failed")

        metadata_payload.update(
            {
                "totem_summary_confidence": confidence,
                "totem_input_chars_used": used_chars,
                "totem_input_chars_total": total_chars,
                "totem_input_coverage_ratio": coverage_ratio,
                "totem_input_selection_strategy": "salience",
                "totem_meta_provider": provider,
                "totem_meta_model": model,
                "totem_meta_version": summary_config.version,
                "totem_meta_created_at": datetime.now(timezone.utc).isoformat(),
            }
        )

        updated_text = update_frontmatter(
            note_text=note_text,
            frontmatter_lines=frontmatter_lines if has_frontmatter else None,
            body_text=body_text,
            updates=metadata_payload,
        )

        try:
            note_path.write_text(updated_text, encoding="utf-8")
        except OSError as exc:
            return _log_failed(ledger_writer, note_path, f"write_failed: {exc}")

        ledger_writer.append_event(
            event_type="CHATGPT_METADATA_GENERATED",
            payload={
                "note_path": str(note_path),
                "provider": provider,
                "model": model,
                "coverage_ratio": coverage_ratio,
                "input_chars_used": used_chars,
                "input_chars_total": total_chars,
                "summary_confidence": confidence,
            },
        )

        return MetadataResult(
            status="generated",
            provider=provider,
            model=model,
            coverage_ratio=coverage_ratio,
        )
    except Exception as exc:
        return _log_failed(ledger_writer, note_path, f"unexpected_error: {exc}")


def build_metadata_prompt(
    transcript_input: str,
    total_chars: int,
    input_chars_used: int,
    coverage_ratio: float,
    confidence: str,
) -> str:
    """Build the metadata prompt text for the LLM."""
    system_rules = (
        "You generate structured metadata for a ChatGPT conversation transcript excerpt.\n"
        "Output JSON only. No markdown. No em dashes. No disclaimers. Avoid the phrase \"this conversation discusses\".\n"
        "Use concrete language only and do not invent details.\n"
        "signpost: exactly one sentence, present tense, orienting cue (max 140 chars).\n"
        "summary: 3 sentences max, each <= 25 words, total <= 400 chars. Focus on core insights, tensions, or decisions.\n"
        "themes: 3 to 5 short noun phrases.\n"
        "open_questions: 2 to 3 questions.\n"
        "If coverage is partial, use cautious language without explicit disclaimers.\n"
        "End the response with a single '}' and nothing else. Keep output under 700 characters."
    )
    coverage_block = (
        f"COVERAGE\n"
        f"total_chars: {total_chars}\n"
        f"input_chars_used: {input_chars_used}\n"
        f"coverage_ratio: {coverage_ratio}\n"
        f"coverage: {confidence}\n"
    )
    return f"{system_rules}\n\n{coverage_block}\nTRANSCRIPT INPUT\n{transcript_input}"


def call_metadata_llm(
    provider: str,
    model: str,
    api_key: str,
    prompt: str,
    temperature: float,
    timeout_seconds: int,
) -> str:
    """Call the selected provider with a prompt and return raw text."""
    if provider == "openai":
        return _call_openai_metadata(model, api_key, prompt, temperature, timeout_seconds)
    if provider == "gemini":
        return _call_gemini_metadata(model, api_key, prompt, temperature, timeout_seconds)
    raise ValueError(f"Unsupported provider: {provider}")


def _call_with_retries(
    provider: str,
    model: str,
    api_key: str,
    prompt: str,
    temperature: float,
    timeout_seconds: int,
    max_attempts: int = 4,
) -> str:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return call_metadata_llm(
                provider=provider,
                model=model,
                api_key=api_key,
                prompt=prompt,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            last_error = exc
            retry_seconds = _parse_retry_seconds(str(exc))
            if retry_seconds is None:
                break
            if attempt == max_attempts:
                break
            time.sleep(retry_seconds)
    if last_error:
        raise last_error
    raise ValueError("llm_call_failed")


def _call_openai_metadata(
    model: str,
    api_key: str,
    prompt: str,
    temperature: float,
    timeout_seconds: int,
) -> str:
    url = "https://api.openai.com/v1/chat/completions"
    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": 700,
        "messages": [
            {"role": "system", "content": "You are a careful summarizer that outputs JSON only."},
            {"role": "user", "content": prompt},
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    response = _post_json(url, headers, payload, timeout_seconds)
    try:
        return response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"Unexpected OpenAI response: {exc}") from exc


def _call_gemini_metadata(
    model: str,
    api_key: str,
    prompt: str,
    temperature: float,
    timeout_seconds: int,
) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": prompt}]}
        ],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": 1024,
            "responseMimeType": "application/json",
        },
    }
    headers = {"Content-Type": "application/json"}
    response = _post_json(url, headers, payload, timeout_seconds)
    try:
        return _extract_gemini_text(response)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError(f"Unexpected Gemini response: {exc}") from exc


def _post_json(url: str, headers: dict, payload: dict, timeout_seconds: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, context=context, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise ValueError(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Connection error: {exc}") from exc


def _extract_gemini_text(response: dict) -> str:
    if not isinstance(response, dict):
        raise ValueError("response_not_dict")
    candidates = response.get("candidates") or []
    for candidate in candidates:
        content = candidate.get("content") if isinstance(candidate, dict) else None
        if not isinstance(content, dict):
            continue
        parts = content.get("parts") or []
        for part in parts:
            if isinstance(part, dict) and "text" in part:
                return str(part["text"])
        if "text" in content:
            return str(content["text"])
    if "text" in response:
        return str(response["text"])
    raise ValueError("no_text_in_response")


def parse_llm_json(raw_text: str) -> dict | None:
    """Parse JSON output with a single cleanup retry."""
    cleaned = (raw_text or "").strip().lstrip("\ufeff")
    if not cleaned:
        return None

    for candidate in _iter_json_candidates(cleaned):
        parsed = _try_parse_json(candidate)
        if isinstance(parsed, dict):
            return parsed
    return None


def _iter_json_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    candidates.append(text)
    cleaned = strip_code_fences(text)
    if cleaned not in candidates:
        candidates.append(cleaned)
    extracted = extract_json_object(cleaned)
    if extracted and extracted not in candidates:
        candidates.append(extracted)
    return candidates


def _try_parse_json(text: str) -> dict | None:
    try:
        parsed = json.loads(text)
        if isinstance(parsed, str):
            return _try_parse_json(parsed)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    repaired = _repair_braces(text)
    if repaired != text:
        try:
            parsed = json.loads(repaired)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass

    normalized = _normalize_json_like(text)
    if normalized != text:
        try:
            parsed = json.loads(normalized)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass

    try:
        parsed = ast.literal_eval(text)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, SyntaxError):
        return None


def _repair_braces(text: str) -> str:
    if not text.lstrip().startswith("{"):
        return text
    open_count = text.count("{")
    close_count = text.count("}")
    if open_count <= close_count:
        return text
    return text + ("}" * (open_count - close_count))


def _normalize_json_like(text: str) -> str:
    normalized = text.strip().lstrip("\ufeff")
    normalized = normalized.replace("\u201c", "\"").replace("\u201d", "\"")
    normalized = normalized.replace("\u2018", "'").replace("\u2019", "'")
    normalized = re.sub(r",\s*([}\]])", r"\1", normalized)
    return normalized


def _parse_retry_seconds(message: str) -> float | None:
    text = message or ""
    match = re.search(r"retryDelay\"\\s*:\\s*\"(\\d+)s\"", text)
    if match:
        return float(match.group(1))
    match = re.search(r"Please retry in\\s+([0-9.]+)s", text)
    if match:
        return float(match.group(1))
    if "HTTP 429" in text:
        return 20.0
    return None


def strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].startswith("```"):
            text = "\n".join(lines[1:-1])
    return text.strip()


def extract_json_object(text: str) -> str | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


def normalize_metadata_payload(data: dict) -> dict | None:
    required = ["signpost", "summary", "themes", "open_questions"]
    if not all(key in data for key in required):
        return None

    signpost = _clean_sentence(data.get("signpost"))
    summary = _clean_sentence(data.get("summary"))

    themes = data.get("themes")
    if not isinstance(themes, list):
        return None
    themes = [str(item).strip() for item in themes if str(item).strip()]
    if len(themes) > 7:
        themes = themes[:7]

    open_questions = data.get("open_questions")
    if not isinstance(open_questions, list):
        return None
    open_questions = [str(item).strip() for item in open_questions if str(item).strip()]
    if len(open_questions) > 5:
        open_questions = open_questions[:5]

    if not signpost or not summary:
        return None

    return {
        "totem_signpost": signpost,
        "totem_summary": summary,
        "totem_themes": themes,
        "totem_open_questions": open_questions,
    }


def _clean_sentence(text: Any) -> str:
    if text is None:
        return ""
    cleaned = str(text).strip().replace("\n", " ").replace("\r", " ")
    cleaned = cleaned.replace("\u2014", "-").replace("\u2013", "-")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def select_provider(summary_config: ChatGptSummaryConfig) -> tuple[str, str, str] | None:
    provider = summary_config.provider
    model_override = summary_config.model

    if provider == "auto":
        if os.environ.get("GEMINI_API_KEY"):
            return ("gemini", model_override or DEFAULT_GEMINI_MODEL, os.environ["GEMINI_API_KEY"])
        if os.environ.get("OPENAI_API_KEY"):
            return ("openai", model_override or DEFAULT_OPENAI_MODEL, os.environ["OPENAI_API_KEY"])
        return None

    if provider == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return None
        return ("gemini", model_override or DEFAULT_GEMINI_MODEL, api_key)

    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None
        return ("openai", model_override or DEFAULT_OPENAI_MODEL, api_key)

    raise ValueError(f"Unsupported provider setting: {provider}")


def split_frontmatter(note_text: str) -> tuple[list[str], str, bool]:
    """Split frontmatter lines and body text."""
    if not note_text.startswith("---"):
        return ([], note_text, False)

    lines = note_text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return ([], note_text, False)

    pos = len(lines[0])
    front_lines: list[str] = []
    for idx in range(1, len(lines)):
        line = lines[idx]
        if line.strip() == "---":
            body_text = note_text[pos + len(line) :]
            front_text = "".join(front_lines)
            frontmatter_lines = front_text.splitlines()
            return (frontmatter_lines, body_text, True)
        front_lines.append(line)
        pos += len(line)

    return ([], note_text, False)


def parse_frontmatter(frontmatter_lines: list[str]) -> dict:
    """Parse frontmatter into a simple dict for known keys."""
    data: dict[str, Any] = {}
    i = 0
    while i < len(frontmatter_lines):
        line = frontmatter_lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        match = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if not match:
            i += 1
            continue
        key = match.group(1)
        rest = match.group(2).strip()
        if rest == "":
            items: list[str] = []
            j = i + 1
            while j < len(frontmatter_lines):
                next_line = frontmatter_lines[j]
                if re.match(r"^[A-Za-z0-9_]+:", next_line):
                    break
                item_match = re.match(r"^\s*-\s*(.*)$", next_line)
                if item_match:
                    items.append(_strip_quotes(item_match.group(1).strip()))
                j += 1
            data[key] = items
            i = j
            continue
        data[key] = _parse_scalar(rest)
        i += 1
    return data


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        items = [item.strip() for item in inner.split(",") if item.strip()]
        return [_strip_quotes(item) for item in items]
    value = _strip_quotes(value)
    if value.isdigit():
        return int(value)
    try:
        if re.match(r"^-?\d+\.\d+$", value):
            return float(value)
    except ValueError:
        pass
    return value


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        unquoted = value[1:-1]
        unquoted = unquoted.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
        return unquoted
    return value


def update_frontmatter(
    note_text: str,
    frontmatter_lines: list[str] | None,
    body_text: str,
    updates: dict,
) -> str:
    """Update frontmatter with new metadata, preserving unrelated keys."""
    existing_lines = frontmatter_lines or []
    cleaned_lines = remove_frontmatter_keys(existing_lines, TOTEM_KEYS)
    updated_lines = cleaned_lines + format_updates(updates)
    front_block = "---\n" + "\n".join(updated_lines) + "\n---\n"
    return front_block + body_text


def remove_frontmatter_keys(lines: list[str], keys: set[str]) -> list[str]:
    """Remove existing lines for specified keys, including multiline blocks."""
    cleaned: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = re.match(r"^([A-Za-z0-9_]+):", line)
        if match and match.group(1) in keys:
            i += 1
            while i < len(lines):
                next_line = lines[i]
                if re.match(r"^[A-Za-z0-9_]+:", next_line):
                    break
                i += 1
            continue
        cleaned.append(line)
        i += 1
    return cleaned


def format_updates(updates: dict) -> list[str]:
    """Format metadata updates in canonical key order."""
    lines: list[str] = []
    for key in TOTEM_KEYS_ORDER:
        if key not in updates:
            continue
        value = updates[key]
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {format_yaml_string(item)}")
        else:
            lines.append(f"{key}: {format_yaml_value(value)}")
    return lines


def format_yaml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return format_yaml_string(value)


def format_yaml_string(value: Any) -> str:
    text = str(value).replace("\n", " ").replace("\r", " ")
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    return f"\"{text}\""


def _is_metadata_up_to_date(frontmatter_data: dict, version: int) -> bool:
    return (
        frontmatter_data.get("totem_meta_version") == version
        and bool(frontmatter_data.get("totem_signpost"))
        and bool(frontmatter_data.get("totem_summary"))
    )


def extract_transcript_text(body_text: str) -> str:
    """Extract transcript text from the note body."""
    lines = body_text.splitlines(keepends=True)
    pos = 0
    for line in lines:
        if line.strip() == "## Transcript":
            start = pos + len(line)
            transcript = body_text[start:]
            return transcript.lstrip("\n")
        pos += len(line)
    return body_text


def build_salience_input(
    transcript_text: str,
    max_input_chars: int,
) -> tuple[str, int, str]:
    """Build salience input from transcript text."""
    total_chars = len(transcript_text)
    if total_chars <= max_input_chars:
        return (transcript_text, total_chars, "full")

    first_len = max(1, int(max_input_chars * 0.25))
    last_len = max(1, int(max_input_chars * 0.25))
    if first_len + last_len > max_input_chars:
        first_len = max_input_chars // 2
        last_len = max_input_chars - first_len

    first_span = (0, min(first_len, total_chars))
    last_span = (max(total_chars - last_len, 0), total_chars)

    mandatory = [first_span]
    if last_span[0] > first_span[1]:
        mandatory.append(last_span)
    else:
        mandatory[0] = (0, max(first_span[1], last_span[1]))

    salient_spans = [
        (start, end)
        for start, end, line in iter_line_spans(transcript_text)
        if line_matches_salience(line)
    ]

    candidates = [
        span for span in salient_spans if not any(_overlaps(span, m) for m in mandatory)
    ]

    sep_len = len("\n\n")
    mandatory_len = sum(end - start for start, end in mandatory)
    mandatory_len += sep_len * (len(mandatory) - 1) if len(mandatory) > 1 else 0
    remaining = max_input_chars - mandatory_len

    selected: list[tuple[int, int]] = []
    for span in candidates:
        span_len = span[1] - span[0]
        cost = span_len + sep_len
        if cost <= remaining:
            selected.append(span)
            remaining -= cost

    spans = sorted(mandatory + selected, key=lambda item: item[0])
    segments = [transcript_text[start:end] for start, end in spans]
    salience_text = "\n\n".join(segments)

    if len(salience_text) > max_input_chars:
        salience_text = salience_text[:max_input_chars]

    return (salience_text, len(salience_text), "partial")


def iter_line_spans(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    start = 0
    for line in text.splitlines(keepends=True):
        end = start + len(line)
        spans.append((start, end, line))
        start = end
    return spans


def line_matches_salience(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    normalized = stripped.replace("\u2019", "'")
    lower = normalized.lower()
    if "?" in stripped:
        return True
    if stripped.startswith(("-", "*", "\u2022")):
        return True
    return any(token in lower for token in SALIENT_SUBSTRINGS)


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def read_metadata_fields(note_path: Path) -> dict[str, Any]:
    """Read metadata fields used for daily note rendering."""
    try:
        note_text = note_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    frontmatter_lines, _body_text, _has_frontmatter = split_frontmatter(note_text)
    data = parse_frontmatter(frontmatter_lines)
    open_questions = data.get("totem_open_questions")
    if not isinstance(open_questions, list):
        open_questions = []
    return {
        "totem_signpost": data.get("totem_signpost"),
        "totem_summary_confidence": data.get("totem_summary_confidence"),
        "totem_open_questions": open_questions,
    }


def backfill_conversation_metadata(
    obsidian_chatgpt_dir: Path,
    summary_config: ChatGptSummaryConfig,
    ledger_writer: LedgerWriter,
    limit: int | None = None,
    dry_run: bool = False,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict:
    """Backfill metadata for existing conversation notes."""
    if not summary_config.enabled:
        ledger_writer.append_event(
            event_type="CHATGPT_METADATA_BACKFILL_PROGRESS",
            payload={
                "status": "disabled",
                "processed": 0,
                "generated": 0,
                "skipped": 0,
                "failed": 0,
                "total": 0,
                "dry_run": dry_run,
            },
        )
        return {"processed": 0, "generated": 0, "skipped": 0, "failed": 0, "total": 0}

    if not summary_config.backfill_enabled:
        ledger_writer.append_event(
            event_type="CHATGPT_METADATA_BACKFILL_PROGRESS",
            payload={
                "status": "disabled",
                "processed": 0,
                "generated": 0,
                "skipped": 0,
                "failed": 0,
                "total": 0,
                "dry_run": dry_run,
            },
        )
        return {"processed": 0, "generated": 0, "skipped": 0, "failed": 0, "total": 0}

    if not obsidian_chatgpt_dir.exists():
        ledger_writer.append_event(
            event_type="CHATGPT_METADATA_BACKFILL_PROGRESS",
            payload={
                "status": "missing_dir",
                "processed": 0,
                "generated": 0,
                "skipped": 0,
                "failed": 0,
                "total": 0,
                "dry_run": dry_run,
            },
        )
        return {"processed": 0, "generated": 0, "skipped": 0, "failed": 0, "total": 0}

    note_paths = sorted(obsidian_chatgpt_dir.rglob("*.md"))
    effective_limit = limit if limit is not None else summary_config.backfill_limit
    if effective_limit is not None:
        note_paths = note_paths[:effective_limit]

    total = len(note_paths)
    batch_size = max(1, summary_config.backfill_batch_size)
    sleep_seconds = summary_config.backfill_sleep_ms / 1000.0 if summary_config.backfill_sleep_ms else 0.0

    processed = generated = skipped = failed = 0

    ledger_writer.append_event(
        event_type="CHATGPT_METADATA_BACKFILL_PROGRESS",
        payload={
            "status": "started",
            "processed": 0,
            "generated": 0,
            "skipped": 0,
            "failed": 0,
            "total": total,
            "dry_run": dry_run,
            "batch_size": batch_size,
        },
    )

    for idx, note_path in enumerate(note_paths, start=1):
        result = ensure_conversation_metadata(
            note_path=note_path,
            summary_config=summary_config,
            ledger_writer=ledger_writer,
            dry_run=dry_run,
        )
        processed += 1
        if result.status == "generated":
            generated += 1
        elif result.status == "skipped":
            skipped += 1
        else:
            failed += 1

        if progress_callback:
            progress_callback(processed, total, result.status)

        if processed % batch_size == 0 or idx == total:
            ledger_writer.append_event(
                event_type="CHATGPT_METADATA_BACKFILL_PROGRESS",
                payload={
                    "status": "progress",
                    "processed": processed,
                    "generated": generated,
                    "skipped": skipped,
                    "failed": failed,
                    "total": total,
                    "dry_run": dry_run,
                },
            )
            if sleep_seconds and idx != total:
                time.sleep(sleep_seconds)

    ledger_writer.append_event(
        event_type="CHATGPT_METADATA_BACKFILL_PROGRESS",
        payload={
            "status": "completed",
            "processed": processed,
            "generated": generated,
            "skipped": skipped,
            "failed": failed,
            "total": total,
            "dry_run": dry_run,
        },
    )

    return {
        "processed": processed,
        "generated": generated,
        "skipped": skipped,
        "failed": failed,
        "total": total,
    }


def _log_skip(
    ledger_writer: LedgerWriter,
    note_path: Path,
    reason: str,
) -> MetadataResult:
    ledger_writer.append_event(
        event_type="CHATGPT_METADATA_SKIPPED",
        payload={
            "note_path": str(note_path),
            "reason": reason,
        },
    )
    return MetadataResult(status="skipped", reason=reason)


def _log_failed(
    ledger_writer: LedgerWriter,
    note_path: Path,
    reason: str,
) -> MetadataResult:
    ledger_writer.append_event(
        event_type="CHATGPT_METADATA_FAILED",
        payload={
            "note_path": str(note_path),
            "error": reason,
        },
    )
    return MetadataResult(status="failed", reason=reason)


def _make_preview(text: str, limit: int = 800) -> str:
    cleaned = (text or "").replace("\n", " ").replace("\r", " ").strip()
    if len(cleaned) > limit:
        return cleaned[:limit] + "..."
    return cleaned
