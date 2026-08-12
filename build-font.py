#!/usr/bin/env python3
"""
Build ai-labels-font from EU AI label SVG icons.

Glyphs (PUA):
  U+E001 → ai.general        (TYPE: ai / AI / Ai)
  U+E002 → ai.modified       (TYPE: aim / AIM / AiM)
  U+E003 → ai.generated      (TYPE: aig / AIG / AiG)

Outputs: TTF (via fontBuilder), WOFF2, HTML demo, CSS

Usage:  python3 build-font.py
"""
import os
import sys
import re
import subprocess
import shutil as shutil
from xml.etree import ElementTree as ET

from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.fontBuilder import FontBuilder

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


# ─── SVG → pen commands ─────────────────────────────────

def parse_svg_d(d_str):
    """Parse SVG path 'd' string into list of (cmd, [floats])."""
    d = d_str.replace(",", " ").replace("\t", " ")
    tokens = re.findall(r"([HhMmCcLlQqTtSsVvZz])|([+-]?\d*(?:\.\d+)?(?:e[+-]?\d+)?)", d, re.I)
    parts = []
    for c, n in tokens:
        if c: parts.append(c)
        elif n:
            try: parts.append(float(n))
            except ValueError: pass

    result = []
    i = 0
    while i < len(parts):
        cmd = parts[i]; i += 1
        if not isinstance(cmd, str): continue
        n = {"C": 6, "S": 4, "Q": 4, "T": 2, "M": 2, "L": 2, "H": 1, "V": 1, "Z": 0}[cmd.upper()]
        coords = []
        for _ in range(n):
            if i < len(parts) and isinstance(parts[i], float):
                coords.append(parts[i]); i += 1
        result.append((cmd.upper(), coords))
    return result


def svg_path_to_recording(d_str, vb_w, vb_h, target=1000):
    """Convert SVG path data to a list of pen commands.
    
    Returns list of (method_name, args) tuples suitable for replaying
    on a pen object.
    """
    scale = target / max(vb_w, vb_h)
    cmds = []
    for cmd, coords in parse_svg_d(d_str):
        if cmd == "M" and len(coords) >= 2:
            x, y = coords[0] * scale, (vb_h - coords[1]) * scale
            cmds.append(("moveTo", ((x, y),)))
        elif cmd == "L" and len(coords) >= 2:
            x, y = coords[0] * scale, (vb_h - coords[1]) * scale
            cmds.append(("lineTo", ((x, y),)))
        elif cmd == "C" and len(coords) >= 6:
            pts = [(coords[k*2]*scale, (vb_h-coords[k*2+1])*scale) for k in range(3)]
            cmds.append(("curveTo", tuple(pts)))
        elif cmd == "S" and len(coords) >= 4:
            pts = [(coords[k*2]*scale, (vb_h-coords[k*2+1])*scale) for k in range(2)]
            cmds.append(("curveTo", tuple(pts)))
        elif cmd == "Q" and len(coords) >= 4:
            pts = [(coords[k*2]*scale, (vb_h-coords[k*2+1])*scale) for k in range(2)]
            cmds.append(("qCurveTo", tuple(pts)))
        elif cmd == "T" and len(coords) >= 2:
            x, y = coords[0] * scale, (vb_h - coords[1]) * scale
            cmds.append(("lineTo", ((x, y),)))
        elif cmd == "H" and len(coords) >= 1:
            x = coords[0] * scale
            cmds.append(("lineTo", ((x, 0),)))
        elif cmd == "V" and len(coords) >= 1:
            y = coords[0] * scale
            cmds.append(("lineTo", ((0, y),)))
        elif cmd == "Z":
            cmds.append(("closePath", ()))
    return cmds


