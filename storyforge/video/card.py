"""Carte d'accroche (style post de forum) affichée pendant la lecture du titre."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


def _font(fonts_dir: Path, name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (fonts_dir / name, fonts_dir / "Montserrat-ExtraBold.ttf"):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in text.split():
        test = f"{cur} {word}".strip()
        if draw.textlength(test, font=font) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _accent(name: str) -> tuple[int, int, int]:
    palette = [(255, 92, 92), (61, 139, 255), (155, 93, 229), (0, 187, 166), (255, 159, 28), (241, 91, 181)]
    idx = int(hashlib.md5(name.encode()).hexdigest(), 16) % len(palette)
    return palette[idx]


def render_hook_card(title: str, channel_name: str, out_path: Path, fonts_dir: Path, *, width: int = 1080) -> Path:
    card_w = int(width * 0.86)
    pad = 46
    title_font = _font(fonts_dir, "Montserrat-ExtraBold.ttf", 52)
    name_font = _font(fonts_dir, "Montserrat-ExtraBold.ttf", 38)
    small_font = _font(fonts_dir, "Montserrat-ExtraBold.ttf", 30)

    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lines = _wrap(probe, title, title_font, card_w - 2 * pad)[:6]
    line_h = 66
    avatar = 92
    card_h = pad + avatar + 30 + len(lines) * line_h + 34 + 44 + pad

    margin = 40  # place pour l'ombre
    img = Image.new("RGBA", (card_w + 2 * margin, card_h + 2 * margin), (0, 0, 0, 0))

    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin + 10, margin + card_w, margin + card_h + 10), radius=40, fill=(0, 0, 0, 110)
    )
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(18)))
    d = ImageDraw.Draw(img)
    x0, y0 = margin, margin
    d.rounded_rectangle((x0, y0, x0 + card_w, y0 + card_h), radius=40, fill=(255, 255, 255, 255))

    # avatar
    accent = _accent(channel_name)
    ax, ay = x0 + pad, y0 + pad
    d.ellipse((ax, ay, ax + avatar, ay + avatar), fill=accent + (255,))
    initial = (channel_name.strip()[:1] or "S").upper()
    av_font = _font(fonts_dir, "Montserrat-Black.ttf", 50)
    d.text((ax + avatar / 2, ay + avatar / 2 + 2), initial, font=av_font, fill="white", anchor="mm")

    # nom + badge vérifié
    nx = ax + avatar + 24
    d.text((nx, ay + 10), channel_name[:28], font=name_font, fill=(17, 17, 17))
    bx = nx + d.textlength(channel_name[:28], font=name_font) + 14
    by = ay + 14
    d.ellipse((bx, by, bx + 34, by + 34), fill=(29, 155, 240))
    d.line([(bx + 9, by + 18), (bx + 15, by + 24), (bx + 26, by + 11)], fill="white", width=5)
    d.text((nx, ay + 56), "storytime", font=small_font, fill=(130, 130, 140))

    # titre
    ty = ay + avatar + 30
    for i, line in enumerate(lines):
        d.text((x0 + pad, ty + i * line_h), line, font=title_font, fill=(15, 15, 20))

    # barre de réactions
    ry = ty + len(lines) * line_h + 34
    hx = x0 + pad
    heart = [(hx + 18, ry + 36), (hx, ry + 16), (hx + 4, ry + 4), (hx + 18, ry + 10), (hx + 32, ry + 4), (hx + 36, ry + 16)]
    d.polygon(heart, fill=(255, 64, 88))
    d.ellipse((hx - 1, ry, hx + 19, ry + 20), fill=(255, 64, 88))
    d.ellipse((hx + 17, ry, hx + 37, ry + 20), fill=(255, 64, 88))
    d.text((hx + 50, ry + 20), "99+", font=small_font, fill=(90, 90, 100), anchor="lm")
    cx = hx + 160
    d.rounded_rectangle((cx, ry + 2, cx + 38, ry + 30), radius=9, fill=(120, 120, 135))
    d.polygon([(cx + 8, ry + 28), (cx + 8, ry + 40), (cx + 20, ry + 28)], fill=(120, 120, 135))
    d.text((cx + 52, ry + 20), "99+", font=small_font, fill=(90, 90, 100), anchor="lm")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def render_cover(frame: Path, title: str, out_path: Path, fonts_dir: Path, *, size: tuple[int, int] = (1280, 720)) -> Path:
    """Miniature YouTube 16:9 : image du fond assombrie + titre en grand (vidéo longue)."""
    W, H = size
    img = Image.open(frame).convert("RGB")
    scale = max(W / img.width, H / img.height)
    img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1))
    left, top = (img.width - W) // 2, (img.height - H) // 2
    img = img.crop((left, top, left + W, top + H))
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for y in range(H):  # dégradé sombre vers le bas pour la lisibilité
        sd.line([(0, y), (W, y)], fill=(0, 0, 0, int(40 + 150 * (y / H))))
    img = Image.alpha_composite(img.convert("RGBA"), shade)
    d = ImageDraw.Draw(img)
    size_px = 92
    font = _font(fonts_dir, "Montserrat-Black.ttf", size_px)
    lines = _wrap(d, title.upper(), font, W - 120)
    while len(lines) > 3 and size_px > 56:
        size_px -= 8
        font = _font(fonts_dir, "Montserrat-Black.ttf", size_px)
        lines = _wrap(d, title.upper(), font, W - 120)
    lines = lines[:3]
    line_h = int(size_px * 1.12)
    y = H - 70 - line_h * len(lines)
    for line in lines:
        d.text((60, y), line, font=font, fill="white", stroke_width=6, stroke_fill="black")
        y += line_h
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "JPEG", quality=90)
    return out_path


def render_card_cover(frame: Path, card_png: Path, out_path: Path, *, size: tuple[int, int] = (1280, 720)) -> Path:
    """Miniature YouTube 16:9 : la carte d'accroche, centrée sur une image du fond floutée et assombrie."""
    W, H = size
    bg = Image.open(frame).convert("RGB")
    scale = max(W / bg.width, H / bg.height)
    bg = bg.resize((int(bg.width * scale) + 1, int(bg.height * scale) + 1))
    left, top = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(6))
    bg = Image.alpha_composite(bg.convert("RGBA"), Image.new("RGBA", (W, H), (0, 0, 0, 70)))
    card = Image.open(card_png).convert("RGBA")
    k = min(W * 0.78 / card.width, H * 0.92 / card.height)
    card = card.resize((max(1, int(card.width * k)), max(1, int(card.height * k))), Image.LANCZOS)
    bg.alpha_composite(card, ((W - card.width) // 2, (H - card.height) // 2))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bg.convert("RGB").save(out_path, "JPEG", quality=92)
    return out_path
