#!/usr/bin/env python3
"""
Build ai-labels-font from EU AI label SVG icons.

Glyphs (PUA):
  U+E001 → ai.general        (TYPE: ai / AI / Ai)
  U+E002 → ai.modified       (TYPE: aim / AIM / AiM)
  U+E003 → ai.generated      (TYPE: aig / AIG / AiG)

Outputs: TTF, WOFF2, CSS

Usage:  python3 build-font.py
"""
import itertools
import os
import sys
from xml.etree import ElementTree as ET

import pathops
from svg.path import parse_path, Move, Line, CubicBezier, QuadraticBezier, Close

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.fontBuilder import FontBuilder
from fontTools.ttLib import TTFont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = SCRIPT_DIR
ICON_DIR = os.path.join(PROJECT_ROOT, "icons")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "dist")

FONT_FAMILY = "ai-labels-font"
FONT_FULL_NAME = FONT_FAMILY
FONT_PS_NAME = FONT_FAMILY

GLYPH_DEFS = [
    ("ai", 0xE001, "LABEL_AI", "ai.general"),
    ("aim", 0xE002, "LABEL_AI MODIFIED", "ai.modified"),
    ("aig", 0xE003, "LABEL_AI GENERATED", "ai.generated"),
]

# ASCII glyphs required for OpenType ligature substitutions.
ASCII_GLYPHS = {
    "a": 0x61, "i": 0x69, "m": 0x6D, "g": 0x67,
    "A": 0x41, "I": 0x49, "M": 0x4D, "G": 0x47,
}
ASCII_ADVANCE = 500


# ─── SVG → Skia path ────────────────────────────────────────

def svg_path_to_skia(d_str):
    """Convert an SVG path 'd' string to a pathops.Path object."""
    path = parse_path(d_str)
    sk = pathops.Path()
    for seg in path:
        if isinstance(seg, Move):
            sk.moveTo(seg.end.real, seg.end.imag)
        elif isinstance(seg, Line):
            sk.lineTo(seg.end.real, seg.end.imag)
        elif isinstance(seg, CubicBezier):
            sk.cubicTo(
                seg.control1.real, seg.control1.imag,
                seg.control2.real, seg.control2.imag,
                seg.end.real, seg.end.imag,
            )
        elif isinstance(seg, QuadraticBezier):
            sk.quadTo(
                seg.control.real, seg.control.imag,
                seg.end.real, seg.end.imag,
            )
        elif isinstance(seg, Close):
            sk.close()
    return sk


