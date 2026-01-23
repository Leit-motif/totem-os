"""Email parsing utilities for extracting ChatGPT export download URLs."""

import re
import logging
import html
from typing import Optional, List, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def extract_download_urls(email_body: str) -> list[str]:
    """Extract potential download URLs from email body.

    Extracts from BOTH HTML href attributes AND plain text URLs.

    Args:
        email_body: Email body content (HTML or plain text)

    Returns:
        List of all extracted URLs
    """
    urls = []

    # Always try HTML href extraction (even if body looks like plain text)
    # HTML entities like &amp; need to be decoded
    decoded_body = html.unescape(email_body)

    # Extract href URLs from HTML
    href_pattern = r'href=["\']([^"\']+)["\']'
    html_urls = re.findall(href_pattern, decoded_body, re.IGNORECASE)
    urls.extend(html_urls)

    # Extract plain text URLs
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    text_urls = re.findall(url_pattern, decoded_body)
    urls.extend(text_urls)

    # Remove duplicates while preserving order
    seen = set()
    unique_urls = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)

    return unique_urls


def extract_estuary_zip_url(email_body: str) -> Optional[str]:
    """Extract the signed ChatGPT export ZIP URL from the email body.

    The export email contains a button whose href points directly to an Estuary ZIP:
    https://chatgpt.com/backend-api/estuary/content?...<something>.zip&...

    We decode HTML entities first, then search for the first matching URL.
    """
    decoded_body = html.unescape(email_body or "")

    # Match either in href="..." or plain text; stop at quotes/angle brackets/whitespace.
    pattern = (
        r"https://chatgpt\\.com/backend-api/estuary/content\\?[^\"'<>\s]+?\\.zip[^\"'<>\s]*"
    )
    m = re.search(pattern, decoded_body, re.IGNORECASE)
    if not m:
        return None
    return m.group(0)


def score_download_url(url: str) -> Tuple[int, str]:
    """Score a URL for likelihood of being a ChatGPT export download link.

    Returns:
        Tuple of (score, reason) where higher score is better.
        Negative scores indicate URLs to avoid.
    """
    score = 0
    reasons = []
    url_lower = url.lower()
    parsed = urlparse(url)

    # Strong negative signals - ChatGPT conversation links
    if url.startswith("https://chatgpt.com/c/"):
        score -= 100
        reasons.append("chat_conversation_link")
        return score, ", ".join(reasons)

    # Strong positive: exact export download endpoint
    if "chatgpt.com/backend-api/estuary/content" in url_lower and ".zip" in url_lower:
        score += 100
        reasons.append("estuary_export_zip")

    # Negative signals - settings and other non-download links
    if "#settings" in url_lower or "settings" in url_lower:
        score -= 50
        reasons.append("settings_link")

    # Negative signals - unsubscribe/help links
    if any(skip_word in url_lower for skip_word in [
        'unsubscribe', 'preferences', 'help', 'support', 'privacy', 'terms',
        'policy', 'account', 'login', 'signin', 'sign-in', 'feedback'
    ]):
        score -= 10
        reasons.append("unsubscribe_link")

    # Negative signals - images/assets
    if any(ext in parsed.path.lower() for ext in ['.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg']):
        score -= 5
        reasons.append("image_asset")

    # Negative signals - tracking pixels
    if 'pixel' in url_lower or 'tracking' in url_lower or 'analytics' in url_lower:
        score -= 5
        reasons.append("tracking_pixel")

    # Strong positive signals - OpenAI domains (but only if not already disqualified)
    if any(domain in parsed.netloc for domain in ['chatgpt.com', 'chat.openai.com', 'openai.com']):
        score += 10
        reasons.append("openai_domain")

    # Positive signals - specific export/download keywords
    if any(keyword in url_lower for keyword in ['export', 'download', 'archive', 'backup']):
        score += 5
        reasons.append("export_keyword")

    # Positive signals - data-related keywords (but not too generic)
    if any(keyword in url_lower for keyword in ['data-export', 'personal-data', 'takeout']):
        score += 3
        reasons.append("data_keyword")

    # Positive signals - ChatGPT API endpoints
    if 'backend-api' in url_lower and ('estuary' in url_lower or 'content' in url_lower):
        score += 8
        reasons.append("chatgpt_api_endpoint")

    # Positive signals - URLs with .zip in query parameters (indicates ZIP download)
    if '.zip' in url_lower and ('id=' in url_lower or 'file=' in url_lower):
        score += 5
        reasons.append("zip_file_parameter")

    # Positive signals - signed download URLs (contain sig= parameter)
    if 'sig=' in url_lower:
        score += 3
        reasons.append("signed_download_url")

    # Positive signals - CDN/storage domains (common for downloads)
    if any(domain in parsed.netloc for domain in ['cdn', 'storage', 'files', 'downloads']):
        score += 3
        reasons.append("cdn_domain")

    # Positive signals - long tokenized paths (often download links)
    path_parts = parsed.path.strip('/').split('/')
    if len(path_parts) >= 2 and any(len(part) > 20 for part in path_parts):
        score += 2
        reasons.append("tokenized_path")

    # Negative signals - unsubscribe/help links
    if any(skip_word in url_lower for skip_word in [
        'unsubscribe', 'preferences', 'help', 'support', 'privacy', 'terms',
        'policy', 'account', 'login', 'signin', 'sign-in', 'feedback'
    ]):
        score -= 10
        reasons.append("unsubscribe_link")

    # Negative signals - images/assets
    if any(ext in parsed.path.lower() for ext in ['.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg']):
        score -= 5
        reasons.append("image_asset")

    # Negative signals - tracking pixels
    if 'pixel' in url_lower or 'tracking' in url_lower or 'analytics' in url_lower:
        score -= 5
        reasons.append("tracking_pixel")

    return score, ", ".join(reasons)


