"""Generates the banner images in assets/. Run on Windows (uses Segoe UI fonts):  python tools/make_banners.py"""
import os
import re

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
FONTS = r"C:\Windows\Fonts"
S = 2  # draw at 2x, then downscale for smooth edges

NAVY = (5, 9, 22)
BLUE = (0, 127, 253)
CYAN = (0, 233, 253)
PURPLE = (110, 70, 255)
WHITE = (255, 255, 255)

BANNERS = {
    "rules": ("SERVER RULES", "Where ideas get {engineered}."),
    "support": ("SUPPORT", "Our team is {here to help}."),
    "welcome": ("WELCOME", "Aloha! Glad you {made it}."),
    "announcement": ("ANNOUNCEMENT", "The latest from {Hawaii Studio}."),
    "stocks": ("STOCK MARKET", "Own a piece of {our servers}."),
    "moderation": ("MODERATION", "Keeping the community {safe}."),
    "leaderboard": ("LEADERBOARD", "The richest members of {the studio}."),
    "settings": ("SETTINGS", "Your server, {your way}."),
    "commands": ("COMMANDS", "Everything I can {do for you}."),
    "verify": ("VERIFICATION", "Unlock {the full studio}."),
    "boosters": ("BOOSTER PERKS", "Thank you for {supporting us}."),
}


def font(name, size):
    return ImageFont.truetype(os.path.join(FONTS, name), size * S)


def glow(size, center, radius, color, alpha):
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    x, y = center
    ImageDraw.Draw(layer).ellipse([x - radius, y - radius, x + radius, y + radius], fill=color + (alpha,))
    return layer.filter(ImageFilter.GaussianBlur(radius * 0.55))


def background(w, h):
    img = Image.new("RGBA", (w, h), NAVY + (255,))
    for center, radius, color, alpha in [
        ((int(w * 0.06), int(h * 0.05)), int(h * 0.75), BLUE, 150),
        ((int(w * 0.97), int(h * 1.0)), int(h * 0.7), CYAN, 85),
        ((int(w * 0.85), int(h * 0.0)), int(h * 0.45), PURPLE, 70),
    ]:
        img.alpha_composite(glow((w, h), center, radius, color, alpha))

    stripes = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(stripes)
    for x in range(-h, w, 16 * S):
        d.line([(x, h), (x + h, 0)], fill=WHITE + (9,), width=S)
    img.alpha_composite(stripes)
    return img


def glow_line(img, x1, x2, y, color, alpha=170):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.line([(x1, y), (x2, y)], fill=color + (alpha,), width=3 * S)
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(4 * S)))
    img.alpha_composite(layer)


def spaced_text(draw, xy, text, fnt, fill, spacing):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += draw.textlength(ch, font=fnt) + spacing


def spaced_width(draw, text, fnt, spacing):
    return sum(draw.textlength(ch, font=fnt) for ch in text) + spacing * (len(text) - 1)


def banner(title, tagline):
    w, h = 1500 * S, 480 * S
    img = background(w, h)
    d = ImageDraw.Draw(img)

    # Inner frame
    inset = 26 * S
    d.rounded_rectangle([inset, inset, w - inset, h - inset], radius=24 * S, outline=WHITE + (40,), width=2 * S)

    # Decorative lines
    glow_line(img, int(w * 0.05), int(w * 0.2), int(h * 0.66), BLUE)
    glow_line(img, int(w * 0.76), int(w * 0.94), int(h * 0.2), CYAN, 120)
    glow_line(img, int(w * 0.8), int(w * 0.95), int(h * 0.8), BLUE, 140)
    d = ImageDraw.Draw(img)

    # Brand pill
    pill_font = font("segoeuib.ttf", 21)
    pill_text, spacing = "HAWAII STUDIO", 5 * S
    tw = spaced_width(d, pill_text, pill_font, spacing)
    px, py, ph = (w - tw) / 2, 62 * S, 42 * S
    d.rounded_rectangle([px - 26 * S, py, px + tw + 26 * S, py + ph], radius=ph // 2,
                        fill=NAVY + (200,), outline=WHITE + (210,), width=2 * S)
    spaced_text(d, (px, py + 7 * S), pill_text, pill_font, WHITE, spacing)

    # Title (shrinks to fit)
    size = 132
    while True:
        title_font = font("seguibli.ttf", size)
        box = d.textbbox((0, 0), title, font=title_font)
        if box[2] - box[0] <= w * 0.78 or size <= 60:
            break
        size -= 4
    tw, th = box[2] - box[0], box[3] - box[1]
    tx, ty = (w - tw) / 2 - box[0], 150 * S - box[1]
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((tx + 4 * S, ty + 8 * S), title, font=title_font, fill=(0, 0, 0, 200))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(10 * S)))
    title_glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(title_glow).text((tx, ty), title, font=title_font, fill=BLUE + (120,))
    img.alpha_composite(title_glow.filter(ImageFilter.GaussianBlur(18 * S)))
    d = ImageDraw.Draw(img)
    d.text((tx, ty), title, font=title_font, fill=WHITE)

    # Accent bar (cyan -> blue)
    bar_w, bar_h, bar_y = 110 * S, 7 * S, 150 * S + th + 30 * S
    bar = Image.new("RGBA", (bar_w, bar_h))
    for x in range(bar_w):
        t = x / bar_w
        ImageDraw.Draw(bar).line([(x, 0), (x, bar_h)], fill=tuple(int(CYAN[i] * (1 - t) + BLUE[i] * t) for i in range(3)))
    mask = Image.new("L", (bar_w, bar_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, bar_w, bar_h], radius=bar_h // 2, fill=255)
    img.paste(bar, ((w - bar_w) // 2, bar_y), mask)

    # Tagline with a highlighted {word}
    parts = re.split(r"[{}]", tagline)
    regular, bold = font("seguisbi.ttf", 34), font("segoeuiz.ttf", 34)
    fonts = [regular if i % 2 == 0 else bold for i in range(len(parts))]
    total = sum(d.textlength(p, font=f) for p, f in zip(parts, fonts))
    x, y = (w - total) / 2, bar_y + 36 * S
    for i, (part, f) in enumerate(zip(parts, fonts)):
        d.text((x, y), part, font=f, fill=CYAN if i % 2 else (205, 214, 230))
        x += d.textlength(part, font=f)

    return img.resize((w // S, h // S), Image.LANCZOS)


def footer():
    w, h = 1500 * S, 100 * S
    img = background(w, h)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([S, S, w - S, h - S], radius=18 * S, outline=WHITE + (35,), width=2 * S)
    text, spacing = "HAWAII STUDIO", 6 * S
    f = font("seguibli.ttf", 30)
    tw = spaced_width(d, text, f, spacing)
    box = d.textbbox((0, 0), text, font=f)
    x, y = (w - tw) / 2, (h - (box[3] - box[1])) / 2 - box[1]
    spaced_text(d, (x, y), text, f, WHITE, spacing)
    for x1, x2 in [(int(w * 0.06), int(x - 40 * S)), (int(x + tw + 40 * S), int(w * 0.94))]:
        d.line([(x1, h // 2), (x2, h // 2)], fill=WHITE + (70,), width=2 * S)
    return img.resize((w // S, h // S), Image.LANCZOS)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, (title, tagline) in BANNERS.items():
        banner(title, tagline).convert("RGB").save(os.path.join(OUT, f"{name}.png"), optimize=True)
    footer().convert("RGB").save(os.path.join(OUT, "footer.png"), optimize=True)
    print("Saved banners to", OUT)
