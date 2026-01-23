"""Gmail API client for ChatGPT export email retrieval."""

import base64
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


class GmailClient:
    """Gmail API client for retrieving ChatGPT export emails."""

    SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

    def __init__(self, credentials_path: Optional[Path] = None, token_path: Optional[Path] = None):
        """Initialize Gmail client with OAuth credentials.

        Args:
            credentials_path: Path to OAuth client credentials JSON file
            token_path: Path to store/retrieve OAuth token
        """
        self.credentials_path = credentials_path or Path.home() / ".config" / "totem" / "gmail_credentials.json"
        self.token_path = token_path or Path.home() / ".config" / "totem" / "gmail_token.json"
        self.service = None

    def _get_credentials(self) -> Credentials:
        """Get or refresh OAuth credentials."""
        creds = None

        # Load existing token if available
        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), self.SCOPES)

        # Refresh or get new credentials if needed
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self.credentials_path.exists():
                    raise FileNotFoundError(
                        f"Gmail credentials not found at {self.credentials_path}. "
                        "Please download OAuth client credentials from Google Cloud Console "
                        "and save as gmail_credentials.json"
                    )

                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path), self.SCOPES
                )
                creds = flow.run_local_server(port=0)

            # Save credentials for future use
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.token_path, 'w') as token:
                token.write(creds.to_json())

        return creds

    def authenticate(self) -> None:
        """Authenticate and build Gmail service."""
        try:
            creds = self._get_credentials()
            self.service = build('gmail', 'v1', credentials=creds)
        except HttpError as error:
            raise RuntimeError(f"Gmail API authentication failed: {error}") from error

    def search_messages(self, query: str, max_results: int = 10) -> list[dict]:
        """Search for messages matching the query.

        Args:
            query: Gmail search query
            max_results: Maximum number of messages to return

        Returns:
            List of message metadata dictionaries
        """
        if not self.service:
            raise RuntimeError("Gmail client not authenticated. Call authenticate() first.")

        try:
            results = self.service.users().messages().list(
                userId='me',
                q=query,
                maxResults=max_results
            ).execute()

            messages = results.get('messages', [])

            # Get full message details for each message
            detailed_messages = []
            for msg in messages:
                msg_detail = self.service.users().messages().get(
                    userId='me',
                    id=msg['id'],
                    format='full'
                ).execute()
                detailed_messages.append(msg_detail)

            return detailed_messages

        except HttpError as error:
            raise RuntimeError(f"Gmail search failed: {error}") from error

    def get_message_body(self, message: dict) -> str:
        """Extract message body from Gmail message.

        Args:
            message: Gmail message dictionary

        Returns:
            Message body as text (HTML if available, otherwise plain text)
        """
        payload = message.get('payload', {})

        # Check for HTML body first, fall back to plain text
        if 'parts' in payload:
            for part in payload['parts']:
                if part.get('mimeType') == 'text/html':
                    body_data = part.get('body', {}).get('data', '')
                    if body_data:
                        return base64.urlsafe_b64decode(body_data).decode('utf-8')
                elif part.get('mimeType') == 'text/plain':
                    body_data = part.get('body', {}).get('data', '')
                    if body_data:
                        return base64.urlsafe_b64decode(body_data).decode('utf-8')
        else:
            # Simple message without parts
            body_data = payload.get('body', {}).get('data', '')
            if body_data:
                return base64.urlsafe_b64decode(body_data).decode('utf-8')

        return ""

    def get_message_attachments(self, message: dict) -> list[dict]:
        """Extract attachment information from a Gmail message.

        Args:
            message: Gmail message dict

        Returns:
            List of attachment info dicts with keys: filename, attachment_id, size, mime_type
        """
        attachments = []

        def extract_attachments(parts):
            for part in parts:
                if part.get('filename') and part.get('body', {}).get('attachmentId'):
                    attachments.append({
                        'filename': part['filename'],
                        'attachment_id': part['body']['attachmentId'],
                        'size': part.get('body', {}).get('size', 0),
                        'mime_type': part.get('mimeType', ''),
                    })
                if 'parts' in part:
                    extract_attachments(part['parts'])

        payload = message.get('payload', {})
        if 'parts' in payload:
            extract_attachments(payload['parts'])

        return attachments

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """Download attachment data from Gmail.

        Args:
            message_id: Gmail message ID
            attachment_id: Attachment ID

        Returns:
            Attachment data as bytes
        """
        if not self.service:
            raise RuntimeError("Gmail client not authenticated. Call authenticate() first.")

        try:
            attachment = self.service.users().messages().attachments().get(
                userId='me',
                messageId=message_id,
                id=attachment_id
            ).execute()

            import base64
            data = base64.urlsafe_b64decode(attachment['data'])
            return data

        except HttpError as error:
            raise RuntimeError(f"Failed to download attachment: {error}") from error

    def test_connection(self) -> bool:
        """Test Gmail API connection by listing labels.

        Returns:
            True if connection successful
        """
        if not self.service:
            self.authenticate()

        try:
            self.service.users().labels().list(userId='me').execute()
            return True
        except HttpError:
            return False