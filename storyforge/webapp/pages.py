"""Pages web (hors Telegram) : connexion des comptes YouTube / TikTok — Minimal Design System."""

from __future__ import annotations

import html
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from fastapi.responses import HTMLResponse

if TYPE_CHECKING:
    from ..models import Channel

HEAD = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="robots" content="noindex">
  <title>{title}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {{
      --background: 0 0% 100%; --foreground: 240 10% 3.9%; --card: 0 0% 100%;
      --primary: 240 5.9% 10%; --primary-foreground: 0 0% 98%;
      --muted: 240 4.8% 95.9%; --muted-foreground: 240 3.8% 46.1%;
      --border: 240 5.9% 90%; --ring: 240 5.9% 10%; --radius: 0.5rem;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --background: 240 10% 3.9%; --foreground: 0 0% 98%; --card: 240 10% 3.9%;
        --primary: 0 0% 98%; --primary-foreground: 240 5.9% 10%;
        --muted: 240 3.7% 15.9%; --muted-foreground: 240 5% 64.9%;
        --border: 240 3.7% 15.9%; --ring: 240 4.9% 83.9%;
      }}
    }}
    * {{ font-family: 'Inter', system-ui, sans-serif; }}
    body {{ background-color: hsl(var(--background)); color: hsl(var(--foreground)); }}
    .text-muted {{ color: hsl(var(--muted-foreground)); }}
    .bg-muted {{ background-color: hsl(var(--muted)); }}
    .bg-card {{ background-color: hsl(var(--card)); }}
    .border-default {{ border-color: hsl(var(--border)); }}
    .bg-primary {{ background-color: hsl(var(--primary)); }}
    .text-primary-foreground {{ color: hsl(var(--primary-foreground)); }}
    .bg-page {{ background-color: hsl(var(--background)); }}
    a:focus-visible, button:focus-visible, input:focus-visible {{ outline: 2px solid hsl(var(--ring)); outline-offset: 2px; }}
  </style>
</head>
<body class="min-h-screen antialiased">
  <header class="border-b border-default">
    <div class="max-w-2xl mx-auto px-6 h-16 flex items-center gap-3">
      <div class="w-9 h-9 rounded-full bg-muted flex items-center justify-center"><span class="text-xs font-semibold">SF</span></div>
      <div>
        <p class="font-semibold tracking-tight leading-tight">StoryForge</p>
        <p class="text-xs text-muted">{subtitle}</p>
      </div>
    </div>
  </header>
  <main class="max-w-2xl mx-auto px-6 py-12 sm:py-16">
"""

FOOT = """  </main>
  <footer class="border-t border-default mt-8">
    <div class="max-w-2xl mx-auto px-6 py-6"><p class="text-xs text-muted">{note}</p></div>
  </footer>
