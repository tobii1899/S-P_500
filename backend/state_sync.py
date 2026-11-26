"""Holt den Bot-Zustand (SQLite-Datei) vom Branch `bot-data` des GitHub-Repos auf den lokalen Rechner."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .config import ROOT

BRANCH = "bot-data"


def pull_state(dest_dir: Path, repo_dir: Path = ROOT) -> str:
    def run(*args: str) -> bytes:
        try:
            res = subprocess.run(["git", *args], cwd=repo_dir, capture_output=True, check=True)
        except FileNotFoundError as exc:
            raise RuntimeError("Git ist nicht installiert.") from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(exc.stderr.decode("utf-8", "replace").strip() or "Git-Fehler") from exc
        return res.stdout

    run("fetch", "--quiet", "origin", BRANCH)
    blob = run("show", f"origin/{BRANCH}:trading.db")  # Binärdaten: kein Text-Umweg, daher auch unter Windows korrekt
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / "trading.db.tmp"
    tmp.write_bytes(blob)
    os.replace(tmp, dest_dir / "trading.db")
    return "Zustand von GitHub geladen"
