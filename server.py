"""Obsidian MCP Server — remote MCP server for Obsidian via CouchDB/LiveSync."""

import json
import os
import re
import secrets
import time
import copy
from typing import Any

from pydantic import AnyHttpUrl
from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.transport_security import TransportSecuritySettings
from couchdb import CouchDBClient
from auth_config import StaticBearerTokenVerifier, select_auth_mode
from oauth_provider import SimpleOAuthProvider

# --- Configuration ---
COUCHDB_URL = os.environ.get("COUCHDB_URL", "http://localhost:5443")
COUCHDB_USER = os.environ.get("COUCHDB_USER", "admin")
COUCHDB_PASSWORD = os.environ.get("COUCHDB_PASSWORD", "")
COUCHDB_DATABASE = os.environ.get("COUCHDB_DATABASE", "obsidian")
MCP_API_KEY = os.environ.get("MCP_API_KEY", "")
OAUTH_PASSWORD = os.environ.get("OAUTH_PASSWORD", "")
OAUTH_CLIENT_ID = os.environ.get("OAUTH_CLIENT_ID", "")
OAUTH_CLIENT_SECRET = os.environ.get("OAUTH_CLIENT_SECRET", "")
SERVER_URL = os.environ.get("MCP_SERVER_URL", "https://localhost:8484")

# --- Initialize MCP ---
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("MCP_PORT", "8484"))
AUTH_MODE = select_auth_mode(MCP_API_KEY, OAUTH_PASSWORD)

mcp_kwargs: dict[str, Any] = {
    "name": "Obsidian MCP Server",
    "instructions": "MCP server for Obsidian vault via CouchDB/LiveSync",
    "host": MCP_HOST,
    "port": MCP_PORT,
    "transport_security": TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
}

if AUTH_MODE == "bearer":
    mcp_kwargs["token_verifier"] = StaticBearerTokenVerifier(MCP_API_KEY)
    mcp_kwargs["auth"] = AuthSettings(
        issuer_url=AnyHttpUrl(SERVER_URL),
        resource_server_url=AnyHttpUrl(f"{SERVER_URL}/mcp"),
        required_scopes=["obsidian"],
    )
elif AUTH_MODE == "oauth":
    oauth_provider = SimpleOAuthProvider(
        server_url=SERVER_URL,
        access_password=OAUTH_PASSWORD,
        client_id=OAUTH_CLIENT_ID,
        client_secret=OAUTH_CLIENT_SECRET,
    )
    mcp_kwargs["auth_server_provider"] = oauth_provider
    mcp_kwargs["auth"] = AuthSettings(
        issuer_url=AnyHttpUrl(SERVER_URL),
        resource_server_url=AnyHttpUrl(f"{SERVER_URL}/mcp"),
        client_registration_options=ClientRegistrationOptions(
            enabled=False,
            valid_scopes=["claudeai"],
            default_scopes=["claudeai"],
        ),
    )

mcp = FastMCP(**mcp_kwargs)

db = CouchDBClient(
    url=COUCHDB_URL,
    username=COUCHDB_USER,
    password=COUCHDB_PASSWORD,
    database=COUCHDB_DATABASE,
)


# --- Helper functions ---