</body>
</html>"""

BTN_PRIMARY = ("inline-flex items-center justify-center gap-2 h-11 px-6 bg-primary text-primary-foreground "
               "text-sm font-medium rounded-md hover:opacity-90 transition-opacity")
BTN_SECONDARY = ("inline-flex items-center justify-center gap-2 h-11 px-6 border border-default text-sm font-medium "
                 "rounded-md hover:bg-muted transition-colors")
BTN_GHOST = ("inline-flex items-center justify-center h-11 px-4 text-sm font-medium text-muted rounded-md "
             "hover:bg-muted hover:text-[hsl(var(--foreground))] transition-colors")

BADGE = {
    "success": "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20",
    "warning": "bg-yellow-500/10 text-yellow-700 dark:text-yellow-400 border border-yellow-500/20",
    "neutral": "bg-muted text-muted border border-default",
}
ALERT = {
    "success": "bg-emerald-500/10 border-emerald-500/20 text-emerald-700 dark:text-emerald-400",
    "error": "bg-red-500/10 border-red-500/20 text-red-700 dark:text-red-400",
    "warning": "bg-yellow-500/10 border-yellow-500/20 text-yellow-800 dark:text-yellow-400",
    "info": "bg-blue-500/10 border-blue-500/20 text-blue-700 dark:text-blue-400",
}

ICON_CHECK = ('<svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
              'stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>')
ICON_X = ('<svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
          'stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>')

PLATFORMS = {
    "youtube": {
        "name": "YouTube",
        "provider": "Google",
        "scopes": ["Mettre en ligne des vidéos sur ta chaîne", "Lire le nom de ta chaîne"],
        "hint": "Si Google affiche « Application non validée », clique sur « Paramètres avancés » puis "
                "« Accéder à StoryForge » : c'est ton application.",
    },
    "instagram": {
        "name": "Instagram",
        "provider": "Instagram",
        "scopes": ["Publier des Reels sur ton compte professionnel", "Lire ton nom d'utilisateur"],
        "hint": "Il faut un compte Instagram professionnel (créateur ou entreprise). Instagram récupère chaque "
                "vidéo sur ton serveur au moment de la publication.",
    },
    "tiktok": {
        "name": "TikTok",
        "provider": "TikTok",
        "scopes": ["Publier des vidéos ou les envoyer en brouillon", "Lire ton nom d'affichage"],
        "hint": "Tant que ton app TikTok n'est pas auditée, les vidéos sont publiées en privé ou envoyées en brouillon.",
    },
}


def e(value: object) -> str:
    return html.escape(str(value), quote=True)


def page(title: str, body: str, *, subtitle: str = "Connexion des comptes", note: str = "", status: int = 200) -> HTMLResponse:
    content = HEAD.format(title=e(title), subtitle=e(subtitle)) + body + FOOT.format(
        note=e(note or "Tu peux révoquer l'accès à tout moment depuis les réglages de ton compte Google, TikTok ou Instagram.")
    )
    return HTMLResponse(content, status_code=status, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def alert(kind: str, text: str) -> str:
    return f'<div role="status" class="mb-8 p-4 rounded-lg border {ALERT[kind]}"><p class="text-sm">{text}</p></div>'


def _platform_card(platform: str, ch: Channel, token: str, *, problem: str | None, automatic: bool) -> str:
    meta = PLATFORMS[platform]
    linked = getattr(ch, f"{platform}_linked")
    account = getattr(ch, f"{platform}_account")
    enabled = getattr(ch, f"{platform}_enabled")
    if linked:
        badge = f'<span class="text-xs px-2.5 py-1 rounded-full {BADGE["success"]}">Connecté</span>'
    elif problem:
        badge = f'<span class="text-xs px-2.5 py-1 rounded-full {BADGE["warning"]}">À configurer</span>'
    else:
        badge = f'<span class="text-xs px-2.5 py-1 rounded-full {BADGE["neutral"]}">Non connecté</span>'

    scopes = "".join(
        f'<li class="flex items-start gap-2"><span class="mt-2 w-1 h-1 rounded-full bg-[hsl(var(--muted-foreground))] shrink-0"></span>{e(x)}</li>'
        for x in meta["scopes"]
    )
    body = [
        '<article class="p-6 rounded-lg border border-default bg-card">',
        '  <div class="flex items-start justify-between gap-4">',
        f'    <div><h2 class="font-semibold tracking-tight">{meta["name"]}</h2>',
        f'      <p class="text-sm text-muted mt-1">{e(account) if linked else "Aucun compte connecté"}'
        f'{"" if enabled else " · publication désactivée pour cette chaîne"}</p></div>',
        f'    {badge}',
        '  </div>',
    ]
    if problem:
        body.append(f'  <p class="text-sm text-muted mt-4 leading-relaxed">{e(problem)} '
                    'Consulte la section « Configuration des plateformes » du README.</p>')
    else:
        body += [
            '  <p class="text-sm mt-6 mb-2 font-medium">StoryForge pourra :</p>',
            f'  <ul class="text-sm text-muted space-y-1.5">{scopes}</ul>',
            '  <div class="flex flex-wrap items-center gap-3 mt-6">',
            f'    <a href="/connect/{e(token)}/{platform}" class="{BTN_PRIMARY if not linked else BTN_SECONDARY}">'
            f'{"Changer de compte" if linked else "Se connecter avec " + meta["name"]}</a>',
        ]
        if linked:
            body.append(
                f'    <form method="post" action="/connect/{e(token)}/{platform}/disconnect">'
                f'<button type="submit" class="{BTN_GHOST}">Déconnecter</button></form>'
            )
        body.append('  </div>')
        body.append(f'  <p class="text-xs text-muted mt-4 leading-relaxed">{e(meta["hint"])}</p>')
        if not automatic:
            body.append(
                f'''  <details class="mt-6 pt-6 border-t border-default">
    <summary class="text-sm font-medium cursor-pointer">La page de retour affiche une erreur ?</summary>
    <p class="text-sm text-muted mt-3 leading-relaxed">C'est normal avec cette configuration. Copie l'adresse complète
    de cette page d'erreur et colle-la ici.</p>
    <form method="post" action="/connect/{e(token)}/{platform}/paste" class="mt-4 flex flex-col sm:flex-row gap-3">
      <label for="u-{platform}" class="sr-only">Adresse de la page de retour</label>
      <input id="u-{platform}" name="url" type="url" required placeholder="http://localhost:8765/?state=…&code=…"
        class="flex-1 h-11 px-4 rounded-md border border-default bg-page text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] focus:ring-offset-2">
      <button type="submit" class="{BTN_SECONDARY}">Valider</button>
    </form>
  </details>'''
            )
    body.append('</article>')
    return "\n".join(body)


def portal(ch: Channel, token: str, expires: int, *, problems: dict[str, str | None], automatic: dict[str, bool],
           flash: tuple[str, str] | None = None, tz: str = "UTC") -> HTMLResponse:
    try:
        zone = ZoneInfo(tz)
    except Exception:
        zone = ZoneInfo("UTC")
    until = datetime.fromtimestamp(expires, zone).strftime("%H:%M")
    parts = [
        '<p class="text-sm text-muted mb-3">Chaîne</p>',
        f'<h1 class="text-3xl sm:text-4xl font-bold tracking-tight mb-4">{e(ch.name)}</h1>',
        '<p class="text-muted leading-relaxed mb-10 max-w-xl">Connecte les comptes sur lesquels cette chaîne publiera. '
        "Tu seras redirigé vers la plateforme pour autoriser StoryForge, puis ramené ici.</p>",
    ]
    if flash:
        parts.append(alert(*flash))
    parts.append('<div class="space-y-4">')
    for platform in ("youtube", "tiktok", "instagram"):
        parts.append(_platform_card(platform, ch, token, problem=problems.get(platform), automatic=automatic.get(platform, False)))
    parts.append("</div>")
    return page(f"Comptes · {ch.name}", "\n".join(parts), note=f"Ce lien expire à {until}. Tu peux en générer un nouveau depuis le bot.")


def result(*, ok: bool, heading: str, message: str, portal_url: str = "", telegram_url: str = "") -> HTMLResponse:
    icon_cls = ("bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" if ok
                else "bg-red-500/10 text-red-600 dark:text-red-400")
    buttons = []
    if portal_url:
        buttons.append(f'<a href="{e(portal_url)}" class="{BTN_PRIMARY}">Retour aux comptes</a>')
    if telegram_url:
        buttons.append(f'<a href="{e(telegram_url)}" class="{BTN_SECONDARY}">Ouvrir Telegram</a>')
    body = f"""
    <div class="max-w-md">
      <div class="w-11 h-11 rounded-full flex items-center justify-center mb-6 {icon_cls}">{ICON_CHECK if ok else ICON_X}</div>
      <h1 class="text-2xl font-semibold tracking-tight mb-3">{e(heading)}</h1>
      <p class="text-muted leading-relaxed mb-8">{message}</p>
      <div class="flex flex-wrap gap-3">{''.join(buttons)}</div>
    </div>"""
    return page(heading, body, status=200 if ok else 400)


def expired(telegram_url: str = "") -> HTMLResponse:
    link = f' <a class="underline underline-offset-4" href="{e(telegram_url)}">Ouvrir Telegram</a>' if telegram_url else ""
    body = f"""
    <div class="max-w-md">
      <h1 class="text-2xl font-semibold tracking-tight mb-3">Lien expiré</h1>
      <p class="text-muted leading-relaxed">Ce lien de connexion n'est plus valable. Dans le bot, ouvre la chaîne puis
      « Comptes » pour en obtenir un nouveau.{link}</p>
    </div>"""
    return page("Lien expiré", body, status=403)
