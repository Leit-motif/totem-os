# Phase 3: Agent Over Daemon / Memory Reasoning

Phase 3 extends the daemon vault index/search into an evidence-first “ask” loop with:
- deterministic retrieval + packing,
- optional deterministic graph expansion,
- deterministic reranking/filtering interfaces,
- full traceability (every decision and excerpt is logged),
- session continuity (state across interactions),
- time-aware retrieval as a first-class layer,
- a minimal Telegram intake surface wired to sessions.

## Non-negotiables
- **Determinism**: same inputs + unchanged DBs produce identical outputs (ordering, packing, citations, answer).
- **Citations for every excerpt**: `rel_path + start_byte + end_byte` for all quoted/snippet content.
- **No hub explosion** (GraphRAG): aggressive caps, append-only expansion, never reorder primary hits.
- **Config-driven**: defaults are changeable via repo config (`.totem/config.toml`) and/or flags.
- **No hosted UI / no Neo4j / no multi-agent swarm**.

## Track split
Phase 3 is split into two tracks:

### Phase 3A: Evidence-first agent loop (Milestones 1–4)
Build the deterministic ask pipeline atop the daemon index/search DB.

### Phase 3B: Daemon continuity + temporal reasoning + Telegram intake (Milestones 5–7)
Add sessions, temporal reasoning, and Telegram intake without breaking determinism.

## Defaults (decisions locked now)
- `totem daemon ask` returns:
  1) final answer,
  2) citations,
  3) a short **“why these sources”** section (2–4 bullets max).
  Disable via `--quiet` (or config).
- Graph expansion default: **OFF**. Enable via `--graph` (or config). Rationale: tune noise characteristics first.

---

# Phase 3A Milestones

## Milestone 1: Default Retrieval Policy + Context Packing
**Goal**: deterministically retrieve candidates and pack context within strict budgets.

**Key behaviors**
- Deterministic candidate pool creation:
  - stable query normalization,
  - stable `top_k` and per-file caps,
  - stable tie-break rules (no randomness).
- Deterministic context packing:
  - pack excerpts in a stable order,
  - never reorder primary hits due to expansion,
  - enforce byte/char budgets deterministically.
- Every excerpt in packed context has citations (`rel_path`, `start_byte`, `end_byte`).

## Milestone 2: GraphRAG Expansion (deterministic subgraph builder)
**Goal**: expand evidence via deterministic, bounded graph traversal (outlinks/backlinks).

**Key behaviors**
- Default OFF; enable explicitly.
- Append-only expansion; primary hits remain in original order.
- Aggressive caps:
  - max expanded files,
  - max chunks per expanded file,
  - no multi-hop by default (unless explicitly configured).
- Deterministic neighbor selection and representative chunk selection.

## Milestone 3: Reranking / Filtering Stage (deterministic baseline + interface)
**Goal**: insert a stable rerank/filter stage to:
- apply deterministic policies (dedupe, per-file caps, expansion demotion),
- expose a clean interface for time-aware scoring (Phase 3B) and future modules.

## Milestone 4: Agent CLI Loop + Traceability
**Goal**: deliver `totem daemon ask` (retrieve → optional graph → rerank → pack → reason → answer).

**Trace requirements**
- Store traces for every ask, including:
  - query,
  - retrieval config snapshot,
  - candidate pools (top N) and scores,
  - selected excerpts with byte ranges,
  - packed context,
  - final answer + citations,
  - “why these sources”.

---

# Phase 3B Milestones

## Milestone 5: Session Model + Stateful Ask
**Purpose**: introduce continuity of being without breaking determinism.

**Session state (persisted locally)**
- `session_id` (stable)
- `created_at` (ISO)
- `updated_at` (ISO)
- `topic_tags` (optional, derived deterministically)
- `last_n_queries` (query strings + timestamps)
- `last_n_selected_sources` (pointers only: `rel_path` + byte ranges)
- retrieval budget config snapshot (to keep runs reproducible)

**CLI**
- `totem daemon ask "<q>" [--session <id>] [--new-session] [--resume]`
- If no session is provided: create or use a deterministic “current session” pointer.

**Deterministic rule**
- Session influences retrieval only through stored session fields.

**Acceptance**
- Same query in same session with unchanged DBs yields identical packed context, ordering, citations, and answer.
- Same query in two sessions may yield different context; traces must show session deltas.
- All session state reads/writes are included in the trace.

## Milestone 6: Temporal Reasoning Layer (first-class)
**Purpose**: time is a governing signal, not a rerank footnote.

**Implement**
- A time-aware scoring module usable as:
  - pre-filter (time windows),
  - scoring signal (decay curve).
- Configurable windows and decay:
  - windows: last 7d, 30d, 180d, all-time (defaults are changeable),
  - deterministic decay parameters.
- Document time metadata:
  - prefer vault frontmatter dates (if indexed),
  - else fallback to file mtime **as stored in the index DB**.
- Query knob:
  - `--time=recent|month|year|all|hybrid` (or equivalent),
  - default `hybrid` (favor recent but allow evergreen).

**Acceptance**
- “What have I been struggling with lately” prefers recent journal chunks.
- “My long-term values” prefers evergreen/summary notes.
- Deterministic eval set demonstrates ranking changes as expected.
- Trace includes time features for top candidates (timestamp, window, decay score).

## Milestone 7: Telegram Intake Surface (minimal, session-backed)
**Purpose**: phone-first interaction without a hosted UI.

**Transport**
- `TELEGRAM_BOT_TOKEN` from env var.
- Optional allowlist of Telegram `user_id`s in config.

**Bot behavior**
- `/start`: create new session; confirm `session_id`.
- Plain message: call ask pipeline with that session.
- `/new`: start new session.
- `/session <id>`: switch active session.

**Output**
- Answer text
- Citations section (compact)
- Optional “why these sources” bullets (2–4)

**Hosting**
- Two modes:
  1) long polling (default)
  2) webhook (optional; may be deferred)

**Determinism + idempotency**
- Determinism does not depend on Telegram message IDs.
- Messages are processed idempotently across retries/restarts via a persisted dedupe record.
- Telegram traces are identical to CLI traces (session snapshot + citations included).

