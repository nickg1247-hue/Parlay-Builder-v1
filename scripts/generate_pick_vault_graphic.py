"""Generate a Pick Vault social graphic — reference style, logos only, saves to Downloads."""

from __future__ import annotations

import io
import random
from datetime import date
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

MLB_STATS = "https://statsapi.mlb.com/api/v1"
DOWNLOADS = Path.home() / "Downloads"

NEON = "#C8FF00"
NEON_DIM = "#8BB800"
BG = "#080808"
WHITE = "#F2F2F2"
MUTED = "#AAAAAA"

TEAM_LOGOS = {
    "rays": "https://a.espncdn.com/i/teamlogos/mlb/500/tb.png",
    "reds": "https://a.espncdn.com/i/teamlogos/mlb/500/cin.png",
}

PICKS = [
    {"player": "JASE BOWEN", "team_key": "rays", "prop": "UNDER 0.5 RBIs", "odds": -413},
    {"player": "BLAKE DUNN", "team_key": "reds", "prop": "UNDER 0.5 RBIs", "odds": -439},
    {"player": "MATT MCLAIN", "team_key": "reds", "prop": "UNDER 0.5 RBIs", "odds": -364},
]


def _client() -> httpx.Client:
    return httpx.Client(timeout=25, follow_redirects=True)


def _search_player_id(client: httpx.Client, name: str) -> int | None:
    resp = client.get(f"{MLB_STATS}/people/search", params={"names": name.title(), "sportId": 1})
    resp.raise_for_status()
    for person in resp.json().get("people") or []:
        if person.get("fullName", "").lower() == name.title().lower():
            return int(person["id"])
    people = resp.json().get("people") or []
    return int(people[0]["id"]) if people else None


def _rbi_game_values(client: httpx.Client, player_id: int, season: int) -> list[int]:
    resp = client.get(
        f"{MLB_STATS}/people/{player_id}/stats",
        params={"stats": "gameLog", "group": "hitting", "season": season},
    )
    resp.raise_for_status()
    splits = ((resp.json().get("stats") or [{}])[0].get("splits") or [])
    return [int((s.get("stat") or {})["rbi"]) for s in splits if (s.get("stat") or {}).get("rbi") is not None]


def _under_hit_rate(values: list[int], line: float, window: int) -> float | None:
    sample = values[-window:]
    if not sample:
        return None
    return sum(1 for v in sample if v < line) / len(sample)


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{round(value * 100)}%"


def _enrich_pick(client: httpx.Client, pick: dict, season: int) -> dict:
    out = dict(pick)
    pid = _search_player_id(client, pick["player"])
    if pid is None:
        out["l5"] = out["l10"] = "—"
        return out
    vals = _rbi_game_values(client, pid, season)
    out["l5"] = _pct(_under_hit_rate(vals, 0.5, 5))
    out["l10"] = _pct(_under_hit_rate(vals, 0.5, 10))
    return out


def _font(size: int, bold: bool = False, impact: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    paths = []
    if impact:
        paths.append("C:/Windows/Fonts/impact.ttf")
    if bold:
        paths += ["C:/Windows/Fonts/ariblk.ttf", "C:/Windows/Fonts/arialbd.ttf"]
    paths += ["C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf"]
    for p in paths:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _fetch_logo(client: httpx.Client, url: str) -> Image.Image:
    resp = client.get(url)
    resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content)).convert("RGBA")


def _fit_logo(logo: Image.Image, size: int) -> Image.Image:
    img = logo.copy()
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    return img


def _circular_logo(logo: Image.Image, diameter: int, ring: int = 4) -> Image.Image:
    canvas = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((0, 0, diameter - 1, diameter - 1), fill="#111111", outline=NEON, width=ring)
    inner = diameter - ring * 2 - 16
    fitted = _fit_logo(logo, inner)
    ox = (diameter - fitted.width) // 2
    oy = (diameter - fitted.height) // 2
    canvas.paste(fitted, (ox, oy), fitted)
    return canvas


