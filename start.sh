#!/usr/bin/env bash
set -e

if [ $# -gt 0 ]; then
    exec "$@"
else
    exec python3 server.py
fi
