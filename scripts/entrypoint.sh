#!/usr/bin/env sh
set -eu

/app/scripts/init_storage.sh
python /app/scripts/wait_for_storage.py

exec "$@"
