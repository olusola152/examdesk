"""AI Professor engine.

Three modes, chosen automatically:
  1. Claude API  - when ANTHROPIC_API_KEY is set (online, best quality)
  2. Ollama      - a free AI model running on this computer (fully OFFLINE, good quality)
  3. Basic       - built-in keyword method when neither is available (offline, weak)
Force a mode with AI_PROVIDER=claude | ollama | basic.
"""
import json
import os
import random
import re
import time
import urllib.request
from collections import Counter

API_KEY = lambda: os.environ.get("ANTHROPIC_API_KEY", "").strip()
MODEL = lambda: os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
OLLAMA_URL = lambda: os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = lambda: os.environ.get("OLLAMA_MODEL", "llama3.2:3b")

PROFESSOR = ("You are 'AI Professor', a warm, experienced university lecturer in Nigeria. "
             "You never just read notes aloud: you explain ideas in your own words, build intuition "
             "first, use simple analogies and everyday Nigerian examples (markets, danfo buses, NEPA, "
             "football, family life), connect each idea to the one before it, ask the class short "
             "rhetorical questions, and summarise before moving on. Speak naturally, as in a real "
             "classroom, in clear spoken English (no markdown, no bullet symbols in speech).")
EXAMINER = "You are a careful, fair university examiner. Reply with JSON only."

_ollama_cache = {"t": 0, "ok": False}


