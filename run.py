"""Entry point for the Obsidian MCP Server."""

from server import mcp

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
