#!/usr/bin/env bash
# StoryForge — installation ou mise à jour sur un serveur Ubuntu (ex. AWS Lightsail).
#
#   bash install.sh            installe ou met à jour le bot (service systemd « storyforge »)
#   bash install.sh --https    + page de connexion et Mini App en HTTPS (Caddy + domaine sslip.io)
#
# Sécurité : le script n'efface rien en dehors de son propre dossier (~/storyforge par défaut),
# installe des paquets sans jamais en retirer (apt --no-remove) et refuse d'écraser un service,
# un dossier ou une configuration qui ne lui appartient pas. Relancer le script = mise à jour
# (le .env, les vidéos et la base de données sont conservés).
set -euo pipefail

REPO="argentfacile0000-png/rhealyst"
BRANCH="${STORYFORGE_BRANCH:-claude/telegram-dashboard-video-automation-92ebn6}"
APP="${STORYFORGE_DIR:-$HOME/storyforge}"
SERVICE="storyforge"
UNIT="/etc/systemd/system/${SERVICE}.service"
UPDATE_UNIT="/etc/systemd/system/${SERVICE}-update.service"
UPDATE_TIMER="/etc/systemd/system/${SERVICE}-update.timer"
HARDENING="/etc/systemd/system/${SERVICE}.service.d/10-hardening.conf"
MARKER="# Managed by StoryForge install.sh"
WITH_HTTPS=0
for arg in "$@"; do
  case "$arg" in
    --https) WITH_HTTPS=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Option inconnue : $arg" >&2; exit 1 ;;
  esac
done

step() { printf '\n\033[1m→ %s\033[0m\n' "$*"; }
ok() { printf '  ✓ %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*"; }
die() { printf '\n✕ %s\n' "$*" >&2; exit 1; }
has_tty() { [ -t 0 ] || { [ -r /dev/tty ] && : </dev/tty; } 2>/dev/null; }

# ask VAR "Question" [secret] [optional] : lit la valeur (variable d'environnement sinon question).
ask() {
  local var="$1" prompt="$2" secret="${3:-}" optional="${4:-}" value="${!1:-}"
  if [ -z "$value" ]; then
    has_tty || { [ -n "$optional" ] && { printf -v "$var" '%s' ""; return; }; die "$var manquant (lance le script dans un terminal)"; }
    while :; do
      if [ -n "$secret" ]; then read -r -s -p "  $prompt : " value </dev/tty; echo; else read -r -p "  $prompt : " value </dev/tty; fi
      value="$(printf '%s' "$value" | tr -d '[:space:]')"
      [ -n "$value" ] || [ -n "$optional" ] && break
      echo "  Valeur requise."
    done
  fi
  printf -v "$var" '%s' "$value"
}

# set_env CLE valeur : ajoute ou remplace une ligne du .env
set_env() {
  local key="$1" value="$2" file="$APP/.env"
  touch "$file"
  if grep -q "^${key}=" "$file"; then
    local tmp; tmp="$(mktemp)"
    awk -v k="$key" -v v="$value" 'BEGIN{FS=OFS="="} $1==k {print k"="v; next} {print}' "$file" >"$tmp"
    cat "$tmp" >"$file"; rm -f "$tmp"
  else
    printf '%s=%s\n' "$key" "$value" >>"$file"
  fi
}

# ---------------------------------------------------------------- 0. vérifications
step "Vérifications"
[ "$(id -u)" -ne 0 ] || die "Lance ce script avec ton utilisateur habituel (ex. ubuntu), pas en root."
command -v sudo >/dev/null || die "sudo est requis."
if [ -r /etc/os-release ]; then . /etc/os-release; ok "Système : ${PRETTY_NAME:-inconnu}"; fi
[ "${ID:-}" = "ubuntu" ] || [ "${ID_LIKE:-}" != "${ID_LIKE#*debian}" ] || warn "Système non testé (Ubuntu recommandé)."
if [ -d "$APP" ] && [ -n "$(ls -A "$APP" 2>/dev/null)" ] && [ ! -f "$APP/.storyforge" ]; then
  die "$APP existe déjà et n'appartient pas à StoryForge. Choisis un autre dossier : STORYFORGE_DIR=~/autre bash install.sh"
fi
for unit_file in "$UNIT" "$UPDATE_UNIT" "$UPDATE_TIMER" "$HARDENING"; do
  if [ -f "$unit_file" ] && ! grep -q "$MARKER" "$unit_file"; then
    die "$unit_file existe déjà et n'a pas été créé par StoryForge : rien n'est modifié."
  fi
done
mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
free_gb=$(df -Pk "$HOME" | awk 'NR==2 {print int($4/1024/1024)}')
ok "Mémoire : ${mem_mb} Mo · disque libre : ${free_gb} Go"
[ "$free_gb" -ge 3 ] || warn "Moins de 3 Go libres : prévois de la place pour les vidéos de fond."

mkdir -p "$APP/data/backgrounds" "$APP/data/music" "$APP/data/state" "$APP/secrets" "$APP/bin"
touch "$APP/.storyforge"
chmod 700 "$APP" "$APP/secrets" "$APP/data"

# ---------------------------------------------------------------- 1. paquets système (ajout uniquement)
step "Paquets système (ffmpeg)"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-remove ffmpeg curl ca-certificates fontconfig >/dev/null
ok "$(ffmpeg -version | head -1 | cut -c1-60)"

# Swap : utile sur les petites instances (rendu vidéo)
if [ "$mem_mb" -lt 1800 ] && [ -z "$(swapon --noheadings 2>/dev/null)" ] && [ ! -e /swapfile ]; then
  answer="o"
  if has_tty; then read -r -p "  Peu de mémoire (${mem_mb} Mo). Créer 2 Go de swap ? [O/n] " answer </dev/tty || true; fi
  case "${answer:-o}" in
    [nN]*) warn "Pas de swap : les rendus pourraient manquer de mémoire." ;;
    *) sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
       grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
       ok "Swap de 2 Go activé" ;;
  esac
