"""Reject private documents and common secrets in tracked or newly added source files."""

import re
import subprocess
from pathlib import Path

ALLOWED_ROOT = {
    ".gitignore",
    ".env.example",
    "README.md",
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "package.json",
    "package-lock.json",
}
ALLOWED_PREFIXES = ("app/", "tests/", "scripts/", "infra/", ".github/")
ALLOWED_SUFFIXES = {".py", ".js", ".cjs", ".css", ".html", ".md", ".txt", ".toml", ".yml", ".bicep"}
SECRETS = [
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"AccountKey=[A-Za-z0-9+/=]{20,}"),
    re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    re.compile(rb"[\w.+-]+@(?!example\.(?:invalid|com)\b)[\w.-]+\.[A-Za-z]{2,}"),
    re.compile(rb"(?:sig=)[A-Za-z0-9%+/=]{24,}"),
]


def check():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    failures = []
    for raw in set(result.stdout.split(b"\0")) - {b""}:
        name = raw.decode("utf-8")
        path = root.joinpath(*name.split("/"))
        if not path.exists():
            continue
        if not (
            name in ALLOWED_ROOT
            or name.startswith(ALLOWED_PREFIXES)
            and path.suffix in ALLOWED_SUFFIXES
        ):
            failures.append(f"Unapproved repository file: {name}")
            continue
        content = path.read_bytes()
        if len(content) > 250_000 or any(pattern.search(content) for pattern in SECRETS):
            failures.append(f"Potential private content or credential: {name}")
    if failures:
        raise SystemExit("\n".join(failures))
    print("Repository file and credential guards passed.")


if __name__ == "__main__":
    check()
