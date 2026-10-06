#!/usr/bin/env python3
"""Render Cursor usage stats as a watermark on the base wallpaper."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_BASE = SCRIPT_DIR / "data" / "base_wallpaper.png"
DEFAULT_DATA = SCRIPT_DIR / "data" / "latest.json"
DEFAULT_OUTPUT = SCRIPT_DIR / "generated" / "cursor-wallpaper.png"

FONT_CANDIDATES = [
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
]


def load_config() -> dict:
    env_path = SCRIPT_DIR / "config.env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()

    return {
        "position": os.getenv("OVERLAY_POSITION", "bottom-right").lower(),
        "opacity": float(os.getenv("OVERLAY_OPACITY", "0.6")),
    }


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def fmt_percent(value: float | int | None) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):.1f}%"


def fmt_dollars(value: float | int | None) -> str:
    if value is None:
        return "N/A"
    return f"${float(value):.2f}"


def fmt_used_limit(used: float | int | None, limit: float | int | None) -> str:
    used_text = fmt_dollars(used if used is not None else 0)
    if limit is None:
        return f"{used_text} / Unlimited"
    return f"{used_text} / {fmt_dollars(limit)}"


def build_lines(summary: dict) -> list[str]:
    lines = [
        "Cursor Usage",
        "",
        f"Updated: {summary.get('current_date', 'N/A')}",
        "",
        f"API usage:   {fmt_percent(summary.get('api_percent_used'))}",
        f"Auto usage:  {fmt_percent(summary.get('auto_percent_used'))}",
        f"Total usage: {fmt_percent(summary.get('total_percent_used'))}",
        "",
        f"Personal included:  {fmt_used_limit(summary.get('included_spend_dollars'), summary.get('included_limit_dollars'))}",
    ]

    bonus = summary.get("bonus_spend_dollars")
    if bonus is not None:
        lines.append(f"Bonus (free extra):  {fmt_dollars(bonus)}")

    lines.append(
        f"Personal on-demand: {fmt_used_limit(summary.get('spend_dollars'), summary.get('personal_on_demand_limit_dollars'))}"
    )

    team_limit = summary.get("team_limit_dollars")
    if team_limit is not None or summary.get("team_spend_dollars") is not None:
        lines.append(
            f"Team on-demand:     {fmt_used_limit(summary.get('team_spend_dollars'), team_limit)}"
        )

    reset_date = summary.get("reset_date")
    days = summary.get("days_until_reset")
    if reset_date:
        suffix = f" ({days} days)" if days is not None else ""
        lines.append(f"Resets:             {reset_date}{suffix}")

    return lines


def measure_text_block(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    title_font: ImageFont.ImageFont,
    body_font: ImageFont.ImageFont,
    line_gap: int,
) -> tuple[int, int]:
    max_width = 0
    total_height = 0
    for index, line in enumerate(lines):
        font = title_font if index == 0 else body_font
        bbox = draw.textbbox((0, 0), line or " ", font=font)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        max_width = max(max_width, width)
        total_height += height + (line_gap if index < len(lines) - 1 else 0)
    return max_width, total_height


def panel_origin(
    image_size: tuple[int, int],
    panel_size: tuple[int, int],
    position: str,
    margin: int,
) -> tuple[int, int]:
    width, height = image_size
    panel_w, panel_h = panel_size

    if position in {"center", "center-center"}:
        return (width - panel_w) // 2, (height - panel_h) // 2
    if position == "top-center":
        return (width - panel_w) // 2, margin
    if position == "bottom-center":
        return (width - panel_w) // 2, height - panel_h - margin
    if position == "bottom-left":
        return margin, height - panel_h - margin
    if position == "top-right":
        return width - panel_w - margin, margin
    if position == "top-left":
        return margin, margin
    if position == "center-left":
        return margin, (height - panel_h) // 2
    if position == "center-right":
        return width - panel_w - margin, (height - panel_h) // 2
    return width - panel_w - margin, height - panel_h - margin


def render_wallpaper(
    base_path: Path,
    data_path: Path,
    output_path: Path,
    position: str,
    opacity: float,
) -> Path:
    if not base_path.exists():
        raise FileNotFoundError(
            f"Base wallpaper not found at {base_path}. Run ./install.sh first."
        )
    if not data_path.exists():
        raise FileNotFoundError(
            f"Usage data not found at {data_path}. Run fetch.py or update_wallpaper.sh first."
        )

    payload = json.loads(data_path.read_text(encoding="utf-8"))
    summary = payload.get("summary") or payload
    lines = build_lines(summary)

    image = Image.open(base_path).convert("RGBA")
    draw = ImageDraw.Draw(image)

    scale = max(image.width, image.height) / 1920
    title_font = load_font(max(18, int(24 * scale)))
    body_font = load_font(max(14, int(18 * scale)))
    padding = int(24 * scale)
    line_gap = int(6 * scale)
    margin = int(32 * scale)

    text_width, text_height = measure_text_block(draw, lines, title_font, body_font, line_gap)
    panel_w = text_width + padding * 2
    panel_h = text_height + padding * 2

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    x0, y0 = panel_origin(image.size, (panel_w, panel_h), position, margin)
    x1, y1 = x0 + panel_w, y0 + panel_h
    alpha = int(max(0.0, min(1.0, opacity)) * 255)
    overlay_draw.rounded_rectangle(
        (x0, y0, x1, y1),
        radius=int(12 * scale),
        fill=(15, 15, 20, alpha),
    )

    text_x = x0 + padding
    text_y = y0 + padding
    for index, line in enumerate(lines):
        font = title_font if index == 0 else body_font
        if not line:
            bbox = draw.textbbox((0, 0), " ", font=body_font)
            text_y += (bbox[3] - bbox[1]) + line_gap
            continue
        overlay_draw.text((text_x, text_y), line, font=font, fill=(245, 245, 245, 255))
        bbox = draw.textbbox((0, 0), line, font=font)
        text_y += (bbox[3] - bbox[1]) + line_gap

    composed = Image.alpha_composite(image, overlay).convert("RGB")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    composed.save(output_path, format="PNG", optimize=True)
    return output_path


def main() -> int:
    config = load_config()
    parser = argparse.ArgumentParser(description="Render Cursor usage watermark")
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--position", default=config["position"])
    parser.add_argument("--opacity", type=float, default=config["opacity"])
    args = parser.parse_args()

    try:
        output = render_wallpaper(
            args.base,
            args.data,
            args.output,
            args.position.lower(),
            args.opacity,
        )
        print(output)
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI entrypoint
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