def draw_glyph_from_svg(svg_path, target=1000):
    """Build a quadratic TTGlyph from an SVG file.

    The EU icons use a black background shape with white knockout text.
    This function unions all black paths and subtracts the white paths so the
    resulting glyph renders correctly as solid black in any colour.
    """
    tree = ET.parse(svg_path)
    root = tree.getroot()

    # Strip namespace
    for elem in root.iter():
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]

    viewBox = root.get("viewBox", "0 0 566.93 566.93").split()
    vb_w, vb_h = float(viewBox[2]), float(viewBox[3])
    # Scale by height so all icons share the same vertical size (≈1 em).
    scale = target / vb_h

    def to_font(x, y):
        return (x * scale, (vb_h - y) * scale)

    black_paths = []
    white_paths = []

    for path in root.iter("path"):
        d = path.get("d", "")
        fill = (path.get("fill") or "").lower().strip()
        cls = (path.get("class") or "").strip()
        sk = svg_path_to_skia(d)
        if fill in ("#fff", "#ffffff", "white") or "cls-2" in cls:
            white_paths.append(sk)
        else:
            black_paths.append(sk)

    if not black_paths:
        print(f"  ERROR: no black path found in {svg_path}")
        sys.exit(1)

    # Union black shapes
    black = pathops.Path()
    pathops.union(black_paths, black.getPen())

    # Subtract white knockout shapes
    result = black
    for wp in white_paths:
        rec = RecordingPen()
        pathops.difference([result], [wp], rec)
        result = pathops.Path()
        rec.replay(result.getPen())

    result = pathops.simplify(result, fix_winding=True)

    # Draw result into a RecordingPen, scaling and flipping Y
    rec = RecordingPen()
    result.draw(rec)
    scaled = RecordingPen()
    for method, args in rec.value:
        if method == "moveTo":
            scaled.moveTo(to_font(args[0][0], args[0][1]))
        elif method == "lineTo":
            scaled.lineTo(to_font(args[0][0], args[0][1]))
        elif method == "curveTo":
            scaled.curveTo(*[to_font(p[0], p[1]) for p in args])
        elif method == "qCurveTo":
            scaled.qCurveTo(*[to_font(p[0], p[1]) for p in args])
        elif method == "closePath":
            scaled.closePath()
        elif method == "endPath":
            scaled.endPath()

    # Center the glyph vertically in the em square.
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.transformPen import TransformPen
    bounds_pen = BoundsPen(None)
    scaled.replay(bounds_pen)
    x1, y1, x2, y2 = bounds_pen.bounds
    y_offset = (target / 2) - (y1 + y2) / 2

    # Convert cubics to quadratics, translate, and build the TTGlyph
    tt_pen = TTGlyphPen(None)
    qu_pen = Cu2QuPen(tt_pen, max_err=1.0)
    transform_pen = TransformPen(qu_pen, (1, 0, 0, 1, 0, y_offset))
    scaled.replay(transform_pen)
    return tt_pen.glyph()


# ─── OpenType ligature feature file ────────────────────────────

def build_liga_fea():
    """Generate a Feature File snippet for all case ligature variants."""
    lines = ["feature liga {"]
    for seq, gname in [
        ("ai", "ai.general"),
        ("aim", "ai.modified"),
        ("aig", "ai.generated"),
    ]:
        # Emit every case combination (e.g. ai, aI, Ai, AI, ...)
        for combo in itertools.product(*[(c.lower(), c.upper()) for c in seq]):
            lines.append(f"    sub {' '.join(combo)} by {gname};")
    lines.append("} liga;")
    return "\n".join(lines)


# ─── Font builder helper ─────────────────────────────────────

