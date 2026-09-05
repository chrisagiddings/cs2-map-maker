"""Font fallback so place names in any script render on the sheets.

matplotlib's DejaVu Sans has no CJK, Devanagari, Cyrillic-extended or Arabic glyphs; a
Japanese river name would draw as boxes. Matplotlib >= 3.6 accepts a fallback list, so
we prepend DejaVu and append whatever wide-coverage fonts the machine has.
"""
from __future__ import annotations

CANDIDATES = ["DejaVu Sans", "Segoe UI", "Yu Gothic UI", "Yu Gothic", "Meiryo", "MS Gothic",
              "Noto Sans CJK JP", "Noto Sans", "Nirmala UI", "Arial Unicode MS", "Malgun Gothic"]
_done = False


def install() -> list[str]:
    global _done
    import matplotlib
    from matplotlib import font_manager
    if _done:
        return matplotlib.rcParams["font.family"]
    available = {f.name for f in font_manager.fontManager.ttflist}
    fam = [c for c in CANDIDATES if c in available]
    if fam:
        matplotlib.rcParams["font.family"] = fam
        # text drawn with family="monospace" resolves through font.monospace, not font.family
        mono = ["DejaVu Sans Mono"] + [c for c in ("MS Gothic", "Yu Gothic", "Noto Sans Mono CJK JP", "Consolas", "Nirmala UI", "Malgun Gothic") if c in available]
        matplotlib.rcParams["font.monospace"] = mono
    matplotlib.rcParams["axes.unicode_minus"] = False
    _done = True
    return fam