def filter_download_urls(urls: list[str]) -> list[str]:
    """Filter URLs to find likely ChatGPT export download links.

    Uses scoring system to rank and filter URLs. Requires download-related keywords.

    Args:
        urls: List of URLs to filter

    Returns:
        Filtered list of candidate download URLs (score >= 0 AND has download keywords)
    """
    candidates = []

    # Required keywords for any URL to be considered a download candidate
    # Must contain estuary/content OR .zip
    download_indicators = [
        'backend-api/estuary/content',
        'export',
        'download',
        '.zip',
    ]

    for url in urls:
        score, reason = score_download_url(url)
        url_lower = url.lower()

        # Hard requirement: must include estuary/content OR .zip
        has_download_indicator = (
            'backend-api/estuary/content' in url_lower or '.zip' in url_lower
        )

        if score >= 0 and has_download_indicator:
            candidates.append(url)
            logger.debug(f"Accepted URL (score {score}): {url} - {reason}")
        else:
            rejection_reason = reason
            if not has_download_indicator:
                rejection_reason += ", missing_download_indicator"
            logger.debug(f"Rejected URL (score {score}): {url} - {rejection_reason}")

    return candidates


def select_best_download_url(urls: list[str]) -> Optional[str]:
    """Select the best download URL from candidates using scoring.

    Args:
        urls: List of candidate URLs

    Returns:
        Best URL to try, or None if no good candidates
    """
    if not urls:
        return None

    if len(urls) == 1:
        return urls[0]

    # Score all URLs and pick the highest scoring one
    scored_urls = [(url, score_download_url(url)[0]) for url in urls]
    scored_urls.sort(key=lambda x: x[1], reverse=True)

    best_url, best_score = scored_urls[0]

    logger.info(f"Selected download URL (score {best_score}): {best_url}")
    for url, score in scored_urls[1:]:
        logger.debug(f"Alternative URL (score {score}): {url}")

    return best_url