def _make_background(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(img)

    # Stadium light glow at top
    for cx, cy, r, alpha in [(180, 60, 120, 35), (540, 40, 180, 45), (900, 70, 130, 35), (420, 90, 90, 25)]:
        glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(200, 255, 0, alpha))
        glow = glow.filter(ImageFilter.GaussianBlur(40))
        img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
        draw = ImageDraw.Draw(img)

    # Green vignette
    vignette = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    vd = ImageDraw.Draw(vignette)
    vd.rectangle((0, 0, w, h), fill=(0, 0, 0, 120))
    vd.rectangle((40, 80, w - 40, h - 40), fill=(0, 0, 0, 0))
    for edge in [(0, 0, w, 80), (0, h - 120, w, h)]:
        vd.rectangle(edge, fill=(20, 40, 0, 80))
    img = Image.alpha_composite(img.convert("RGBA"), vignette).convert("RGB")

    # Noise / grit
    random.seed(42)
    noise = Image.new("RGBA", (w, h))
    nd = ImageDraw.Draw(noise)
    for _ in range(w * h // 18):
        x, y = random.randint(0, w - 1), random.randint(0, h - 1)
        v = random.randint(0, 40)
        nd.point((x, y), fill=(v, v + 10, v, 30))
    noise = noise.filter(ImageFilter.GaussianBlur(0.5))
    img = Image.alpha_composite(img.convert("RGBA"), noise).convert("RGB")
    return img


def _draw_lock(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color: str = NEON) -> None:
    bw, bh = size * 0.5, size * 0.38
    sw, sh = size * 0.34, size * 0.26
    draw.rounded_rectangle(
        (cx - bw / 2, cy - bh * 0.1, cx + bw / 2, cy + bh),
        radius=max(2, size // 10),
        fill=color,
    )
    draw.arc(
        (cx - sw / 2, cy - sh * 1.5, cx + sw / 2, cy + sh * 0.2),
        180, 0, fill=color, width=max(2, size // 10),
    )


def _brush_banner(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, font) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y = 18, 8
    w, h = tw + pad_x * 2, th + pad_y * 2
    pts = [(x, y + 4), (x + w - 8, y), (x + w, y + h), (x + 10, y + h + 4)]
    draw.polygon(pts, fill=NEON)
    draw.text((x + pad_x, y + pad_y - 2), text, fill="#111111", font=font)


def _glow_rect(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int = 14) -> None:
    x0, y0, x1, y1 = box
    for offset, color in [(6, "#1a2a00"), (3, NEON_DIM), (0, NEON)]:
        draw.rounded_rectangle(
            (x0 - offset, y0 - offset, x1 + offset, y1 + offset),
            radius=radius + offset,
            outline=color,
            width=2,
        )
    draw.rounded_rectangle(box, radius=radius, fill="#0D0D0D", outline=NEON, width=2)


def _draw_pick_row(
    base: Image.Image,
    pick: dict,
    logo: Image.Image,
    y: int,
    row_h: int,
) -> None:
    draw = ImageDraw.Draw(base)
    x0, x1 = 50, 1030
    _glow_rect(draw, (x0, y, x1, y + row_h))

    # Large circular team logo (replaces player photo)
    badge = _circular_logo(logo, 130, ring=3)
    base.paste(badge, (x0 + 18, y + (row_h - 130) // 2), badge)

    # Small logo + name
    small = _fit_logo(logo, 44)
    name_x = x0 + 168
    base.paste(small, (name_x, y + 28), small)

    font_name = _font(34, bold=True, impact=True)
    font_prop = _font(20, bold=True)
    font_odds_lbl = _font(16, bold=True)
    font_odds = _font(52, bold=True, impact=True)
    font_stat_l = _font(14, bold=True)
    font_stat_v = _font(22, bold=True)

    draw.text((name_x + 54, y + 30), pick["player"], fill=WHITE, font=font_name)
    _brush_banner(draw, name_x, y + 78, pick["prop"], font_prop)

    # L5 / L10 mini boxes
    for i, (lbl, val) in enumerate([("L5 HIT", pick["l5"]), ("L10 HIT", pick["l10"])]):
        bx = name_x + i * 108
        by = y + row_h - 52
        draw.rounded_rectangle((bx, by, bx + 96, by + 38), radius=6, outline=NEON, width=2, fill="#0A0A0A")
        draw.text((bx + 48, by + 6), lbl, fill=MUTED, font=font_stat_l, anchor="mt")
        draw.text((bx + 48, by + 32), val, fill=NEON, font=font_stat_v, anchor="mb")

    # Odds column
    div_x = x1 - 155
    draw.line([(div_x, y + 20), (div_x, y + row_h - 20)], fill=NEON_DIM, width=2)
    draw.text((div_x + 78, y + 36), "ODDS", fill=NEON, font=font_odds_lbl, anchor="mt")
    draw.text((div_x + 78, y + row_h // 2 + 8), str(pick["odds"]), fill=NEON, font=font_odds, anchor="mm")


def _draw_footer(base: Image.Image, y: int) -> None:
    draw = ImageDraw.Draw(base)
    w = base.width
    font_sm = _font(13, bold=True)
    font_cta1 = _font(22, bold=True, impact=True)
    font_cta2 = _font(26, bold=True, impact=True)
    font_bar = _font(18, bold=True, impact=True)
    font_disc = _font(11)

    # Feature labels
    labels = [("▮▮▮", "DATA DRIVEN"), ("◎", "SHARP INSIGHT"), ("✓", "PROVEN EDGE")]
    lx = 80
    for icon, label in labels:
        draw.text((lx, y), icon, fill=NEON, font=_font(20, bold=True))
        draw.text((lx, y + 26), label, fill=WHITE, font=font_sm)
        lx += 155

    # Lock + CTA
    _draw_lock(draw, w // 2 - 180, y + 58, 36)
    draw.text((w // 2 - 130, y + 48), "THE EDGE ISN'T LUCK.", fill=WHITE, font=font_cta1)
    draw.text((w // 2 - 130, y + 76), "IT'S DATA.", fill=NEON, font=font_cta2)

    # Bottom green bar
    bar_y = y + 130
    draw.rectangle((0, bar_y, w, bar_y + 44), fill=NEON)
    draw.text((w // 2, bar_y + 22), "🏆  LONG TERM PROFIT  >  PERFECT DAYS  >>>", fill="#111111", font=font_bar, anchor="mm")
    draw.text((w // 2, bar_y + 58), "*ODDS SUBJECT TO CHANGE", fill=MUTED, font=font_disc, anchor="mt")


def generate(output_path: Path | None = None) -> Path:
    w, h = 1080, 1350
    base = _make_background(w, h)
    draw = ImageDraw.Draw(base)

    # Header lock + brand
    _draw_lock(draw, w // 2 - 120, 42, 28)
    font_vault = _font(28, bold=True, impact=True)
    draw.text((w // 2 - 82, 34), "THE PICK VAULT", fill=NEON, font=font_vault)

    font_today = _font(58, bold=True, impact=True)
    font_top = _font(72, bold=True, impact=True)
    draw.text((w // 2, 88), "TODAY'S", fill=WHITE, font=font_today, anchor="mt")
    draw.text((w // 2, 148), "TOP PICKS", fill=NEON, font=font_top, anchor="mt")

    # Tagline bar
    bar_y = 228
    draw.rectangle((0, bar_y, w, bar_y + 36), fill=NEON)
    draw.text((w // 2, bar_y + 18), "◎  DATA. ANALYTICS. RESULTS. WE FIND THE EDGE.", fill="#111111", font=_font(17, bold=True), anchor="mm")

    season = date.today().year
    with _client() as client:
        enriched = [_enrich_pick(client, p, season) for p in PICKS]
        logos = {k: _fetch_logo(client, v) for k, v in TEAM_LOGOS.items()}

    row_h = 168
    row_gap = 22
    start_y = 290
    for i, pick in enumerate(enriched):
        _draw_pick_row(base, pick, logos[pick["team_key"]], start_y + i * (row_h + row_gap), row_h)

    _draw_footer(base, 870)

    if output_path is None:
        output_path = DOWNLOADS / f"pick-vault-top-picks-{date.today().isoformat()}.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    base.save(output_path, format="PNG", optimize=True)
    return output_path


if __name__ == "__main__":
    path = generate()
    print(path)
