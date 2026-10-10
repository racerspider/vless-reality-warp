#!/bin/sh
set -eu
cd "$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
# .env contains only non-secret settings; never source it as shell code.
exec docker compose --env-file versions.env --env-file .env "$@"
