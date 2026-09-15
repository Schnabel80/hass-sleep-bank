"""Erzeugt die Bilddateien der Integration aus der Vorlage.

Aufruf (Pillow und NumPy werden nur hier gebraucht, nicht von der Integration):

    uvx --with pillow --with numpy --with pyoxipng python artwork/make_brand_assets.py

Quelle ist `artwork/icon-source.png`, die gestaltete Vorlage. Dieses Skript leitet
daraus alle von Home Assistant geforderten Fassungen ab und legt sie in
`custom_components/sleep_bank/brand/` ab — genau dort sucht die
HACS-Prüfung sie. Die Vorlage selbst wird nie verändert; wer die Gestaltung
ändern will, tauscht sie aus und lässt das Skript neu laufen.

Warum überhaupt Nachbearbeitung
-------------------------------
Die Vorlage kommt als undurchsichtiges Quadrat mit **weißen Ecken**. Auf einem
dunklen Home-Assistant-Theme sähe das aus wie ein kaputtes Bild. Die Ecken werden
deshalb freigestellt.

Beim Freistellen entsteht leicht ein weißer Saum: Die Randpixel sind Mischungen
aus Motiv und weißem Untergrund, und beim Verkleinern zieht dieses Weiß in die
Kante. Dagegen hilft nur, die Farbe des Motivs vor dem Skalieren **nach außen
fortzusetzen** und die Form allein über den Alphakanal zu bestimmen.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ARTWORK_DIR = Path(__file__).resolve().parent
SOURCE = ARTWORK_DIR / "icon-source.png"

#: Ablageort der erzeugten Dateien. Der Unterordner `brand` ist verbindlich:
#: Die HACS-Prüfung sucht die Bildmarke ausdrücklich unter
#: `custom_components/<domain>/brand/icon.png` und fällt sonst auf die zentrale
#: Home-Assistant-Markendatenbank zurück, in der die Integration nicht steht.
OUTPUT_DIR = ARTWORK_DIR.parent / "custom_components" / "sleep_bank" / "brand"

#: Ab diesem Helligkeitswert gilt ein Pixel als Teil des weißen Untergrunds.
WHITE_THRESHOLD = 235

#: Wie weit die Motivfarbe über die Kante hinaus fortgesetzt wird, bevor skaliert
#: wird. Vier Pixel reichen für jede hier verwendete Verkleinerung.
COLOUR_BLEED_PIXELS = 4

#: Weichzeichnung der Alphakante in Pixeln der Vorlage.
EDGE_FEATHER = 1.0

#: Vergrößerung des Motivs innerhalb des Rahmens.
#:
#: In der Vorlage nimmt das Motiv nur rund ein Drittel der Kantenlänge ein. Bei
#: 32 px — der Größe in Listen und Auswahldialogen — wird der Mond dadurch zum
#: Klecks. Das Bild wird deshalb vergrößert und anschließend auf die ursprüngliche
#: Eckform zurückgeschnitten; der Rahmen bleibt also identisch, nur das Motiv
#: wächst. 1,18 ist der Wert, bei dem es spürbar lesbarer wird, ohne dass der
#: Behälter unten an den Rahmen stößt.
MOTIF_SCALE = 1.18

#: Schriftzug in zwei Fassungen. Ein Logo wird auf hellem *und* dunklem Grund
#: gezeigt; ein einzelner heller Schriftzug verschwindet auf weißem Untergrund.
WORDMARK_LIGHT_BG = (35, 43, 74)
WORDMARK_DARK_BG = (240, 242, 248)

#: Schriftkandidaten mit ausdrücklichem Schnitt-Index. Der Index ist nötig, weil
#: eine .ttc mehrere Schnitte enthält und der Standard je nach System ein
#: kursiver sein kann.
FONT_CANDIDATES = (
    ("/System/Library/Fonts/Avenir Next.ttc", 2),
    ("/System/Library/Fonts/HelveticaNeue.ttc", 1),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
)


def _outside_mask(rgb: np.ndarray) -> np.ndarray:
    """Den *zusammenhängenden* weißen Außenbereich bestimmen.

    Bewusst über ein Flutfüllen von den Ecken aus und nicht über eine reine
    Helligkeitsschwelle: Weiße Flächen **innerhalb** des Motivs — hier die
    Umrandung des Behälters und die gestrichelte Ziellinie — müssen erhalten
    bleiben.
    """
    height, width = rgb.shape[:2]
    is_white = rgb.min(axis=2) >= WHITE_THRESHOLD

    outside = np.zeros((height, width), dtype=bool)
    stack = [
        (y, x)
        for y, x in ((0, 0), (0, width - 1), (height - 1, 0), (height - 1, width - 1))
        if is_white[y, x]
    ]
    while stack:
        y, x = stack.pop()
        if outside[y, x] or not is_white[y, x]:
            continue
        outside[y, x] = True
        if y > 0:
            stack.append((y - 1, x))
        if y < height - 1:
            stack.append((y + 1, x))
        if x > 0:
            stack.append((y, x - 1))
        if x < width - 1:
            stack.append((y, x + 1))
    return outside


def _bleed_colour(rgb: np.ndarray, outside: np.ndarray) -> np.ndarray:
    """Motivfarbe in den Außenbereich fortsetzen, damit kein weißer Saum entsteht."""
    result = rgb.astype(np.float32).copy()
    unknown = outside.copy()
    for _ in range(COLOUR_BLEED_PIXELS):
        known = ~unknown
        total = np.zeros_like(result)
        count = np.zeros(result.shape[:2], dtype=np.float32)
        for shift_y, shift_x in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            shifted_colour = np.roll(result, (shift_y, shift_x), axis=(0, 1))
            shifted_known = np.roll(known, (shift_y, shift_x), axis=(0, 1))
            total += shifted_colour * shifted_known[..., None]
            count += shifted_known
        fillable = unknown & (count > 0)
        result[fillable] = (total[fillable] / count[fillable, None]).astype(np.float32)
        unknown &= ~fillable
        if not unknown.any():
            break
    return np.clip(result, 0, 255).astype(np.uint8)


def load_icon() -> Image.Image:
    """Vorlage laden, Motiv vergrößern und die weißen Ecken freistellen."""
    source = Image.open(SOURCE).convert("RGB")
    width, height = source.size

    # Die Eckform stammt aus der *unveränderten* Vorlage und wird nach dem
    # Vergrößern erneut angewendet. So bleibt der Rahmen exakt wie gestaltet.
    outside = _outside_mask(np.asarray(source))

    if MOTIF_SCALE != 1.0:
        enlarged = source.resize(
            (round(width * MOTIF_SCALE), round(height * MOTIF_SCALE)),
            Image.Resampling.LANCZOS,
        )
        left = (enlarged.width - width) // 2
        top = (enlarged.height - height) // 2
        source = enlarged.crop((left, top, left + width, top + height))

    filled = _bleed_colour(np.asarray(source), outside)

    alpha = Image.fromarray(np.where(outside, 0, 255).astype(np.uint8), mode="L")
    alpha = alpha.filter(ImageFilter.GaussianBlur(EDGE_FEATHER))

    icon = Image.fromarray(filled, mode="RGB").convert("RGBA")
    icon.putalpha(alpha)
    return icon.crop(icon.getbbox() or (0, 0, *icon.size))


def render_icon(base: Image.Image, size: int) -> Image.Image:
    """Quadratisches Icon in der gewünschten Kantenlänge."""
    return base.resize((size, size), Image.Resampling.LANCZOS)


def _font(height: int) -> ImageFont.FreeTypeFont | None:
    """Erste verfügbare aufrechte Schrift für den Schriftzug."""
    for path, index in FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            font = ImageFont.truetype(path, height, index=index)
        except OSError:
            continue
        if "Italic" not in font.getname()[1]:
            return font
    return None


def render_logo(
    base: Image.Image, height: int, *, for_dark_background: bool = False
) -> Image.Image:
    """Waagerechtes Logo: Icon plus Schriftzug, eng beschnitten."""
    icon = render_icon(base, height)
    font = _font(round(height * 0.40))
    if font is None:
        return icon

    gap = round(height * 0.20)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    box = probe.textbbox((0, 0), "Sleep Bank", font=font)
    text_width, text_height = box[2] - box[0], box[3] - box[1]

    colour = WORDMARK_DARK_BG if for_dark_background else WORDMARK_LIGHT_BG
    logo = Image.new("RGBA", (height + gap + text_width, height), (0, 0, 0, 0))
    logo.paste(icon, (0, 0), icon)
    ImageDraw.Draw(logo).text(
        (height + gap - box[0], (height - text_height) / 2 - box[1]),
        "Sleep Bank",
        font=font,
        fill=(*colour, 255),
    )
    return logo


def _shrink(path: Path) -> float:
    """Verlustfrei nachkomprimieren, falls oxipng verfügbar ist.

    Verlustbehaftete Verfahren scheiden aus: Die Vorlage enthält feines Rauschen
    im Farbverlauf, und eine Reduktion der Farbtiefe erzeugt dort sichtbare
    Streifen. Der Gewinn bleibt deshalb bescheiden — die Dateigröße stammt aus
    der Vorlage, nicht aus dieser Verarbeitung.
    """
    try:
        import oxipng
    except ImportError:
        return 0.0
    before = path.stat().st_size
    path.write_bytes(oxipng.optimize_from_memory(path.read_bytes(), level=6, optimize_alpha=True))
    return 100.0 * (1.0 - path.stat().st_size / before)


def main() -> None:
    """Alle Bilddateien schreiben."""
    if not SOURCE.exists():
        raise SystemExit(f"Vorlage fehlt: {SOURCE}")

    base = load_icon()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    outputs = (
        ("icon.png", render_icon(base, 256)),
        ("icon@2x.png", render_icon(base, 512)),
        ("logo.png", render_logo(base, 160)),
        ("logo@2x.png", render_logo(base, 320)),
        ("dark_logo.png", render_logo(base, 160, for_dark_background=True)),
        ("dark_logo@2x.png", render_logo(base, 320, for_dark_background=True)),
    )
    for name, image in outputs:
        path = OUTPUT_DIR / name
        image.save(path, "PNG", optimize=True)
        saved = _shrink(path)
        note = f"  (-{saved:.0f} %)" if saved else ""
        print(
            f"{name:18s} {image.size[0]:5d} x {image.size[1]:<5d} "
            f"{path.stat().st_size / 1024:6.1f} kB{note}"
        )


if __name__ == "__main__":
    main()
