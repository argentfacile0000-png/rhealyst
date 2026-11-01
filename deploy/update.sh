#!/usr/bin/env bash
# StoryForge — mise à jour automatique depuis GitHub (lancée toutes les 5 min par storyforge-update.timer).
#
# 1. compare le dernier commit de la branche avec la version installée ;
# 2. si une nouvelle version existe et qu'aucun rendu/publication n'est en cours :
#    télécharge le code, installe les dépendances, bascule, redémarre le service ;
# 3. si le service ne redémarre pas, revient automatiquement à la version précédente.
#
# Ne touche qu'au dossier de StoryForge. Désactivation : AUTO_UPDATE=false dans le .env
# (ou depuis le bot : Réglages → Mises à jour auto).
set -euo pipefail

APP="${STORYFORGE_DIR:?STORYFORGE_DIR manquant}"
APP_USER="${STORYFORGE_USER:?STORYFORGE_USER manquant}"
BRANCH="${STORYFORGE_BRANCH:-claude/telegram-dashboard-video-automation-92ebn6}"
REPO="${STORYFORGE_REPO:-argentfacile0000-png/rhealyst}"
SERVICE="storyforge"
FORCE="${1:-}"

log() { echo "[storyforge-update] $*"; }
as_user() { runuser -u "$APP_USER" -- "$@"; }

if [ "$FORCE" != "--force" ]; then
  if grep -qiE '^AUTO_UPDATE=(false|0|no|non)\s*$' "$APP/.env" 2>/dev/null || [ -f "$APP/data/state/auto_update_disabled" ]; then
    exit 0
  fi
fi

current="$(cat "$APP/src/.version" 2>/dev/null || true)"
latest="$(curl -fsS --max-time 20 -H 'Accept: application/vnd.github.sha' \
  "https://api.github.com/repos/${REPO}/commits/${BRANCH}" || true)"
if ! [[ "$latest" =~ ^[0-9a-f]{40}$ ]]; then
  log "GitHub injoignable ou réponse inattendue, nouvel essai plus tard"
  exit 0
fi
[ "$latest" = "$current" ] && [ "$FORCE" != "--force" ] && exit 0
# Une version qui a déjà échoué n'est pas retentée (on attend le commit suivant).
if [ "$FORCE" != "--force" ] && [ "$(cat "$APP/data/state/failed_version" 2>/dev/null || true)" = "$latest" ]; then
  exit 0
fi

# Ne pas couper un rendu ou une publication en cours (le fichier est rafraîchi par l'application).
busy="$APP/data/state/busy"
if [ -f "$busy" ] && [ "$FORCE" != "--force" ]; then
  age=$(( $(date +%s) - $(stat -c %Y "$busy") ))
  if [ "$age" -lt 2700 ]; then
    log "Nouvelle version ${latest:0:7} disponible, report : tâche en cours"
    exit 0
  fi
fi

log "Mise à jour ${current:0:7} → ${latest:0:7}"
tmp="$(as_user mktemp -d /tmp/storyforge-update.XXXXXX)"
trap 'rm -rf -- "${tmp:?}"' EXIT
as_user bash -c "curl -fsSL --max-time 300 'https://codeload.github.com/${REPO}/tar.gz/${latest}' | tar xz -C '$tmp' --strip-components=1"
[ -f "$tmp/storyforge/__main__.py" ] || { log "Archive incomplète, abandon"; exit 1; }

# Métadonnées de version (affichées et annoncées par le bot)
info="$(curl -fsS --max-time 20 "https://api.github.com/repos/${REPO}/commits/${latest}" || true)"
as_user env INFO="$info" SHA="$latest" OUT="$tmp/.version.json" "$APP/.venv/bin/python" - <<'PY' || true
import json, os
try:
    c = json.loads(os.environ.get("INFO") or "{}")
except ValueError:
    c = {}
commit = c.get("commit", {})
json.dump({
    "sha": os.environ["SHA"],
    "message": (commit.get("message") or "").split("\n")[0][:200],
    "date": (commit.get("committer") or {}).get("date", ""),
}, open(os.environ["OUT"], "w"), ensure_ascii=False)
PY
echo "$latest" | as_user tee "$tmp/.version" >/dev/null

# Dépendances (uv réutilise son cache : quelques secondes si rien n'a changé)
UV="$(as_user bash -lc 'command -v uv || echo $HOME/.local/bin/uv')"
as_user "$UV" pip install -q --python "$APP/.venv/bin/python" -r "$tmp/requirements.txt" yt-dlp

# Bascule atomique, avec la version précédente gardée pour le retour arrière
as_user rm -rf -- "${APP:?}/src.previous"
[ -d "$APP/src" ] && as_user mv "$APP/src" "$APP/src.previous"
as_user mv "$tmp" "$APP/src"
trap - EXIT

# Le programme de mise à jour se met lui-même à jour
if [ -f "$APP/src/deploy/update.sh" ]; then
  install -m 755 "$APP/src/deploy/update.sh" "$APP/bin/update.sh.new" && mv "$APP/bin/update.sh.new" "$APP/bin/update.sh"
fi

systemctl restart "$SERVICE"
sleep 15
if systemctl is-active --quiet "$SERVICE"; then
  log "Version ${latest:0:7} en ligne"
  exit 0
fi

log "Le service ne démarre pas avec ${latest:0:7} : retour à la version précédente"
echo "$latest" | as_user tee "$APP/data/state/failed_version" >/dev/null
journalctl -u "$SERVICE" -n 20 --no-pager || true
if [ -d "$APP/src.previous" ]; then
  as_user rm -rf -- "${APP:?}/src.failed"
  as_user mv "$APP/src" "$APP/src.failed"
  as_user mv "$APP/src.previous" "$APP/src"
  systemctl restart "$SERVICE"
fi
exit 1
