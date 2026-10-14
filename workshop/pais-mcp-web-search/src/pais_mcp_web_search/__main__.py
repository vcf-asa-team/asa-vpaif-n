from __future__ import annotations

import os

import uvicorn

from .config import transport_security_from_env
from .server import mcp


def main() -> None:
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8000"))
    app = mcp.streamable_http_app(transport_security=transport_security_from_env())
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