fi

# ---------------------------------------------------------------- 2. Python isolé (uv)
step "Python 3.12 (isolé, sans toucher au Python du système)"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null
fi
ok "$(uv --version)"

# ---------------------------------------------------------------- 3. code
step "Code de StoryForge ($BRANCH)"
sha="$(curl -fsS -H 'Accept: application/vnd.github.sha' "https://api.github.com/repos/${REPO}/commits/${BRANCH}" || true)"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "Impossible de lire la dernière version sur GitHub."
tmp="$(mktemp -d /tmp/storyforge-install.XXXXXX)"
curl -fsSL "https://codeload.github.com/${REPO}/tar.gz/${sha}" | tar xz -C "$tmp" --strip-components=1
[ -f "$tmp/storyforge/__main__.py" ] || die "Archive inattendue : téléchargement incomplet ?"
echo "$sha" >"$tmp/.version"
printf '{"sha": "%s", "message": "", "date": "%s"}\n' "$sha" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$tmp/.version.json"
rm -rf -- "${APP:?}/src.previous"
[ -d "$APP/src" ] && mv "$APP/src" "$APP/src.previous"
mv "$tmp" "$APP/src"
ok "Version ${sha:0:7} installée dans $APP/src"

# ---------------------------------------------------------------- 4. dépendances
step "Dépendances Python"
[ -x "$APP/.venv/bin/python" ] || uv venv -q --python 3.12 "$APP/.venv"
uv pip install -q --python "$APP/.venv/bin/python" -r "$APP/src/requirements.txt" yt-dlp
ok "Environnement prêt ($("$APP/.venv/bin/python" --version))"

