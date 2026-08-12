#!/usr/bin/env sh
set -eu
if [ "$#" -gt 0 ]; then
  exec "$@"
fi
exec ha-mcp-readonly