def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from note content. Returns (properties, body)."""
    if not content.startswith("---"):
        return {}, content
    end = content.find("---", 3)
    if end == -1:
        return {}, content
    fm_text = content[3:end].strip()
    body = content[end + 3:].lstrip("\n")
    props = {}
    current_key = None
    current_list = None
    for line in fm_text.split("\n"):
        if not line.strip():
            continue
        if line.startswith("  - ") and current_key and current_list is not None:
            current_list.append(line.strip()[2:].strip().strip('"').strip("'"))
            props[current_key] = current_list
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            current_key = key
            if val == "":
                current_list = []
            elif val.startswith("[") and val.endswith("]"):
                items = [v.strip().strip('"').strip("'") for v in val[1:-1].split(",") if v.strip()]
                props[key] = items
                current_list = None
            elif val.lower() in ("true", "false"):
                props[key] = val.lower() == "true"
                current_list = None
            else:
                try:
                    props[key] = int(val)
                except ValueError:
                    try:
                        props[key] = float(val)
                    except ValueError:
                        props[key] = val.strip('"').strip("'")
                current_list = None
    return props, body


def _serialize_frontmatter(props: dict) -> str:
    """Serialize properties dict to YAML frontmatter string."""
    if not props:
        return ""
    lines = ["---"]
    for key, val in props.items():
        if isinstance(val, list):
            lines.append(f"{key}:")
            for item in val:
                lines.append(f"  - {item}")
        elif isinstance(val, bool):
            lines.append(f"{key}: {'true' if val else 'false'}")
        else:
            lines.append(f"{key}: {val}")
    lines.append("---")
    return "\n".join(lines)


def _resolve_doc_id(path: str) -> str:
    """Resolve a path to a CouchDB document ID (lowercase)."""
    return path.lower()


# === TOOLS ===

# 1. obsidian-search
@mcp.tool()
async def obsidian_search(query: str, regex: bool = False) -> str:
    """Search notes in the vault by content.

    Args:
        query: Search query (text or regex pattern)
        regex: If true, treat query as regex pattern
    """
    results = await db.search_content(query, regex=regex)
    if not results:
        return "No matches found."
    return json.dumps(results, ensure_ascii=False, indent=2)


# 2. obsidian-fetch
@mcp.tool()
async def obsidian_fetch(path: str, include_properties: bool = True, include_stats: bool = False) -> str:
    """Fetch the content and metadata of a note, base, or canvas file.

    Args:
        path: Path to the file (e.g. "My Note.md", "DB.base", "Board.canvas")
        include_properties: Include frontmatter properties in response
        include_stats: Include word/character count statistics
    """
    doc_id = _resolve_doc_id(path)
    metadata = await db.read_note_metadata(doc_id)
    if metadata is None:
        return f"Note '{path}' not found."

    content = await db.read_note_content(doc_id)
    result: dict[str, Any] = {"path": metadata["path"]}

    if include_properties and content and path.lower().endswith(".md"):
        props, body = _parse_frontmatter(content)
        if props:
            result["properties"] = props
        result["content"] = body
    else:
        result["content"] = content

    result["ctime"] = metadata.get("ctime")
    result["mtime"] = metadata.get("mtime")

    if include_stats and content:
        text = content
        if path.lower().endswith(".md"):
            _, text = _parse_frontmatter(content)
        words = len(text.split())
        chars = len(text)
        lines = text.count("\n") + 1
        links = len(re.findall(r'\[\[([^\]]+)\]\]', text))
        result["stats"] = {
            "words": words,
            "characters": chars,
            "lines": lines,
            "links": links,
        }

    return json.dumps(result, ensure_ascii=False, indent=2)


# 3. obsidian-create-note
@mcp.tool()
async def obsidian_create_note(
    path: str,
    content: str = "",
    properties: str = "",
) -> str:
    """Create a new note (.md) or base (.base) file.

    Args:
        path: File path (e.g. "folder/My Note.md", "Tasks.base")
        content: Note content (markdown for .md, YAML for .base)
        properties: JSON string of frontmatter properties (for .md files)
    """
    doc_id = _resolve_doc_id(path)
    existing = await db.read_note_metadata(doc_id)
    if existing and not existing.get("deleted"):
        return f"Note '{path}' already exists."

    full_content = content
    if properties and path.lower().endswith(".md"):
        try:
            props = json.loads(properties)
        except json.JSONDecodeError:
            return "Invalid JSON in properties parameter."
        fm = _serialize_frontmatter(props)
        full_content = fm + "\n" + content if content else fm

    await db.write_note(path, full_content, create=True)
    return f"Created '{path}'."


# 4. obsidian-update-note
@mcp.tool()
async def obsidian_update_note(
    path: str,
    mode: str = "overwrite",
    content: str = "",
    search: str = "",
    replace: str = "",
    heading: str = "",
    regex: bool = False,
) -> str:
    """Update a note's content.

    Args:
        path: Path to the note
        mode: One of: overwrite, append, prepend, after-heading, search-replace
        content: New content (for overwrite/append/prepend/after-heading modes)
        search: Search string (for search-replace mode)
        replace: Replacement string (for search-replace mode)
        heading: Heading text to insert after (for after-heading mode)
        regex: Use regex for search-replace
    """
    doc_id = _resolve_doc_id(path)
    try:
        existing = await db.read_note_content(doc_id)
    except ValueError as e:
        return f"Error reading '{path}': {e}"
    if existing is None:
        return f"Note '{path}' not found."

    if mode == "overwrite":
        new_content = content
    elif mode == "append":
        new_content = existing + "\n" + content
    elif mode == "prepend":
        new_content = content + "\n" + existing
    elif mode == "after-heading":
        if not heading:
            return "Heading parameter required for after-heading mode."
        lines = existing.split("\n")
        inserted = False
        new_lines = []
        for i, line in enumerate(lines):
            new_lines.append(line)
            stripped = line.strip()
            if not inserted and stripped.startswith("#"):
                heading_text = stripped.lstrip("#").strip()
                if heading_text.lower() == heading.lower():
                    new_lines.append(content)
                    inserted = True
        if not inserted:
            return f"Heading '{heading}' not found in '{path}'."
        new_content = "\n".join(new_lines)
    elif mode == "search-replace":
        if not search:
            return "Search parameter required for search-replace mode."
        if regex:
            new_content = re.sub(search, replace, existing)
        else:
            new_content = existing.replace(search, replace)
        if new_content == existing:
            return "No matches found for search string."
    else:
        return f"Unknown mode: {mode}. Use: overwrite, append, prepend, after-heading, search-replace"

    await db.write_note(path, new_content)
    return f"Updated '{path}' ({mode})."


# 5. obsidian-delete-note
@mcp.tool()
async def obsidian_delete_note(path: str) -> str:
    """Delete a note from the vault.

    Args:
        path: Path to the note to delete
    """
    doc_id = _resolve_doc_id(path)
    success = await db.delete_note(doc_id)
    if success:
        return f"Deleted '{path}'."
    return f"Note '{path}' not found."


# 6. obsidian-move-note
@mcp.tool()
async def obsidian_move_note(path: str, new_path: str) -> str:
    """Move or rename a note.

    Args:
        path: Current path of the note
        new_path: New path for the note
    """
    doc_id = _resolve_doc_id(path)
    try:
        await db.rename_note(doc_id, new_path)
    except ValueError as e:
        return str(e)
    return f"Moved '{path}' -> '{new_path}'."


# 7. obsidian-duplicate-note
@mcp.tool()
async def obsidian_duplicate_note(path: str, new_path: str = "") -> str:
    """Duplicate a note.

    Args:
        path: Path to the note to duplicate
        new_path: Path for the copy (default: adds ' copy' before extension)
    """
    doc_id = _resolve_doc_id(path)
    content = await db.read_note_content(doc_id)
    if content is None:
        return f"Note '{path}' not found."
    if not new_path:
        base, ext = path.rsplit(".", 1) if "." in path else (path, "md")
        new_path = f"{base} copy.{ext}"
    await db.write_note(new_path, content, create=True)
    return f"Duplicated '{path}' -> '{new_path}'."


# 8. obsidian-list-notes
@mcp.tool()
async def obsidian_list_notes(
    folder: str = "",
    extension: str = "",
) -> str:
    """List notes in the vault, optionally filtered by folder and extension.

    Args:
        folder: Filter by folder path (e.g. "projects/"). Empty for root.
        extension: Filter by extension (e.g. ".md", ".base", ".canvas")
    """
    notes = await db.list_notes(
        folder=folder or None,
        extension=extension or None,
    )
    if not notes:
        return "No notes found."
    result = []
    for n in notes:
        entry = {"path": n["path"], "size": n["size"]}
        if n.get("mtime"):
            entry["modified"] = n["mtime"]
        result.append(entry)
    return json.dumps(result, ensure_ascii=False, indent=2)


# 9. obsidian-manage-properties
@mcp.tool()
async def obsidian_manage_properties(
    path: str,
    action: str = "get",
    properties: str = "",
    keys: str = "",
) -> str:
    """Manage frontmatter properties of a note.

    Args:
        path: Path to the .md note
        action: One of: get, set, delete
        properties: JSON string of properties to set (for 'set' action)
        keys: Comma-separated property keys to delete (for 'delete' action)
    """
    doc_id = _resolve_doc_id(path)
    content = await db.read_note_content(doc_id)
    if content is None:
        return f"Note '{path}' not found."

    props, body = _parse_frontmatter(content)

    if action == "get":
        if not props:
            return "No properties found."
        return json.dumps(props, ensure_ascii=False, indent=2)

    elif action == "set":
        if not properties:
            return "Properties parameter required for 'set' action."
        try:
            new_props = json.loads(properties)
        except json.JSONDecodeError:
            return "Invalid JSON in properties parameter."
        props.update(new_props)
        fm = _serialize_frontmatter(props)
        new_content = fm + "\n" + body
        await db.write_note(path, new_content)
        return f"Updated properties of '{path}'."

    elif action == "delete":
        if not keys:
            return "Keys parameter required for 'delete' action."
        for key in [k.strip() for k in keys.split(",")]:
            props.pop(key, None)
        if props:
            fm = _serialize_frontmatter(props)
            new_content = fm + "\n" + body
        else:
            new_content = body
        await db.write_note(path, new_content)
        return f"Deleted properties from '{path}'."

    return f"Unknown action: {action}. Use: get, set, delete"


# 10. obsidian-manage-tags
@mcp.tool()
async def obsidian_manage_tags(
    path: str,
    action: str = "list",
    tags: str = "",
) -> str:
    """Manage tags on a note (frontmatter and inline).

    Args:
        path: Path to the note
        action: One of: list, add, remove
        tags: Comma-separated tags (without #) for add/remove
    """
    doc_id = _resolve_doc_id(path)
    content = await db.read_note_content(doc_id)
    if content is None:
        return f"Note '{path}' not found."

    props, body = _parse_frontmatter(content)

    # Get current tags from frontmatter
    fm_tags = props.get("tags", [])
    if isinstance(fm_tags, str):
        fm_tags = [fm_tags]

    # Get inline tags
    inline_tags = re.findall(r'(?<!\w)#([\w/\-]+)', body)

    if action == "list":
        return json.dumps({
            "frontmatter_tags": fm_tags,
            "inline_tags": list(set(inline_tags)),
        }, ensure_ascii=False, indent=2)

    tag_list = [t.strip().lstrip("#") for t in tags.split(",") if t.strip()]
    if not tag_list:
        return "Tags parameter required."

    if action == "add":
        for tag in tag_list:
            if tag not in fm_tags:
                fm_tags.append(tag)
        props["tags"] = fm_tags
        fm = _serialize_frontmatter(props)
        new_content = fm + "\n" + body
        await db.write_note(path, new_content)
        return f"Added tags {tag_list} to '{path}'."

    elif action == "remove":
        fm_tags = [t for t in fm_tags if t not in tag_list]
        if fm_tags:
            props["tags"] = fm_tags
        else:
            props.pop("tags", None)
        fm = _serialize_frontmatter(props)
        new_content = fm + "\n" + body if props else body
        await db.write_note(path, new_content)
        return f"Removed tags {tag_list} from '{path}'."

    return f"Unknown action: {action}. Use: list, add, remove"


# 11. obsidian-get-all-tags
@mcp.tool()
async def obsidian_get_all_tags() -> str:
    """Get all tags across the vault with usage counts."""
    tags = await db.get_all_tags()
    if not tags:
        return "No tags found."
    sorted_tags = sorted(tags.items(), key=lambda x: x[1], reverse=True)
    return json.dumps(dict(sorted_tags), ensure_ascii=False, indent=2)


# 12. obsidian-get-links
@mcp.tool()
async def obsidian_get_links(
    path: str,
    direction: str = "both",
) -> str:
    """Get links for a note.

    Args:
        path: Path to the note
        direction: One of: out (outgoing), in (backlinks), both
    """
    doc_id = _resolve_doc_id(path)
    result: dict[str, Any] = {"path": path}

    if direction in ("out", "both"):
        links = await db.get_links_from_note(doc_id)
        result["outgoing"] = links

    if direction in ("in", "both"):
        # Use the path without extension for backlink search
        backlinks = await db.get_backlinks(path)
        result["backlinks"] = backlinks

    return json.dumps(result, ensure_ascii=False, indent=2)


# 13. obsidian-get-link-graph
@mcp.tool()
async def obsidian_get_link_graph() -> str:
    """Get the full link graph of the vault (all nodes and edges)."""
    graph = await db.get_link_graph()
    return json.dumps(graph, ensure_ascii=False, indent=2)


# 14. obsidian-find-orphaned-notes
@mcp.tool()
async def obsidian_find_orphaned_notes() -> str:
    """Find notes that have no incoming or outgoing links."""
    graph = await db.get_link_graph()
    connected = set()
    for edge in graph["edges"]:
        connected.add(edge["from"])
        connected.add(edge["to"])
    orphans = [n for n in graph["nodes"] if n not in connected]
    if not orphans:
        return "No orphaned notes found."
    return json.dumps(orphans, ensure_ascii=False, indent=2)


# 15. obsidian-find-hub-notes
@mcp.tool()
async def obsidian_find_hub_notes(min_links: int = 3) -> str:
    """Find the most connected notes in the vault.

    Args:
        min_links: Minimum total links (in + out) to be considered a hub
    """
    graph = await db.get_link_graph()
    link_counts: dict[str, dict[str, int]] = {}
    for node in graph["nodes"]:
        link_counts[node] = {"in": 0, "out": 0}
    for edge in graph["edges"]:
        src = edge["from"]
        tgt = edge["to"]
        if src in link_counts:
            link_counts[src]["out"] += 1
        if tgt in link_counts:
            link_counts[tgt]["in"] += 1
    hubs = []
    for node, counts in link_counts.items():
        total = counts["in"] + counts["out"]
        if total >= min_links:
            hubs.append({"path": node, "incoming": counts["in"], "outgoing": counts["out"], "total": total})
    hubs.sort(key=lambda x: x["total"], reverse=True)
    if not hubs:
        return f"No notes with {min_links}+ links found."
    return json.dumps(hubs, ensure_ascii=False, indent=2)


# 16. obsidian-canvas-node
@mcp.tool()
async def obsidian_canvas_node(
    path: str,
    action: str = "add",
    node_type: str = "text",
    text: str = "",
    file: str = "",
    url: str = "",
    x: int = 0,
    y: int = 0,
    width: int = 250,
    height: int = 60,
    node_id: str = "",
) -> str:
    """Add or remove a node in a canvas file.

    Args:
        path: Path to the .canvas file
        action: "add" or "remove"
        node_type: Type of node: text, file, link, group
        text: Text content (for text/group nodes)
        file: File path (for file nodes)
        url: URL (for link nodes)
        x: X position
        y: Y position
        width: Node width
        height: Node height
        node_id: Node ID (required for remove)
    """
    doc_id = _resolve_doc_id(path)
    content = await db.read_note_content(doc_id)
    if content is None and action != "add":
        return f"Canvas '{path}' not found."

    try:
        canvas = json.loads(content) if content else {"nodes": [], "edges": []}
    except json.JSONDecodeError:
        return "Invalid canvas format."

    if action == "add":
        new_id = node_id or secrets.token_hex(8)
        node: dict[str, Any] = {
            "id": new_id,
            "type": node_type,
            "x": x, "y": y,
            "width": width, "height": height,
        }
        if node_type == "text":
            node["text"] = text
        elif node_type == "file":
            node["file"] = file
        elif node_type == "link":
            node["url"] = url
        elif node_type == "group":
            node["label"] = text
        canvas["nodes"].append(node)
        await db.write_note(path, json.dumps(canvas, ensure_ascii=False), create=True)
        return f"Added {node_type} node '{new_id}' to '{path}'."

    elif action == "remove":
        if not node_id:
            return "node_id required for remove action."
        canvas["nodes"] = [n for n in canvas["nodes"] if n["id"] != node_id]
        canvas["edges"] = [e for e in canvas["edges"] if e.get("fromNode") != node_id and e.get("toNode") != node_id]
        await db.write_note(path, json.dumps(canvas, ensure_ascii=False))
        return f"Removed node '{node_id}' from '{path}'."

    return f"Unknown action: {action}. Use: add, remove"


# 17. obsidian-canvas-edge
@mcp.tool()
async def obsidian_canvas_edge(
    path: str,
    action: str = "add",
    from_node: str = "",
    to_node: str = "",
    label: str = "",
    edge_id: str = "",
) -> str:
    """Add or remove an edge in a canvas file.

    Args:
        path: Path to the .canvas file
        action: "add" or "remove"
        from_node: Source node ID (for add)
        to_node: Target node ID (for add)
        label: Edge label (optional)
        edge_id: Edge ID (required for remove)
    """
    doc_id = _resolve_doc_id(path)
    content = await db.read_note_content(doc_id)
    if content is None:
        return f"Canvas '{path}' not found."

    try:
        canvas = json.loads(content)
    except json.JSONDecodeError:
        return "Invalid canvas format."

    if action == "add":
        if not from_node or not to_node:
            return "from_node and to_node required for add action."
        new_id = edge_id or secrets.token_hex(8)
        edge: dict[str, Any] = {
            "id": new_id,
            "fromNode": from_node,
            "toNode": to_node,
        }
        if label:
            edge["label"] = label
        canvas["edges"].append(edge)
        await db.write_note(path, json.dumps(canvas, ensure_ascii=False))
        return f"Added edge '{new_id}' ({from_node} -> {to_node}) to '{path}'."

    elif action == "remove":
        if not edge_id:
            return "edge_id required for remove action."
        canvas["edges"] = [e for e in canvas["edges"] if e["id"] != edge_id]
        await db.write_note(path, json.dumps(canvas, ensure_ascii=False))
        return f"Removed edge '{edge_id}' from '{path}'."

    return f"Unknown action: {action}. Use: add, remove"


# 18. obsidian-vault-stats
@mcp.tool()
async def obsidian_vault_stats() -> str:
    """Get aggregate statistics for the entire vault."""
    notes = await db.list_notes()
    total = len(notes)
    by_ext: dict[str, int] = {}
    total_size = 0
    for n in notes:
        path = n["path"]
        ext = path.rsplit(".", 1)[-1] if "." in path else "unknown"
        by_ext[ext] = by_ext.get(ext, 0) + 1
        total_size += n.get("size", 0)

    tags = await db.get_all_tags()

    return json.dumps({
        "total_notes": total,
        "by_extension": by_ext,
        "total_size_bytes": total_size,
        "total_tags": len(tags),
    }, ensure_ascii=False, indent=2)


# 19. obsidian-create-from-template
@mcp.tool()
async def obsidian_create_from_template(
    template_path: str,
    new_path: str,
    variables: str = "",
) -> str:
    """Create a new note from a template, replacing {{variables}}.

    Args:
        template_path: Path to the template note
        new_path: Path for the new note
        variables: JSON string of variable replacements (e.g. {"title": "My Note", "date": "2026-03-20"})
    """
    doc_id = _resolve_doc_id(template_path)
    content = await db.read_note_content(doc_id)
    if content is None:
        return f"Template '{template_path}' not found."

    if variables:
        try:
            vars_dict = json.loads(variables)
        except json.JSONDecodeError:
            return "Invalid JSON in variables parameter."
        for key, val in vars_dict.items():
            content = content.replace("{{" + key + "}}", str(val))

    new_doc_id = _resolve_doc_id(new_path)
    existing = await db.read_note_metadata(new_doc_id)
    if existing and not existing.get("deleted"):
        return f"Note '{new_path}' already exists."

    await db.write_note(new_path, content, create=True)
    return f"Created '{new_path}' from template '{template_path}'."


# 20. obsidian-list-templates
@mcp.tool()
async def obsidian_list_templates(folder: str = "templates") -> str:
    """List available templates in the templates folder.

    Args:
        folder: Templates folder path (default: "templates")
    """
    notes = await db.list_notes(folder=folder)
    if not notes:
        return f"No templates found in '{folder}/'."
    return json.dumps(
        [{"path": n["path"], "size": n["size"]} for n in notes],
        ensure_ascii=False, indent=2,
    )


# --- Main ---
if __name__ == "__main__":
    mcp.run(transport="sse")