def draw_glyph_from_svg(svg_path, target=1000):
    """Build a TTGlyph (quadratic) from SVG file.
    
    Uses cu2qu to convert cubic Bezier curves to quadratics.
    """
    import cu2qu
    
    tree = ET.parse(svg_path)
    root = tree.getroot()

    # Strip namespace
    for elem in root.iter():
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]

    viewBox = root.get("viewBox", "0 0 566.93 566.93")
    vb = [float(v) for v in viewBox.split()]
    vb_w, vb_h = vb[2], vb[3]

    # Use RecordingPen to capture all drawing commands as tuples:
    # [("moveTo", ((x, y),)), ("curveTo", ((x1,y1), (x2,y2), (x3,y3))), ...]
    from fontTools.pens.recordingPen import RecordingPen
    rec_pen = RecordingPen()

    for path in root.iter("path"):
        d = path.get("d", "")
        fill = (path.get("fill") or "").lower().strip()
        if fill in ("#fff", "#ffffff", "white"):
            continue

        for method, args in svg_path_to_recording(d, vb_w, vb_h, target):
            # Only call moveTo if this isn't the first command in the recording
            if method != "moveTo" or not rec_pen.value:
                getattr(rec_pen, method)(*args)

    # Now convert cubic curves (curveTo) to quadratics
    # and draw them to a TTGlyphPen
    from fontTools.pens.pointPen import SegmentToPointPen
    
    tt_pen = TTGlyphPen(None)
    current_point = None  # track pen position for cubic bezier start
    
    for method, args in rec_pen.value:
        if method == "moveTo":
            current_point = args[0]
            tt_pen.moveTo(*args)
        elif method == "curveTo":
            # args can be ((x1,y1), (x2,y2), (x3,y3)) for C or ((x1,y1), (x2,y2)) for S
            if len(args) == 3:
                # Full cubic: (cp1, cp2, end) + start from current_point
                if current_point:
                    c1, c2, end = args
                    cubic = [current_point, c1, c2, end]
                    q_pts = cu2qu.curve_to_quadratic(cubic, max_err=1/16)
                    for q in q_pts:
                        if isinstance(q[0], tuple) and len(q) == 2:  # single quad spline: ((cp, end))
                            tt_pen.qCurveTo(*q)
                        elif isinstance(q[0], tuple) and len(q) == 3:  # rare: triple point
                            tt_pen.curveTo(*q)
                    current_point = end
            elif len(args) == 2:
                # S (smooth cubic) - just pass as-is as quadratic
                tt_pen.qCurveTo(*args)
                current_point = args[-1]
        elif method == "qCurveTo":
            # Already quadratic, but check point count
            if len(args) >= 2:
                tt_pen.qCurveTo(*args)
                current_point = args[-1]
        else:
            getattr(tt_pen, method)(*args)
            if method == "lineTo" and args:
                current_point = args[0]
    
    return tt_pen.glyph()


# ─── Font builder helper ─────────────────────────────────────

def build_font():
    """Build and save the TTF font."""
    print("=" * 60)
    print("Building ai-labels-font")
    print("=" * 60)

    glyph_order = [".notdef"] + [g[3] for g in GLYPH_DEFS] + ["space"]

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
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    notdef = TTGlyphPen(None)
    notdef.moveTo((0, 800))
    notdef.lineTo((0, 0))
    notdef.lineTo((600, 0))
    notdef.lineTo((600, 800))
    notdef.closePath()
    glyph_objects[".notdef"] = notdef.glyph()
    
    space_plain = TTGlyphPen(None)  # empty glyph for space
    glyph_objects["space"] = space_plain.glyph()

    # 2. Build the font using FontBuilder
    print("\n2. Building font with FontBuilder...")
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap({
        0xE001: "ai.general",
        0xE002: "ai.modified",
        0xE003: "ai.generated",
    })

    # Setup glyf and hmtx tables
    # Create glyf table first with glyphDataFormat=1 (allows cubic Bezier)
    from fontTools.ttLib import newTable
    glyf = newTable("glyf")
    glyf.glyphDataFormat = 1
    fb.font["glyf"] = glyf
    
    fb.setupGlyf(glyphs=glyph_objects)
    fb.setupMetrics("hmtx", {
        ".notdef": (600, 0),
        "space": (600, 0),
        **{g: (1000, 0) for _, _, _, g in GLYPH_DEFS},
    })
    
    # Setup post table
    fb.setupPost()
    
    # Setup name table
    fb.setupNameTable({
        "familyName": FONT_FAMILY,
        "styleName": "Regular",
    })
    
    # Setup OS/2 table
    fb.setupOS2(
        sTypoAscender=800, sTypoDescender=-200, sTypoLineGap=0,
        sCapHeight=800, usWinAscent=1000, usWinDescent=200
    )

    # Save TTF
    ttf_path = os.path.join(OUTPUT_DIR, f"{FONT_FAMILY}.ttf")
    font = fb.font
    font.save(ttf_path)

    print(f"\n✓ TTF: {ttf_path} ({os.path.getsize(ttf_path)} bytes)")
    return ttf_path


