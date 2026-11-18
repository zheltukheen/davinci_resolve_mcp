"""Entry point used by the installed console script.

Delegates to json_server.py so the running MCP row returns JSON objects instead
of prose. The original string-returning implementation is kept as
server_legacy.py for reference.
"""
import asyncio
import os
import sys

# server/src/davinci_resolve_mcp/server.py -> server/
APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    if APP_DIR not in sys.path:
        sys.path.insert(0, APP_DIR)
    import json_server

    asyncio.run(json_server.main())


if __name__ == "__main__":
    main()
