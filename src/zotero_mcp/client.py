"""
Zotero client wrapper for MCP server.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from dotenv import load_dotenv
from markitdown import MarkItDown
from pyzotero import zotero

from zotero_mcp.utils import format_creators

# Load environment variables
load_dotenv()

# Runtime library override state — set by zotero_switch_library tool.
# When non-empty, these values override the corresponding environment variables
# in get_zotero_client(). Keys: "library_id", "library_type".
_active_library_override: dict[str, str] = {}


def set_active_library(library_id: str, library_type: str) -> None:
    """Set runtime library override for all subsequent get_zotero_client() calls."""
    _active_library_override["library_id"] = library_id
    _active_library_override["library_type"] = library_type


def clear_active_library() -> None:
    """Clear runtime library override, reverting to environment variable defaults."""
    _active_library_override.clear()


def get_active_library() -> dict[str, str]:
    """Return the current active library override (empty dict if using defaults)."""
    return dict(_active_library_override)


@dataclass
class AttachmentDetails:
    """Details about a Zotero attachment."""

    key: str
    title: str
    filename: str
    content_type: str


def get_zotero_client() -> zotero.Zotero:
    """
    Get authenticated Zotero client using environment variables.
    If a runtime library override is active (via set_active_library()),
    those values take precedence over environment variables.
    Returns:
        A configured Zotero client instance.

    Raises:
        ValueError: If required environment variables are missing.
    """
    # Runtime overrides take precedence over environment variables
    override = _active_library_override
    library_id = override.get("library_id") or os.getenv("ZOTERO_LIBRARY_ID")
    library_type = override.get("library_type") or os.getenv("ZOTERO_LIBRARY_TYPE", "user")
    api_key = os.getenv("ZOTERO_API_KEY")
    local = os.getenv("ZOTERO_LOCAL", "").lower() in ["true", "yes", "1"]

    # For local API, default to user ID 0 if not specified
    if local and not library_id:
        library_id = "0"

    # For remote API, we need both library_id and api_key
    if not local and not (library_id and api_key):
        raise ValueError(
            "Missing required environment variables. Please set ZOTERO_LIBRARY_ID and ZOTERO_API_KEY, "
            "or use ZOTERO_LOCAL=true for local Zotero instance."
        )

    return zotero.Zotero(
        library_id=library_id,
        library_type=library_type,
        api_key=api_key,
        local=local,
    )


def get_local_zotero_client() -> zotero.Zotero | None:
    """
    Get a local Zotero client for file access (WebDAV/local storage).

    This client connects to the local Zotero instance running on port 23119.
    It's useful for accessing PDF files stored via WebDAV when the main
    client is configured for web API.

    Returns:
        A local Zotero client instance, or None if local Zotero is not available.
    """
    try:
        # Create a local client - library_id 0 is the default for local
        client = zotero.Zotero(
            library_id="0",
            library_type="user",
            api_key=None,
            local=True,
        )
        # Test connection by making a simple request
        client.items(limit=1)
        return client
    except Exception:
        return None


def get_web_zotero_client() -> zotero.Zotero | None:
    """
    Get a web API Zotero client for write operations.

    This client connects to the Zotero web API and can create/modify items.
    Requires ZOTERO_API_KEY and ZOTERO_LIBRARY_ID environment variables.

    Returns:
        A web API Zotero client instance, or None if credentials are not available.
    """
    library_id = os.getenv("ZOTERO_LIBRARY_ID")
    library_type = os.getenv("ZOTERO_LIBRARY_TYPE", "user")
    api_key = os.getenv("ZOTERO_API_KEY")

    if not library_id or not api_key:
        return None

    return zotero.Zotero(
        library_id=library_id,
        library_type=library_type,
        api_key=api_key,
        local=False,
    )


def is_local_zotero_available() -> bool:
    """Check if local Zotero instance is running and accessible."""
    client = get_local_zotero_client()
    return client is not None


def check_write_permissions(zot: zotero.Zotero) -> Dict[str, Any]:
    """
    Check if the Zotero client has write permissions.

    Args:
        zot: A Zotero client instance.

    Returns:
        Dictionary with permission information:
        - has_write_access: bool - Whether write access is available
        - is_local: bool - Whether using local API
        - permission_type: str - Type of access ("read-write", "read-only", "local", "unknown")
        - message: str - Human-readable message about permissions
        - checked_via: str - How permissions were determined
    """
    # Check if using local API
    local = os.getenv("ZOTERO_LOCAL", "").lower() in ["true", "yes", "1"]

    if local:
        # Local API always has write access
        return {
            "has_write_access": True,
            "is_local": True,
            "permission_type": "local",
            "message": "Local Zotero API - full read/write access",
            "checked_via": "local_mode"
        }

    # For web API, check the key permissions
    api_key = os.getenv("ZOTERO_API_KEY", "")

    if not api_key:
        return {
            "has_write_access": False,
            "is_local": False,
            "permission_type": "unknown",
            "message": "No API key provided - cannot determine permissions",
            "checked_via": "no_api_key"
        }

    try:
        # The Zotero API returns key permissions in the response headers
        # We can check this by making a request and examining the Key-Access header
        # Attempt to get user/group info which includes key permissions
        library_type = os.getenv("ZOTERO_LIBRARY_TYPE", "user")

        # Make a minimal request to check permissions
        # This uses the key_info() method if available, or falls back to a test request
        try:
            # Try to get key permissions directly
            key_info = zot.key_info()

            if key_info:
                # Check the access level
                access = key_info.get("access", {})

                # Check if we have write access to the library
                if library_type == "user":
                    user_access = access.get("user", {})
                    library_access = user_access.get("library", False)
                    write_access = user_access.get("write", False)
                else:  # group
                    group_id = os.getenv("ZOTERO_LIBRARY_ID", "")
                    groups_access = access.get("groups", {})
                    group_access = groups_access.get(group_id, {})
                    library_access = group_access.get("library", False)
                    write_access = group_access.get("write", False)

                has_write = library_access and write_access

                return {
                    "has_write_access": has_write,
                    "is_local": False,
                    "permission_type": "read-write" if has_write else "read-only",
                    "message": f"Web API with {'read-write' if has_write else 'read-only'} access",
                    "checked_via": "key_info_api",
                    "key_info": key_info
                }
        except AttributeError:
            # pyzotero might not have key_info method, fall back to test request
            pass

        # Fallback: Try to make a test write request (create then delete a tag)
        # This is more invasive but works when key_info is not available
        try:
            # Try to get the library's tags (this tests read access)
            zot.tags(limit=1)

            # Unfortunately, without key_info, we can't reliably test write access
            # without actually modifying something. We'll return a conservative result.
            return {
                "has_write_access": None,  # Unknown
                "is_local": False,
                "permission_type": "unknown",
                "message": "Web API - write permissions could not be determined. Attempting write operations will reveal actual permissions.",
                "checked_via": "read_test_only"
            }

        except Exception as test_error:
            return {
                "has_write_access": False,
                "is_local": False,
                "permission_type": "no_access",
                "message": f"Cannot access library: {str(test_error)}",
                "checked_via": "failed_test"
            }

    except Exception as e:
        return {
            "has_write_access": False,
            "is_local": False,
            "permission_type": "error",
            "message": f"Error checking permissions: {str(e)}",
            "checked_via": "error"
        }


def require_write_permissions(zot: zotero.Zotero) -> Optional[str]:
    """
    Check if the Zotero client has write permissions and return an error message if not.

    Args:
        zot: A Zotero client instance.

    Returns:
        None if write permissions are available, or an error message string if not.
    """
    permissions = check_write_permissions(zot)

    # If we have confirmed write access, return None (no error)
    if permissions["has_write_access"] is True:
        return None

    # If we explicitly don't have write access, return error
    if permissions["has_write_access"] is False:
        return f"Error: {permissions['message']}. Write operations are not permitted with current API key."

    # If permission status is unknown (None), allow the operation to proceed
    # The actual API call will fail if permissions are insufficient
    return None


def format_item_metadata(item: Dict[str, Any], include_abstract: bool = True) -> str:
    """
    Format a Zotero item's metadata as markdown.

    Args:
        item: A Zotero item dictionary.
        include_abstract: Whether to include the abstract in the output.

    Returns:
        Markdown-formatted metadata.
    """
    data = item.get("data", {})
    item_type = data.get("itemType", "unknown")

    # Basic information
    lines = [
        f"# {data.get('title', 'Untitled')}",
        f"**Type:** {item_type}",
        f"**Item Key:** {data.get('key')}",
    ]

    # Date
    if date := data.get("date"):
        lines.append(f"**Date:** {date}")

    # Authors/Creators
    if creators := data.get("creators", []):
        lines.append(f"**Authors:** {format_creators(creators)}")

    # Publication details based on item type
    if item_type == "journalArticle":
        if journal := data.get("publicationTitle"):
            journal_info = f"**Journal:** {journal}"
            if volume := data.get("volume"):
                journal_info += f", Volume {volume}"
            if issue := data.get("issue"):
                journal_info += f", Issue {issue}"
            if pages := data.get("pages"):
                journal_info += f", Pages {pages}"
            lines.append(journal_info)
    elif item_type == "book":
        if publisher := data.get("publisher"):
            book_info = f"**Publisher:** {publisher}"
            if place := data.get("place"):
                book_info += f", {place}"
            lines.append(book_info)

    # DOI and URL
    if doi := data.get("DOI"):
        lines.append(f"**DOI:** {doi}")
    if url := data.get("url"):
        lines.append(f"**URL:** {url}")

    # Extra field often holds citation key / misc metadata
    if extra := data.get("extra"):
        lines.extend(["", "## Extra", extra])

        # Try to surface a citation key if present in Extra
        for line in extra.splitlines():
            if "citation key" in line.lower():
                key_part = line.split(":", 1)[1].strip() if ":" in line else line.strip()
                lines.append(f"**Citation Key (from Extra):** {key_part}")
                break
    
    # Tags
    if tags := data.get("tags"):
        tag_list = [f"`{tag['tag']}`" for tag in tags]
        if tag_list:
            lines.append(f"**Tags:** {' '.join(tag_list)}")

    # Abstract
    if include_abstract and (abstract := data.get("abstractNote")):
        lines.extend(["", "## Abstract", abstract])

    # Collections
    if collections := data.get("collections", []):
        if collections:
            lines.append(f"**Collections:** {len(collections)} collections")

    # Notes - this requires additional API calls, so we just indicate if there are notes
    if "meta" in item and item["meta"].get("numChildren", 0) > 0:
        lines.append(f"**Notes/Attachments:** {item['meta']['numChildren']}")

    return "\n\n".join(lines)


def generate_bibtex(item: dict[str, Any]) -> str:
    """
    Generate BibTeX format for a Zotero item.

    Args:
        item: Zotero item data

    Returns:
        BibTeX formatted string
    """
    data = item.get("data", {})
    item_key = data.get("key")

    # Try Better BibTeX first
    try:
        from zotero_mcp.better_bibtex_client import ZoteroBetterBibTexAPI
        bibtex = ZoteroBetterBibTexAPI()

        if bibtex.is_zotero_running():
            return bibtex.export_bibtex(item_key)

    except Exception:
        # Continue to fallback method if Better BibTeX fails
        pass

    # Fallback to basic BibTeX generation
    item_type = data.get("itemType", "misc")

    if item_type in ["attachment", "note"]:
        raise ValueError(f"Cannot export BibTeX for item type '{item_type}'")

    # Map Zotero item types to BibTeX types
    type_map = {
        "journalArticle": "article",
        "book": "book",
        "bookSection": "incollection",
        "conferencePaper": "inproceedings",
        "thesis": "phdthesis",
        "report": "techreport",
        "webpage": "misc",
        "manuscript": "unpublished"
    }

    # Create citation key
    creators = data.get("creators", [])
    author = ""
    if creators:
        first = creators[0]
        author = first.get("lastName", first.get("name", "").split()[-1] if first.get("name") else "").replace(" ", "")

    year = data.get("date", "")[:4] if data.get("date") else "nodate"
    cite_key = f"{author}{year}_{item_key}"

    # Build BibTeX entry
    bib_type = type_map.get(item_type, "misc")
    lines = [f"@{bib_type}{{{cite_key},"]

    # Add fields
    field_mappings = [
        ("title", "title"),
        ("publicationTitle", "journal"),
        ("volume", "volume"),
        ("issue", "number"),
        ("pages", "pages"),
        ("publisher", "publisher"),
        ("DOI", "doi"),
        ("url", "url"),
        ("abstractNote", "abstract")
    ]

    for zotero_field, bibtex_field in field_mappings:
        if value := data.get(zotero_field):
            # Escape special characters
            value = value.replace("{", "\\{").replace("}", "\\}")
            lines.append(f'  {bibtex_field} = {{{value}}},')

    # Add authors
    if creators:
        authors = []
        for creator in creators:
            if creator.get("creatorType") == "author":
                if "lastName" in creator and "firstName" in creator:
                    authors.append(f"{creator['lastName']}, {creator['firstName']}")
                elif "name" in creator:
                    authors.append(creator["name"])
        if authors:
            lines.append(f'  author = {{{" and ".join(authors)}}},')

    # Add year
    if year != "nodate":
        lines.append(f'  year = {{{year}}},')

    # Remove trailing comma from last field and close entry
    if lines[-1].endswith(','):
        lines[-1] = lines[-1][:-1]
    lines.append("}")

    return "\n".join(lines)


def get_attachment_details(
    zot: zotero.Zotero, item: dict[str, Any]
) -> AttachmentDetails | None:
    """
    Get attachment details for a Zotero item, finding the most relevant attachment.

    Args:
        zot: A Zotero client instance.
        item: A Zotero item dictionary.

    Returns:
        AttachmentDetails if found, None otherwise.
    """
    data = item.get("data", {})
    item_type = data.get("itemType")
    item_key = data.get("key")

    # Direct attachment
    if item_type == "attachment":
        return AttachmentDetails(
            key=item_key,
            title=data.get("title", "Untitled"),
            filename=data.get("filename", ""),
            content_type=data.get("contentType", ""),
        )

    # For regular items, look for child attachments
    try:
        children = zot.children(item_key)

        # Group attachments by content type
        pdfs = []
        htmls = []
        others = []

        for child in children:
            child_data = child.get("data", {})
            if child_data.get("itemType") == "attachment":
                content_type = child_data.get("contentType", "")
                filename = child_data.get("filename", "")
                title = child_data.get("title", "Untitled")
                key = child.get("key", "")

                # Use MD5 as proxy for size (longer MD5 usually means larger file)
                size_proxy = len(child_data.get("md5", ""))

                attachment = (key, title, filename, content_type, size_proxy)

                if content_type == "application/pdf":
                    pdfs.append(attachment)
                elif content_type.startswith("text/html"):
                    htmls.append(attachment)
                else:
                    others.append(attachment)

        # Return first match in priority order (PDF > HTML > other)
        # Sort each category by size (descending) to get largest/most complete file
        for category in [pdfs, htmls, others]:
            if category:
                category.sort(key=lambda x: x[4], reverse=True)
                key, title, filename, content_type, _ = category[0]
                return AttachmentDetails(
                    key=key,
                    title=title,
                    filename=filename,
                    content_type=content_type,
                )
    except Exception:
        pass

    return None


def convert_to_markdown(file_path: str | Path) -> str:
    """
    Convert a file to markdown using markitdown library.

    Args:
        file_path: Path to the file to convert.

    Returns:
        Markdown text.
    """
    try:
        md = MarkItDown()
        result = md.convert(str(file_path))
        return result.text_content
    except Exception as e:
        return f"Error converting file to markdown: {str(e)}"
