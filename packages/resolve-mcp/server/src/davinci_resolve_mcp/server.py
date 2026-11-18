"""Entry point used by the installed console script.

Delegates to json_server.py so the running MCP row returns JSON objects instead
of prose. The original string-returning implementation is kept as
server_legacy.py for reference.
"""
import asyncio
import os
import sys

APP_DIR = "/Users/zheltukheen/Documents/адб/resolve-mcp/server"


def main():
    sys.path.insert(0, APP_DIR)
    import json_server

    asyncio.run(json_server.main())


if __name__ == "__main__":
    main()
