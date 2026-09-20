"""Rebuild the erbgut logo from erbgut-logo-source.jpeg as clean SVG.

Every number below was measured off the JPEG, by colour masking the four
capsule colours and the cyan ribbon and running connected components over the
black type, then regularised to a symmetric design. Source pixel coordinates
map into the output with

    X = (x_src - 228) * 0.5      Y = (y_src - 286) * 0.5

so the full lockup lands in a 434 x 380 viewBox.

Run it from the repository root:

    uv run --with fonttools --with brotli python marketing/logo/build_logo.py

It rewrites the four SVGs next to it. Nothing at runtime depends on it; it
exists so the artwork can be re-derived rather than hand-patched.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
FONT = os.path.join(REPO, "site", "assets", "fonts", "InstrumentSans-latin.woff2")
CHARS = "erbgutATGC01"


def load_glyphs():
    """Instrument Sans outlines at the two weights the artwork uses.

    Needs fonttools and brotli, which are not project dependencies:
        uv run --with fonttools --with brotli python marketing/logo/build_logo.py
    """
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.boundsPen import BoundsPen

    out = {}
    for wght in (600, 700):
        f = TTFont(FONT)
        instantiateVariableFont(f, {"wght": wght}, inplace=True)
        gs, cmap = f.getGlyphSet(), f.getBestCmap()
        glyphs = {}
        for ch in CHARS:
            gn = cmap[ord(ch)]
            pen = SVGPathPen(gs, ntos=lambda v: ("%.1f" % v).rstrip("0").rstrip("."))
            gs[gn].draw(pen)
            bp = BoundsPen(gs)
            gs[gn].draw(bp)
            glyphs[ch] = {"d": pen.getCommands(), "adv": f["hmtx"][gn][0],
                          "bbox": bp.bounds}
        out[str(wght)] = {"upem": f["head"].unitsPerEm, "glyphs": glyphs}
    return out


G = load_glyphs()

# ---------------------------------------------------------------- palette
CYAN = "#32C7DB"
PINK = "#FC68D6"
YELLOW = "#FEC746"
GREEN = "#5BD67C"
INK = "#101418"          # base letters + binary, light backgrounds
WORD = "#4F4F4F"         # wordmark, light backgrounds
INK_DARK = "#E6F6F8"     # binary on dark
WORD_DARK = "#F2F7F6"    # wordmark on dark

# ---------------------------------------------------------------- helix
MID = 153.25             # helix centre line
AMP_C = 64.5             # half separation at the centre lobe
AMP_E = 52.8             # half separation at the two end lobes
SW = 25.0                # ribbon stroke width
X0, X1 = 0.0, 434.0      # ribbon extent
XC = 217.0               # centre
CR_L, CR_R = 115.0, 319.0  # the two crossings

# Cubic control ratios fitted to the JPEG's own ribbon: flatter shoulders and a
# steeper crossing than a plain cosine (weighted rms 0.014 of the amplitude,
# against 0.08 for a cosine). P1 = (A_X*L, peak), P2 = (B_X*L, C_Y*amplitude).
A_X, B_X, C_Y = 0.27, 0.67, 0.79


def n(v):
    return ("%.2f" % v).rstrip("0").rstrip(".")


def n6(v):
    """Font scale factors are ~0.03, so they need real precision."""
    return ("%.6f" % v).rstrip("0").rstrip(".")


def quarter_down(x0, y_peak, x1, y_mid):
    """Peak -> zero crossing. Horizontal tangent at the peak."""
    L = x1 - x0
    a = y_mid + (y_peak - y_mid) * C_Y
    return "C%s %s %s %s %s %s" % (
        n(x0 + A_X * L), n(y_peak), n(x0 + B_X * L), n(a), n(x1), n(y_mid))


def quarter_up(x0, y_mid, x1, y_peak):
    """Zero crossing -> peak. Horizontal tangent at the peak."""
    L = x1 - x0
    a = y_mid + (y_peak - y_mid) * C_Y
    return "C%s %s %s %s %s %s" % (
        n(x1 - B_X * L), n(a), n(x1 - A_X * L), n(y_peak), n(x1), n(y_peak))


def strand(sign):
    """sign=-1: starts high on the left. sign=+1: its mirror."""
    top = MID - sign * AMP_E
    ctr = MID + sign * AMP_C
    d = ["M%s %s" % (n(X0), n(top))]
    d.append(quarter_down(X0, top, CR_L, MID))
    d.append(quarter_up(CR_L, MID, XC, ctr))
    d.append(quarter_down(XC, ctr, CR_R, MID))
    d.append(quarter_up(CR_R, MID, X1, top))
    return "".join(d)


def helix(indent="  "):
    out = []
    for sign in (-1, 1):
        out.append('%s<path d="%s" fill="none" stroke="%s" stroke-width="%s"/>'
                   % (indent, strand(sign), CYAN, n(SW)))
    return out


# ---------------------------------------------------------------- base pairs
# x0, x1, y0, y1, colour, top letter, bottom letter
CAPSULES = [
    (17.0, 39.5, 101.0, 190.0, PINK, None, None),
    (53.5, 76.0, 105.5, 177.5, YELLOW, None, None),
    (159.0, 181.5, 119.0, 205.0, YELLOW, "A", "T"),
    (195.0, 218.0, 103.5, 218.0, GREEN, "G", "C"),
    (231.5, 254.5, 104.0, 217.5, PINK, "C", "G"),
    (267.5, 290.5, 120.5, 205.5, GREEN, "T", "A"),
    (365.5, 388.5, 105.5, 181.5, PINK, None, None),
    (400.0, 422.5, 101.0, 191.0, YELLOW, None, None),
]
BASE_TOP = 148.75        # baseline of the upper letter row
BASE_BOT = 185.75        # baseline of the lower letter row
CAP_H = 20.5             # cap height of the base letters


def capsules(indent="  ", which=None):
    out = []
    for x0, x1, y0, y1, col, _, _ in (which or CAPSULES):
        w = x1 - x0
        out.append('%s<rect x="%s" y="%s" width="%s" height="%s" rx="%s" fill="%s"/>'
                   % (indent, n(x0), n(y0), n(w), n(y1 - y0), n(w / 2), col))
    return out


# ---------------------------------------------------------------- type
def text_paths(s, wght, size, cx, baseline, fill, indent="  ", tracking=0.0):
    """Centred run of text, emitted as outlines inside one transform group."""
    gl = G[str(wght)]["glyphs"]
    upem = G[str(wght)]["upem"]
    sc = size / upem
    advs = [gl[c]["adv"] for c in s]
    track_u = tracking * upem
    # optical centring uses the real ink bounds, not the advance box
    pen = 0.0
    lefts, rights = [], []
    for c, a in zip(s, advs):
        b = gl[c]["bbox"]
        lefts.append(pen + b[0]); rights.append(pen + b[2])
        pen += a + track_u
    ink_l, ink_r = min(lefts), max(rights)
    start_u = -(ink_l + ink_r) / 2.0
    out = ['%s<g transform="translate(%s %s) scale(%s %s)" fill="%s">'
           % (indent, n(cx), n(baseline), n6(sc), n6(-sc), fill)]
    pen = start_u
    for c, a in zip(s, advs):
        out.append('%s  <path transform="translate(%s 0)" d="%s"/>'
                   % (indent, n(pen), gl[c]["d"]))
        pen += a + track_u
    out.append("%s</g>" % indent)
    return out, (ink_r - ink_l) * sc


def base_letters(indent="  ", fill=INK, keep=None):
    out = []
    size = CAP_H / 0.720  # cap height of Instrument Sans is 720/1000
    for x0, x1, _, _, _, top, bot in (keep or CAPSULES):
        if not top:
            continue
        cx = (x0 + x1) / 2
        for ch, base in ((top, BASE_TOP), (bot, BASE_BOT)):
            p, _ = text_paths(ch, 700, size, cx, base, fill, indent)
            out += p
    return out


# ---------------------------------------------------------------- binary
# (column centre X, [(digit, baseline Y), ...])
BINARY = [
    (171.0, [("1", 56.25), ("1", 81.25)]),
    (207.5, [("0", 20.25), ("0", 45.25), ("1", 72.25)]),
    (245.5, [("0", 20.25), ("1", 45.25), ("0", 74.25)]),
    (280.5, [("0", 59.75), ("1", 86.25)]),
    (171.0, [("1", 243.25)]),
    (207.5, [("0", 252.25), ("1", 273.75)]),
    (245.5, [("1", 251.75), ("0", 275.25), ("0", 299.25)]),
    (280.5, [("0", 243.75)]),
]


def binary(indent="  ", fill=INK):
    out = []
    size = CAP_H / 0.720
    for cx, digits in BINARY:
        for ch, base in digits:
            p, _ = text_paths(ch, 700, size, cx, base, fill, indent)
            out += p
    return out


# ---------------------------------------------------------------- wordmark
WORD_SIZE = 58.0         # with the tracking below this lands on the source's
WORD_TRACK = -0.05       # wordmark to within 3% in width and 2% in height
WORD_BASE = 367.75


def wordmark(indent="  ", fill=WORD):
    return text_paths("erbgut", 600, WORD_SIZE, XC, WORD_BASE, fill,
                      indent, WORD_TRACK)


# ---------------------------------------------------------------- files
HEAD = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="%s" width="%s" '
        'height="%s" role="img" aria-labelledby="t">')

ORIGIN = ("Generated by marketing/logo/build_logo.py from "
          "marketing/logo/erbgut-logo-source.jpeg. Edit the script, not this file.")
TYPE_NOTE = ("Type is Instrument Sans, the brand face, converted to outlines, "
             "so this file needs no font at render time.")


def svg(view, w, h, title, body, note=""):
    lines = [HEAD % (view, n(w), n(h)),
             "  <title id=\"t\">%s</title>" % title,
             "  <!-- %s" % ORIGIN]
    for line in note.splitlines():
        lines.append("       %s" % line)
    lines.append("  -->")
    lines += body
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def lockup(dark=False):
    ink = INK_DARK if dark else INK
    word = WORD_DARK if dark else WORD
    body = helix() + capsules() + base_letters(fill=INK) + binary(fill=ink)
    body += wordmark(fill=word)[0]
    where = ("dark backgrounds: the binary and the wordmark lighten, the helix "
             "and the base pairs do not"
             if dark else "light backgrounds. This is the primary lockup")
    return svg("0 0 434 380", 434, 380,
               "Erbgut: a DNA double helix spelling A-T G-C C-G T-A, with binary "
               "written through it, above the erbgut wordmark",
               body,
               "The full lockup, for %s.\n%s" % (where, TYPE_NOTE))


# The mark crops the lockup to one closed lens, crossing to crossing, with the
# four base pairs inside it. That is the only part of the artwork that keeps its
# shape in a square-ish frame.
MARK_X0, MARK_X1 = 111.0, 323.0
MARK_Y0, MARK_Y1 = 70.0, 236.0


def mark(letters=True):
    w = MARK_X1 - MARK_X0
    h = MARK_Y1 - MARK_Y0
    keep = [c for c in CAPSULES if MARK_X0 < c[0] and c[1] < MARK_X1]
    inner = helix("    ") + capsules("    ", keep)
    if letters:
        inner += base_letters("    ", keep=keep)
    body = ['  <g transform="translate(%s %s)">' % (n(-MARK_X0), n(-MARK_Y0))]
    body += inner
    body.append("  </g>")
    return svg("0 0 %s %s" % (n(w), n(h)), w, h,
               "Erbgut mark: four DNA base pairs, A-T G-C C-G T-A, held between "
               "the two strands of a double helix",
               body,
               "One lens of the lockup, crossing to crossing, for avatars, the\n"
               "deck footer and anywhere the wordmark is already present. The\n"
               "colours read on either background, so there is no dark variant.\n"
               "Below about 48px the letters close up: use erbgut-mark-small.svg\n"
               "there instead.\n" + TYPE_NOTE)


# ---------------------------------------------------------------- small mark
# The lettered mark loses its letters below about 48px, so favicons, the site
# header and anything else that renders small get their own drawing: the same
# lens, but proportionally fatter, with three base pairs instead of four and no
# letters. Tuned by eye at 16px and 32px.
S_MID, S_AMP, S_SW = 32.0, 24.0, 9.0
S_L, S_R = 1.5, 62.5
S_CAPS = [(10.5, 18.0, 46.0, YELLOW), (26.75, 11.0, 53.0, PINK),
          (43.0, 18.0, 46.0, GREEN)]
S_CAP_W = 10.5


def small_mark():
    L = (S_R - S_L) / 2.0
    body = []
    for sign in (-1, 1):
        peak = S_MID + sign * S_AMP
        d = ("M%s %s" % (n(S_L), n(S_MID))
             + quarter_up(S_L, S_MID, S_L + L, peak)
             + quarter_down(S_L + L, peak, S_R, S_MID))
        body.append('  <path d="%s" fill="none" stroke="%s" stroke-width="%s" '
                    'stroke-linecap="round"/>' % (d, CYAN, n(S_SW)))
    for x, y0, y1, col in S_CAPS:
        body.append('  <rect x="%s" y="%s" width="%s" height="%s" rx="%s" '
                    'fill="%s"/>' % (n(x), n(y0), n(S_CAP_W), n(y1 - y0),
                                     n(S_CAP_W / 2), col))
    return svg("0 0 64 64", 64, 64,
               "Erbgut mark: three DNA base pairs held between the two strands "
               "of a double helix",
               body,
               "The small-size mark: one lens, three fatter base pairs, no\n"
               "letters. Drawn for 16 to 32px, where the lettered mark closes\n"
               "up. The colours read on either background, so there is no dark\n"
               "variant of this one.")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else HERE
    open(os.path.join(out, "erbgut-logo.svg"), "w").write(lockup(False))
    open(os.path.join(out, "erbgut-logo-dark.svg"), "w").write(lockup(True))
    open(os.path.join(out, "erbgut-mark.svg"), "w").write(mark())
    open(os.path.join(out, "erbgut-mark-small.svg"), "w").write(small_mark())
    print("wrote 4 files to", out)
