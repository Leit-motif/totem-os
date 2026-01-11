# Totem OS — Project Constraints

This repository implements Totem OS.

Non-negotiable principles:
- Local-first: no cloud dependencies unless explicitly requested.
- Append-only raw data: never mutate files in 00_inbox/.
- Trust over cleverness: prefer explicit steps, logs, and schemas.
- Restartability: no design should assume continuous daily use.
- Small outputs: summaries <= 3 bullets, actions <= 3 items.
- Determinism over autonomy: no agent should take irreversible actions without user review.

When generating code:
- Prefer boring, explicit Python.
- Use Pydantic for schemas.
- Avoid premature abstractions.
- Ask before introducing new dependencies or frameworks.
