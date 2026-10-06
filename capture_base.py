#!/usr/bin/env python3
"""Capture the current macOS desktop wallpaper as the overlay base image."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = SCRIPT_DIR / "data" / "base_wallpaper.png"
PLACEHOLDER_RGB = (30, 60, 120)


def get_current_wallpaper_path() -> Path:
    script = 'tell application "System Events" to get picture of desktop 1'
    result = subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "not allowed assistive access" in stderr.lower() or "(-1743)" in stderr:
            raise RuntimeError(
                "Could not read current desktop wallpaper. "
                "Grant Automation permission for your terminal app -> System Events "
                "in System Settings -> Privacy & Security -> Automation."
            )
        raise RuntimeError(
            f"Could not read current desktop wallpaper: {stderr or 'unknown osascript error'}"
        )

    path = Path(result.stdout.strip())
    if not path.exists():
        raise RuntimeError(f"Desktop wallpaper path does not exist: {path}")
    return path


def is_generated_wallpaper(path: Path) -> bool:
    resolved = str(path.resolve())
    generated_dir = str((SCRIPT_DIR / "generated").resolve())
    return resolved.startswith(generated_dir)


def is_placeholder_image(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            if image.size != (1920, 1080):
                return False
            rgb = image.convert("RGB").getpixel((10, 10))
            return rgb == PLACEHOLDER_RGB
    except OSError:
        return False


def capture_wallpaper(output_path: Path, force: bool = False) -> Path:
    source = get_current_wallpaper_path()

    if is_generated_wallpaper(source) and not force:
        raise RuntimeError(
            "Your current desktop wallpaper is a generated cursor-wallpaper image. "
            "Set your real wallpaper in System Settings -> Wallpaper first, then run:\n"
            "  ./install.sh --refresh-base"
        )

    convert_to_png(source, output_path)
    return output_path


def convert_to_png(source: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with Image.open(source) as image:
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA")
            image.save(output_path, format="PNG", optimize=True)
            return
    except OSError:
        pass

    result = subprocess.run(
        [
            "/usr/bin/sips",
            "-s",
            "format",
            "png",
            str(source),
            "--out",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not output_path.exists():
        stderr = result.stderr.strip() or "sips conversion failed"
        raise RuntimeError(f"Could not convert wallpaper image {source}: {stderr}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture current desktop wallpaper")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--force", action="store_true", help="Allow capturing generated wallpapers")
    args = parser.parse_args()

    try:
        output = capture_wallpaper(args.output, force=args.force)
        print(output)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
