"""Scan reachable Git blobs and paths; print findings by type, path and commit only."""

import json
import re
from pathlib import PurePosixPath

from scripts.build_windows_rc import git

SIGNATURES = {
    "private_key": rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----",
    "github_token": rb"gh[pousr]_[a-zA-Z0-9]{36,}|github_pat_[a-zA-Z0-9_]{40,}",
    "aws_key": rb"(?:AKIA|ASIA)[A-Z0-9]{16}",
    "slack_token": rb"xox[baprs]-[a-zA-Z0-9-]{16,}",
    "account_or_credential_literal": rb'''["'](?:account_number|accountId|api_key|access_token|password)["']\s*:\s*["'][A-Za-z0-9_+/=-]{8,}["']''',
    "sqlite_capture": rb"\ASQLite format 3\x00",
    "private_checkpoint": rb'"capture_checkpoint"\s*:\s*\{.*?"session_id"\s*:\s*"[0-9a-f]{32}"',
}
PATTERNS = {name: re.compile(value, re.DOTALL) for name, value in SIGNATURES.items()}
MAX_BLOB_BYTES = 16 * 1024 * 1024


def path_risk(path):
    p = PurePosixPath(path.casefold())
    if p.name in {".env", ".credentials", "id_rsa", "id_ed25519"} or (
        p.name.startswith(".env.") and p.name != ".env.example"
    ):
        return "credential_path"
    if (p.suffix in {".db", ".parquet", ".duckdb", ".pcap", ".pcapng", ".zip", ".pem", ".key", ".pfx"}
            or ".sqlite" in p.name
            or any(part in {".venv", "captures"} for part in p.parts)
            or (p.suffix in {".json", ".jsonl"} and p.stem.endswith(("checkpoint", "diagnostic", "capture")))):
        return "private_artifact_path"
    return None


def main():
    refs = git("for-each-ref", "--format=%(refname)").decode().splitlines()
    shallow = git("rev-parse", "--is-shallow-repository").strip() == b"true"
    findings = []
    paths = set(git("log", "--all", "HEAD", "--format=", "--name-only", "-z").decode().split("\0")) - {""}
    for path in sorted(paths):
        if kind := path_risk(path):
            commit = git("log", "--all", "HEAD", "-1", "--format=%H", "--", path).decode().strip()
            findings.append({"path": path, "commit": commit, "type": kind})
    scanned = 0
    for obj in git("rev-list", "--objects", "--all", "HEAD").decode().splitlines():
        oid, _, path = obj.partition(" ")
        if not path or git("cat-file", "-t", oid).strip() != b"blob":
            continue
        size = int(git("cat-file", "-s", oid))
        kinds = ["unscanned_oversize_blob"] if size > MAX_BLOB_BYTES else []
        if not kinds:
            data = git("cat-file", "blob", oid)
            scanned += 1
            kinds = [kind for kind, signature in PATTERNS.items() if signature.search(data)]
        if kinds:
            commit = git("log", "--all", "HEAD", "-1", "--format=%H", f"--find-object={oid}").decode().strip()
            findings.extend({"path": path, "commit": commit, "type": kind} for kind in kinds)
    print(json.dumps({"basis": "reachable Git objects only", "shallow": shallow, "refs": len(refs),
                      "paths": len(paths), "blobs_scanned": scanned, "findings": findings}))
    return 1 if findings or shallow else 0


if __name__ == "__main__":
    raise SystemExit(main())
