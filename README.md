# obsidian-livesync-mcp

Remote MCP server for accessing your Obsidian vault via CouchDB / [Self-hosted LiveSync](https://github.com/vrtmrz/obsidian-livesync). Works with Claude Code, Claude.ai, and any MCP-compatible client.

No Obsidian desktop app required on the server — connects directly to CouchDB.

## Architecture

```
Obsidian (phone/desktop) <-> CouchDB <-> MCP Server <-> Claude Code / Claude.ai
```

Obsidian clients sync via the LiveSync plugin to a CouchDB instance. This MCP server reads and writes directly to CouchDB, providing 20 tools for managing your vault — no Obsidian REST API or desktop app needed on the server side.

## Features

### Notes
- **search** — Full-text search (text/regex)
- **fetch** — Get content, properties, and stats (.md/.base/.canvas)
- **create_note** — Create notes with frontmatter
- **update_note** — Modify (overwrite/append/prepend/after-heading/search-replace)
- **delete_note** — Delete notes
- **move_note** — Move/rename
- **duplicate_note** — Duplicate
- **list_notes** — List files with folder/extension filters

### Properties & Tags
- **manage_properties** — Get/set/delete frontmatter properties
- **manage_tags** — Add/remove/list tags
- **get_all_tags** — All vault tags with counts

### Graph & Links
- **get_links** — Outgoing/incoming links
- **get_link_graph** — Full vault graph
- **find_orphaned_notes** — Notes with no links
- **find_hub_notes** — Most connected notes

### Canvas
- **canvas_node** — Add/remove canvas nodes
- **canvas_edge** — Add/remove canvas edges

### Other
- **vault_stats** — Vault statistics
- **create_from_template** — Create from template with variable substitution
- **list_templates** — List available templates

## Prerequisites

- CouchDB with Obsidian LiveSync data
- Python 3.11+

## Setup

### 1. Install

```bash
git clone https://github.com/YOUR_USERNAME/obsidian-livesync-mcp.git
cd obsidian-livesync-mcp
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your settings
```

| Variable | Description |
|---|---|
| `COUCHDB_URL` | CouchDB URL (e.g. `http://localhost:5984`) |
| `COUCHDB_USER` | CouchDB admin username |
| `COUCHDB_PASSWORD` | CouchDB admin password |
| `COUCHDB_DATABASE` | Database name (default: `obsidian`) |
| `OAUTH_PASSWORD` | Password for OAuth consent (leave empty to disable OAuth) |
| `OAUTH_CLIENT_ID` | OAuth client ID for pre-registered client |
| `OAUTH_CLIENT_SECRET` | OAuth client secret |
| `MCP_SERVER_URL` | Public HTTPS URL of this server |
| `MCP_HOST` | Bind address (default: `0.0.0.0`) |
| `MCP_PORT` | Port (default: `8484`) |

### 3. Run

```bash
python run.py
```

### 4. Systemd service (optional)

```bash
sudo cp obsidian-mcp.service /etc/systemd/system/
# Edit the service file: set User and paths
sudo systemctl daemon-reload
sudo systemctl enable obsidian-mcp
sudo systemctl start obsidian-mcp
```

## Connecting Clients

### Claude Code (CLI)

```bash
claude mcp add --transport http obsidian https://your-server/mcp
```

### Claude.ai (Web)

1. Settings > Connectors > Add custom connector
2. URL: `https://your-server/mcp`
3. Client ID and Client Secret from your `.env`

## Authentication

The server supports OAuth 2.1 with authorization code flow + PKCE.

### Current limitations

Out of the box, only **Claude** (Claude.ai and Claude Code) is supported as a client. The pre-registered OAuth client has hardcoded `redirect_uris` for Claude's callback URLs and `scope: claudeai`.

### Adding other MCP clients

To support other clients you can either:

1. **Pre-register additional clients** — add more clients in `oauth_provider.py` `__init__` with the appropriate `redirect_uris` for your client.

2. **Enable Dynamic Client Registration (DCR)** — in `server.py`, change `ClientRegistrationOptions(enabled=False, ...)` to `enabled=True`. This allows any MCP-compatible client to register itself automatically. Note: this means anyone who knows your server URL can register a client.

## License

Apache 2.0
