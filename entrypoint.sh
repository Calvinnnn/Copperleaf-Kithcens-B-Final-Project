#!/bin/sh

# Stop on errors
set -e

echo "Checking SQLite database..."
if [ ! -f "/app/db/copperleaf.db" ]; then
    echo "Database /app/db/copperleaf.db not found. Initializing database..."
    python mcp_server/init_db.py
else
    echo "Database found. Skipping initialization."
fi

echo "Starting Starlette Backend API and MCP server..."
exec python start.py