def extract_download_url_from_email(email_body: str, debug: bool = False) -> Optional[str]:
    """Extract the ChatGPT export download URL from email body.

    Strict detector that only accepts URLs that:
    - are on chatgpt.com (or chat.openai.com)
    - and contain "/backend-api/estuary/content"
    - and include ".zip" in the URL

    Args:
        email_body: Email body content
        debug: Enable debug logging of URL extraction process

    Returns:
        Export download URL or None if not found
    """
    # Extract URLs from both HTML and plain text
    all_urls = extract_download_urls(email_body)

    # Strict filtering: only accept estuary export URLs on ChatGPT domains
    for url in all_urls:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)

            # Must be on ChatGPT/OpenAI domain
            if parsed.netloc not in ['chatgpt.com', 'chat.openai.com']:
                continue

            # Must contain estuary content path
            if '/backend-api/estuary/content' not in url:
                continue

            # Must contain .zip somewhere in the URL
            if '.zip' not in url:
                continue

            # Found a valid export URL
            if debug:
                logger.info(f"Selected export URL (strict estuary match): {url}")
            return url

        except Exception as e:
            # Skip malformed URLs
            if debug:
                logger.debug(f"Skipping malformed URL {url}: {e}")
            continue

    # No valid export URL found
    logger.info("No valid ChatGPT export URL found in email")
    if debug:
        logger.info(f"Found {len(all_urls)} total URLs in email")
        for i, url in enumerate(all_urls):
            logger.info(f"  URL {i+1}: {url}")

    return None


def save_debug_email_artifact(
    message: dict,
    email_body: str,
    extracted_urls: List[str],
    debug_dir: str,
    message_id: str
) -> str:
    """Save debug artifact when URL extraction fails.

    Args:
        message: Gmail message dict
        email_body: Email body content
        extracted_urls: All URLs extracted from email
        debug_dir: Directory to save debug artifacts
        message_id: Gmail message ID

    Returns:
        Path to the saved debug file
    """
    import json
    import base64
    from pathlib import Path
    from datetime import datetime

    debug_file = Path(debug_dir) / f"{message_id}.json"
    debug_file.parent.mkdir(parents=True, exist_ok=True)

    # Parse timestamp
    timestamp = None
    if 'internalDate' in message:
        try:
            # Convert milliseconds to seconds
            timestamp = datetime.fromtimestamp(int(message['internalDate']) / 1000)
        except (ValueError, TypeError):
            pass

    # Extract headers
    headers = {}
    if 'payload' in message and 'headers' in message['payload']:
        for header in message['payload']['headers']:
            headers[header['name'].lower()] = header.get('value', '')

    # Extract raw HTML and text from message payload
    raw_html = ""
    raw_text = ""

    payload = message.get('payload', {})
    if 'parts' in payload:
        for part in payload['parts']:
            mime_type = part.get('mimeType', '')
            body_data = part.get('body', {}).get('data', '')
            if body_data:
                try:
                    decoded_body = base64.urlsafe_b64decode(body_data).decode('utf-8')
                    if mime_type == 'text/html':
                        raw_html = decoded_body
                    elif mime_type == 'text/plain':
                        raw_text = decoded_body
                except Exception:
                    pass
    elif 'body' in payload and 'data' in payload['body']:
        # Simple message without parts
        try:
            body_data = payload['body']['data']
            decoded_body = base64.urlsafe_b64decode(body_data).decode('utf-8')
            raw_text = decoded_body  # Assume it's text if no parts
        except Exception:
            pass

    debug_data = {
        "message_id": message_id,
        "timestamp": timestamp.isoformat() if timestamp else None,
        "subject": headers.get('subject', ''),
        "from": headers.get('from', ''),
        "snippet": message.get('snippet', ''),
        "raw_html": raw_html[:50000] if raw_html else "",  # First 50k chars
        "raw_text": raw_text[:50000] if raw_text else "",  # First 50k chars
        "processed_body_preview": email_body[:1000] if email_body else "",
        "extracted_urls": extracted_urls,
        "url_scores": [
            {
                "url": url,
                "score": score_download_url(url)[0],
                "reason": score_download_url(url)[1],
                "has_download_indicator": (
                    'backend-api/estuary/content' in url.lower() or '.zip' in url.lower()
                ),
                "accepted": score_download_url(url)[0] >= 0 and any(indicator in url.lower() for indicator in [
                    'export', 'download', 'archive', 'zip', 'privacy', 'data-export',
                    'myaccount', 'personal-data', 'takeout'
                ])
            }
            for url in extracted_urls
        ]
    }

    with open(debug_file, 'w', encoding='utf-8') as f:
        json.dump(debug_data, f, indent=2, ensure_ascii=False)

    logger.info(f"Saved debug artifact: {debug_file}")
    return str(debug_file)