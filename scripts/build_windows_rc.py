"""Build and verify a reproducible Windows source/launcher ZIP from a Git commit."""

import argparse
from hashlib import sha256
import json
import platform
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tomllib
from zipfile import BadZipFile, ZIP_STORED, ZipFile, ZipInfo

METADATA = "RELEASE.json"
ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "README.md", "pyproject.toml", "uv.lock", "docs/WINDOWS_LIVE_VALIDATION.md",
    "observatory/__main__.py", "observatory/static/index.html", "scripts/windows/install.ps1",
    "scripts/windows/preflight.ps1", "scripts/windows/run-mock.ps1", "scripts/windows/run-live.ps1",
    "scripts/windows/smoke.ps1", "scripts/windows/stop.ps1", "scripts/windows_acceptance.py",
}
SECRETS = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{40,}|AKIA[A-Z0-9]{16}")


def release_path(name):
    parts = PurePosixPath(name).parts
    if not parts or "/".join(parts) != name or any(
        part in ("..", "/") or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", part)
        for part in parts
    ):
        return False
    return (
        name in {"README.md", "pyproject.toml", "uv.lock", "scripts/windows_acceptance.py",
                 "scripts/benchmark_phase02.py", "scripts/benchmark_phase03.py"}
        or (parts[0] == "observatory" and PurePosixPath(name).suffix in {".py", ".html", ".js", ".css"})
        or (len(parts) == 2 and parts[0] == "docs" and name.endswith(".md")
            and parts[1] not in {"CODEX_FULL_PROJECT_DELIVERY_MASTER_PROMPT.md",
                                 "GC_RC_RELEASE_CLOSEOUT_MASTER_PROMPT.md"})
        or (len(parts) == 3 and parts[:2] == ("scripts", "windows") and name.endswith(".ps1"))
    )


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def source_files(sha):
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or git("rev-parse", sha + "^{commit}").decode().strip() != sha:
        raise ValueError("Expected an exact Git commit SHA")
    files = {}
    for entry in git("ls-tree", "-rz", sha).decode().split("\0"):
        if not entry:
            continue
        header, name = entry.split("\t", 1)
        if not release_path(name):
            continue
        mode, kind, oid = header.split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError(f"Non-regular file in release: {name}")
        files[name] = git("cat-file", "blob", oid)
    if not REQUIRED <= files.keys() or len(files) != len({name.casefold() for name in files}):
        raise ValueError("Commit lacks required or unambiguous Windows release files")
    return files


def filename(version, sha):
    return f"gc-live-observatory-{version}-rc-{sha[:12]}-windows.zip"


def verify(artifact, expected_sha):
    checksum = sha256(artifact.read_bytes()).hexdigest()
    sidecar = artifact.with_suffix(".zip.sha256")
    if sidecar.read_text(encoding="ascii").split() != [checksum, artifact.name]:
        raise ValueError("ZIP checksum mismatch")
    with ZipFile(artifact) as archive:
        names = archive.namelist()
        if len(names) != len(set(name.casefold() for name in names)) or names.count(METADATA) != 1:
            raise ValueError("Duplicate paths or missing release metadata")
        manifest = json.loads(archive.read(METADATA))
        files = {}
        for entry in archive.infolist():
            if entry.compress_type != ZIP_STORED or entry.date_time != (1980, 1, 1, 0, 0, 0):
                raise ValueError("Unexpected archive encoding")
            if entry.filename == METADATA:
                continue
            if entry.is_dir() or not release_path(entry.filename) or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError(f"Unexpected archive member: {entry.filename}")
            content = archive.read(entry)
            if SECRETS.search(content):
                raise ValueError(f"Credential signature in {entry.filename}")
            files[entry.filename] = sha256(content).hexdigest()
        version = tomllib.loads(archive.read("pyproject.toml").decode("utf-8"))["project"]["version"]
    if (manifest != {"schema_version": 1, "version": version, "kind": "windows-source-rc",
                     "source_sha": expected_sha, "files": files}
            or artifact.name != filename(version, expected_sha)
            or files != {name: sha256(data).hexdigest() for name, data in source_files(expected_sha).items()}):
        raise ValueError("Release metadata, source SHA or file inventory mismatch")
    return {"file": artifact.name, "version": version, "bytes": artifact.stat().st_size,
            "sha256": checksum, "source_sha": expected_sha,
            "build_os": platform.system(), "python": platform.python_version(),
            "git": git("--version").decode().strip(), "zip_encoding": "stored"}


def build(output, sha):
    contents = source_files(sha)
    version = tomllib.loads(contents["pyproject.toml"].decode("utf-8"))["project"]["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid application version")
    contents[METADATA] = (json.dumps({
        "schema_version": 1, "version": version, "kind": "windows-source-rc", "source_sha": sha,
        "files": {name: sha256(data).hexdigest() for name, data in contents.items()},
    }, indent=2, sort_keys=True) + "\n").encode()
    output.mkdir(parents=True, exist_ok=True)
    artifact = output / filename(version, sha)
    with ZipFile(artifact, "x", compression=ZIP_STORED) as archive:
        for name, data in sorted(contents.items()):
            entry = ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = ZIP_STORED
            archive.writestr(entry, data)
    with artifact.with_suffix(".zip.sha256").open("x", encoding="ascii", newline="\n") as checksum:
        checksum.write(f"{sha256(artifact.read_bytes()).hexdigest()}  {artifact.name}\n")
    return artifact, verify(artifact, sha)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output", type=Path)
    action.add_argument("--verify", type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.verify, args.sha) if args.verify else build(args.output, args.sha)[1]
    except (BadZipFile, OSError, KeyError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Windows RC validation failed: {exc}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
