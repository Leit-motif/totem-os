Milestone 8 (Updated): ChatGPT Local ZIP Ingestion

Summary
This milestone focuses on ingesting ChatGPT export ZIPs from local files only.
The Gmail export flow has been removed to avoid 403/auth issues and confusion.

Supported workflows
1) Ingest the most recent ZIP from Downloads:
   - `totem chatgpt ingest-from-downloads`

2) Ingest a specific ZIP path:
   - `totem chatgpt ingest-from-zip /path/to/export.zip`

Notes
- Idempotency is enforced by content hashes and stable filenames.
- The ingestion manifest records each local ZIP ingest run.
- No Gmail credentials or launchd configuration is required.
