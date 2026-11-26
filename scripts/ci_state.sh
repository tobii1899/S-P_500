#!/usr/bin/env bash
# Der Bot-Zustand (SQLite) liegt auf dem Branch "bot-data" - getrennt vom Code, damit main sauber bleibt.
#   ci_state.sh pull   Zustand in den Ordner state/ holen (oder neu anlegen)
#   ci_state.sh push   Änderungen speichern (mit Wiederholung bei Konflikten)
set -euo pipefail
BRANCH="bot-data"
DIR="${STATE_DIR:-state}"
REMOTE="${STATE_REMOTE:-https://x-access-token:${GH_TOKEN:-}@github.com/${GITHUB_REPOSITORY:-}.git}"

case "${1:-}" in
  pull)
    if git ls-remote --exit-code --heads "$REMOTE" "$BRANCH" >/dev/null 2>&1; then
      git clone --quiet --depth 1 --branch "$BRANCH" "$REMOTE" "$DIR"
      echo "Zustand geladen:"
      ls -la "$DIR" | head -n 8
    else
      git init --quiet -b "$BRANCH" "$DIR"
      git -C "$DIR" remote add origin "$REMOTE"
      echo "Noch kein Zustand vorhanden - Branch $BRANCH wird beim ersten Speichern angelegt."
    fi
    git -C "$DIR" config user.name "spy-bot"
    git -C "$DIR" config user.email "spy-bot@users.noreply.github.com"
    ;;
  push)
    cd "$DIR"
    git add -A
    if git diff --cached --quiet; then
      echo "Keine Änderungen am Zustand."
      exit 0
    fi
    git commit --quiet -m "state $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    for i in 1 2 3 4; do
      if git push --quiet origin "$BRANCH"; then
        echo "Zustand gespeichert."
        exit 0
      fi
      echo "Push fehlgeschlagen (Versuch $i) - hole den neuesten Stand und versuche es erneut"
      git pull --quiet --rebase origin "$BRANCH" || true
      sleep 3
    done
    echo "::error::Der Zustand konnte nicht gespeichert werden"
    exit 1
    ;;
  *)
    echo "Aufruf: ci_state.sh pull|push"
    exit 2
    ;;
esac
