# Totem Daemon Backend Spec (v0.1 — batch workflow)

## Operating assumption (v0.1)

* You interact **once per day** (usually evening).
* You dump files into `00_inbox/YYYY-MM-DD/`.
* You run a command: `totem run` (or `ingest/route/distill` separately).

---

## 1) Stack

* **Python 3.11+**
* **Typer** (CLI)
* **Pydantic v2** (schemas/contracts)
* **pytest** (tests)
* Storage: **markdown + json/jsonl files** (no DB)
* LLM calls: small wrapper (OpenAI SDK or provider-agnostic later)

---

## 2) Vault Layout (as implemented)

```
totem_vault/
  00_inbox/
    YYYY-MM-DD/
      <raw files>
      <raw>.meta.json         # optional; generated if missing
  10_derived/
    transcripts/
    routed/
    distill/
    review_queue/
    corrections/
  20_memory/
    daily/
    entities.json
    principles.md
  30_tasks/
    todo.md
  90_system/
    config.yaml
    ledger.jsonl
    traces/
```

**Invariant:** never modify or delete raw files in `00_inbox/`.

---

## 3) CLI Commands (the whole UX for now)

### `totem init`

Creates:

* vault folder structure
* default `config.yaml`
* empty `ledger.jsonl`
* empty `entities.json`
* empty `todo.md`

### `totem ingest [--date YYYY-MM-DD]`

For each file in the inbox date folder:

* ensure `.meta.json` exists (generate if missing)
* if file is ChatGPT export JSON:

  * create normalized transcript `.md` under `10_derived/transcripts/`
* if file is `.txt/.md`:

  * copy/normalize into derived transcript format
* if audio without transcript:

  * record in meta as transcript missing (no transcription in v0.1)

Writes ledger events:

* `CAPTURE_INGESTED`
* `CAPTURE_META_GENERATED` (if needed)
* `DERIVED_TRANSCRIPT_CREATED` (if applicable)

### `totem route [--date ...]`

For each capture (meta + derived transcript):

* call Router (LLM) → output JSON
* validate via Pydantic
* write routing result to `10_derived/routed/<capture_id>.json`

Ledger:

* `CAPTURE_ROUTED`
* if confidence < `route_confidence_min`: also `FLAGGED_FOR_REVIEW`

### `totem distill [--date ...]`

For that date:

* gather routed captures that are above routing threshold
* feed them to Distill (LLM) to generate:

  * daily summary
  * open loops
  * next actions (max 3)
  * decisions
  * distill confidence
* if distill confidence >= threshold:

  * write/update `20_memory/daily/YYYY-MM-DD.md`
  * update `30_tasks/todo.md` (append actions)
  * update `20_memory/entities.json` (optional in v0.1, gated)
* else:

  * write distill output into `10_derived/review_queue/` (do not update memory/tasks)

Ledger:

* `DISTILL_RUN_STARTED`
* `DISTILL_RESULT_WRITTEN`
* `TASKS_UPDATED` (if applied)
* `MEMORY_PROMOTED` (if applied)
* `FLAGGED_FOR_REVIEW` (if gated)

### `totem review [--date ...]`

Print items in `10_derived/review_queue/` with:

* capture_id
* reason + confidence
* suggested fix instructions

### `totem apply-corrections [--date ...]`

Read `10_derived/corrections/*.md`:

* parse directives
* re-run routing/distill deterministically for affected items
* apply changes (if now above threshold)

Ledger:

* `CORRECTION_APPLIED`

### `totem run [--date ...]`

Convenience command:

* ingest → route → distill → review summary output

---

## 4) Critical Schemas (Pydantic)

### 4.1 `CaptureMeta`

* `id: str`
* `created_at: datetime`
* `source: Literal[...]`
* `type: Literal[...]`
* `files: list[FileRef]`
* `context: dict | None`
* `origin: dict | None`

### 4.2 `LedgerEvent`

* `event_id: str`
* `run_id: str`
* `ts: datetime`
* `event_type: Literal[...]`
* `capture_id: str | None`
* `payload: dict`

Write as JSONL, append-only.

### 4.3 `RouteResult` (LLM output)

* `capture_id: str`
* `route_label: Literal[journal, task, idea, project_update, reference, noise]`
* `confidence: float (0..1)`
* `project: str | None`
* `entities: list[EntityMention]`
* `notes: str | None`

### 4.4 `DistillResult` (LLM output)

* `date: date`
* `summary_bullets: list[str] (max 3)`
* `open_loops: list[str]`
* `decisions: list[str]`
* `next_actions: list[NextAction] (max 3)`
* `confidence: float (0..1)`
* `entity_updates: list[EntityUpdate] | None`

### 4.5 `CorrectionDirective`

Parsed from YAML frontmatter:

* `route: str | None`
* `project: str | None`
* `tasks_add: list[str] | None`
* `tasks_complete: list[str] | None`
* `memory_promote: bool | None`
* `notes: str | None`

---

## 5) “Prompts as APIs” (files in `/prompts`)

* `router.md` → returns RouteResult JSON only
* `distill.md` → returns DistillResult JSON only

Rules:

* must output valid JSON
* no markdown, no commentary
* obey max bullet/action counts

---

## 6) Confidence Gate (Bouncer)

In `config.yaml`:

* `route_confidence_min: 0.70`
* `distill_confidence_min: 0.75`
* `entity_confidence_min: 0.70`

If below:

* write to review queue
* do not mutate curated memory/tasks/entities
* ledger it

---

## 7) Minimal Entity Store (optional v0.1)

File: `20_memory/entities.json`

Only update from distill results above thresholds.
Nodes only:

* Person/Project/Concept/Tool
* `first_seen_at`, `last_seen_at`, `salience`, `source_refs`

No edges yet.

---

# Repo Scaffold (what Cursor should generate)

```
src/totem/
  __init__.py
  cli.py
  config.py
  paths.py
  ledger.py
  ingest.py
  normalize.py
  route.py
  distill.py
  tasks.py
  corrections.py
  models/
    __init__.py
    capture.py
    ledger.py
    routing.py
    distill.py
    corrections.py
  llm/
    __init__.py
    client.py
    prompts.py
tests/
  test_models.py
  test_ledger.py
  test_frontmatter.py
prompts/
  router.md
  distill.md
schemas/
  (optional JSON schema exports)
```

---

## 8) Implementation Order (exact tickets)

1. Vault init + config loader + path resolver
2. Ledger writer (jsonl append + run_id)
3. Meta generation (for arbitrary files)
4. Ingest scanner (date folder)
5. ChatGPT export normalizer → transcript md
6. Router LLM wrapper + Pydantic validation + write routed result
7. Distill LLM wrapper + daily log writer (deterministic sections)
8. Todo updater (append + de-dupe)
9. Review queue writer + CLI `review` output
10. Corrections parser (frontmatter) + apply logic
11. `totem run` convenience command
12. Tests + basic fixtures

---