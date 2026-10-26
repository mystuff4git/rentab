"""GIF для README из кадров `shots.js`: «демо-фирма → смета → КП» (ADR 0010).

Кадры снимает Playwright (`shots.js`, вместе с `manifest.json`: длительность
показа и точка клика), собирает этот скрипт на Pillow — зависимости ядра,
поэтому ни ffmpeg, ни другие системные пакеты не нужны:

    uv run python docs/screenshots/v1.0/make_gif.py /tmp/rentab-frames \
        docs/screenshots/v1.0/rentab.gif

- кадры уменьшаются до ширины `WIDTH`, клик отмечается бордовым кольцом;
- последний кадр — верх первой страницы КП (`docs/examples`, отрисовка PDF);
- одна палитра на все кадры и без дизеринга: текст чёткий, кадры не мерцают,
  Pillow хранит у каждого кадра только изменившуюся область.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[3]
OFFER_PAGE = ROOT / "docs" / "examples" / "demo_trademark_hourly-1.png"
WIDTH = 960
COLORS = 128
# Цвета темы «Бордо и пергамент» (.streamlit/config.toml).
BORDO = (122, 31, 46)
PARCHMENT = (242, 237, 228)
# Метка клика — полое кольцо: бордовое со светлой обводкой, видно и на бордовой
# кнопке, подпись под ним читается.
LIGHT = (251, 249, 245)
CLICK_RADIUS = 19
CLICK_RING = 4
CLICK_HALO = 3
OFFER_HOLD_MS = 4200
# Страница КП: ширина на кадре и отступ сверху; видно до «Итого к оплате».
OFFER_WIDTH = 560
OFFER_TOP = 22
SHADOW_OFFSET = 6
SHADOW_BLUR = 8


def mark_click(image: Image.Image, point: tuple[float, float]) -> None:
    """Кольцо там, куда сейчас нажмут."""
    draw = ImageDraw.Draw(image)
    x, y = point
    outer = CLICK_RADIUS + CLICK_HALO
    draw.ellipse(
        (x - outer, y - outer, x + outer, y + outer),
        outline=LIGHT,
        width=CLICK_RING + 2 * CLICK_HALO,
    )
    r = CLICK_RADIUS
    draw.ellipse((x - r, y - r, x + r, y + r), outline=BORDO, width=CLICK_RING)


def screen_frame(path: Path, click: list[int] | None, scale: float) -> Image.Image:
    """Кадр экрана в ширину GIF, с отметкой клика."""
    image = Image.open(path).convert("RGB")
    size = (WIDTH, round(image.height * scale))
    image = image.resize(size, Image.Resampling.LANCZOS)
    if click is not None:
        mark_click(image, (click[0] * scale, click[1] * scale))
    return image


def offer_frame(size: tuple[int, int]) -> Image.Image:
    """Верх первой страницы КП на пергаменте, с тенью."""
    canvas = Image.new("RGB", size, PARCHMENT)
    page = Image.open(OFFER_PAGE).convert("RGB")
    page = page.resize(
        (OFFER_WIDTH, round(page.height * OFFER_WIDTH / page.width)), Image.Resampling.LANCZOS
    )
    left = (size[0] - OFFER_WIDTH) // 2
    shadow = Image.new("L", size, 0)
    ImageDraw.Draw(shadow).rectangle(
        (
            left + SHADOW_OFFSET,
            OFFER_TOP + SHADOW_OFFSET,
            left + OFFER_WIDTH + SHADOW_OFFSET,
            size[1] + SHADOW_OFFSET,
        ),
        fill=90,
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))
    canvas.paste(Image.new("RGB", size, (0, 0, 0)), mask=shadow)
    canvas.paste(page.crop((0, 0, OFFER_WIDTH, size[1] - OFFER_TOP)), (left, OFFER_TOP))
    return canvas


def build(frames_dir: Path, target: Path) -> None:
    """Собирает GIF из кадров и `manifest.json`."""
    manifest = json.loads((frames_dir / "manifest.json").read_text(encoding="utf-8"))
    scale = WIDTH / manifest["viewport"]["width"]
    images = [
        screen_frame(frames_dir / item["file"], item["click"], scale) for item in manifest["frames"]
    ]
    durations = [int(item["hold"]) for item in manifest["frames"]]
    images.append(offer_frame(images[0].size))
    durations.append(OFFER_HOLD_MS)
    # Общая палитра — по всем кадрам сразу.
    strip = Image.new("RGB", (WIDTH, sum(image.height for image in images)))
    top = 0
    for image in images:
        strip.paste(image, (0, top))
        top += image.height
    palette = strip.quantize(COLORS, method=Image.Quantize.MEDIANCUT)
    frames = [image.quantize(palette=palette, dither=Image.Dither.NONE) for image in images]
    frames[0].save(
        target,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )
    total = sum(durations) / 1000
    size_mb = target.stat().st_size / 1024 / 1024
    print(f"{target}: {len(frames)} кадров, {total:.1f} с, {size_mb:.2f} МБ")


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
