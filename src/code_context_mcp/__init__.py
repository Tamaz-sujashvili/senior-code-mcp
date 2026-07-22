"""code-context-mcp: MCP server for semantic + structural code search.

Lets a coding agent search a codebase two ways:
- semantic similarity via Qdrant vector store
- structural relationships (calls/imports) via a tree-sitter-built graph

Goal: agent reuses existing patterns instead of guessing, and we stop
pasting whole files into context.
"""

__version__ = "0.1.0"
