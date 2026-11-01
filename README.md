# 🎬 StoryForge

**Usine à vidéos « storytime » automatisées, pilotée depuis un dashboard Telegram.**
Histoires originales écrites par ChatGPT → voix off ElevenLabs → séquence de fond tirée au hasard
→ sous-titres mot à mot → publication automatique sur **YouTube Shorts** et **TikTok**,
avec **plusieurs instances** (chaînes) qui publient chacune sur leurs propres comptes.

```
 ⏰ Créneau / ⚡ bouton
        │
        ▼
 🧠 ChatGPT ──► histoire originale (accroche, parties, titre, hashtags)
        │
        ▼
 🎙 ElevenLabs ──► voix + horodatage de chaque mot ──► silences resserrés, accélération
        │
        ▼
 🎞 Banque vidéo ──► vidéo de fond au hasard ──► séquence au hasard de la durée exacte
        │
        ▼
 🎬 ffmpeg ──► 1080×1920 · sous-titres « pop » mot à mot · label PARTIE N · carte d'accroche
        │
        ▼
 ✋ Validation dans Telegram (optionnelle) ──► 🚀 YouTube Shorts + TikTok ──► 🔔 liens dans Telegram
```

---

## La direction artistique reproduite

Le projet reprend le **style** des vidéos de référence (pas leurs textes : chaque histoire est inédite) :

| Élément | Ce que font les vidéos de référence | Ce que fait StoryForge |
|---|---|---|
| Format | Storytime façon Reddit, à la 1ʳᵉ personne | 12 formats : question Reddit, trahison & drama, vengeance, « ai-je tort ? », karma, boulot, famille… |
| Accroche | 1ʳᵉ phrase = titre-confession choc ou question lue à voix haute | Imposée par le prompt, avec boucle ouverte |
| Écriture | Détails ultra précis (« un latte à 7 $ »), phrases courtes, comparaisons exagérées, escalade | Règles détaillées dans le prompt + « ADN » aléatoire (narrateur, lieu, personnage, twist) pour ne jamais se répéter |
| Rythme | ~5 mots/s, quasiment aucun blanc | Vitesse ElevenLabs 1,1× + accélération 1,2× + **silences raccourcis à 0,22 s** |
| Sous-titres | 1 mot à la fois, blanc épais contour noir, au centre | Police Montserrat ExtraBold, effet « pop », ou mode 3 mots surlignés |
| Séries | « PART 1 » en bleu en haut, coupure en plein suspense | Histoires en N parties avec cliffhanger, label « PARTIE N », publication dans l'ordre |
| Fond | Vidéos de cuisine « satisfying » | Ta banque de vidéos, par catégories |

Le prompt est dans [`storyforge/story/prompts.py`](storyforge/story/prompts.py) et les formats/l'ADN dans
[`storyforge/story/styles.py`](storyforge/story/styles.py). Tu peux aussi ajouter des **consignes perso par chaîne**
depuis Telegram (thème, ton, interdits…).

### Le fond vidéo aléatoire

1. Une vidéo est tirée **au hasard** dans la banque (dans les catégories choisies pour la chaîne).
2. Un **point de départ aléatoire** est tiré pour que la séquence couvre exactement la durée de la narration.
   Exemple : fond de 43 min, narration de 1 min 38 → départ quelque part entre 0:05 et 41:17.
3. Les passages **déjà utilisés récemment** (toutes chaînes confondues) sont évités : deux vidéos ne réutilisent pas le même extrait.
4. La vidéo est recadrée en 9:16 (les fonds 16:9 type gameplay fonctionnent aussi). Si un fond est trop court, il est bouclé.

---

## Le dashboard Telegram

