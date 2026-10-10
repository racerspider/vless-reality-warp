# Security and publication

Keep `.env`, `secrets/`, `runtime/`, `data/`, and backups private. A public REALITY
key alone is not secret, but the complete client configuration grants access.
The generated server identity and WARP registration must never be published.

Runtime parent directories are `0700`. Config files mounted into non-root
containers are `0644` **inside those protected directories**, so the container
UID can read them without making the host directory traversable. Do not move
them into a shared directory. Docker administrators can read container secrets.

Review `git diff --cached` before every commit. Run `make check` after staging;
its privacy check reads staged blobs, not just the working tree. The scanner
is a guard against common mistakes, not proof that arbitrary text is safe.
Inspect deployment-specific domains, IPs, usernames, screenshots, logs, and commit
author metadata yourself. Do not use `git add -f` on ignored private files.

Use private vulnerability reporting on GitHub when available. Do not put tokens,
client URIs, registration files, or live addresses in public issues. If a secret
has been shared, rotate it; deleting the visible text does not revoke access.
