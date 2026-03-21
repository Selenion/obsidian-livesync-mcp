"""CouchDB client for Obsidian LiveSync."""

import base64
import hashlib
import time
import urllib.parse
from typing import Any

import httpx


class CouchDBClient:
    """Client for interacting with CouchDB storing Obsidian LiveSync data."""

    def __init__(self, url: str, username: str, password: str, database: str):
        self.url = url.rstrip("/")
        self.database = database
        self.auth = (username, password)
        self._client = httpx.AsyncClient(
            auth=self.auth,
            timeout=30.0,
        )

    @property
    def db_url(self) -> str:
        return f"{self.url}/{self.database}"

    async def close(self):
        await self._client.aclose()

    def _doc_url(self, doc_id: str) -> str:
        return f"{self.db_url}/{urllib.parse.quote(doc_id, safe='')}"

    async def get_doc(self, doc_id: str) -> dict | None:
        resp = await self._client.get(self._doc_url(doc_id))
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    async def put_doc(self, doc_id: str, doc: dict) -> dict:
        resp = await self._client.put(self._doc_url(doc_id), json=doc)
        resp.raise_for_status()
        return resp.json()

    async def delete_doc(self, doc_id: str, rev: str) -> dict:
        resp = await self._client.delete(
            self._doc_url(doc_id), params={"rev": rev}
        )
        resp.raise_for_status()
        return resp.json()

    async def all_docs(
        self, include_docs: bool = False, **params
    ) -> list[dict]:
        p = {**params}
        if include_docs:
            p["include_docs"] = "true"
        resp = await self._client.get(f"{self.db_url}/_all_docs", params=p)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])

    async def find(self, selector: dict, fields: list[str] | None = None, limit: int = 100) -> list[dict]:
        body: dict[str, Any] = {"selector": selector, "limit": limit}
        if fields:
            body["fields"] = fields
        resp = await self._client.post(f"{self.db_url}/_find", json=body)
        resp.raise_for_status()
        return resp.json().get("docs", [])

    # --- LiveSync specific methods ---

    async def list_notes(self, folder: str | None = None, extension: str | None = None) -> list[dict]:
        """List all note metadata documents (not chunks)."""
        rows = await self.all_docs(include_docs=True)
        notes = []
        for row in rows:
            doc = row.get("doc", {})
            doc_id = doc.get("_id", "")
            # Skip chunks, design docs, and internal docs
            if doc_id.startswith("h:") or doc_id.startswith("_") or doc_id == "obsydian_livesync_version":
                continue
            path = doc.get("path", doc_id)
            if extension and not path.lower().endswith(extension.lower()):
                continue
            if folder:
                norm_folder = folder.strip("/")
                path_dir = "/".join(path.split("/")[:-1])
                if norm_folder and path_dir.lower() != norm_folder.lower():
                    continue
                if not norm_folder and "/" in path:
                    continue
            notes.append({
                "id": doc_id,
                "path": path,
                "ctime": doc.get("ctime"),
                "mtime": doc.get("mtime"),
                "size": doc.get("size"),
                "type": doc.get("type"),
                "deleted": doc.get("deleted", False),
            })
        return [n for n in notes if not n.get("deleted")]

    async def read_note_content(self, doc_id: str) -> str | None:
        """Read the full content of a note by assembling its chunks."""
        doc = await self.get_doc(doc_id)
        if doc is None:
            return None
        children = doc.get("children", [])
        if not children:
            return ""
        parts = []
        for child_id in children:
            chunk = await self.get_doc(child_id)
            if chunk is None:
                continue
            data = chunk.get("data", "")
            parts.append(data)
        return "".join(parts)

    async def read_note_metadata(self, doc_id: str) -> dict | None:
        """Read note metadata (without content)."""
        doc = await self.get_doc(doc_id)
        if doc is None:
            return None
        return {
            "id": doc.get("_id"),
            "path": doc.get("path", doc.get("_id")),
            "ctime": doc.get("ctime"),
            "mtime": doc.get("mtime"),
            "size": doc.get("size"),
            "type": doc.get("type"),
            "deleted": doc.get("deleted", False),
            "children_count": len(doc.get("children", [])),
        }

    def _generate_chunk_id(self) -> str:
        """Generate a unique chunk ID in LiveSync format."""
        ts = str(time.time_ns())
        h = hashlib.md5(ts.encode()).hexdigest()[:16]
        return f"h:{h}"

    async def write_note(
        self, path: str, content: str, create: bool = False
    ) -> dict:
        """Write content to a note, replacing all chunks."""
        doc_id = path.lower()
        doc = await self.get_doc(doc_id)
        now = int(time.time() * 1000)

        # Delete old chunks
        if doc and "children" in doc:
            for child_id in doc["children"]:
                chunk = await self.get_doc(child_id)
                if chunk:
                    await self.delete_doc(child_id, chunk["_rev"])

        # Create new chunk
        chunk_id = self._generate_chunk_id()
        await self.put_doc(chunk_id, {
            "_id": chunk_id,
            "data": content,
            "type": "leaf",
        })

        # Update or create metadata doc
        if doc:
            doc["children"] = [chunk_id]
            doc["mtime"] = now
            doc["size"] = len(content.encode("utf-8"))
            if "deleted" in doc:
                del doc["deleted"]
            result = await self.put_doc(doc_id, doc)
        elif create:
            new_doc = {
                "_id": doc_id,
                "children": [chunk_id],
                "path": path,
                "ctime": now,
                "mtime": now,
                "size": len(content.encode("utf-8")),
                "type": "newnote" if not doc_id.endswith(".md") else "plain",
                "eden": {},
            }
            result = await self.put_doc(doc_id, new_doc)
        else:
            raise ValueError(f"Note '{path}' not found. Use create=True to create.")
        return result

    async def delete_note(self, doc_id: str) -> bool:
        """Delete a note and all its chunks."""
        doc = await self.get_doc(doc_id)
        if doc is None:
            return False
        # Delete chunks
        for child_id in doc.get("children", []):
            chunk = await self.get_doc(child_id)
            if chunk:
                await self.delete_doc(child_id, chunk["_rev"])
        # Delete metadata
        await self.delete_doc(doc_id, doc["_rev"])
        return True

    async def rename_note(self, old_id: str, new_path: str) -> dict:
        """Rename/move a note by creating new doc and deleting old."""
        content = await self.read_note_content(old_id)
        if content is None:
            raise ValueError(f"Note '{old_id}' not found")
        old_doc = await self.get_doc(old_id)
        result = await self.write_note(new_path, content, create=True)
        # Preserve original creation time
        new_id = new_path.lower()
        new_doc = await self.get_doc(new_id)
        if new_doc and old_doc:
            new_doc["ctime"] = old_doc.get("ctime", new_doc["ctime"])
            await self.put_doc(new_id, new_doc)
        await self.delete_note(old_id)
        return result

    async def search_content(self, query: str, regex: bool = False) -> list[dict]:
        """Search all notes for content matching query."""
        import re
        notes = await self.list_notes()
        results = []
        pattern = re.compile(query, re.IGNORECASE) if regex else None
        for note in notes:
            content = await self.read_note_content(note["id"])
            if content is None:
                continue
            if regex and pattern:
                matches = pattern.findall(content)
                if matches:
                    results.append({
                        "path": note["path"],
                        "matches": matches[:10],
                    })
            elif not regex and query.lower() in content.lower():
                # Find context around match
                idx = content.lower().index(query.lower())
                start = max(0, idx - 50)
                end = min(len(content), idx + len(query) + 50)
                results.append({
                    "path": note["path"],
                    "context": content[start:end],
                })
        return results

    async def get_all_tags(self) -> dict[str, int]:
        """Get all tags across the vault with usage counts."""
        import re
        notes = await self.list_notes(extension=".md")
        tag_counts: dict[str, int] = {}
        for note in notes:
            content = await self.read_note_content(note["id"])
            if content is None:
                continue
            # Frontmatter tags
            if content.startswith("---"):
                end = content.find("---", 3)
                if end != -1:
                    frontmatter = content[3:end]
                    in_tags = False
                    for line in frontmatter.split("\n"):
                        stripped = line.strip()
                        if stripped.startswith("tags:"):
                            val = stripped[5:].strip()
                            if val.startswith("["):
                                # Inline array
                                for t in re.findall(r'[\w/\-]+', val):
                                    tag_counts[t] = tag_counts.get(t, 0) + 1
                            elif val:
                                tag_counts[val] = tag_counts.get(val, 0) + 1
                            else:
                                in_tags = True
                            continue
                        if in_tags:
                            if stripped.startswith("- "):
                                t = stripped[2:].strip().strip('"').strip("'")
                                if t:
                                    tag_counts[t] = tag_counts.get(t, 0) + 1
                            else:
                                in_tags = False
            # Inline tags
            for tag in re.findall(r'(?<!\w)#([\w/\-]+)', content):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        return tag_counts

    async def get_links_from_note(self, doc_id: str) -> list[str]:
        """Get outgoing wikilinks from a note."""
        import re
        content = await self.read_note_content(doc_id)
        if content is None:
            return []
        links = re.findall(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', content)
        return links

    async def get_backlinks(self, target_path: str) -> list[dict]:
        """Find all notes linking to the target."""
        import re
        target_name = target_path.rsplit("/", 1)[-1]
        if "." in target_name:
            target_name = target_name.rsplit(".", 1)[0]
        notes = await self.list_notes(extension=".md")
        backlinks = []
        for note in notes:
            content = await self.read_note_content(note["id"])
            if content is None:
                continue
            links = re.findall(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', content)
            for link in links:
                link_name = link.rsplit("/", 1)[-1]
                if link_name.lower() == target_name.lower():
                    backlinks.append({
                        "path": note["path"],
                        "link_text": link,
                    })
                    break
        return backlinks

    async def get_link_graph(self) -> dict:
        """Build full link graph of the vault."""
        import re
        notes = await self.list_notes(extension=".md")
        nodes = []
        edges = []
        for note in notes:
            nodes.append(note["path"])
            content = await self.read_note_content(note["id"])
            if content is None:
                continue
            links = re.findall(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', content)
            for link in links:
                edges.append({"from": note["path"], "to": link})
        return {"nodes": nodes, "edges": edges}
