# 🎬 StoryForge

**Usine à vidéos « storytime » automatisées, pilotée depuis un dashboard Telegram.**
Histoires originales écrites par une IA (Gemini ou Groq gratuits, ou OpenAI) → voix off ElevenLabs → séquence de fond tirée au hasard
→ sous-titres mot à mot → publication automatique sur **YouTube** (Shorts et vidéos longues), **TikTok**
et **Instagram Reels**, avec **plusieurs instances** (chaînes) qui publient chacune sur leurs propres comptes.

```
 ⏰ Créneau / ⚡ bouton
        │
        ▼
 🧠 IA (Gemini, Groq, OpenAI) ──► histoire originale (accroche, parties, titre, hashtags)
        │
        ▼
 🎙 ElevenLabs ──► voix + horodatage de chaque mot ──► silences resserrés, accélération
        │
        ▼
 🎞 Banque vidéo ──► vidéo de fond au hasard ──► séquence au hasard de la durée exacte
        │
        ▼
 🎬 ffmpeg ──► Short 1080×1920 ou vidéo longue 1920×1080 · sous-titres mot à mot · carte d'accroche
        │
        ▼
 ✋ Validation dans Telegram (optionnelle) ──► 🚀 YouTube · TikTok · Instagram ──► 🔔 liens dans Telegram
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

## Formats : Shorts et vidéos longues

Chaque chaîne peut produire deux formats, avec chacun sa réserve et ses créneaux (Réglages → Formats) :

| | Short | Vidéo longue |
|---|---|---|
| Image | verticale 1080×1920 | horizontale 1920×1080 |
| Durée | 30 s à 3 min, en une ou plusieurs parties | 5 à 30 min, une histoire en plusieurs actes |
| Publication | YouTube Shorts, TikTok, Instagram Reels | YouTube uniquement |
| Extras | label « Partie N » | miniature YouTube générée avec le titre |

Une minute de narration consomme environ 1 000 à 1 500 caractères ElevenLabs : avec le forfait Starter
(40 000 caractères par mois), compte une trentaine de minutes de vidéos longues par mois, Shorts compris.
La miniature personnalisée n'est acceptée par YouTube que si la chaîne est validée par téléphone ; sinon la
vidéo est publiée avec une image automatique.

## Gérer les vidéos

- **Découper en parties.** Réglages → Formats → « Découpage auto des Shorts » (60, 90 ou 120 s) : un Short plus long
  est coupé automatiquement en Partie 1, 2, 3…, chacune en fin de phrase et de durée équilibrée, avec « Partie N »
  en haut. Le bouton **Découper** d'une vidéo fait la même chose à la demande. La vidéo entière est archivée
  (récupérable) et les parties sont publiées dans l'ordre, une par créneau.
- **Archiver.** Met une vidéo de côté sans décider de son sort : elle sort du stock (une nouvelle vidéo est préparée
  pour la remplacer) et reste consultable dans **Archives**. **Désarchiver** la remet là où elle était.
- **Programmer.** Publie une vidéo à une date précise : « demain 18h », « vendredi 21h », « 25/12 18:30 »,
  « dans 2h »… ou un bouton rapide (ce soir 20:00, demain 12:00…). La date suit le fuseau de la chaîne ;
  les programmations sont conservées après un redémarrage et rattrapées si l'heure est passée entre-temps.
- **Bande son.** Accueil → **Bande son** → Ajouter une piste : envoie un fichier audio (mp3, m4a, wav, ogg, flac),
  une vidéo dont on garde le son, ou un lien (YouTube, SoundCloud…). Chaque vidéo tire une piste au hasard et la
  démarre à un moment au hasard, avec fondu d'entrée et de sortie, mixée sous la voix. Par chaîne (Réglages →
  Vidéo) : **Bande son** règle le volume (5 à 30 %, « Aucune » pour couper) et **Pistes** limite le tirage à
  certaines pistes. Utilise des musiques libres de droits : YouTube et TikTok bloquent ou démonétisent les
  vidéos qui contiennent des musiques protégées.

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
- Une clé API ElevenLabs et une clé d'IA pour écrire les histoires : **Gemini ou Groq (gratuits)**, ou OpenAI (payant)
- Pour publier : un projet Google Cloud (YouTube) et/ou une app TikTok for Developers

### Clés ElevenLabs de secours

Quand une clé ElevenLabs n'a plus de crédits, StoryForge passe automatiquement à la suivante et te prévient dans
Telegram. La clé épuisée est mise de côté jusqu'à la remise à zéro de son quota mensuel. Ajoute des clés :

- depuis le bot : Réglages → **Clés ElevenLabs** → Ajouter une clé (ton message est supprimé, la clé est vérifiée
  puis enregistrée sur le serveur) ;
- ou dans le `.env` : `ELEVENLABS_API_KEYS=clé2,clé3`.

Les voix « premade » (Adam, Liam…) existent sur tous les comptes. Une voix clonée ou ajoutée depuis la bibliothèque
doit être présente sur chaque compte ; sinon la clé suivante est essayée.

### IA des histoires : gratuite ou payante

| Fournisseur | Coût | Clé | Modèle par défaut |
|---|---|---|---|
| Google Gemini | gratuit (modèles Flash, limites par minute et par jour) | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | `gemini-flash-latest` |
| Groq | gratuit (limites par minute et par jour) | [console.groq.com/keys](https://console.groq.com/keys) | `llama-3.3-70b-versatile` |
| OpenAI | payant à l'usage (l'abonnement ChatGPT Plus ne donne pas accès à l'API) | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) | `gpt-4.1` |

Renseigne une seule clé (`GEMINI_API_KEY`, `GROQ_API_KEY` ou `OPENAI_API_KEY`) : le fournisseur est choisi
automatiquement, ou force-le avec `LLM_PROVIDER`. `LLM_MODEL` permet de changer de modèle. Une vidéo ne demande
qu'une ou deux requêtes, très en dessous des limites gratuites. Avec l'offre gratuite de Gemini, Google peut utiliser
les requêtes pour améliorer ses services : ce n'est pas gênant pour des histoires inventées.

### Avec Docker (recommandé)

```bash
cp .env.example .env        # puis remplis les clés
mkdir -p data/backgrounds/cooking && cp mes_fonds/*.mp4 data/backgrounds/cooking/
docker compose up -d --build
docker compose logs -f
```

### Sur un serveur (AWS Lightsail, VPS Ubuntu)

Connecte-toi au serveur (sur Lightsail : bouton « Se connecter via SSH » de l'instance) puis :

```bash
curl -fsSL https://raw.githubusercontent.com/argentfacile0000-png/rhealyst/refs/heads/claude/telegram-dashboard-video-automation-92ebn6/deploy/install.sh -o install.sh
bash install.sh
```

Le script demande le token Telegram, ton identifiant Telegram, la clé ElevenLabs et le fournisseur d'IA
(Gemini gratuit par défaut) avec sa clé, puis installe
ffmpeg, un Python 3.12 isolé et le service `storyforge` (redémarrage automatique). Il ne supprime rien en dehors de
`~/storyforge` et refuse d'écraser un dossier ou un service qui ne lui appartient pas. Relancer le script met à jour
le code en gardant la configuration, les vidéos et la base.

- `bash install.sh --https` ajoute une adresse HTTPS gratuite (`<ip>.sslip.io`, via Caddy) pour la page de connexion
  des comptes, la Mini App et Instagram. Il faut ensuite ouvrir le port 443 dans le pare-feu Lightsail.

**Mises à jour automatiques.** Le serveur vérifie GitHub toutes les 5 minutes. À chaque nouveau commit sur la branche,
il télécharge le code, installe les dépendances et redémarre le bot, sans jamais couper un rendu ou une publication
en cours (il attend la fin). Si la nouvelle version ne démarre pas, il revient seul à la précédente. Le bot annonce
chaque mise à jour dans Telegram, et on peut les suspendre depuis Réglages (ou `AUTO_UPDATE=false` dans le `.env`).
Forcer une vérification : `sudo systemctl start storyforge-update` ; journal : `sudo journalctl -u storyforge-update`.
- Journaux : `sudo journalctl -u storyforge -f`. Configuration : `~/storyforge/.env`.

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
python -m storyforge story --lang fr --format askreddit --parts 2 --seconds 80   # nécessite une clé d'IA
```

---

## Configuration des plateformes

Chaque plateforme exige une « app développeur » à ton nom (gratuite), créée une seule fois. Le bot te guide :
**Réglages → Plateformes**, puis YouTube, TikTok ou Instagram. Chaque guide donne les adresses exactes à copier
(adresse de retour, conditions d'utilisation, politique de confidentialité, suppression des données : le serveur
publie ces pages sur `/legal/…`) et te laisse envoyer le fichier JSON Google ou saisir les clés TikTok / Instagram
directement dans Telegram. Ils restent sur ton serveur et priment sur le `.env` ; tes messages contenant une clé
sont supprimés après lecture. Les sections ci-dessous détaillent les mêmes étapes.

### Connexion des comptes

Chaque chaîne a sa page **Comptes** (bot : chaîne → Comptes ; Mini App : bouton Comptes). Elle ouvre une
**page de connexion** : tu cliques sur « Se connecter avec YouTube » ou « Se connecter avec TikTok », tu choisis
ton compte et tu acceptes les autorisations chez Google ou TikTok, puis tu reviens sur une page de confirmation.
Le bot te prévient. On peut changer de compte ou déconnecter depuis la même page.

Le lien est signé, propre à la chaîne et valable 30 minutes. Pour que la page soit joignable depuis ton téléphone,
le serveur doit avoir une **URL publique HTTPS** :

```env
WEBAPP_ENABLED=true
PUBLIC_URL=https://studio.mondomaine.com
```

Pour obtenir cette URL : un nom de domaine avec Caddy ou Nginx devant le port `8080`, un tunnel Cloudflare nommé,
ou un domaine statique gratuit ngrok. Sans URL publique, le bot propose l'ancienne méthode : tu autorises,
puis tu colles dans Telegram l'adresse de la page de retour.

### YouTube Shorts

1. [Google Cloud Console](https://console.cloud.google.com/) : nouveau projet, puis active **YouTube Data API v3**.
2. Écran de consentement OAuth, type *Externe*. Ajoute ton compte Google en *utilisateur test*
   (ou publie l'application pour que les accès n'expirent pas au bout de 7 jours).
3. Identifiants, puis **ID client OAuth** :
   - avec `PUBLIC_URL` : type **Application Web**, URI de redirection autorisée `https://studio.mondomaine.com/oauth/youtube` ;
   - sans URL publique : type **Application de bureau**.
4. Télécharge le JSON et envoie-le au bot (Réglages → Plateformes → YouTube), ou place-le dans
   `secrets/google_client_secret.json`.
5. Connecte la chaîne depuis sa page Comptes. Si Google affiche « Application non validée » : Paramètres avancés,
   puis Accéder à StoryForge (c'est ta propre application).

> **Quota** : un projet Google a 10 000 unités/jour par défaut et un upload coûte environ 1 600 unités, soit ~6 vidéos/jour
> **par projet**. Pour publier plus : demande une augmentation de quota, ou crée un projet par chaîne et dépose son
> fichier sous `secrets/google_client_secret_<ID_CHAINE>.json` (il sera utilisé automatiquement pour cette chaîne).

Les vidéos sont déclarées comme contenant des médias synthétiques (`containsSyntheticMedia`), comme l'exige YouTube pour une voix IA réaliste.

### TikTok

1. [developers.tiktok.com](https://developers.tiktok.com/) : crée une app, ajoute **Login Kit** et **Content Posting API**
   (scopes `user.info.basic` et `video.upload` ; `video.publish` seulement pour la publication directe).
   Retire tout autre scope : un scope inutile bloque la revue de TikTok.
2. Déclare la **Redirect URI** `https://studio.mondomaine.com/oauth/tiktok`.
   Saisis le Client key et le Client secret dans le bot (Réglages → Plateformes → TikTok, ils sont vérifiés
   auprès de TikTok), ou dans le `.env` (`TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`).
3. Connecte le compte depuis la page Comptes de la chaîne.
4. Optionnel : réglage « Pseudo TikTok » de la chaîne, pour que les liens des vidéos soient corrects.

**Publication automatique (Direct Post).** Bot : Réglages → Plateformes → TikTok → « Passer en publication directe »
(scope `video.publish`), puis connecte le compte et ouvre **Réglages de publication** depuis la page Comptes. Cet écran
suit les règles de TikTok : compte affiché, visibilité à choisir (aucune valeur par défaut), commentaires, duos et
stitchs, divulgation du contenu commercial, consentement (Music Usage Confirmation), aperçu de la vidéo. Coche
« Publier automatiquement » : chaque nouvelle vidéo part ensuite seule, aux horaires de la chaîne, avec ces réglages.
Sans ces réglages, StoryForge n'effectue jamais de publication directe : les vidéos partent en brouillon.

> **Important** : tant que ton app TikTok n'a pas passé l'audit de TikTok, la publication directe n'est autorisée
> qu'en **privé** (`SELF_ONLY`). Deux solutions : faire auditer l'app, ou passer la chaîne en mode **Brouillon** :
> la vidéo arrive dans ta boîte de réception TikTok et tu la publies en un tap (pratique aussi pour ajouter un son tendance).
> Les vidéos sont marquées « contenu généré par IA » (`is_aigc`).

### Instagram (Reels)

1. Passe le compte Instagram en **compte professionnel** (créateur ou entreprise), dans l'appli Instagram.
2. [developers.facebook.com](https://developers.facebook.com/) : crée une app, ajoute le produit **Instagram**
   et choisis la configuration **avec connexion Instagram** (permissions `instagram_business_basic` et
   `instagram_business_content_publish`). Ajoute ton compte Instagram comme testeur tant que l'app n'est pas validée.
3. Ajoute l'URI de redirection `https://studio.mondomaine.com/oauth/instagram`, puis saisis l'ID et la clé secrète
   de l'app Instagram dans le bot (Réglages → Plateformes → Instagram), ou dans le `.env`
   (`INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET`).
4. Connecte le compte depuis la page Comptes de la chaîne.

> Avec la connexion Instagram, l'API ne reçoit pas le fichier : Instagram télécharge chaque vidéo sur ton serveur,
> via une adresse signée valable 6 heures. Il faut donc `PUBLIC_URL` en https (`bash install.sh --https`).
> Seuls les Shorts (verticaux) sont publiés en Reels. L'accès est valable 60 jours et prolongé automatiquement.

### Mini App (dashboard visuel)

Avec la même configuration (`WEBAPP_ENABLED` et `PUBLIC_URL`), le bouton **Dashboard** apparaît dans le menu du bot.
Les requêtes sont authentifiées par la signature Telegram (`initData`) et réservées aux admins.

## Sécurité

Seuls les identifiants listés dans `TELEGRAM_ADMIN_IDS` pilotent StoryForge.

- **Bot Telegram.** Une barrière passe avant tout le reste : une personne qui n'est pas admin ne voit aucun écran,
  ne peut appuyer sur aucun bouton (même d'un ancien message transféré) et ne déclenche aucune action. Le bot
  quitte tout groupe ou canal où on l'ajoute : le dashboard ne fonctionne qu'en conversation privée. Chaque
  tentative est bloquée et signalée dans Telegram (« Tentative d'accès bloquée », une fois par heure et par personne)
  ainsi que dans le Journal.
- **Mini App et API.** Chaque requête doit porter la signature Telegram d'un admin, valable 6 h (il suffit ensuite
  de rouvrir le dashboard). La session ne circule jamais dans les adresses : les aperçus utilisent un jeton de
  lecture séparé, temporaire, qui ne permet aucune action. Une adresse IP qui accumule les accès refusés est
  bloquée 30 minutes.
- **Pages web.** Politique de sécurité du contenu stricte (aucun script externe hors Telegram, aucun script inline,
  aucune donnée envoyée ailleurs), pages non intégrables dans un autre site, HTTPS imposé (HSTS). Les liens de
  connexion des comptes sont signés et expirent après 30 minutes ; chaque autorisation OAuth n'est utilisable
  qu'une fois et pour la plateforme demandée.
- **Serveur.** Le serveur web n'écoute que sur `127.0.0.1` (seul Caddy, en HTTPS, est exposé). `.env` et les
  données ne sont lisibles que par ton utilisateur. Le service tourne dans un bac à sable systemd : pas de passage
  root possible, système et noyau en lecture seule. Les liens signés utilisent une clé dédiée (`SECRET_KEY`,
  générée à l'installation).

**Le token du bot est la clé maîtresse.** Quiconque le connaît peut se faire passer pour le bot. S'il a circulé
(capture d'écran, message, dépôt de code), régénère-le :

1. Dans Telegram, @BotFather → `/revoke` → choisis le bot : il donne un nouveau token (l'ancien cesse de marcher).
2. Sur le serveur : `nano ~/storyforge/.env`, remplace la valeur de `TELEGRAM_BOT_TOKEN`, puis
   `sudo systemctl restart storyforge`.

Même principe pour une clé ElevenLabs ou d'IA exposée : crée-en une nouvelle sur le site du fournisseur, supprime
l'ancienne, puis remplace-la dans le `.env` (ou, pour ElevenLabs, ajoute-la dans Réglages → Clés ElevenLabs).
Dans le pare-feu Lightsail, seuls les ports 22 (SSH), 80 et 443 (Caddy) doivent être ouverts.

---

## Utilisation

1. **🎞 Banque vidéos** : dépose des vidéos dans `data/backgrounds/<catégorie>/` (cooking, minecraft, satisfying…).
   Privilégie des vidéos longues (20-60 min) et **dont tu as les droits**. Musique optionnelle dans `data/music/`.
2. **➕ Nouvelle chaîne** : nom → langue → formats, voix, durée, parties…
3. **Comptes** : connecte YouTube, TikTok et/ou Instagram (chaque chaîne a ses propres comptes : c'est ce qui permet le multi-instances).
   Dans Réglages → Formats, active les vidéos longues YouTube si tu en veux.
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
├── story/               # prompts, formats, ADN aléatoire, client IA (Gemini, Groq, OpenAI)
├── tts/                 # ElevenLabs (avec horodatage) + resserrage des silences
├── video/               # banque de fonds, sous-titres ASS, carte d'accroche, rendu ffmpeg
├── publishers/          # YouTube Data API v3 · TikTok Content Posting API · Instagram (Reels)
├── bot/                 # dashboard Telegram (écrans, réglages, notifications)
└── webapp/              # Mini App, page de connexion des comptes, retours OAuth, vidéos pour Instagram
deploy/
├── install.sh           # installation sur un serveur Ubuntu (service systemd)
└── update.sh            # mise à jour automatique depuis GitHub (timer systemd)
```

### Tests

```bash
pip install pytest pytest-asyncio
pytest
```

Les tests couvrent le découpage mot à mot, le resserrage des silences, le tirage du fond, les sous-titres,
la génération d'histoire (IA simulée, dont le repli si un paramètre est refusé), le pipeline complet avec ffmpeg réel (APIs simulées), l'API de la
Mini App, et la navigation complète du bot Telegram.

---

## Bon à savoir

- **Coûts** : une vidéo de 90 s ≈ 1 500-2 000 caractères ElevenLabs et ~3 000 tokens d'IA (gratuits avec Gemini ou Groq).
- **Telegram** limite les envois de bot à 50 Mo (un aperçu allégé est généré si besoin) et les téléchargements à 20 Mo
  (pour de gros fonds, utilise un lien ou dépose-les dans `data/backgrounds/`).
- **Politiques des plateformes** : YouTube et TikTok peuvent limiter la monétisation des contenus jugés « réutilisés » ou
  répétitifs. Les histoires originales, les séquences de fond toujours différentes et la variation des horaires aident,
  mais publie des contenus qui apportent de la valeur et respecte les droits des vidéos de fond.
- Les fichiers vidéo publiés sont supprimés après `KEEP_VIDEOS_DAYS` jours (l'historique reste en base).
