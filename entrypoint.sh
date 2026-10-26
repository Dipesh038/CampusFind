#!/bin/sh
set -e

# Initialize database schema if not already initialized (safe and additive)
python -m flask --app campusfind init-db

# Execute the given command
exec "$@"
