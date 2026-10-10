#!/usr/bin/env python3
"""Scan exactly the Git index, including staged blobs, before a public commit."""
import re
import subprocess
import sys
from pathlib import PurePosixPath

patterns = [
    rb"gh[pousr]_" + rb"[A-Za-z0-9]{20,}",
    rb"github_pat_" + rb"[A-Za-z0-9_]{20,}",
    rb"-----BEGIN " + rb"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    rb"(?:/Users/|/home/)" + rb"[A-Za-z0-9_.-]+/",
    rb"https?://[^\s/]+:[^\s/@]+@",
]
failures = []
paths = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
for name in filter(None, paths):
    path = PurePosixPath(name)
    if (set(path.parts) & {"secrets", "runtime", "data", "backups", ".ssh", "_private"}
            or (path.name.startswith(".env") and path.name != ".env.example")
            or path.suffix in {".pem", ".key", ".jsonl", ".log"}):
        failures.append(f"{name}: private artifact path")
    content = subprocess.check_output(["git", "show", ":" + name])
    if any(re.search(pattern, content) for pattern in patterns):
        failures.append(f"{name}: credential or machine-path pattern")
if failures:
    print("\n".join(failures), file=sys.stderr)
    sys.exit(1)
print("Git index privacy checks passed (heuristic; review the staged diff too).")
