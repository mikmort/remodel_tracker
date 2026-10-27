"""Build a code-only deployment ZIP using an explicit allowlist."""

import argparse
import zipfile
from pathlib import Path


def package(destination: Path):
    root = Path(__file__).resolve().parents[1]
    if destination.resolve().is_relative_to(root):
        raise ValueError("Write deployment archives outside the checkout")
    destination.parent.mkdir(parents=True, exist_ok=True)
    paths = [root / "requirements.txt"]
    paths.extend(sorted((root / "app").glob("*.py")))
    paths.extend(
        root / "app" / "static" / name for name in ("index.html", "theme.js", "style.css", "app.js")
    )
    if any(
        not path.is_file() or path.suffix not in {".py", ".txt", ".html", ".js", ".css"}
        for path in paths
    ):
        raise ValueError("Unexpected deployment file")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, str(path.relative_to(root)))
    print(f"Packaged {len(paths)} application files; no project content or environment files.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    package(parser.parse_args().output)
