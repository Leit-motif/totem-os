# Milestone 8: ChatGPT Export Ingestion

## Overview

Milestone 8 implements automated ingestion of ChatGPT conversation exports from Gmail. The system finds the latest unprocessed export email, downloads the conversation data, parses it, and writes organized Obsidian notes.

## Features

- **Automated Gmail Integration**: OAuth-authenticated Gmail API access
- **Smart Email Parsing**: Extracts download URLs from ChatGPT export emails
- **Robust Data Processing**: Handles various ChatGPT export formats
- **Obsidian Integration**: Creates conversation notes and updates daily notes
- **Idempotent Processing**: Safe to run repeatedly without duplicates
- **State Management**: Tracks processed emails and handles failures
- **Scheduling**: macOS launchd integration for automated execution

## Manual Execution

### Prerequisites

1. **Gmail API Setup**:
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project or select existing
   - Enable Gmail API
   - Create OAuth 2.0 credentials (Desktop application)
   - Download the credentials JSON file

2. **Totem Vault Setup**:
   ```bash
   totem init
   ```

3. **Gmail Credentials**:
   - Place the downloaded `credentials.json` in `~/.config/totem/gmail_credentials.json`
   - On first run, you'll be prompted to authenticate in a browser

4. **Obsidian Vault**:
   - Ensure your Obsidian vault exists at the configured path
   - Default: `/Users/amrit/Workspaces/Totem OS/totem/`

### Configuration

The system uses these configuration settings (can be overridden in `config.yaml`):

```yaml
chatgpt_export:
  gmail_query: 'newer_than:14d from:noreply@tm.openai.com subject:"Your data export is ready"'
  max_results: 10
  state_file: 'state/chatgpt_export_ingest_state.json'
  staging_dir: 'state/chatgpt_exports'
  obsidian_chatgpt_dir: '/Users/amrit/Workspaces/Totem OS/totem/chatgpt/conversations'
  obsidian_daily_dir: '/Users/amrit/Workspaces/Totem OS/totem/daily'
  timezone: 'America/Chicago'

launchd:
  label: 'com.totem.chatgpt.export.ingest'
  interval_seconds: 21600  # 6 hours
```

### Running the Pipeline

**Basic execution (Gmail ingestion)**:
```bash
totem chatgpt ingest-latest-export
```

**With debug logging**:
```bash
totem chatgpt ingest-latest-export --debug
```

**Dry run (preview what would happen)**:
```bash
totem chatgpt ingest-latest-export --dry-run
```

**Override Gmail query lookback**:
```bash
totem chatgpt ingest-latest-export --lookback-days 30
```

### Local ZIP Ingest (Recommended)

If the export download URL requires browser authentication (403 via requests), use the local ingest workflow:

1) Click the export email button in Chrome to download the ZIP to `~/Downloads`.
2) Run:
```bash
totem chatgpt ingest-from-downloads
```

**Ingest a specific ZIP file**:
```bash
totem chatgpt ingest-from-zip /path/to/export.zip
```

### Diagnostics

**Check setup**:
```bash
totem chatgpt doctor
```

This validates:
- Configuration completeness
- Directory permissions
- Gmail authentication
- Obsidian vault accessibility

## Automated Scheduling (macOS)

### One-time Setup

1. **Install the launch agent**:
   ```bash
   totem chatgpt install-launchd
   ```

2. **Load the agent** (as printed by the command):
   ```bash
   launchctl load -w ~/Library/LaunchAgents/com.totem.chatgpt.export.ingest.plist
   ```

### Management

**Check status**:
```bash
launchctl list | grep chatgpt
```

**View logs**:
```bash
tail -f ~/Library/Logs/totem_chatgpt_stdout.log
tail -f ~/Library/Logs/totem_chatgpt_stderr.log
```

**Stop the agent**:
```bash
launchctl unload -w ~/Library/LaunchAgents/com.totem.chatgpt.export.ingest.plist
```

**Restart after config changes**:
```bash
launchctl unload -w ~/Library/LaunchAgents/com.totem.chatgpt.export.ingest.plist
totem chatgpt install-launchd  # Regenerates plist
launchctl load -w ~/Library/LaunchAgents/com.totem.chatgpt.export.ingest.plist
```

## Output Structure

### Conversation Notes

Created in: `{obsidian_chatgpt_dir}/{YYYY-MM-DD}/chatgpt__{conversation_id}.md`

