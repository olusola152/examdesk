"""Offline English dictionary for AI Professor (WordNet 3.0, ~147,000 words; see data/WORDNET_LICENSE.txt).

lookup("pigments") -> {"word": "pigment", "senses": [{"pos", "definition", "example", "synonyms", "kind_of"}]}
"""
import json
import os
import re
import sqlite3
import sys
import threading

_HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(_HERE, "data", "dictionary.db")
_local = threading.local()


def available():
    return os.path.exists(DB)


def _con():
    c = getattr(_local, "con", None)
    if c is None:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, check_same_thread=False)
        _local.con = c
    return c


def _raw(word):
    row = _con().execute("SELECT data FROM words WHERE word=?", (word,)).fetchone()
    return json.loads(row[0]) if row else None


def _candidates(w):
    """Base forms to try: studies->study, running->run, bigger->big, quickly->quick ..."""
    yield w
    rules = [("iest", "y"), ("ier", "y"), ("ies", "y"), ("ied", "y"), ("ves", "f"), ("ves", "fe"), ("es", ""), ("s", ""), ("ing", ""), ("ing", "e"),
             ("ed", ""), ("ed", "e"), ("er", ""), ("est", ""), ("ly", ""), ("ily", "y"), ("ness", ""), ("ment", "")]
    for suf, rep in rules:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            base = w[: -len(suf)] + rep
            yield base
            if len(base) > 3 and base[-1] == base[-2] and suf in ("ing", "ed", "er", "est"):
                yield base[:-1]                       # running -> runn -> run


def lookup(word):
    if not available():
        return None
    w = re.sub(r"[^a-z0-9\s\-']", "", (word or "").lower()).strip()
    w = re.sub(r"\s+", " ", w)
    if not w:
        return None
    for cand in _candidates(w):
        data = _raw(cand)
        if data:
            return {"word": cand, "asked": w, "senses": [
                {"pos": p, "definition": d, "example": ex, "synonyms": syn, "kind_of": hyper}
                for p, d, ex, syn, hyper in data]}
    return None


_AN = lambda w: "an" if w[:1].lower() in "aeiou" else "a"


def _meaning(d):
    """'(computer science) written programs ...' -> ('computer science', 'written programs ...')"""
    d = d.split(";")[0].strip()
    m = re.match(r"^\(([^)]{2,40})\)\s*(.+)$", d)
    return (m.group(1), m.group(2)) if m else (None, d)


def explain(entry, notes_def=None):
    """What the teacher says about a word (plain spoken English)."""
    w = entry["word"] if entry else ""
    say = []
    if notes_def:
        say.append(f"In our lecture notes, {notes_def.rstrip('.')}.")
    if entry:
        s = entry["senses"]
        first = s[0]
        dom, mean = _meaning(first["definition"])
        say.append(f"{'In everyday English, the' if notes_def else 'The'} {'term' if ' ' in w else 'word'} {w} is {_AN(first['pos'])} {first['pos']}. "
                   + (f"In {dom}, it means {mean}." if dom else f"It means {mean}."))
        if first["kind_of"]:
            say.append(f"It is a kind of {first['kind_of']}.")
        if first["example"]:
            say.append(f"For example: {first['example']}.")
        other = next((x for x in s[1:] if x["pos"] != first["pos"] or x["definition"] != first["definition"]), None)
        if other:
            say.append(f"It can also mean {_meaning(other['definition'])[1]}"
                       + (f", as {_AN(other['pos'])} {other['pos']}" if other["pos"] != first["pos"] else "") + ".")
        syn = [x for x in first["synonyms"] if x.lower() != w][:3]
        if syn:
            say.append(f"Words with a similar meaning are {', '.join(syn[:-1]) + ' and ' + syn[-1] if len(syn) > 1 else syn[0]}.")
    return " ".join(say)


def visual(entry, notes_def=None):
    """A word map for the diagram screen."""
    items = []
    if notes_def:
        items.append({"label": "In our notes", "detail": notes_def})
    if entry:
        first = entry["senses"][0]
        dom, mean = _meaning(first["definition"])
        items.append({"label": "Meaning" + (f" ({dom})" if dom else ""), "detail": mean})
        if first["kind_of"]:
            items.append({"label": "Kind of", "detail": first["kind_of"]})
        if first["example"]:
            items.append({"label": "Example", "detail": first["example"]})
        syn = [x for x in first["synonyms"] if x.lower() != entry["word"]][:4]
        if syn:
            items.append({"label": "Similar words", "detail": ", ".join(syn)})
        items.append({"label": "Word type", "detail": first["pos"]})
    return {"type": "concept", "title": (entry["word"] if entry else "").capitalize(), "items": items[:6]}