# ─── WOFF2 conversion ──────────────────────────────────────────

def make_woff2(ttf_path):
    """Convert TTF to WOFF2 using woff2_compress binary or Python fallback."""
    print("\nConverting to WOFF2...")
    woff2_path = ttf_path.replace(".ttf", ".woff2")

    # Try woff2_compress binary first
    bin_path = shutil.which("woff2_compress")
    if bin_path:
        result = subprocess.run([bin_path, ttf_path], capture_output=True)
        if os.path.exists(woff2_path):
            print(f" OK WOFF2: {woff2_path}")
            return woff2_path
        print(f"  woff2_compress returned: {result.stderr}")

    print("  WOFF2 skipped (run: pip install woff2tools or get woff2_compress)")
    return None


# ─── HTML Demo ─────────────────────────────────────────────────

def make_html_demo():
    print("\nGenerating HTML demo page...")
    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>ai-labels-font — AI Content Label Icons</title>
<style>
  @font-face {
    font-family: 'ai-labels-font';
    src: url('ai-labels-font.woff2') format('woff2'),
         url('ai-labels-font.ttf') format('truetype');
    font-weight: normal; font-style: normal;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    font-family:-apple-system,BlinkMacSystemFont,sans-serif;
    background:#f5f5f5; padding:40px 20px; color:#333;
  }
  h1 { text-align:center; margin-bottom:5px; }
  .sub { text-align:center; margin-bottom:30px; color:#666; font-size:14px; }
  .sec { background:#fff; border-radius:8px; padding:25px; margin-bottom:18px;
         box-shadow:0 1px 3px rgba(0,0,0,0.08); }
  .sec h2 { font-size:16px; color:#444; margin-bottom:15px; border-bottom:1px solid #eee; }
  .row { display:flex; align-items:center; gap:20px; flex-wrap:wrap; margin-bottom:12px; }
  .lbl { font-size:12px; color:#888; min-width:100px; }
  .ic {
    font-family:'ai-labels-font'; font-style:normal; font-weight:normal;
    text-rendering:optimizeLegibility; -webkit-font-smoothing:antialiased;
  }
  code { background:#eee; padding:1px 5px; border-radius:3px; font-family:monospace; }
  table { width:100%; border-collapse:collapse; }
  th,td { text-align:center; padding:10px; border-bottom:1px solid #eee; }
  th { background:#fafafa; font-weight:600; }
</style>
</head>
<body>
<h1>ai-labels-font</h1>
<p class="sub">AI Content Label Icons — Designed by the European Union — Public Domain<br>
  Type <code>ai</code> <code>aim</code> <code>aig</code> for icons • Any color, any size</p>

<div class="sec"><h2>All Icons</h2>
  <div class="row" style="justify-content:center; gap:40px">
    <div style="text-align:center">
      <span class="ic" style="font-size:64px">&#xE001;</span><br><span class="lbl">AI</span></div>
    <div style="text-align:center">
      <span class="ic" style="font-size:64px">&#xE002;</span><br><span class="lbl">AI Modified</span></div>
    <div style="text-align:center">
      <span class="ic" style="font-size:64px">&#xE003;</span><br><span class="lbl">AI Generated</span></div>
  </div>
</div>

<div class="sec"><h2>Colors</h2>
  <div class="row">
    <span class="ic" style="font-size:48px">&#xE001;</span>
    <span class="ic" style="font-size:48px; color:#0066cc">&#xE001;</span>
    <span class="ic" style="font-size:48px; color:#cc0000">&#xE001;</span>
    <span class="ic" style="font-size:48px; color:#008844">&#xE001;</span>
    <span class="ic" style="font-size:48px; color:#e67e22">&#xE001;</span>
  </div>
</div>

<div class="sec"><h2>Sizes</h2>
  <div class="row" style="align-items:flex-end">
    <span class="ic" style="font-size:16px">&#xE001;</span>
    <span class="ic" style="font-size:24px">&#xE001;</span>
    <span class="ic" style="font-size:32px">&#xE001;</span>
    <span class="ic" style="font-size:48px">&#xE001;</span>
    <span class="ic" style="font-size:64px">&#xE001;</span>
    <span class="ic" style="font-size:96px">&#xE001;</span>
  </div>
</div>

<div class="sec"><h2>Character Reference</h2>
<table>
  <tr><th>Icon</th><th>Unicode</th><th>HTML</th><th>CSS</th><th>Type</th></tr>
  <tr><td><span class="ic" style="font-size:32px">&#xE001;</span></td><td>U+E001</td>
      <td>&amp;#xE001;</td><td>"\\e001"</td><td><code>ai</code></td></tr>
  <tr><td><span class="ic" style="font-size:32px">&#xE002;</span></td><td>U+E002</td>
      <td>&amp;#xE002;</td><td>"\\e002"</td><td><code>aim</code></td></tr>
  <tr><td><span class="ic" style="font-size:32px">&#xE003;</span></td><td>U+E003</td>
      <td>&amp;#xE003;</td><td>"\\e003"</td><td><code>aig</code></td></tr>
</table>
</div>
</body>
</html>"""

    path = os.path.join(OUTPUT_DIR, f"{FONT_FAMILY}.html")
    with open(path, "w") as fh:
        fh.write(html)
    print(f" HTML demo: {path}")


# ─── CSS ────────────────────────────────────────────────────────

def make_css():
    print("\nGenerating CSS...")
    css = """/* ai-labels-font — AI Content Label Icons */
/* Icons designed by the European Union in the public domain */

@font-face {
  font-family: 'ai-labels-font';
  src: url('ai-labels-font.eot');
  src: url('ai-labels-font.eot?#iefix') format('embedded-opentype'),
       url('ai-labels-font.woff2') format('woff2'),
       url('ai-labels-font.ttf') format('truetype');
  font-weight: normal; font-style: normal; font-display: swap;
}

.ai-icon {
  font-family: 'ai-labels-font';
  font-style: normal; font-weight: normal;
  text-rendering: optimizeLegibility;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  display: inline-block;
}

/* Direct insertion with ::before */
.ai-icon--ai::before { content: "\\e001"; }
.ai-icon--ai-modified::before { content: "\\e002"; }
.ai-icon--ai-generated::before { content: "\\e003"; }

/* Sizes */
.ai-icon--sm { font-size: 16px; }
.ai-icon--md { font-size: 24px; }
.ai-icon--lg { font-size: 32px; }
.ai-icon--xl { font-size: 48px; }

/* Colors */
.ai-icon--black { color: #000000; }
.ai-icon--white { color: #ffffff; }
.ai-icon--blue { color: #0066CC; }
.ai-icon--red { color: #CC0000; }
.ai-icon--green { color: #008844; }
"""
    path = os.path.join(OUTPUT_DIR, f"{FONT_FAMILY}.css")
    with open(path, "w") as fh:
        fh.write(css)
    print(f" CSS: {path}")


# ─── Main ───────────────────────────────────────────────────────

def main():
    ttf = build_font()
    make_woff2(ttf)
    make_html_demo()
    make_css()

    print("\n" + "=" * 60)
    print("Build complete! Files in fonts/:")
    print("=" * 60)
    for fn in sorted(os.listdir(OUTPUT_DIR)):
        if not fn.endswith(".py"):
            fp = os.path.join(OUTPUT_DIR, fn)
            print(f"  {fn} ({os.path.getsize(fp)} bytes)")


if __name__ == "__main__":
    main()