# ---------------------------------------------------------------- 5. configuration (.env)
step "Configuration"
if [ ! -s "$APP/.env" ] || ! grep -q '^TELEGRAM_BOT_TOKEN=.' "$APP/.env"; then
  echo "  Les clés sont enregistrées uniquement sur ce serveur ($APP/.env, lisible par toi seul)."
  ask TELEGRAM_BOT_TOKEN "Token du bot Telegram (BotFather)" secret
  ask TELEGRAM_ADMIN_IDS "Ton identifiant Telegram (plusieurs : 111,222)"
  ask ELEVENLABS_API_KEY "Clé API ElevenLabs" secret
  umask 077
  set_env TELEGRAM_BOT_TOKEN "$TELEGRAM_BOT_TOKEN"
  set_env TELEGRAM_ADMIN_IDS "$TELEGRAM_ADMIN_IDS"
  set_env ELEVENLABS_API_KEY "$ELEVENLABS_API_KEY"
  ok "Clés enregistrées"
else
  ok ".env existant conservé"
fi
chmod 600 "$APP/.env"

# IA qui écrit les histoires (demandée tant qu'aucune clé n'est renseignée)
if ! grep -qE '^(GEMINI_API_KEY|GROQ_API_KEY|OPENAI_API_KEY)=.' "$APP/.env"; then
  provider="${LLM_PROVIDER:-}"
  if [ -z "$provider" ] && has_tty; then
    echo "  IA qui écrit les histoires :"
    echo "    1) Google Gemini   gratuit · clé sur https://aistudio.google.com/apikey"
    echo "    2) Groq            gratuit · clé sur https://console.groq.com/keys"
    echo "    3) OpenAI          payant  · clé sur https://platform.openai.com/api-keys"
    read -r -p "  Choix [1] : " choice </dev/tty || true
    case "${choice:-1}" in 2) provider=groq ;; 3) provider=openai ;; *) provider=gemini ;; esac
  fi
  provider="${provider:-gemini}"
  case "$provider" in groq) key_var=GROQ_API_KEY ;; openai) key_var=OPENAI_API_KEY ;; *) provider=gemini; key_var=GEMINI_API_KEY ;; esac
  ask "$key_var" "Clé API $provider (Entrée pour l'ajouter plus tard)" secret optional
  set_env LLM_PROVIDER "$provider"
  if [ -n "${!key_var:-}" ]; then set_env "$key_var" "${!key_var}"; ok "IA : $provider"; fi
fi
grep -qE '^(GEMINI_API_KEY|GROQ_API_KEY|OPENAI_API_KEY)=.' "$APP/.env" \
  || warn "Aucune clé d'IA pour écrire les histoires : relance le script pour l'ajouter (Gemini est gratuit)."
grep -q '^TIMEZONE=' "$APP/.env" || set_env TIMEZONE "Europe/Paris"
# Clé de signature des liens (connexion des comptes, aperçus) indépendante du token Telegram
grep -qE '^SECRET_KEY=.+' "$APP/.env" || set_env SECRET_KEY "$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
set_env DATA_DIR "$APP/data"
set_env FONTS_DIR "$APP/src/assets/fonts"
grep -q '^GOOGLE_CLIENT_SECRETS_FILE=' "$APP/.env" || set_env GOOGLE_CLIENT_SECRETS_FILE "$APP/secrets/google_client_secret.json"