# ---------------------------------------------------------------- understanding any accent
# Speech engines often mis-hear words said with a different accent or vowel sounds ("ticha" for "teacher",
# "fotosintesis" for "photosynthesis", "tin" for "thin"). We compare words by how they SOUND, ignoring
# vowels/diphthongs, silent r, and sounds that accents commonly swap (th/t/d, v/f, z/s, sh/s, ph/f, ...).
import difflib

FUNCTION_WORDS = set("""a an the and or but if of to in on at by for with from as is are was were be been am do does did
done have has had not no yes it its this that these those there here what which who whom whose when where why how
i me my we us our you your he him his she her they them their can could will would shall should may might must
please sir ma madam teacher prof professor tell explain mean means meaning define definition word words
example examples again repeat difference between about more give show us""".split())


# Nigerian Pidgin / common spoken forms -> English, so "wetin be photosynthesis?" works
SPOKEN = {"wetin": "what", "watin": "what", "wat": "what", "wot": "what", "de": "the", "d": "the", "da": "the", "dis": "this",
          "dat": "that", "dem": "them", "deir": "their", "abeg": "please", "biko": "please", "una": "you", "oga": "sir",
          "sabi": "know", "shey": "is", "abi": "right", "na": "is", "dey": "is", "wey": "which", "comot": "remove",
          "wahala": "problem", "pikin": "child", "plenty": "many", "small": "small", "tink": "think", "ting": "thing",
          "tings": "things", "wit": "with", "becos": "because", "bcos": "because", "cos": "because", "pls": "please",
          "ticha": "teacher", "tisha": "teacher", "tesha": "teacher", "sa": "sir", "mean": "mean", "meanin": "meaning",
          "explan": "explain", "esplain": "explain", "ekplain": "explain", "wen": "when", "wia": "where", "hau": "how", "wai": "why"}


def sound_key(word):
    w = re.sub(r"[^a-z]", "", (word or "").lower())
    if not w:
        return ""
    for a, b in (("tch", "c"), ("sch", "s"), ("chr", "kr"), ("chl", "kl"), ("ph", "f"), ("gh", ""), ("ck", "k"),
                 ("th", "t"), ("dh", "d"), ("sh", "s"), ("ch", "s"), ("qu", "kw"), ("q", "k"), ("x", "ks"),
                 ("dg", "j"), ("wh", "w"), ("z", "s"), ("v", "f")):
        w = w.replace(a, b)
    w = re.sub(r"^kn", "n", w)
    w = re.sub(r"^wr", "r", w)
    w = re.sub(r"^ps", "s", w)
    w = re.sub(r"c(?=[eiy])", "s", w)
    w = w.replace("c", "k")
    first, rest = w[0], w[1:]
    rest = re.sub(r"r(?![aeiouy])", "", rest)          # silent r: "teacher" ~ "ticha", "doctor" ~ "docta"
    rest = re.sub(r"[aeiouyhw]", "", rest)              # vowels, diphthongs, h, w: accents differ most here
    key = (first if first in "aeiouy" else first) + rest
    return re.sub(r"(.)\1+", r"\1", key)                 # double letters sound single


def _dist(a, b):
    """Edit distance (small strings)."""
    if abs(len(a) - len(b)) > 2:
        return 9
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


_index = None
_index_lock = threading.Lock()


def _sound_index():
    """sound key -> dictionary words (built once, ~1 s)."""
    global _index
    with _index_lock:
        if _index is None:
            _index = {}
            if available():
                for (w,) in _con().execute("SELECT word FROM words WHERE word NOT LIKE '% %' AND length(word) >= 3"):
                    _index.setdefault(sound_key(w), []).append(w)
    return _index


