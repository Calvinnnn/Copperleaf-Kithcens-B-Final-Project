"""
server_bridge.py

Bridge module: start.py imports `server_bridge.app` from this file.
We simply re-export the Starlette ASGI app defined in backend.py.
"""

from backend import app  # noqa: F401  (re-export for start.py)
