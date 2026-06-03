#!/bin/bash
set -e
cd "$HOME/Local/Github/RAiner"
exec poetry run python -m rainer.mcp_server