def build_font():
    """Build and save the TTF font."""
    print("=" * 60)
    print("Building ai-labels-font")
    print("=" * 60)

    glyph_order = (
        [".notdef"]
        + [g[3] for g in GLYPH_DEFS]
        + list(ASCII_GLYPHS)
        + ["space"]
    )

    # 1. Build glyph objects from SVGs
    print("\n1. Extracting glyphs from SVG...")
    glyph_objects = {}
    for _, pua, svg_base, gname in GLYPH_DEFS:
        svg_path = os.path.join(ICON_DIR, f"{svg_base}_black.svg")
        if not os.path.exists(svg_path):
            print(f"  ERROR: {svg_path} not found!")
            sys.exit(1)
        glyph = draw_glyph_from_svg(svg_path, target=1000)
        glyph_objects[gname] = glyph
        print(f"  OK  {svg_base} → {gname}")

    # Add placeholder glyphs for non-icon characters
    notdef = TTGlyphPen(None)
    notdef.moveTo((0, 800))
    notdef.lineTo((0, 0))
    notdef.lineTo((600, 0))
    notdef.lineTo((600, 800))
    notdef.closePath()
    glyph_objects[".notdef"] = notdef.glyph()

    space_plain = TTGlyphPen(None)
    glyph_objects["space"] = space_plain.glyph()

    for gname in ASCII_GLYPHS:
        glyph_objects[gname] = TTGlyphPen(None).glyph()

    # 2. Build the font using FontBuilder
    print("\n2. Building font with FontBuilder...")
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap({
        0xE001: "ai.general",
        0xE002: "ai.modified",
        0xE003: "ai.generated",
        **{cp: gname for gname, cp in ASCII_GLYPHS.items()},
    })

    fb.setupGlyf(glyphs=glyph_objects)
    fb.setupMetrics("hmtx", {
        ".notdef": (600, 0),
        "space": (600, 0),
        **{g: (ASCII_ADVANCE, 0) for g in ASCII_GLYPHS},
        **{g: (1000, 0) for _, _, _, g in GLYPH_DEFS},
    })

    # Compute per-glyph metrics and bounding box from actual outlines
    glyphset = fb.font.getGlyphSet()
    metrics = dict(fb.font["hmtx"].metrics)
    x_min = 0
    x_max = 0
    y_min = 0
    y_max = 0
    advance_max = 0

    for _, _, _, gname in GLYPH_DEFS:
        glyph = glyphset[gname]
        pen = BoundsPen(glyphset)
        glyph.draw(pen)
        x1, y1, x2, y2 = pen.bounds
        x_min = min(x_min, x1)
        x_max = max(x_max, x2)
        y_min = min(y_min, y1)
        y_max = max(y_max, y2)
        advance = round(x2 - x1)
        metrics[gname] = (advance, 0)
        advance_max = max(advance_max, advance)

    fb.setupMetrics("hmtx", metrics)

    # Setup post table
    fb.setupPost()

    # Setup name table
    fb.setupNameTable({
        "familyName": FONT_FAMILY,
        "styleName": "Regular",
    })

    # Setup OS/2 table with a vertically centered em square (baseline at 0)
    fb.setupOS2(
        sTypoAscender=1000, sTypoDescender=0, sTypoLineGap=0,
        sCapHeight=1000, usWinAscent=1000, usWinDescent=0
    )

    # Setup head and hhea tables
    fb.setupHead(
        unitsPerEm=1000,
        xMin=int(x_min), yMin=int(y_min),
        xMax=int(x_max), yMax=int(y_max),
    )
    fb.setupHorizontalHeader(
        ascent=1000, descent=0, lineGap=0,
        advanceWidthMax=advance_max,
        minLeftSideBearing=0,
        minRightSideBearing=0,
        xMaxExtent=int(x_max),
        caretSlopeRise=1, caretSlopeRun=0,
    )

    # Add OpenType ligature table (GSUB)
    print("\n3. Adding ligature table...")
    fea = build_liga_fea()
    addOpenTypeFeaturesFromString(fb.font, fea)
    print("  OK ligatures added")

    # Save TTF
    ttf_path = os.path.join(OUTPUT_DIR, f"{FONT_FAMILY}.ttf")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    fb.save(ttf_path)

    print(f"\n✓ TTF: {ttf_path} ({os.path.getsize(ttf_path)} bytes)")
    return ttf_path


# ─── WOFF2 conversion ──────────────────────────────────────────

def make_woff2(ttf_path):
    """Convert TTF to WOFF2 using fontTools (requires Brotli)."""
    print("\nConverting to WOFF2...")
    woff2_path = ttf_path.replace(".ttf", ".woff2")

    try:
        font = TTFont(ttf_path)
        font.flavor = "woff2"
        font.save(woff2_path)
        print(f" OK WOFF2: {woff2_path} ({os.path.getsize(woff2_path)} bytes)")
        return woff2_path
    except Exception as e:
        print(f"  WOFF2 skipped: {e}")
        return None

# ─── Main ───────────────────────────────────────────────────────

def main():
    ttf = build_font()
    make_woff2(ttf)

    print("\n" + "=" * 60)
    print("Build complete! Files in dist/:")
    print("=" * 60)
    for fn in sorted(os.listdir(OUTPUT_DIR)):
        if not fn.endswith(".py"):
            fp = os.path.join(OUTPUT_DIR, fn)
            print(f"  {fn} ({os.path.getsize(fp)} bytes)")


if __name__ == "__main__":
    main()