Example:
```
---
source: chatgpt_export
conversation_id: abc123
title: "My ChatGPT Conversation"
created_at: 2022-01-01T12:00:00
updated_at: 2022-01-01T12:05:00
ingested_from: gmail:msg_456
content_hash: a1b2c3...
---

# My ChatGPT Conversation

## User (12:00)

Hello world

## Assistant (12:01)

Hi there! How can I help you today?
```

### Daily Notes

Updated in: `{obsidian_daily_dir}/{YYYY-MM-DD}.md`

Adds a block like:
```markdown
<!-- TOTEM:CHATGPT:START -->
## ChatGPT
- [[../chatgpt/2022-01-01/chatgpt__abc123|My ChatGPT Conversation]] (12:00)
<!-- TOTEM:CHATGPT:END -->
```

## State Management

State is tracked in: `{vault_path}/state/chatgpt_export_ingest_state.json`

```json
{
  "processed_message_ids": ["msg_123", "msg_456"],
  "last_success_at": "2022-01-01T12:00:00Z",
  "last_attempt_at": "2022-01-01T12:00:00Z",
  "last_error": null
}
```

### Resetting State

To reprocess emails (e.g., after fixing parsing issues):

```bash
# Edit the state file to remove specific message IDs
# Or delete the file entirely to start fresh
rm state/chatgpt_export_ingest_state.json
```

## Troubleshooting

### Gmail Authentication Issues

**"Gmail credentials not found"**:
- Ensure `~/.config/totem/gmail_credentials.json` exists
- Download fresh credentials from Google Cloud Console

**"Gmail authentication failed"**:
```bash
totem chatgpt doctor
# Check the Gmail authentication section
```

### Email Processing Issues

**"No download URL found in email"**:
- Check that the email contains a ChatGPT export download link
- The system looks for URLs containing "download", "export", or "data"
- Use `--debug` to see all URLs found in the email

**"Invalid ZIP file"**:
- Ensure the download link points to a valid ZIP file
- Check that the file starts with "PK" (ZIP header)

### Parsing Issues

**"No conversations JSON file found"**:
- Verify the export ZIP contains conversation data
- The system looks for files with conversation-related keywords

**"Failed to parse conversations"**:
- ChatGPT export formats may vary
- Check logs for specific parsing errors
- The parser handles multiple format variations

### File System Issues

**Permission errors**:
```bash
totem chatgpt doctor
# Check directory permissions
```

**Obsidian vault not found**:
- Verify the `obsidian_chatgpt_dir` and `obsidian_daily_dir` paths exist
- Update configuration if paths have changed

### Launchd Issues

**Agent not running**:
```bash
launchctl list | grep chatgpt
# Should show the process if running
```

**Logs not appearing**:
- Check log file locations in the plist
- Ensure log directory exists and is writable

## Technical Details

### Pipeline Steps

1. **Gmail Search**: Find unprocessed export emails using configured query
2. **Email Parsing**: Extract download URL from email HTML/text
3. **Download**: HTTP GET with redirects, validate ZIP format
4. **Extraction**: Unzip to staging directory
5. **JSON Discovery**: Locate conversations JSON file
6. **Parsing**: Convert JSON to normalized conversation objects
7. **Note Writing**: Create Obsidian conversation notes with frontmatter
8. **Daily Note Update**: Insert/update ChatGPT block in daily notes
9. **State Update**: Mark email as processed

### Idempotency

- Conversation notes use content hashing to avoid rewriting unchanged content
- Daily note blocks use marker-based replacement (like OMI blocks)
- State file prevents reprocessing of already handled emails
- Safe to run repeatedly without creating duplicates

### Error Handling

- Pipeline failures don't mark emails as processed
- State tracks last error and attempt times
- Comprehensive logging for debugging
- Graceful degradation when individual conversations fail to parse

## Dependencies

- `google-api-python-client`: Gmail API access
- `google-auth-oauthlib`: OAuth authentication
- `requests`: HTTP downloads
- Existing Totem dependencies (typer, pydantic, rich, etc.)

## Testing

Run the test suite:
```bash
python -m pytest tests/test_chatgpt.py -v
```

Tests cover:
- URL extraction from emails
- Conversation parsing
- Obsidian note formatting
- Daily note block replacement
- State management
- Configuration validation