Le design suit le [Minimal Design System](https://github.com/holger1411/minimal-design-system-skill) :
palette neutre, typographie Inter, cartes à bordure, beaucoup d'espace, couleur réservée aux statuts.

**Dans le bot**, tout se pilote depuis un seul message qui se met à jour. Les messages sont typographiques
(titres et valeurs en gras, informations secondaires en italique), sans emoji décoratif ; les statuts utilisent
des glyphes monochromes (● actif, ○ en pause, ✓ fait, ✕ échec) toujours accompagnés d'un libellé.

- **Accueil** : état du système, générées / publiées / à valider / échecs du jour, tâches en cours, chaînes et prochains créneaux, crédits ElevenLabs.
- **Chaînes** : activer ou mettre en pause, générer maintenant, publier la suivante, historique des vidéos, liaison YouTube / TikTok.
- **Réglages par chaîne** (Histoire, Voix, Vidéo, Planning, Publication) : langue, formats, narrateur, parties, durée, voix ElevenLabs, vitesse, fonds, style des sous-titres, label « Partie N », carte d'accroche, musique, horaires, fuseau, décalage aléatoire, validation manuelle, réserve, visibilité YouTube / TikTok, mode TikTok.
- **Progression en direct** : histoire → voix off → séquence de fond → montage (barre de progression) → finalisation.
- **Validation** : chaque vidéo arrive avec Publier maintenant · Valider · Régénérer · Jeter · Aperçu · Texte complet.
- **Notifications** : liens YouTube / TikTok dès la publication, erreurs avec Réessayer.
- **File d'attente**, **Statistiques** (14 jours), **Banque vidéo** (ajout par fichier ou par lien), **Journal**, **Réglages** (pause globale, diagnostic des APIs).

**La Mini App** (optionnelle) s'ouvre dans Telegram : indicateurs du jour, graphique des publications
(infobulle au toucher et vue tableau), vidéos à valider avec lecteur, chaînes avec interrupteurs, dernières
publications. Thème clair ou sombre selon Telegram. Construite avec Tailwind CSS et les tokens HSL du design system.

Seuls les comptes listés dans `TELEGRAM_ADMIN_IDS` peuvent utiliser le bot.

---

## Installation

### Prérequis

- Python 3.11+ et **ffmpeg** (avec libass), ou Docker
- Un bot Telegram (créé avec [@BotFather](https://t.me/BotFather))
- Une clé API OpenAI et une clé API ElevenLabs
- Pour publier : un projet Google Cloud (YouTube) et/ou une app TikTok for Developers

### Avec Docker (recommandé)

```bash
cp .env.example .env        # puis remplis les clés
mkdir -p data/backgrounds/cooking && cp mes_fonds/*.mp4 data/backgrounds/cooking/
docker compose up -d --build
docker compose logs -f
```

### En local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # + pip install yt-dlp pour ajouter des fonds par lien
cp .env.example .env
python -m storyforge run
```

Ouvre ton bot et tape `/start`. Si ton ID n'est pas encore dans `TELEGRAM_ADMIN_IDS`, le bot te le donne.

### Vérifier le montage sans aucune clé API

```bash
python -m storyforge demo            # sous-titres « pop » 1 mot
python -m storyforge demo --style trio --card
```
La vidéo est écrite dans `data/demo/`.

### Tester le prompt d'histoire

```bash
python -m storyforge story --lang fr --format askreddit --parts 2 --seconds 80
```

---

## Configuration des plateformes

### YouTube Shorts

1. [Google Cloud Console](https://console.cloud.google.com/) → nouveau projet → activer **YouTube Data API v3**.
2. Écran de consentement OAuth (type *Externe*), ajoute ton compte Google en *utilisateur test*
   (ou publie l'application pour éviter l'expiration des jetons au bout de 7 jours).
3. Identifiants → **ID client OAuth** → type **Application de bureau** → télécharge le JSON
   → place-le dans `secrets/google_client_secret.json`.
4. Dans Telegram : chaîne → 🔗 YouTube → autorise → copie l'adresse de la page finale (même si elle affiche une erreur `localhost`) → colle-la dans le chat.

> **Quota** : un projet Google a 10 000 unités/jour par défaut et un upload coûte environ 1 600 unités, soit ~6 vidéos/jour
> **par projet**. Pour publier plus : demande une augmentation de quota, ou crée un projet par chaîne et dépose son
> fichier sous `secrets/google_client_secret_<ID_CHAINE>.json` (il sera utilisé automatiquement pour cette chaîne).

Les vidéos sont déclarées comme contenant des médias synthétiques (`containsSyntheticMedia`), comme l'exige YouTube pour une voix IA réaliste.

### TikTok

1. [developers.tiktok.com](https://developers.tiktok.com/) → crée une app → ajoute **Login Kit** et **Content Posting API**
   (scopes `user.info.basic`, `video.upload`, `video.publish`).
2. Déclare une **Redirect URI** en `https://…` (n'importe quelle page à toi) → renseigne `TIKTOK_CLIENT_KEY`,
   `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI`.
3. Dans Telegram : chaîne → 🔗 TikTok → autorise → colle l'adresse de la page de redirection.
4. Optionnel : réglage « @ TikTok » de la chaîne pour que les liens des vidéos soient corrects.

> **Important** : tant que ton app TikTok n'a pas passé l'audit de TikTok, la publication directe n'est autorisée
> qu'en **privé** (`SELF_ONLY`). Deux solutions : faire auditer l'app, ou passer la chaîne en mode **📥 Brouillon** :
> la vidéo arrive dans ta boîte de réception TikTok et tu la publies en un tap (pratique aussi pour ajouter un son tendance).
> Les vidéos sont marquées « contenu généré par IA » (`is_aigc`).

### Mini App (dashboard visuel)

Il faut une URL publique **HTTPS** vers le port `WEBAPP_PORT` (reverse proxy Caddy/Nginx, Cloudflare Tunnel…) :

```env
WEBAPP_ENABLED=true
PUBLIC_URL=https://studio.mondomaine.com
```

Au démarrage, le bouton **Dashboard** apparaît dans le menu du bot. Les requêtes sont authentifiées par la signature
Telegram (`initData`) et réservées aux admins. Avec `PUBLIC_URL`, la liaison YouTube se fait aussi automatiquement
(ajoute `https://…/oauth/youtube` aux URI de redirection autorisées du client OAuth, type *Application Web*).

---

## Utilisation

1. **🎞 Banque vidéos** : dépose des vidéos dans `data/backgrounds/<catégorie>/` (cooking, minecraft, satisfying…).
   Privilégie des vidéos longues (20-60 min) et **dont tu as les droits**. Musique optionnelle dans `data/music/`.
2. **➕ Nouvelle chaîne** : nom → langue → formats, voix, durée, parties…
3. **🔗 Lier** YouTube et/ou TikTok (chaque chaîne a ses propres comptes : c'est ce qui permet le multi-instances).
4. **▶️ Activer** : la chaîne fabrique sa **réserve** de vidéos d'avance, puis publie à chaque **créneau**
   (avec une variation aléatoire de quelques minutes pour un rythme naturel).
5. Avec **validation manuelle** ON, rien ne part sans ton ✅ ; OFF, tout est 100 % automatique.

Après un échec, la génération automatique d'une chaîne réessaie avec un délai croissant (5 min → 2 h) pour ne pas consommer de crédits en boucle.

---

## Architecture

```
storyforge/
├── __main__.py          # CLI : run | demo | story
├── config.py            # réglages (.env)
├── models.py · db.py    # SQLite (chaînes, histoires, vidéos, publications, journal)
├── core.py              # services partagés : files, planning, OAuth, quotas
├── pipeline.py          # histoire → voix → fond → montage → publication
├── scheduler.py         # créneaux par chaîne (APScheduler) + réapprovisionnement
├── jobs.py              # files asynchrones (rendus / publications)
├── stats.py             # agrégats pour les dashboards
├── story/               # prompts, formats, ADN aléatoire, client OpenAI
├── tts/                 # ElevenLabs (avec horodatage) + resserrage des silences
├── video/               # banque de fonds, sous-titres ASS, carte d'accroche, rendu ffmpeg
├── publishers/          # YouTube Data API v3 · TikTok Content Posting API
├── bot/                 # dashboard Telegram (écrans, réglages, notifications)
└── webapp/              # Mini App (FastAPI + page unique) et retours OAuth
```

### Tests

```bash
pip install pytest pytest-asyncio
pytest
```

Les tests couvrent le découpage mot à mot, le resserrage des silences, le tirage du fond, les sous-titres,
la génération d'histoire (OpenAI simulé), le pipeline complet avec ffmpeg réel (APIs simulées), l'API de la
Mini App, et la navigation complète du bot Telegram.

---

## Bon à savoir

- **Coûts** : une vidéo de 90 s ≈ 1 500-2 000 caractères ElevenLabs et ~3 000 tokens OpenAI.
- **Telegram** limite les envois de bot à 50 Mo (un aperçu allégé est généré si besoin) et les téléchargements à 20 Mo
  (pour de gros fonds, utilise un lien ou dépose-les dans `data/backgrounds/`).
- **Politiques des plateformes** : YouTube et TikTok peuvent limiter la monétisation des contenus jugés « réutilisés » ou
  répétitifs. Les histoires originales, les séquences de fond toujours différentes et la variation des horaires aident,
  mais publie des contenus qui apportent de la valeur et respecte les droits des vidéos de fond.
- Les fichiers vidéo publiés sont supprimés après `KEEP_VIDEOS_DAYS` jours (l'historique reste en base).