# ---------------------------------------------------------------- 6. HTTPS (optionnel)
if [ "$WITH_HTTPS" = 1 ]; then
  step "HTTPS : page de connexion des comptes et Mini App"
  ip="$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')"
  domain="${STORYFORGE_DOMAIN:-${ip//./-}.sslip.io}"
  busy="$(sudo ss -ltnpH '( sport = :80 or sport = :443 )' 2>/dev/null | grep -v caddy || true)"
  if [ -n "$busy" ]; then
    warn "Les ports 80/443 sont déjà utilisés par un autre programme : HTTPS non configuré (rien n'a été modifié)."
    warn "$busy"
  else
    if ! command -v caddy >/dev/null; then
      sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-remove debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null
      curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
      curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
      sudo apt-get update -qq
      sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-remove caddy >/dev/null
    fi
    printf '%s\n%s {\n\treverse_proxy 127.0.0.1:8080\n}\n' "$MARKER" "$domain" | sudo tee /etc/caddy/storyforge.caddy >/dev/null
    sudo grep -q 'import /etc/caddy/storyforge.caddy' /etc/caddy/Caddyfile 2>/dev/null \
      || printf '\nimport /etc/caddy/storyforge.caddy\n' | sudo tee -a /etc/caddy/Caddyfile >/dev/null
    sudo systemctl enable -q caddy
    sudo systemctl reload caddy 2>/dev/null || sudo systemctl restart caddy
    set_env WEBAPP_ENABLED true
    set_env WEBAPP_HOST 127.0.0.1
    set_env WEBAPP_PORT 8080
    set_env PUBLIC_URL "https://$domain"
    ok "Adresse publique : https://$domain"
    warn "Ouvre le port 443 (HTTPS) dans Lightsail : instance → Mise en réseau → Pare-feu IPv4 → Ajouter une règle HTTPS."
  fi
fi

# ---------------------------------------------------------------- 7. service systemd
step "Service systemd"
sudo tee "$UNIT" >/dev/null <<EOF
$MARKER
[Unit]
Description=StoryForge (bot Telegram + usine à vidéos)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(id -un)
WorkingDirectory=$APP/src
EnvironmentFile=$APP/.env
ExecStart=$APP/.venv/bin/python -m storyforge run
Restart=always
RestartSec=10
Nice=5

[Install]
WantedBy=multi-user.target
EOF
# Bac à sable du service (pas de root, système en lecture seule, noyau protégé)
sudo mkdir -p "$(dirname "$HARDENING")"
sudo install -m 644 "$APP/src/deploy/storyforge-hardening.conf" "$HARDENING"
# Mise à jour automatique : vérifie GitHub toutes les 5 minutes
install -m 755 "$APP/src/deploy/update.sh" "$APP/bin/update.sh"
sudo tee "$UPDATE_UNIT" >/dev/null <<EOF
$MARKER
[Unit]
Description=StoryForge : mise à jour automatique depuis GitHub
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=STORYFORGE_DIR=$APP
Environment=STORYFORGE_USER=$(id -un)
Environment=STORYFORGE_BRANCH=$BRANCH
Environment=STORYFORGE_REPO=$REPO
ExecStart=/bin/bash $APP/bin/update.sh
EOF
sudo tee "$UPDATE_TIMER" >/dev/null <<EOF
$MARKER
[Unit]
Description=StoryForge : vérification des mises à jour toutes les 5 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
RandomizedDelaySec=30
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable -q "$SERVICE"
sudo systemctl enable -q --now "${SERVICE}-update.timer"
sudo systemctl restart "$SERVICE"
sleep 6
if systemctl is-active --quiet "$SERVICE"; then
  ok "StoryForge tourne"
else
  warn "Le service ne démarre pas. Derniers journaux :"
  sudo journalctl -u "$SERVICE" -n 30 --no-pager || true
  exit 1
fi
sudo journalctl -u "$SERVICE" -n 8 --no-pager -o cat || true

cat <<EOF

Terminé.
  Ouvre ton bot sur Telegram et envoie /start.
  Vidéos de fond : $APP/data/backgrounds/<catégorie>/ (ou depuis le bot, Banque vidéo).
  Journaux      : sudo journalctl -u $SERVICE -f
  Configuration : nano $APP/.env, puis sudo systemctl restart $SERVICE
  Mises à jour  : automatiques à chaque push sur GitHub (vérification toutes les 5 min).
                  Forcer : sudo systemctl start ${SERVICE}-update · Journal : sudo journalctl -u ${SERVICE}-update
EOF