def _ollama_up():
    """Is Ollama running with the chosen model? (cached for 30 s)"""
    if time.time() - _ollama_cache["t"] < 30:
        return _ollama_cache["ok"]
    ok = False
    try:
        with urllib.request.urlopen(OLLAMA_URL() + "/api/tags", timeout=2) as r:
            names = [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
        want = OLLAMA_MODEL()
        ok = any(n == want or n.split(":")[0] == want.split(":")[0] for n in names)
        if not ok:
            print(f"Ollama is running but model '{want}' is not downloaded. Run: ollama pull {want}")
    except Exception:
        ok = False
    _ollama_cache.update(t=time.time(), ok=ok)
    return ok


def provider():
    forced = os.environ.get("AI_PROVIDER", "").strip().lower()
    if forced in ("claude", "ollama", "basic"):
        return forced
    if API_KEY():
        return "claude"
    if _ollama_up():
        return "ollama"
    return "basic"


def ai_enabled():
    return provider() != "basic"


def provider_label():
    return {"claude": "Claude (online)", "ollama": f"Offline AI — {OLLAMA_MODEL()}", "basic": "Basic offline mode"}[provider()]


# ------------------------------------------------------------------ model calls
def _claude(system, prompt, max_tokens=4000):
    body = json.dumps({"model": MODEL(), "max_tokens": max_tokens, "system": system,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, method="POST",
                                 headers={"x-api-key": API_KEY(), "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read())
    return "".join(b.get("text", "") for b in data.get("content", []))


def _ollama(system, prompt, max_tokens=1500, as_json=False):
    payload = {"model": OLLAMA_MODEL(), "stream": False,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
               "options": {"num_predict": max_tokens, "temperature": 0.4, "num_ctx": 8192}}
    if as_json:
        payload["format"] = "json"
    req = urllib.request.Request(OLLAMA_URL() + "/api/chat", data=json.dumps(payload).encode(), method="POST",
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:     # CPU-only laptops can be slow
        return json.loads(r.read())["message"]["content"]


def _llm(system, prompt, max_tokens=2000, as_json=False):
    if provider() == "claude":
        return _claude(system, prompt, max_tokens)
    return _ollama(system, prompt, max_tokens, as_json)


def _json_from(text):
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    idx = [i for i in (text.find("{"), text.find("[")) if i != -1]
    return json.loads(text[min(idx) if idx else 0:])


# ------------------------------------------------------------------ text extraction
def extract_text(path):
    """Text of a lecture file. Headings are kept on their own line starting with '# '."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            return _pdf_text([(p.extract_text() or "") for p in PdfReader(path).pages])
        if ext == ".docx":
            import docx
            out = []
            doc = docx.Document(path)
            for p in doc.paragraphs:
                t = p.text.strip()
                if not t:
                    continue
                style = (p.style.name or "").lower() if p.style is not None else ""
                if style.startswith(("heading", "title")):
                    out.append("# " + t)
                elif "list" in style or p._p.pPr is not None and p._p.pPr.numPr is not None:
                    out.append("- " + t)                      # bullet / numbered list item
                else:
                    out.append(t)
            for table in doc.tables:                       # text inside tables too
                for row in table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        out.append(" — ".join(dict.fromkeys(cells)) + ".")
            return "\n".join(out)
        if ext == ".pptx":
            from pptx import Presentation
            out = []
            for i, slide in enumerate(Presentation(path).slides, 1):
                title = slide.shapes.title.text_frame.text.strip() if slide.shapes.title is not None and slide.shapes.title.has_text_frame else ""
                out.append("# " + (title or f"Slide {i}"))
                for sh in slide.shapes:
                    if sh.has_text_frame and sh != slide.shapes.title:
                        for para in sh.text_frame.paragraphs:
                            t = "".join(r.text for r in para.runs).strip()
                            if t:
                                out.append("- " + (t if t[-1] in ".!?:" else t + "."))
                if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
                    out.append(slide.notes_slide.notes_text_frame.text.strip())
            return "\n".join(out)
        if ext in (".txt", ".md"):
            with open(path, encoding="utf-8", errors="ignore") as f:
                return f.read()
    except Exception as e:  # corrupt / unsupported file
        return f"[Could not read file: {e}]"
    return ""


_BULLET = re.compile(r"^\s*(?:[•●▪◦■□➢➤►✓\-*–]|o\s|\(?[a-z0-9]{1,2}[.)])\s*")


def _pdf_text(pages):
    """Turn PDF page text into clean notes: no running headers/footers or page numbers, real paragraphs, '# ' headings."""
    norm = lambda l: re.sub(r"\d+", "#", l.strip().lower())
    page_lines = [[l.strip() for l in pg.splitlines() if l.strip()] for pg in pages]
    if len(page_lines) >= 2:                                  # lines repeated on most pages = header/footer
        counts = Counter(n for pl in page_lines for n in set(norm(l) for l in pl))
        repeated = {n for n, c in counts.items() if c >= max(2, len(page_lines) * 0.5)}
    else:
        repeated = set()
    out, para = [], ""

    def flush():
        nonlocal para
        if para.strip():
            out.append(para.strip())
        para = ""
    for pl in page_lines:
        for i, l in enumerate(pl):
            if norm(l) in repeated or re.fullmatch(r"(page\s*)?\d+(\s*(of|/)\s*\d+)?", l, re.I):
                continue
            nxt = pl[i + 1] if i + 1 < len(pl) else ""
            numbered = re.match(r"^\d+(\.\d+)*[.)]?\s+[A-Z]", l) and len(l.split()) <= 10 and l[-1:] not in ".,;"
            first = not out and not para and len(l.split()) <= 10 and l[-1:] not in ".,;"          # document title
            heading = numbered or first or (len(l) < 80 and l[-1:] not in ".,;" and not _BULLET.match(l)
                                             and (para == "" or para[-1:] in ".!?:") and _is_heading(l, nxt))
            if heading:
                flush(); out.append("# " + l); continue
            if _BULLET.match(l) and len(l) > 3:
                flush(); para = "- " + _BULLET.sub("", l); continue
            if para.endswith("-") and l[:1].islower():           # word split over two lines
                para = para[:-1] + l
            else:
                para = (para + " " + l) if para else l
            if l[-1:] in ".!?" and len(l) < 55:                   # short last line of a paragraph
                flush()
    flush()
    return "\n".join(out)


# ------------------------------------------------------------------ helpers
STOP = set("""a an the and or but if then of to in on for with by from as at is are was were be been being this that
these those it its into than so such can could will would should may might must do does did done not no yes we you they he she
i me my our your their his her them us also which who whom what when where why how all any each other some more most
very just only about over under between through during before after above below up down out off again further once here
there both few own same too s t don now one two three use used using called like while whereas however
therefore thus also introduction chapter section part""".split())


def _sentences(text):
    text = re.sub(r"\s+", " ", text)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if 30 < len(s.strip()) < 400]


def _keywords(text, n=40):
    words = re.findall(r"[A-Za-z][A-Za-z\-]{3,}", text.lower())
    return [w for w, _ in Counter(w for w in words if w not in STOP).most_common(n)]


def _is_heading(line, nxt):
    if line.startswith("#"):
        return True
    words = line.split()
    if not words or len(line) > 70 or len(words) > 9 or line[-1] in ".,;!" or line.startswith("- "):
        return False
    if re.match(r"^(\d+(\.\d+)*[.)]?|chapter|topic|week|unit|module|lesson|part|section)\b", line, re.I):
        return True
    if line.endswith("?") and len(words) <= 8 and line[:1].isupper() and (not nxt or len(nxt) > len(line) + 15):
        return True
    if line.isupper() and len(words) <= 8:
        return True
    capital = sum(1 for w in words if w[:1].isupper()) / len(words)
    return line[:1].isupper() and capital >= 0.5 and (not nxt or len(nxt) > len(line) + 15)


def _sections(text):
    """Split notes into (heading, body) parts. Headings never leak into the body text."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    secs, head, buf = [], None, []
    for i, l in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if _is_heading(l, nxt):
            if buf:
                secs.append((head or "Introduction", "\n".join(buf)))
                buf = []
            head = re.sub(r"^#+\s*", "", l).strip(" :")
            head = re.sub(r"^\d+(\.\d+)*[.)]?\s+", "", head) or head
        else:
            buf.append(l if l[-1:] in ".!?:" else l + ".")
    if buf:
        secs.append((head or "Introduction", "\n".join(buf)))
    out = []
    for h, b in secs:                                       # merge tiny parts into the previous one
        bullets = sum(1 for l in b.splitlines() if l.startswith("- "))
        if out and len(b) < 160 and bullets < 2:            # (a slide with a list of points stays its own part)
            out[-1] = (out[-1][0], out[-1][1] + "\n" + b)
        else:
            out.append((h, b))
    return out[:15] or [("Introduction", text[:3000])]


_DEF_RE = re.compile(r"^(?:an? |the )?([A-Za-z][\w\-]*(?: [\w\-]+){0,5}?(?: \([A-Z]{2,8}\))?)\s+(?:is|are|refers to|means|can be defined as|is defined as|is called)\s+(.{12,240}?)[.!]?$", re.I)
_NOT_TERM = {"it", "this", "that", "these", "those", "there", "they", "he", "she", "we", "you", "which", "what", "here", "one",
             "testing", "important", "the aim", "the goal", "the purpose", "the main", "our"}


def definitions(text):
    """[(term, definition, sentence)] for sentences like 'Software is a set of programs ...'."""
    out, seen = [], set()
    text = "\n".join(re.sub(r"^-\s+", "", l.strip()) for l in text.splitlines() if not l.strip().startswith("#"))
    for sn in _sentences(text):
        sn = sn.strip()
        m = _DEF_RE.match(sn)
        if not m:
            continue
        term, defin = m.group(1).strip(), m.group(2).strip().rstrip(".")
        low = term.lower()
        if low in _NOT_TERM or low.split()[0] in _NOT_TERM or len(term) < 3 or any(w in STOP for w in low.split()[-1:]):
            continue
        dl = defin.lower()
        if len(defin) < 20 or dl.startswith(("not ", "also ", "very ", "important", "essential", "the main", "the most",
                                             "one of the", "performed", "done ", "needed", "required", "usually", "often",
                                             "always", "now ", "still ", "being ", "going ")):
            continue
        if low in seen:
            continue
        seen.add(low)
        out.append((term[0].upper() + term[1:], defin, sn))
    return out


def _clip(text, n=95):
    text = " ".join(text.split())
    if len(text) <= n:
        return text
    cut = text[:n]
    for sep in (", ", "; ", " and ", " that ", " which "):
        i = cut.rfind(sep)
        if i > n * 0.7:
            return cut[:i]
    return cut.rsplit(" ", 1)[0] + "…"


def make_question(term, defin, pool, rnd, style=None):
    """A real understanding question from a definition, with other definitions/terms as wrong options."""
    others = [d for d in pool if d[0].lower() != term.lower()]
    if len(others) < 2:
        return None
    rnd.shuffle(others)
    picks = others[:3]
    style = style or rnd.choice(["what", "which"])
    if style == "what":
        opts = [_clip(defin, 110)] + [_clip(o[1], 110) for o in picks]
        q = f"What does “{term if term.isupper() or term[1:2].isupper() else term.lower()}” mean?"
    else:
        opts = [term] + [o[0] for o in picks]
        q = f"Which term means: “{_clip(defin, 120)}”?"
    if len(set(o.lower() for o in opts)) < len(opts):
        return None
    correct = opts[0]
    rnd.shuffle(opts)
    return {"question": q, "options": opts, "answer": "ABCD"[opts.index(correct)], "explain": f"{term} is {defin}."}


def key_terms(text, n=8):
    """Important terms: defined terms first, then repeated multi-word phrases and capitalised terms."""
    terms = [d[0] for d in definitions(text)]
    words = re.findall(r"\b[A-Za-z][a-z]+(?: [A-Za-z][a-z]+)?\b", text)
    pairs = Counter(w.lower() for w in words if " " in w and not any(x in STOP for x in w.lower().split()))
    for ph, c in pairs.most_common(20):
        if c >= 2 and ph not in (t.lower() for t in terms):
            terms.append(ph.title())
    return terms[:n]


def build_lecture(title, text):
    """Return enriched segments: {title, kind, points[], speech, visual, check}."""
    segs = enrich(_build_lecture(title, text), text)
    if segs:
        segs[0]["v"] = BUILD_VERSION
        segs[0]["engine"] = provider()
    return segs


def _build_lecture(title, text):
    p = provider()
    try:
        if p == "claude":
            prompt = (f"Turn these lecture notes titled '{title}' into a live classroom lecture.\n"
                      "Return ONLY JSON: {\"segments\": [{\"title\": str, \"points\": [3-5 short board "
                      "points students will copy], \"speech\": str, \"visual\": " + VISUAL_SPEC + ", \"check\": "
                      + CHECK_SPEC + "}]}. 6-12 segments. Segment 1 greets the class and says what we'll learn and "
                      "why it matters (no check). Each speech is 150-300 words that EXPLAINS the idea (examples, "
                      "analogies) rather than repeating the notes, and walks through the diagram items in order. "
                      "Last segment summarises (no check).\n\nNOTES:\n" + text[:60000])
            segs = _json_from(_claude(PROFESSOR, prompt, 8000))["segments"]
            if segs:
                return segs
        elif p == "ollama":
            return _ollama_lecture(title, text)
    except Exception as e:
        print("AI lecture failed, using basic mode:", e)
    return _offline_lecture(title, text)


def _ollama_lecture(title, text):
    """Small local models do better one section at a time."""
    secs = _sections(text)
    segs = [{"title": "Welcome", "kind": "intro", "points": [h for h, _ in secs[:5]],
             "speech": _ollama(PROFESSOR, f"Write a warm 80-120 word opening for today's lecture '{title}'. "
                               f"Greet the class, say why this topic matters in real life, and list what we will cover: "
                               f"{', '.join(h for h, _ in secs[:6])}. Plain spoken text only.", 400).strip()}]
    for h, body in secs:
        prompt = (f"Lecture: {title}. Section: {h}.\nNotes for this section:\n{body[:4000]}\n\n"
                  "Teach this section to the class. Explain it in your own words with a simple everyday example "
                  "and one quick question to the class. Do not just read the notes. Return JSON: "
                  '{"title": "short section title", "points": ["3 to 5 short board points"], '
                  '"speech": "150-250 words of what you say to the class", "visual": ' + VISUAL_SPEC +
                  ', "check": ' + CHECK_SPEC + '}')
        try:
            seg = _json_from(_ollama(PROFESSOR, prompt, 900, as_json=True))
            if seg.get("speech"):
                segs.append({"title": str(seg.get("title") or h)[:80],
                             "points": [str(x)[:140] for x in (seg.get("points") or [])][:5],
                             "speech": str(seg["speech"]), "visual": seg.get("visual"), "check": seg.get("check"),
                             "source": body[:2000]})
                continue
        except Exception as e:
            print("Ollama section failed:", e)
        segs.extend(_offline_lecture(title, f"{h}\n{body}")[1:-1][:1])
    segs.append({"title": "Summary", "kind": "summary", "points": [h for h, _ in secs[:6]],
                 "speech": _ollama(PROFESSOR, f"In 60-100 words, summarise today's lecture '{title}' which covered "
                                   f"{', '.join(h for h, _ in secs)}. End by inviting questions. Plain spoken text.", 300).strip()})
    return segs


BUILD_VERSION = 5          # bump when the way lectures are built improves; old basic-mode lectures rebuild themselves


def glossary(text, n=14):
    """Key terms and their meanings from the notes: [{term, meaning}]."""
    return [{"term": t, "meaning": d} for t, d, _ in definitions(text)[:n]]


def _items(body):
    """Bullet items written in the notes (lines starting with '- ')."""
    return [l[2:].strip() for l in body.splitlines() if l.startswith("- ") and len(l) > 6]


_OPENERS = ["Now, let us look at {h}.", "Our next topic is {h}. Pay attention here.", "Let's move on to {h}.",
            "Next, we shall talk about {h}.", "Now we come to {h}. This one is important."]
_CLOSERS = ["Write that down, because it will help you later.", "Keep that in mind as we continue.",
            "If this part is not clear, press Explain simpler or ask me a question.",
            "Make sure you understand this before we move on.", "That is the main idea of this part."]
_CONNECT = ["Also, ", "Another point is this: ", "Note that ", "Remember, ", "In addition, "]


def _mid(h, body):
    """A heading said in the middle of a sentence: 'Functions of an OS' -> 'functions of an OS'. Names and acronyms keep capitals."""
    text = "\n".join(l for l in body.splitlines() if not l.startswith("#"))
    words = []
    for w in h.rstrip("?:. ").split():
        core = w.strip("(),")
        name = re.search(rf"[a-z,;]\s+{re.escape(core)}\b", text) is not None          # capitalised mid-sentence = a name
        words.append(w if (core.isupper() and len(core) > 1) or (core[:1].isupper() and name) else w.lower())
    return " ".join(words)


def _opener(i, h, body):
    if h.endswith("?"):
        return f"Our question now is this: {_mid(h, body)}?"
    return _OPENERS[i % len(_OPENERS)].format(h=_mid(h, body))


def _teach_part(i, h, body):
    """Board notes + a teacher-style explanation for one part, built only from the notes (no AI needed)."""
    defs = definitions(body)
    bullets = _items(body)
    sents = [x for x in _sentences(re.sub(r"^- ", "", body, flags=re.M)) if len(x) < 300]
    steps = [sn for sn in sents if _STEP.match(sn)]
    # ---- board notes: bullets, steps, definitions, then key sentences (whole sentences, short)
    on_board = []
    if len(steps) >= 2:
        lead = next((d[2] for d in defs), None)
        if lead:
            on_board.append(lead)
        on_board += steps[:6 - len(on_board)]
    else:
        on_board += bullets[:5]
        for t, d, sn in defs:
            if len(on_board) < 5 and sn not in on_board:
                on_board.append(sn)
        for sn in sents:
            if len(on_board) >= 4:
                break
            if sn not in on_board and not any(sn.startswith(b[:25]) for b in bullets):
                on_board.append(sn)
    points = [_clip(x, 115) for x in on_board[:6]] or [_clip(body, 115)]
    # ---- explanation: every sentence of the notes is explained once, in order, with a teacher's words
    say, spoken = [_opener(i, h, body)], set()
    for n, (t, d, sn) in enumerate(defs[:3]):
        sentence = sn if sn[-1:] in ".!?" else sn + "."
        if n == 0 and h.endswith("?") and t.lower() in h.lower():
            say.append(sentence)                              # the heading already asked the question
        else:
            say.append(f"So, what is {_term_mid(t)}? {sentence}")
        spoken.add(sn)
    if len(steps) >= 2:
        say.append(f"This happens in {len(steps)} steps. Follow me one by one.")
        say.extend(steps); spoken.update(steps)
    said_defs = [d[2] for d in defs[:3]]
    bullets_say = [b for b in bullets if not any(b.rstrip(".")[:40] in d for d in said_defs)]
    if len(bullets_say) >= 2 and len(steps) < 2:
        say.append(f"There are {len(bullets_say)} points to note here.")
        for k, b in enumerate(bullets_say[:6]):
            b2 = b if b[-1:] in ".!?" else b + "."
            say.append(f"{_ORD[k]}, {b2[0].lower() + b2[1:] if not b2.split()[0].isupper() else b2}")
        spoken.update(sn for sn in sents if any(sn.startswith(b[:25]) for b in bullets))
    k = 0
    for sn in sents:
        if sn in spoken:
            continue
        low = sn.lower()
        if k == 0 or any(x in low for x in ("for example", "e.g.", "such as", "for instance")):
            say.append(sn)
        else:
            say.append(_CONNECT[k % len(_CONNECT)] + (sn[0].lower() + sn[1:] if not sn.split()[0].isupper() else sn))
        spoken.add(sn); k += 1
    if defs and len(defs[0][1]) <= 100:
        sn0 = defs[0][2].rstrip(".")
        say.append("So remember: " + (sn0[0].lower() + sn0[1:] if not sn0.split()[0].isupper() else sn0) + ".")
    say.append(_CLOSERS[i % len(_CLOSERS)])
    return points, " ".join(say)


def _term_mid(t):
    """'Software development life cycle (SDLC)' -> 'the software development life cycle (SDLC)'; acronyms stay upper case."""
    words = [w if (w.strip("()").isupper() and len(w.strip("()")) > 1) else w.lower() for w in t.split()]
    out = " ".join(words)
    return ("the " + out) if len(words) >= 3 and not out.startswith("the ") else out


_STEP = re.compile(r"^(first|firstly|second|secondly|third|thirdly|then|next|after that|afterwards|finally|lastly)\b", re.I)
_ORD = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth"]


def _and_list(items):
    items = [i.rstrip("?.: ") for i in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


def _offline_lecture(title, text):
    secs = _sections(text)
    topics = [h for h, _ in secs]
    spoken_topics = _and_list([_mid(h, text) for h in topics[:6]])
    segs = [{"title": "Welcome", "kind": "intro", "points": topics[:6],
             "speech": f"Good day class, and welcome. Today our lecture is on {title}. "
                       f"We shall cover {len(secs)} main topic{'s' if len(secs) != 1 else ''}: {spoken_topics}. "
                       "For each one, I will write the key points on the board and give you time to copy them. "
                       "When you are done, say done, and I will explain with a diagram. Let us begin."}]
    for i, (h, body) in enumerate(secs):
        points, speech = _teach_part(i, h, body)
        segs.append({"title": h, "points": points, "speech": speech, "source": body[:2500]})
    terms = definitions(text)[:4]
    recap = " ".join(f"{t} is {_clip(d, 60).rstrip('.…')}." for t, d, _ in terms)
    segs.append({"title": "Summary", "kind": "summary", "points": topics[:6],
                 "speech": f"Let us summarise today's lecture on {title}. We looked at {spoken_topics}. "
                           + (f"The key terms to remember are these. {recap} " if recap else "")
                           + "Read over your notes, open My notes to print them, and try the practice quiz if you want "
                             "to test yourself. Thank you for your attention."})
    return segs


VISUAL_SPEC = ('{"type": "flow|cycle|timeline|concept|hierarchy|compare", "title": "short caption", '
               '"items": [{"label": "2-4 words", "detail": "one short line"}]}  (3-6 items; flow = steps in order, '
               'cycle = repeating process, timeline = dated events, concept = central idea with related parts, '
               'hierarchy = types/kinds of the title, compare = exactly 2 items being contrasted, each detail lists '
               'their features separated by ";")')
CHECK_SPEC = ('{"question": "one quick understanding question", "options": ["4 short options"], '
              '"answer": "A|B|C|D", "explain": "one sentence why"}')
VISUAL_TYPES = ("flow", "cycle", "timeline", "concept", "hierarchy", "compare")


def _short(t, n):
    t = " ".join(str(t).split())
    return t if len(t) <= n else t[:n - 1].rsplit(" ", 1)[0] + "…"


_VERBISH = set("""is are was were be can will may must should has have had helps help manages manage makes make gives give
uses use allows allow provides provide controls control checks check runs run means refers includes include contains contain
performs perform supports support creates create stores store builds build follows follow""".split())


def _subject(sn):
    """'System software manages the hardware' -> 'System software' (the thing the sentence is about)."""
    words = re.sub(r"^(first|firstly|then|next|after that|afterwards|finally|lastly|also|however)[,:]?\s+", "", sn, flags=re.I).split()
    if not words or words[0].lower() in ("there", "it", "this", "these", "they", "we", "you", "i", "he", "she"):
        return None
    for k, w in enumerate(words[:6]):
        wl = w.lower().strip(",.")
        if k >= 1 and (wl in _VERBISH or (wl.endswith("s") and len(wl) > 4 and k >= 2 and not wl.endswith("ss"))):
            subj = " ".join(words[:k]).strip(",")
            subj = re.sub(r"^(the|a|an)\s+", "", subj, flags=re.I)
            return subj[:1].upper() + subj[1:] if 1 <= len(subj.split()) <= 4 else None
    return None


_LEAD = re.compile(r"^(first|firstly|second|secondly|third|then|next|after that|afterwards|finally|lastly|also)[,:]?\s+", re.I)
_TRAIL = {"the", "a", "an", "of", "from", "and", "or", "for", "to", "in", "on", "with", "by", "is", "are", "which", "that"}


def _phrase(sn, n=4):
    """First few meaningful words of a sentence: 'Then, they design the system architecture' -> 'Design the system architecture'."""
    clean = _LEAD.sub("", sn.strip())
    clean = re.sub(r"^(it|this|they|we|you|there)\s+(is|are|was|were)?\s*", "", clean, flags=re.I)
    clean = re.sub(r"^(the|a|an)\s+", "", clean, flags=re.I)
    words = clean.split(",")[0].rstrip(".").split()[:n]
    while words and words[-1].lower() in _TRAIL:
        words.pop()
    out = " ".join(words)
    return out[:1].upper() + out[1:]


def _label(sn, title, rank, taken=()):
    """A short diagram label for one sentence."""
    taken = {t.lower() for t in taken}
    subj = _subject(sn)
    if subj and subj.lower() not in taken and subj.lower() != title.lower().rstrip("?"):
        return _short(subj, 28)
    return _short(_phrase(sn, 4) or sn, 30)


def _heuristic_visual(seg):
    title, points = seg.get("title", ""), [p for p in seg.get("points", []) if p]
    body = re.sub(r"^-\s+", "", (seg.get("source") or " ".join(points) + " " + seg.get("speech", "")), flags=re.M)
    low = body.lower()
    if seg.get("kind") in ("intro", "summary"):
        return {"type": "flow" if seg.get("kind") == "summary" else "concept", "title": _short(title, 50),
                "items": [{"label": _short(p, 28), "detail": ""} for p in points[:6]]}
    defs = definitions(body)
    if re.search(r"\b(first|then|next|finally|step|stage|afterwards)\b", low) and len(points) >= 3:
        kind = "flow"
    elif re.search(r"\b(cycle|repeat|loop|recycl|continuous)", low):
        kind = "cycle"
    elif len(re.findall(r"\b(1[5-9]\d\d|20\d\d)\b", body)) >= 2:
        kind = "timeline"
    elif re.search(r"\b(types? of|kinds? of|classif|categor|forms of)\b", low):
        kind = "hierarchy"
    elif len(defs) == 2 and re.search(r"\b(while|whereas|unlike|differ|versus|vs)\b", low):
        kind = "compare"
    else:
        kind = "concept"
    if kind == "compare":
        items = [{"label": t, "detail": "; ".join(_clip(x, 40) for x in re.split(r",| and ", d)[:4])} for t, d, _ in defs]
    elif kind == "timeline":
        items = [{"label": m, "detail": _short(sn, 70)} for sn in _sentences(body)
                 for m in re.findall(r"\b(1[5-9]\d\d|20\d\d)\b", sn)[:1]][:6]
    elif defs and kind == "concept":
        items = [{"label": _short(t, 28), "detail": _clip(d, 70)} for t, d, _ in defs[:5]]
        rank = _keywords(body, 30)
        for sn in _sentences(body):
            if len(items) >= 5:
                break
            if any(sn == d[2] for d in defs):
                continue
            lab = _label(sn, title, rank, [i["label"] for i in items])
            if lab.lower() not in (i["label"].lower() for i in items):
                items.append({"label": lab, "detail": _clip(sn, 70)})
    else:
        rank = _keywords(body, 30)
        src = [x for x in _sentences(body) if _STEP.match(x)] if kind == "flow" else []
        src = src if len(src) >= 2 else [p for p in points if not p.lower().startswith(("there are", "there is"))]
        items = []
        for p in src[:6]:
            if ":" in p[:45] and kind != "flow":
                lab, det = p.split(":", 1)
            elif kind == "flow":
                lab, det = _phrase(p, 4), _LEAD.sub("", p)
            else:
                lab, det = _label(p, title, rank, [i["label"] for i in items]), _LEAD.sub("", p)
            items.append({"label": _short(lab[:1].upper() + lab[1:], 28), "detail": _clip(det[:1].upper() + det[1:], 70)})
    if len(items) < 2:
        items = [{"label": _short(p.split(":")[0], 28), "detail": _clip(p, 70)} for p in points[:5]]
    return {"type": kind, "title": _short(title, 50), "items": items}


def _heuristic_check(seg, pool, index=0):
    """A proper question from this part's definitions (pool = all definitions in the lecture), or None."""
    body = seg.get("source") or " ".join(seg.get("points", []))
    defs = definitions(body)
    if not defs:
        return None
    rnd = random.Random(seg.get("title", "") + body[:50])
    term, defin, _ = defs[0]
    return make_question(term, defin, pool, rnd, "what" if index % 2 else "which")


def _valid_visual(v):
    if not isinstance(v, dict) or v.get("type") not in VISUAL_TYPES:
        return None
    items = []
    for it in v.get("items") or []:
        if isinstance(it, dict) and it.get("label"):
            items.append({"label": _short(it["label"], 32), "detail": _short(it.get("detail", ""), 140)})
        elif isinstance(it, str) and it.strip():
            items.append({"label": _short(it, 32), "detail": ""})
    if v["type"] == "compare" and len(items) != 2:
        return None
    if len(items) < 2:
        return None
    return {"type": v["type"], "title": _short(v.get("title", ""), 60), "items": items[:6]}


def _valid_check(c):
    if not isinstance(c, dict) or not c.get("question"):
        return None
    opts = [str(o).strip() for o in (c.get("options") or [])][:4]
    ans = str(c.get("answer", "")).strip().upper()[:1]
    if len(opts) < 2 or not ans or ans not in "ABCD"[:len(opts)] or not all(opts):
        return None
    if len(set(o.lower() for o in opts)) < len(opts) or str(c["question"]).lower().startswith("fill in the gap"):
        return None
    return {"question": str(c["question"]), "options": opts, "answer": ans, "explain": str(c.get("explain", ""))}


def enrich(segs, text=""):
    """Make sure every part has a kind, a diagram and (for teaching parts) a check question."""
    pool = definitions(text or " ".join((s.get("source") or "") for s in segs))
    out = []
    for i, s in enumerate(segs):
        s = dict(s)
        if "kind" not in s:
            s["kind"] = "intro" if i == 0 and len(segs) > 2 else ("summary" if i == len(segs) - 1 and len(segs) > 2 else "teach")
        s["points"] = [str(p) for p in (s.get("points") or [])][:6]
        s["visual"] = _valid_visual(s.get("visual")) or _heuristic_visual(s)
        if s["kind"] == "teach" and s.get("check") is not False:
            s["check"] = _valid_check(s.get("check")) or _heuristic_check(s, pool, i)
        else:
            s["check"] = None
        out.append(s)
    return out


def simpler(lecture_title, part_title, part_speech, text):
    """Re-explain one part more simply."""
    if provider() != "basic":
        try:
            prompt = (f"Lecture: {lecture_title}. A student did not understand the part '{part_title}'. You said:\n"
                      f"{part_speech}\n\nExplain it again much more simply in 80-150 words, as if to a 12-year-old, "
                      "with a new everyday Nigerian example. Plain spoken text.")
            return _llm(PROFESSOR, prompt, 500).strip()
        except Exception as e:
            print("AI simpler failed:", e)
    sents = _sentences(part_speech)
    return ("Let me say it another way. " + " ".join(sents[1:3] or sents[:2]) +
            " In short: " + part_title + ". Read it on the board again, and ask me if you are still not clear.")


def answer_question(lecture_title, text, question, history=None):
    p = provider()
    if p != "basic":
        try:
            ctx = text[:40000] if p == "claude" else _relevant(text, question, 6000)
            prompt = (f"Lecture: {lecture_title}\nLecture notes:\n{ctx}\n\nA student asks: {question}\n\n"
                      "Answer like a patient teacher in 60-150 words of natural speech. Base it on the notes, but "
                      "explain clearly with an example. If the question is unrelated to the course, politely bring "
                      "them back to the topic.")
            return _llm(PROFESSOR, prompt, 600).strip()
        except Exception as e:
            print("AI answer failed:", e)
    qk = set(_keywords(question, 10))
    scored = sorted(_sentences(text), key=lambda s: -len(qk & set(_keywords(s, 20))))
    if not scored or not qk & set(_keywords(scored[0], 20)):
        return ("That's a good question. I could not find it directly in today's notes, so please ask "
                "your lecturer in class, or rephrase it using words from the lecture.")
    return "Good question. From our notes: " + " ".join(scored[:2]) + " I hope that is clearer now."


def _relevant(text, query, limit):
    """Pick the note sections most related to the query (keeps prompts small for local models)."""
    qk = set(_keywords(query, 15))
    secs = sorted(_sections(text), key=lambda hb: -len(qk & set(_keywords(hb[0] + " " + hb[1], 40))))
    out = ""
    for h, b in secs:
        if len(out) > limit:
            break
        out += f"{h}\n{b}\n\n"
    return out[:limit]


# ------------------------------------------------------------------ monthly exam
def generate_exam(course_title, text, n_obj=10, n_theory=3, theory_marks=10):
    p = provider()
    try:
        if p == "claude":
            prompt = (f"Set a monthly examination for the course '{course_title}' strictly from these notes. "
                      f"Return ONLY JSON: {{\"objective\": [{{\"text\": str, \"options\": [4 strings], "
                      f"\"answer\": \"A\"|\"B\"|\"C\"|\"D\"}}] x{n_obj}, \"theory\": [{{\"text\": str, "
                      f"\"marking_guide\": str}}] x{n_theory}}}. Objective questions must test understanding, "
                      f"not trivia, with plausible distractors and the correct answer position varied. Theory "
                      f"questions are worth {theory_marks} marks each; the marking guide lists the key points "
                      f"and how marks are shared.\n\nNOTES:\n{text[:60000]}")
            data = _json_from(_claude(EXAMINER, prompt, 8000))
            if data.get("objective") or data.get("theory"):
                return data
        elif p == "ollama":
            return _ollama_exam(course_title, text, n_obj, n_theory, theory_marks)
    except Exception as e:
        print("AI exam generation failed, using basic mode:", e)
    return _offline_exam(text, n_obj, n_theory)


def _ollama_exam(course_title, text, n_obj, n_theory, theory_marks):
    secs = _sections(text)
    objective, theory = [], []
    per = max(1, -(-n_obj // len(secs)))           # objective questions per section (ceil)
    for i, (h, body) in enumerate(secs):
        if len(objective) >= n_obj:
            break
        prompt = (f"Course: {course_title}. Topic: {h}.\nNotes:\n{body[:3500]}\n\nWrite {per} multiple-choice "
                  "question(s) that test understanding of these notes. Each has exactly 4 options and one correct "
                  'answer. Return JSON: {"questions": [{"text": "...", "options": ["...","...","...","..."], '
                  '"answer": "A"}]} where answer is A, B, C or D.')
        try:
            for q in _json_from(_ollama(EXAMINER, prompt, 900, as_json=True)).get("questions", []):
                opts = [str(o) for o in (q.get("options") or [])][:4]
                ans = str(q.get("answer", "")).strip().upper()[:1]
                if len(opts) == 4 and ans in "ABCD" and ans and q.get("text"):
                    objective.append({"text": str(q["text"]), "options": opts, "answer": ans})
        except Exception as e:
            print("Ollama objective failed:", e)
    for h, body in secs[:n_theory]:
        prompt = (f"Course: {course_title}. Topic: {h}.\nNotes:\n{body[:3500]}\n\nWrite ONE theory exam question "
                  f"worth {theory_marks} marks on this topic and a marking guide listing the key points and marks for each. "
                  'Return JSON: {"text": "...", "marking_guide": "..."}')
        try:
            q = _json_from(_ollama(EXAMINER, prompt, 600, as_json=True))
            if q.get("text"):
                theory.append({"text": str(q["text"]), "marking_guide": str(q.get("marking_guide", ""))})
                continue
        except Exception as e:
            print("Ollama theory failed:", e)
        theory.append({"text": f"Explain {h} in detail, giving relevant examples.",
                       "marking_guide": "Key points: " + ", ".join(_keywords(body, 10))})
    if len(objective) < n_obj:                       # top up with basic questions if the model fell short
        objective += _offline_exam(text, n_obj - len(objective), 0)["objective"]
    return {"objective": objective[:n_obj], "theory": theory}


def _offline_exam(text, n_obj, n_theory):
    defs = definitions(text)
    rnd = random.Random(len(text))
    objective, used = [], set()
    for i, (t, d, _) in enumerate(defs):
        if len(objective) >= n_obj:
            break
        q = make_question(t, d, defs, rnd, "what" if i % 2 == 0 else "which")
        if q and q["question"] not in used:
            used.add(q["question"])
            objective.append({"text": q["question"], "options": q["options"], "answer": q["answer"]})
    for i, (t, d, _) in enumerate(defs):                     # second pass: the other question style
        if len(objective) >= n_obj:
            break
        q = make_question(t, d, defs, rnd, "which" if i % 2 == 0 else "what")
        if q and q["question"] not in used:
            used.add(q["question"])
            objective.append({"text": q["question"], "options": q["options"], "answer": q["answer"]})
    theory = []
    for h, body in _sections(text)[:n_theory]:
        theory.append({"text": f"Explain {h} in detail, giving relevant examples.",
                       "marking_guide": "Key points: " + ", ".join(key_terms(body, 8))})
    return {"objective": objective, "theory": theory}


# ------------------------------------------------------------------ marking
def mark_theory(items, context=""):
    """items: [{id, text, marks, guide, answer}] -> {id: (score, feedback)}"""
    items = list(items)
    if not items:
        return {}
    p = provider()
    if p == "claude":
        try:
            payload = [{"id": i["id"], "question": i["text"], "max_marks": i["marks"],
                        "marking_guide": i.get("guide") or "", "student_answer": i.get("answer") or ""} for i in items]
            prompt = ("Mark these theory answers fairly, like a strict but fair Nigerian university examiner. "
                      "Award partial marks for correct points; empty or irrelevant answers get 0. Return ONLY "
                      "JSON: {\"results\": [{\"id\": int, \"marks\": number, \"feedback\": \"one or two "
                      "sentences to the student\"}]}.\n\n" +
                      (f"Course notes for reference:\n{context[:30000]}\n\n" if context else "") +
                      json.dumps(payload, ensure_ascii=False))
            res = _json_from(_claude(EXAMINER, prompt, 4000))["results"]
            out = {}
            for r in res:
                mx = next(i["marks"] for i in items if i["id"] == int(r["id"]))
                out[int(r["id"])] = (max(0.0, min(float(r["marks"]), mx)), r.get("feedback", ""))
            if len(out) == len(items):
                return out
        except Exception as e:
            print("AI marking failed, using basic mode:", e)
    elif p == "ollama":
        out = {}
        for i in items:
            ans = (i.get("answer") or "").strip()
            if not ans:
                out[i["id"]] = (0.0, "No answer was provided.")
                continue
            prompt = (f"Question ({i['marks']} marks): {i['text']}\nMarking guide: {i.get('guide') or 'Use the course notes.'}\n"
                      + (f"Relevant notes: {_relevant(context, i['text'], 2500)}\n" if context else "")
                      + f"Student answer: {ans[:4000]}\n\nMark it fairly; award partial marks for correct points. "
                      f'Return JSON: {{"marks": number from 0 to {i["marks"]}, "feedback": "one or two sentences"}}')
            try:
                r = _json_from(_ollama(EXAMINER, prompt, 300, as_json=True))
                out[i["id"]] = (max(0.0, min(float(r["marks"]), i["marks"])), str(r.get("feedback", "")))
            except Exception as e:
                print("Ollama marking failed for one answer:", e)
                out.update(_basic_mark([i]))
        return out
    return _basic_mark(items)


def _basic_mark(items):
    out = {}
    for i in items:
        ans = (i.get("answer") or "").strip()
        guide_kw = set(_keywords((i.get("guide") or "") + " " + i["text"], 12))
        if not ans:
            out[i["id"]] = (0.0, "No answer was provided.")
            continue
        hit = guide_kw & set(_keywords(ans, 80))
        ratio = len(hit) / max(1, len(guide_kw))
        length_bonus = min(1.0, len(ans.split()) / 120)
        score = round(i["marks"] * min(1.0, 0.75 * ratio * 1.6 + 0.25 * length_bonus) * 2) / 2
        out[i["id"]] = (score, f"Covered {len(hit)} of {len(guide_kw)} expected key points (offline marking).")
    return out


def class_report(exam_title, stats, weak_questions):
    if provider() != "basic":
        try:
            prompt = (f"Write a short report (120-200 words) to the lecturer about the class performance in "
                      f"'{exam_title}'. Stats: {json.dumps(stats)}. Hardest questions: "
                      f"{json.dumps(weak_questions)}. Highlight what the class understood, what to re-teach, "
                      "and students who need attention. Plain text.")
            return _llm(PROFESSOR, prompt, 700).strip()
        except Exception as e:
            print("AI report failed:", e)
    s = stats
    txt = (f"{s['count']} students wrote this exam. Average score {s['avg']}%, highest {s['high']}%, "
           f"lowest {s['low']}%, pass rate {s['pass_rate']}%.")
    if weak_questions:
        txt += " The class struggled most with: " + "; ".join(w["text"][:80] for w in weak_questions[:3]) + \
               ". Consider re-teaching these topics."
    return txt


# ------------------------------------------------------------------ words, voice and answer diagrams
_WORD_Q = [
    r"what (?:does|do|is) (?:the )?(?:word |term )?[\"“']?(?P<w>[a-z][a-z \-']{1,40}?)[\"”']? (?:mean|means|stand for)\b",
    r"(?:what is |tell me |give me |explain )?(?:the )?meaning of (?:the )?(?:word |term )?[\"“']?(?P<w>[a-z][a-z \-']{1,40}?)[\"”']?$",
    r"(?:please )?define (?:the )?(?:word |term )?[\"“']?(?P<w>[a-z][a-z \-']{1,40}?)[\"”']?$",
    r"^(?:what is|what are|what's|who is) (?:an? |the )?[\"“']?(?P<w>[a-z][a-z\-']{1,30}(?: [a-z\-']{2,20})?)[\"”']?$",
]


def word_question(q):
    """'what does pigment mean?' / 'meaning of osmosis' / 'define catalyst' / 'what is a stomata' -> the word, else None."""
    t = re.sub(r"^(teacher|sir|ma|prof|professor|please)[, ]+", "", (q or "").lower().strip())
    t = re.sub(r"[?.!]+$", "", t).strip()
    for pat in _WORD_Q:
        m = re.search(pat, t)
        if m:
            w = m.group("w").strip(" -'")
            if 1 <= len(w.split()) <= 3 and w not in ("it", "this", "that", "they", "he", "she"):
                return w
    return None


def notes_definition(text, word):
    """The lecture's own definition of a word, if the notes define it."""
    wl = word.lower()
    for t, d, sn in definitions(text or ""):
        if t.lower() == wl or t.lower().rstrip("s") == wl.rstrip("s"):
            return sn
    return None


def lecture_vocab(text, n=400):
    """Words of this lecture (used to correct mis-heard voice questions and to guide speech recognition)."""
    words = re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text or "")
    freq = Counter(w.lower() for w in words if w.lower() not in STOP)
    terms = [t.lower() for t, _, _ in definitions(text or "")] + [k.lower() for k in key_terms(text or "", 20)]
    return list(dict.fromkeys(terms + [w for w, _ in freq.most_common(n)]))


def answer_visual(question, answer, topic=None):
    """A diagram for an answer, built from what the teacher actually says, so it forms as he speaks."""
    sents = _sentences(answer) or [answer]
    rank = _keywords(answer, 30)
    qwords = [w for w in re.findall(r"[a-z][a-z\-]{2,}", question.lower()) if w not in STOP and w not in
              ("teacher", "please", "explain", "important", "mean", "meaning", "need", "work", "works", "happen", "happens")]
    topic = topic or word_question(question) or next((w for w in rank if w in qwords), None) or (qwords[0] if qwords else "Answer")
    items = []
    for sn in sents:
        if len(items) >= 5:
            break
        low = sn.lower()
        if low.startswith(("good question", "that's a good question", "i hope", "is that clear", "let me")) and len(sents) > 2:
            continue
        lab = _label(sn, topic, rank, [i["label"] for i in items])
        if lab.lower() in (i["label"].lower() for i in items):
            continue
        items.append({"label": lab, "detail": _clip(re.sub(r"^(good question\.?\s*|from our notes:\s*)", "", sn, flags=re.I), 90)})
    if len(items) < 2:
        items = [{"label": "Answer", "detail": _clip(answer, 120)}] + items
    kind = "flow" if sum(1 for s in sents if _STEP.match(s)) >= 2 else "concept"
    return {"type": kind, "title": _short(topic[:1].upper() + topic[1:], 40), "items": items[:5]}
