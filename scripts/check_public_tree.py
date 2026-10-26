from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_SUFFIXES = {
    ".7z",
    ".db",
    ".key",
    ".log",
    ".p12",
    ".pcap",
    ".pcapng",
    ".pem",
    ".pfx",
    ".rar",
    ".sqlite",
    ".sqlite3",
    ".zip",
}

FORBIDDEN_PATH_PARTS = {
    ".git",
    ".local",
    ".next",
    ".next-build",
    ".venv",
    "__pycache__",
    "debug",
    "dist",
    "logs",
    "node_modules",
    "reports",
}

CONTENT_RULES = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "private workspace path": re.compile(r"D:\\newproject|C:\\Users\\tangg", re.IGNORECASE),
    "production server path": re.compile(r"/srv/endfield", re.IGNORECASE),
    "legacy production IP": re.compile(r"(?<!\d)1\.14\.96\.10(?!\d)"),
    "tracked embedded key module": re.compile(r"\bembedded_keys\b"),
}

ALLOWED_RESOURCE_FILES = {
    Path("endfield-logs/data/README.md"),
    Path("endfield-pcap/data/README.md"),
    Path("endfield-pcap/jsondata/README.md"),
    Path("endfield-pcap/proto/README.md"),
    Path("endfield-pcap/secrets/README.md"),
    Path("endfield-logs/apps/web/public/images/README.md"),
    Path("endfield-logs/apps/web/public/endaxis/README.md"),
}

RESOURCE_ROOTS = tuple(path.parent for path in ALLOWED_RESOURCE_FILES)


def iter_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if any(part in FORBIDDEN_PATH_PARTS for part in relative.parts):
            continue
        files.append(path)
    return files


def main() -> int:
    issues: list[str] = []
    # Ignored local tooling may exist during development, but force-added files
    # must not bypass the public boundary simply because traversal skips caches.
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    for name in filter(None, tracked):
        relative = Path(name)
        if any(part in FORBIDDEN_PATH_PARTS for part in relative.parts):
            issues.append(f"tracked private or generated directory: {relative}")
    for path in iter_files():
        relative = path.relative_to(ROOT)
        lower_name = path.name.lower()
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            issues.append(f"forbidden file type: {relative}")
        if lower_name == ".env" or (lower_name.startswith(".env.") and lower_name != ".env.example"):
            issues.append(f"forbidden environment file: {relative}")
        for resource_root in RESOURCE_ROOTS:
            if resource_root in relative.parents and relative not in ALLOWED_RESOURCE_FILES:
                issues.append(f"unexpected runtime resource: {relative}")
        if path.suffix.lower() in {".ico", ".png", ".webp", ".jpg", ".jpeg"}:
            continue
        if relative == Path("scripts/check_public_tree.py"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in CONTENT_RULES.items():
            if pattern.search(text):
                issues.append(f"{label}: {relative}")

        for email in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text):
            if not email.lower().endswith("@example.com"):
                issues.append(f"non-example email: {relative}")
                break

    if issues:
        print("Public-tree audit failed:")
        for issue in sorted(set(issues)):
            print(f"- {issue}")
        return 1
    print("Public-tree audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
