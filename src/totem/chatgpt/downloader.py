"""Download utilities for ChatGPT export ZIP files."""

import logging
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class DownloadError(Exception):
    """Exception raised for download-related errors."""
    pass


def download_zip(url: str, output_path: Path, timeout: int = 300, debug: bool = False) -> None:
    """Download ZIP file from URL with validation.

    Args:
        url: Download URL
        output_path: Path to save the downloaded file
        timeout: Request timeout in seconds
        debug: Enable debug logging

    Raises:
        DownloadError: If download fails or file is invalid
    """
    logger.info(f"Downloading from {url}")

    try:
        # Download with redirects enabled
        response = requests.get(url, stream=True, timeout=timeout, allow_redirects=True)
        response.raise_for_status()

        # Debug logging
        if debug:
            logger.info(f"Response status: {response.status_code}")
            content_type = response.headers.get('content-type', 'unknown')
            logger.info(f"Response content-type: {content_type}")

            # Read first chunk to log header bytes
            response_content = response.content
            if response_content:
                header_bytes = response_content[:16]
                header_hex = ' '.join(f'{b:02x}' for b in header_bytes)
                logger.info(f"First 16 bytes (hex): {header_hex}")

                # Check if response looks like HTML/text instead of ZIP
                if content_type.startswith('text/') or b'<html' in response_content[:100].lower():
                    raise DownloadError("Download URL returned HTML/text instead of ZIP. Export link may not be directly accessible (Cloudflare/login page).")

        # Write to temporary file first, then validate
        temp_path = output_path.with_suffix('.tmp')
        total_size = 0

        # Reset response for streaming if we consumed it for debug
        if debug and hasattr(response, '_content_consumed') and response._content_consumed:
            response = requests.get(url, stream=True, timeout=timeout, allow_redirects=True)
            response.raise_for_status()

        with open(temp_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    total_size += len(chunk)

        # Validate downloaded file
        if total_size < 10240:  # Less than 10KB
            temp_path.unlink(missing_ok=True)
            raise DownloadError(f"Downloaded file too small: {total_size} bytes")

        # Check ZIP header (full 4-byte signature: PK\x03\x04)
        with open(temp_path, 'rb') as f:
            header = f.read(4)
            if header != b'PK\x03\x04':
                # If debug, log what we actually got
                if debug:
                    header_hex = ' '.join(f'{b:02x}' for b in header)
                    logger.info(f"Actual file header (hex): {header_hex}")
                temp_path.unlink(missing_ok=True)
                raise DownloadError("Downloaded file is not a valid ZIP (missing PK\\x03\\x04 header)")

        # Move to final location
        temp_path.rename(output_path)
        logger.info(f"Downloaded {total_size} bytes to {output_path}")

    except requests.RequestException as e:
        raise DownloadError(f"Download failed: {e}") from e
    except OSError as e:
        raise DownloadError(f"File operation failed: {e}") from e


def unzip_archive(zip_path: Path, extract_to: Path) -> None:
    """Unzip archive to specified directory.

    Args:
        zip_path: Path to ZIP file
        extract_to: Directory to extract to

    Raises:
        DownloadError: If unzip fails
    """
    logger.info(f"Unzipping {zip_path} to {extract_to}")

    try:
        extract_to.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            file_list = zip_ref.namelist()
            zip_ref.extractall(extract_to)

        logger.info(f"Extracted {len(file_list)} files from ZIP")
        logger.debug(f"ZIP contents: {file_list}")

        # Debug: show actual extracted files
        extracted_files = list(extract_to.rglob("*"))
        logger.debug(f"Extracted files: {[str(f.relative_to(extract_to)) for f in extracted_files[:20]]}")

    except zipfile.BadZipFile as e:
        raise DownloadError(f"Invalid ZIP file: {e}") from e
    except OSError as e:
        raise DownloadError(f"Unzip failed: {e}") from e


def find_conversation_json(extract_dir: Path) -> Optional[Path]:
    """Find the conversations JSON file in extracted directory.

    Args:
        extract_dir: Directory containing extracted files

    Returns:
        Path to conversations JSON file, or None if not found
    """
    # Look for JSON files recursively
    json_files = list(extract_dir.rglob("*.json"))

    if not json_files:
        logger.warning(f"No JSON files found in {extract_dir}")
        # Debug: list all files in extract directory
        all_files = list(extract_dir.rglob("*"))
        logger.warning(f"All files in extract dir: {[str(f) for f in all_files[:20]]}")
        return None

    logger.debug(f"Found {len(json_files)} JSON files: {[str(f) for f in json_files]}")

    # First, try to find files with obvious conversation-related names
    conversation_names = ['conversations.json', 'conversations', 'chat', 'chats']
    for json_file in json_files:
        file_name_lower = json_file.name.lower()
        for conv_name in conversation_names:
            if conv_name in file_name_lower:
                logger.info(f"Found conversations JSON by filename: {json_file}")
                return json_file

    # Score files by relevance to conversations
    scored_files = []
    for json_file in json_files:
        score = 0
        content_preview = ""
        file_name_lower = json_file.name.lower()

        try:
            # Read first few KB to check content
            with open(json_file, 'r', encoding='utf-8') as f:
                content_preview = f.read(4096).lower()

            # Check if it's valid JSON at all
            import json as json_module
            try:
                json_module.loads(content_preview[:1000])  # Try to parse first part
                score += 1  # Valid JSON
            except json_module.JSONDecodeError:
                logger.debug(f"Invalid JSON in {json_file.name}")
                continue

            # Score based on conversation-related keywords
            keywords = ['conversation', 'mapping', 'message', 'create_time', 'chatgpt', 'title']
            for keyword in keywords:
                if keyword in content_preview:
                    score += 1

            # Score based on filename keywords
            filename_keywords = ['conversation', 'chat', 'data', 'export']
            for keyword in filename_keywords:
                if keyword in file_name_lower:
                    score += 2

            # Prefer larger files (likely to contain actual data)
            file_size = json_file.stat().st_size
            if file_size > 10000:  # > 10KB
                score += 1

            scored_files.append((json_file, score, file_size))
            logger.debug(f"Scored {json_file.name}: score={score}, size={file_size}")

        except (OSError, UnicodeDecodeError) as e:
            logger.debug(f"Could not read {json_file}: {e}")
            continue

    if not scored_files:
        logger.warning("No valid JSON files found")
        return None

    # Sort by score (descending), then by size (descending)
    scored_files.sort(key=lambda x: (x[1], x[2]), reverse=True)

    best_file = scored_files[0][0]
    best_score = scored_files[0][1]
    logger.info(f"Selected conversations JSON: {best_file} (score: {best_score})")

    # If best score is very low, still use it but log warnings
    if best_score < 1:
        logger.warning(f"Very low confidence in selected JSON file (score: {best_score}). This might not be a conversations file.")
        logger.warning("Top scoring files:")
        for file_path, score, size in scored_files[:3]:
            logger.warning(f"  {file_path.name}: score={score}, size={size}")
    elif best_score < 2:
        logger.warning(f"Low confidence in selected JSON file (score: {best_score}). File: {best_file}")

    return best_file