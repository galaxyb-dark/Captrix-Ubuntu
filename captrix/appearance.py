"""Transcript font, remembered between launches."""

from __future__ import annotations

import json
import os
from pathlib import Path

import gi

gi.require_version("Pango", "1.0")
from gi.repository import Pango

DEFAULT_FONT = "Sans 16"


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "captrix" / "font.json"


def load_font() -> str:
    try:
        data = json.loads(config_path().read_text())
        font = str(data["font"]).strip()
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_FONT
    if not font:
        return DEFAULT_FONT
    return font


def save_font(font: str) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"font": font}) + "\n")
    temporary.replace(path)


def transcript_css(font: str) -> str:
    description = Pango.FontDescription.from_string(font or DEFAULT_FONT)
    family = description.get_family() or "Sans"
    family = family.replace("\\", "\\\\").replace('"', '\\"')
    size = description.get_size()
    if size <= 0:
        size_css = "16pt"
    elif description.get_size_is_absolute():
        size_css = f"{size / Pango.SCALE:.1f}px"
    else:
        size_css = f"{size / Pango.SCALE:.1f}pt"
    style = {
        Pango.Style.ITALIC: "italic",
        Pango.Style.OBLIQUE: "oblique",
    }.get(description.get_style(), "normal")
    weight = int(description.get_weight())
    return f"""
    textview.transcript,
    textview.transcript text {{
      background: transparent;
      font-family: "{family}";
      font-size: {size_css};
      font-style: {style};
      font-weight: {weight};
    }}
    """
