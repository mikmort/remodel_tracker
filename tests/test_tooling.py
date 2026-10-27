import json
import subprocess
import sys
import zipfile
from pathlib import Path

from scripts import package_app


def test_packaging_is_an_explicit_code_only_allowlist(tmp_path, monkeypatch):
    root = tmp_path / "synthetic-project"
    (root / "scripts").mkdir(parents=True)
    (root / "app" / "static").mkdir(parents=True)
    (root / "requirements.txt").write_text("fastapi\n")
    (root / "app" / "__init__.py").write_text("")
    for name in ("index.html", "theme.js", "style.css", "app.js"):
        (root / "app" / "static" / name).write_text("synthetic application code")
    (root / ".env").write_text("SYNTHETIC_PRIVATE_SETTING=not-for-packaging")
    (root / "app" / "static" / "unapproved.html").write_text("Not an application entry point")
    monkeypatch.setattr(package_app, "__file__", str(root / "scripts" / "package_app.py"))
    target = tmp_path / "code-only.zip"
    package_app.package(target)
    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        assert names == {
            "requirements.txt",
            "app/__init__.py",
            "app/static/index.html",
            "app/static/theme.js",
            "app/static/style.css",
            "app/static/app.js",
        }


def test_inspection_cli_prints_counts_not_source_content(source):
    result = subprocess.run(
        [sys.executable, "-m", "app", "inspect", "--source", str(source)],
        check=True,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert json.loads(result.stdout)["documents"] == 1
    assert "Example faucet" not in result.stdout
    assert str(source) not in result.stdout