def best_match(word, vocab=(), min_ratio=0.5):
    """The word the speaker most likely meant: lecture words first, then the whole dictionary."""
    w = (word or "").lower()
    if len(w) < 3:
        return None
    k = sound_key(w)
    best, score = None, 0.0
    for cand in vocab:
        if " " in cand:
            continue
        ck = sound_key(cand)
        d = _dist(k, ck)
        if d <= (0 if len(k) <= 3 else 1):
            r = difflib.SequenceMatcher(None, w, cand.lower()).ratio() + (0.3 if d == 0 else 0)
            if r > score:
                best, score = cand, r
    if best and score >= min_ratio:
        return best
    close = difflib.get_close_matches(w, [v for v in vocab if " " not in v], n=1, cutoff=0.68)
    if close:                                            # a lecture word spelt/heard a bit differently
        return close[0]
    pool = [c for c in _sound_index().get(k, []) if abs(len(c) - len(w)) <= 3]
    if pool:                                             # whole-dictionary guesses must be close in spelling too
        cand = max(pool, key=lambda c: difflib.SequenceMatcher(None, w, c).ratio())
        if difflib.SequenceMatcher(None, w, cand).ratio() >= max(min_ratio, 0.7):
            return cand
    if available():                                      # plain misspelling: "enviroment" -> "environment"
        rows = [r[0] for r in _con().execute("SELECT word FROM words WHERE word LIKE ? AND length(word) BETWEEN ? AND ?",
                                             (w[:2] + "%", len(w) - 2, len(w) + 3))]
        close = difflib.get_close_matches(w, rows, n=1, cutoff=0.8)
        if close:
            return close[0]
    return None


def known(word):
    w = (word or "").lower()
    return w in FUNCTION_WORDS or (available() and any(_raw(c) for c in _candidates(w)))


def correct_text(text, vocab=()):
    """Fix mis-heard words in a spoken question using the lecture's own words, then the dictionary.
    Returns (corrected_text, [(heard, meant), ...])."""
    lecture = [v for v in dict.fromkeys(x.lower() for x in vocab) if len(v) >= 3]
    vocab = lecture + [w for w in sorted(FUNCTION_WORDS) if len(w) >= 3 and w not in lecture]
    vset = set(vocab)
    vkeys = {}
    for v in lecture:                                     # sound-alikes of LECTURE words only
        vkeys.setdefault(sound_key(v.replace(" ", "")), v)
    tokens = re.findall(r"[A-Za-z']+|[^A-Za-z']+", text or "")
    words = [i for i, t in enumerate(tokens) if re.match(r"[A-Za-z]", t)]
    out, changes, skip = list(tokens), [], set()
    for i in words:                                       # spoken / Pidgin forms first
        rep_ = SPOKEN.get(tokens[i].lower())
        if rep_ and rep_ != tokens[i].lower():
            changes.append((tokens[i], rep_))
            out[i] = tokens[i] = rep_
            skip.add(i)
    # 1) several heard words that together sound like one lecture word: "photo synthesis" / "foto sin tesis"
    for size in (3, 2):
        for n in range(len(words) - size + 1):
            idxs = words[n:n + size]
            if any(i in skip for i in idxs) or any(tokens[i].lower() in FUNCTION_WORDS for i in idxs):
                continue
            joined = "".join(tokens[i] for i in idxs).lower()
            target = vkeys.get(sound_key(joined))
            if target and len(joined) >= 6 and joined != target.replace(" ", "") and target not in FUNCTION_WORDS:
                if difflib.SequenceMatcher(None, joined, target.replace(" ", "")).ratio() >= 0.55:
                    changes.append((" ".join(tokens[i] for i in idxs), target))
                    out[idxs[0]] = target
                    for i in idxs[1:]:
                        out[i] = ""
                    skip.update(idxs)
    # 2) single words that are not real words (or that sound exactly like a lecture word)
    for i in words:
        if i in skip:
            continue
        t = tokens[i]
        low = t.lower()
        if low in vset or low in FUNCTION_WORDS:
            continue
        exact_lecture = vkeys.get(sound_key(low))
        if exact_lecture and " " not in exact_lecture and exact_lecture != low and \
                difflib.SequenceMatcher(None, low, exact_lecture).ratio() >= 0.55:
            meant = exact_lecture
        elif known(low):
            continue
        else:   # lecture words win over everyday words ("hardwer" -> hardware, not "had")
            meant = best_match(low, lecture) or best_match(low, sorted(FUNCTION_WORDS), min_ratio=0.6)
        if meant and meant != low:
            changes.append((t, meant))
            out[i] = meant
    fixed = re.sub(r"\s{2,}", " ", "".join(out)).strip()
    fixed = re.sub(r"\b(what|who|how) be\b", r"\1 is", fixed, flags=re.I)        # Pidgin: "wetin be X" -> "what is X"
    return fixed, changes


def lookup_any(word, vocab=()):
    """lookup() that also understands misspelt or mis-heard words. Adds 'did_you_mean' when it guessed."""
    e = lookup(word)
    if e:
        return e
    w = re.sub(r"[^a-z\s\-']", "", (word or "").lower()).strip()
    guess = best_match(w, vocab) if " " not in w else None
    if guess:
        e = lookup(guess)
        if e:
            e["did_you_mean"] = guess
            return e
    return None
