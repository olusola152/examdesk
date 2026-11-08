"""Lecture diagrams drawn in Python (SVG), timed to the teacher's speech.

render(visual, speech) -> {"svg": "<svg ...>", "cues": [0.0 .. 1.0 per step]}
Each diagram element has data-step="n"; the browser shows step n when the teacher's voice reaches cues[n],
which is the moment he says that box's words. So the diagram forms as he speaks.
Types: flow (steps), cycle, timeline, concept (mind map), hierarchy (types/kinds), compare (A vs B).
"""
import math
import re
from html import escape

W, H = 960, 500
_STOP = set("""a an the and or of to in on for with by from as at is are was were be this that these those it its into
than so such can will not no we you they he she our your their them which who what when where how all any each other some
more most very just only about over also there here one two three""".split())


def _wrap(text, n):
    words, lines, line = str(text or "").split(), [], ""
    for w in words:
        if line and len(line) + 1 + len(w) > n:
            lines.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    if line:
        lines.append(line)
    return lines


class _Svg:
    def __init__(self):
        self.parts, self.steps, self.cue_words = [], 0, []

    def add(self, s):
        self.parts.append(s)

    def step(self, words=""):
        """Start a new step; `words` are what the teacher says when this step should appear."""
        self.cue_words.append(words)
        self.steps += 1
        return self.steps - 1

    def text(self, x, y, s, size=16, weight=400, fill="#14243a", width=22, anchor="middle", max_lines=4, lh=1.25):
        lines = _wrap(s, width)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = re.sub(r"\s*\S*$", "", lines[-1]) + "…"
        y0 = y - (len(lines) - 1) * size * lh / 2
        spans = "".join(f'<tspan x="{x:.1f}" dy="{0 if i == 0 else size * lh:.1f}">{escape(l)}</tspan>' for i, l in enumerate(lines))
        return (f'<text x="{x:.1f}" y="{y0:.1f}" text-anchor="{anchor}" font-size="{size}" font-weight="{weight}" '
                f'fill="{fill}" dominant-baseline="middle">{spans}</text>'), len(lines)

    def card(self, cx, cy, w, h, item, badge=None, size=17, words=None):
        st = self.step(words if words is not None else f"{item.get('label', '')} {item.get('detail', '')}")
        g = [f'<g class="dg-step dg-node" data-step="{st}" transform="translate({cx:.1f},{cy:.1f})">',
             f'<rect x="{-w / 2:.1f}" y="{-h / 2:.1f}" width="{w}" height="{h}" rx="14" class="dg-card"/>']
        if badge is not None:
            g.append(f'<circle cx="{-w / 2 + 4:.1f}" cy="{-h / 2 + 4:.1f}" r="15" class="dg-badge"/>')
            g.append(self.text(-w / 2 + 4, -h / 2 + 5, str(badge), size=14, weight=700)[0])
        lmax, lh = int(w / 10), size * 1.25
        detail = item.get("detail") and h >= 80
        if not detail:
            g.append(self.text(0, 0, item.get("label", ""), size=size, weight=700, width=lmax, max_lines=3)[0])
        else:
            n_lab = min(2, len(_wrap(item.get("label", ""), lmax)))
            top = -h / 2 + 12
            g.append(self.text(0, top + n_lab * lh / 2, item.get("label", ""), size=size, weight=700, width=lmax, max_lines=2)[0])
            d_top, d_lh = top + n_lab * lh + 4, 12.5 * 1.25
            room = h / 2 - 8 - d_top
            d_lines = max(1, int(room // d_lh))
            shown = min(d_lines, len(_wrap(item["detail"], int(w / 7.2))))
            g.append(self.text(0, d_top + shown * d_lh / 2, item["detail"], size=12.5, fill="#4a5a72",
                               width=int(w / 7.2), max_lines=d_lines)[0])
        g.append("</g>")
        self.add("".join(g))
        return st

    def arrow(self, d, words=""):
        st = self.step(words)
        self.add(f'<path d="{d}" class="dg-step dg-arrow" data-step="{st}" marker-end="url(#dgArrow)"/>')
        return st


# ---------------------------------------------------------------- layouts
def _flow(s, v):
    items = v["items"]
    n = len(items)
    per = n if n <= 4 else math.ceil(n / 2)
    rows = math.ceil(n / per)
    w, h, gap = min(200, (W - 80) / per - 40), 120, (W - 60) / per
    pos = []
    for i in range(n):
        r = i // per
        c = per - 1 - (i % per) if r % 2 else i % per
        pos.append((30 + gap * (c + 0.5), 240 if rows == 1 else 140 + r * 200))
    for i, it in enumerate(items):
        if i:
            (x1, y1), (x2, y2) = pos[i - 1], pos[i]
            if y1 == y2:
                sgn = 1 if x2 > x1 else -1
                d = f"M{x1 + sgn * w / 2 + 4:.1f},{y1:.1f} L{x2 - sgn * w / 2 - sgn * 8:.1f},{y2:.1f}"
            else:
                d = f"M{x1:.1f},{y1 + h / 2 + 4:.1f} L{x2:.1f},{y2 - h / 2 - 10:.1f}"
            s.arrow(d, it.get("label", ""))
        s.card(pos[i][0], pos[i][1], w, h, it, badge=i + 1)


def _cycle(s, v):
    items = v["items"]
    n, cx, cy, rx, ry, w, h = len(items), W / 2, 245, 330, 165, 175, 92
    s.add(s.text(cx, cy, v.get("title", ""), size=20, weight=700, fill="#ffd166", width=18)[0].replace("<text", '<text class="dg-center"', 1))
    pos = [(cx + rx * math.cos(-math.pi / 2 + i * 2 * math.pi / n), cy + ry * math.sin(-math.pi / 2 + i * 2 * math.pi / n)) for i in range(n)]
    for i, it in enumerate(items):
        s.card(pos[i][0], pos[i][1], w, h, it, badge=i + 1)
        (x1, y1), (x2, y2) = pos[i], pos[(i + 1) % n]
        mx = (x1 + x2) / 2 + (cy - (y1 + y2) / 2) * 0.25 * (-1 if x2 > x1 else 1)
        my = (y1 + y2) / 2 + ((x1 + x2) / 2 - cx) * 0.25
        sx, sy = x1 + (x2 - x1) * 0.28, y1 + (y2 - y1) * 0.28
        ex, ey = x1 + (x2 - x1) * 0.72, y1 + (y2 - y1) * 0.72
        s.arrow(f"M{sx:.1f},{sy:.1f} Q{mx:.1f},{my:.1f} {ex:.1f},{ey:.1f}", items[(i + 1) % n].get("label", ""))


def _timeline(s, v):
    items = v["items"]
    n, x0, x1, y = len(items), 110, W - 110, 250
    st = s.step(v.get("title", ""))
    s.add(f'<line x1="{x0}" y1="{y}" x2="{x1}" y2="{y}" class="dg-step dg-axis" data-step="{st}"/>')
    for i, it in enumerate(items):
        x = x0 + (x1 - x0) * (0.5 if n == 1 else i / (n - 1))
        up = i % 2 == 0
        st = s.step(f"{it.get('label', '')} {it.get('detail', '')}")
        g = [f'<g class="dg-step dg-node" data-step="{st}" transform="translate({x:.1f},{y})">', '<circle r="11" class="dg-dot"/>',
             f'<line x1="0" y1="{-14 if up else 14}" x2="0" y2="{-60 if up else 60}" class="dg-tick"/>',
             f'<rect x="-85" y="{-165 if up else 60}" width="170" height="105" rx="12" class="dg-card"/>',
             s.text(0, -140 if up else 85, it.get("label", ""), size=17, weight=700, width=16)[0],
             s.text(0, -95 if up else 130, it.get("detail", ""), size=12, fill="#4a5a72", width=24, max_lines=3)[0], "</g>"]
        s.add("".join(g))


def _concept(s, v):
    items = v["items"]
    n, cx, cy, rx, ry = len(items), W / 2, 245, 340, 170
    st = s.step(v.get("title", ""))
    s.add(f'<g class="dg-step dg-node" data-step="{st}" transform="translate({cx},{cy})"><circle r="78" class="dg-hub"/>'
          + s.text(0, 0, v.get("title", ""), size=18, weight=800, width=13, max_lines=4)[0] + "</g>")
    for i, it in enumerate(items):
        a = -math.pi / 2 + i * 2 * math.pi / n
        x, y = cx + rx * math.cos(a), cy + ry * math.sin(a)
        s.arrow(f"M{cx + 82 * math.cos(a):.1f},{cy + 82 * math.sin(a) * 0.9:.1f} L{x - 100 * math.cos(a):.1f},{y - 58 * math.sin(a):.1f}",
                it.get("label", ""))
        s.card(x, y, 200, 112, it)


def _hierarchy(s, v):
    items = v["items"]
    n, top, row = len(items), 70, 300
    w = min(190, (W - 60) / n - 18)
    st = s.step(v.get("title", ""))
    s.add(f'<g class="dg-step dg-node" data-step="{st}" transform="translate({W / 2},{top})">'
          f'<rect x="-150" y="-34" width="300" height="68" rx="14" class="dg-card dg-root"/>'
          + s.text(0, 0, v.get("title", ""), size=19, weight=800, width=26, max_lines=2)[0] + "</g>")
    for i, it in enumerate(items):
        x = 30 + (W - 60) / n * (i + 0.5)
        s.arrow(f"M{W / 2},{top + 36} L{W / 2},{top + 90} L{x:.1f},{top + 90} L{x:.1f},{row - 92}", it.get("label", ""))
        s.card(x, row, w, 170, it)


def _compare(s, v):
    cols, w, h, y = v["items"][:2], 380, 330, 255
    for i, it in enumerate(cols):
        x = W - 40 - w / 2 if i else 40 + w / 2
        st = s.step(f"{it.get('label', '')} {it.get('detail', '')}")
        g = [f'<g class="dg-step dg-node" data-step="{st}" transform="translate({x},{y})">',
             f'<rect x="{-w / 2}" y="{-h / 2}" width="{w}" height="{h}" rx="16" class="dg-card"/>',
             f'<rect x="{-w / 2}" y="{-h / 2}" width="{w}" height="58" rx="16" class="{"dg-head2" if i else "dg-head1"}"/>',
             s.text(0, -h / 2 + 29, it.get("label", ""), size=20, weight=800, fill="#fff", width=30, max_lines=1)[0]]
        pts = [p.strip() for p in re.split(r";|•", str(it.get("detail", ""))) if p.strip()][:5]
        for k, p in enumerate(pts):
            g.append(s.text(-w / 2 + 26, -h / 2 + 95 + k * 50, "• " + p, size=14.5, anchor="start", width=40, max_lines=2)[0])
        g.append("</g>")
        s.add("".join(g))
    st = s.step("versus while whereas unlike difference")
    s.add(f'<g class="dg-step dg-node" data-step="{st}" transform="translate({W / 2},{y})"><circle r="34" class="dg-hub"/>'
          + s.text(0, 1, "VS", size=22, weight=900)[0] + "</g>")


LAYOUTS = {"flow": _flow, "cycle": _cycle, "timeline": _timeline, "concept": _concept, "hierarchy": _hierarchy, "compare": _compare}


# ---------------------------------------------------------------- timing: when does the teacher say each box?
def _words(s):
    return [w for w in re.findall(r"[a-z0-9]{3,}", str(s).lower()) if w not in _STOP]


def _cues(cue_words, speech):
    low = (speech or "").lower()
    n, total = len(cue_words), max(1, len(low))
    found, start = [], 0
    for words in cue_words:
        ws = _words(words)
        pos = None
        for frm in (start, 0):                             # look AFTER the previous box first: boxes follow the speech
            phrase = " ".join(ws[:2])
            if phrase and low.find(phrase, frm) >= 0:
                pos = low.find(phrase, frm)
            else:
                hits = [low.find(w, frm) for w in ws[:4] if low.find(w, frm) >= 0]
                pos = min(hits) if hits else None
            if pos is not None:
                break
        if pos is not None and pos >= start:
            start = pos + 1
        found.append(None if pos is None else pos / total)
    # keep the order of the steps; fill the gaps evenly; arrows appear just before the box they point to
    cues, last = [], 0.0
    for i, c in enumerate(found):
        if c is None or c < last:
            nxt = next((x for x in found[i + 1:] if x is not None and x >= last), None)
            remaining = next((k for k, x in enumerate(found[i + 1:], 1) if x is not None and x >= last), n - i)
            c = last + ((nxt if nxt is not None else 0.97) - last) / (remaining + 1)
        cues.append(round(min(0.97, max(last, c)), 4))
        last = cues[-1]
    return cues


def render(visual, speech=""):
    v = visual if visual and visual.get("type") in LAYOUTS and visual.get("items") else \
        {"type": "concept", "title": (visual or {}).get("title", ""), "items": (visual or {}).get("items") or [{"label": "…"}]}
    s = _Svg()
    LAYOUTS[v["type"]](s, v)
    title = escape(v.get("title", ""))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" class="dg-svg" data-steps="{s.steps}">'
           '<defs><marker id="dgArrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" '
           'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#ffd166"/></marker></defs>'
           f'<g class="dg-cam">{"".join(s.parts)}</g>'
           f'<g class="dg-caption"><rect x="0" y="{H - 40}" width="{W}" height="40" fill="rgba(0,0,0,.35)"/>'
           f'<text x="20" y="{H - 15}" fill="#ffd166" font-size="16" font-weight="700">🎬  {title}</text></g></svg>')
    return {"svg": svg, "cues": _cues(s.cue_words, speech)}
