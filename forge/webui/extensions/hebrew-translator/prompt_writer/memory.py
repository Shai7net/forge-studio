"""What the writer learns from 👍 and 👎: every rating is kept, and each new prompt is written with the closest
liked examples in front of the model and the notes the user gave.

One JSON file (data/learned.json), a list of entries:
  {id, time, source, target, checkpoint, written, final, rating (1 or -1), note}
`written` is what the writer wrote, `final` what was in the prompt box when 👍 was pressed (the user may have
corrected it first: that is the best lesson of all). The same file is the data set for training the writer later.
"""

import json
import math
import os
import re
import threading
import time
import uuid
from collections import Counter

from . import styles

_NIQQUD = re.compile(r"[֑-ׇ]")
_WORD = re.compile(r"[\w']+", re.U)


def _grams(text):
    """Character 3-grams of each word (with its edges), so 'בשדה' and 'שדה' still meet."""
    text = _NIQQUD.sub("", (text or "").lower())
    out = Counter()
    for word in _WORD.findall(text):
        w = f" {word} "
        for i in range(len(w) - 2):
            out[w[i:i + 3]] += 1
    return out


_HEBREW = re.compile(r"[֐-׿]")
_PREFIXES = "והבלמשכ"


def glossary_pairs(note):
    """Word lessons in a note: "צב = turtle; ג'חנון = jachnun" -> [("צב", "turtle"), ("ג'חנון", "jachnun")]."""
    pairs = []
    for part in re.split(r"[;\n]", note or ""):
        if "=" not in part:
            continue
        left, right = (s.strip(" .,\"'") for s in part.split("=", 1))
        if left and right and _HEBREW.search(left) and not _HEBREW.search(right):
            pairs.append((_NIQQUD.sub("", left), right))
    return pairs


def mentions(source, word):
    """Whether `word` is in `source` as a word, also with Hebrew prefix letters (הצב, והצב), not inside another
    word (צב is not in צבע). A two-letter word takes only ה and ו in front: מצב is a word of its own."""
    words = word.split()
    prefixes = "וה" if len(words[0]) <= 2 else _PREFIXES
    tokens = _WORD.findall(_NIQQUD.sub("", source or ""))
    for i in range(len(tokens) - len(words) + 1):
        first = tokens[i]
        stripped = {first} | {first[n:] for n in (1, 2) if len(first) > n and all(c in prefixes for c in first[:n])}
        if words[0] in stripped and tokens[i + 1:i + len(words)] == words[1:]:
            return True
    return False


def similarity(a, b):
    ga, gb = _grams(a), _grams(b)
    if not ga or not gb:
        return 0.0
    dot = sum(v * gb.get(k, 0) for k, v in ga.items())
    return dot / math.sqrt(sum(v * v for v in ga.values()) * sum(v * v for v in gb.values()))


class Memory:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.entries = self._read()

    def _read(self):
        try:
            with open(self.path, encoding="utf8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except FileNotFoundError:
            return []
        except Exception as e:  # noqa: BLE001 - a broken file is kept aside, never overwritten
            bad = f"{self.path}.broken-{int(time.time())}"
            try:
                os.replace(self.path, bad)
            except OSError:
                pass
            print(f"[Prompt writer] {self.path} could not be read ({e}); kept as {bad}")
            return []

    def _write(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf8") as f:
            json.dump(self.entries, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------ ratings

    def rate(self, source, target, written, final, rating, checkpoint="", note="", entry_id=None, meaning=""):
        """Adds a rating (or changes one: the same id, e.g. a note typed after 👎). Returns the entry. `meaning` is
        the writer's literal translation (kept so liked examples show the model the whole answer)."""
        with self.lock:
            entry = next((e for e in self.entries if entry_id and e["id"] == entry_id), None)
            if entry is None:
                # rating the same text again replaces the earlier rating instead of counting it twice
                entry = next((e for e in self.entries if e["source"] == source and e["target"] == target
                              and e["final"] == (final or written)), None)
            if entry is None:
                entry = {"id": uuid.uuid4().hex[:12], "source": source, "target": target}
                self.entries.append(entry)
            entry.update(time=time.strftime("%Y-%m-%d %H:%M:%S"), checkpoint=checkpoint or entry.get("checkpoint", ""),
                         meaning=meaning or entry.get("meaning", ""),
                         written=written, final=final or written, rating=1 if rating > 0 else -1,
                         note=(note if note is not None else entry.get("note", "")).strip())
            self._write()
            return dict(entry)

    def note(self, entry_id, note):
        with self.lock:
            for e in self.entries:
                if e["id"] == entry_id:
                    e["note"] = (note or "").strip()
                    self._write()
                    return dict(e)
        return None

    def forget(self, entry_id):
        with self.lock:
            before = len(self.entries)
            self.entries = [e for e in self.entries if e["id"] != entry_id]
            if len(self.entries) != before:
                self._write()
                return True
        return False

    def counts(self):
        liked = sum(1 for e in self.entries if e.get("rating", 0) > 0)
        return {"liked": liked, "disliked": len(self.entries) - liked}

    # ------------------------------------------------------------------ what a new prompt is written with

    def lessons(self, source, target, examples=3, max_notes=8):
        """(examples as (source, answer) pairs, notes, disliked versions to avoid, words) for writing `source` for
        `target`.

        Examples: liked prompts of the same target, closest idea last (nearest to the question); while there are
        fewer than two, the target's built-in example fills in. Notes: the newest ones, the target's own first.
        Avoid: disliked prompts written for nearly the same idea. Words: "צב = turtle" lessons from any note whose
        Hebrew word is in `source` (for every target: a word means the same everywhere)."""
        with self.lock:
            entries = list(self.entries)
        family = styles.FAMILY[target]
        scored = []
        for n, e in enumerate(entries):
            if e.get("rating", 0) <= 0 or e.get("target") not in styles.FAMILY:
                continue
            same = e["target"] == target
            if not same and styles.FAMILY[e["target"]] != family:
                continue
            # the same target counts most, then how close the idea is, and newer a little more
            score = similarity(source, e["source"]) + (0.35 if same else 0.0) + 0.1 * n / max(1, len(entries))
            scored.append((score, e))
        scored.sort(key=lambda s: s[0])
        chosen = [(e["source"], styles.answer(e.get("meaning", ""), e["final"])) for _, e in scored[-examples:]]
        if len(chosen) < 2 and target in styles.EXAMPLES:
            chosen.insert(0, styles.example(target))

        notes, words = [], {}
        for e in sorted(entries, key=lambda e: (e.get("target") == target, e.get("time", "")), reverse=True):
            text = (e.get("note") or "").strip()
            pairs = glossary_pairs(text)
            for word, english in pairs:
                if word not in words and mentions(source, word):
                    words[word] = english  # the newest lesson for a word wins
            if pairs and all("=" in part for part in re.split(r"[;\n]", text) if part.strip()):
                continue  # only word lessons: they are in `words`
            if text and text not in notes and styles.FAMILY.get(e.get("target")) == family:
                notes.append(text)
        avoid = [e["written"] for e in entries
                 if e.get("rating", 0) < 0 and e.get("target") == target and similarity(source, e["source"]) > 0.75]
        return chosen, notes[:max_notes], avoid[-2:], sorted(words.items())

    def listing(self):
        with self.lock:
            return [dict(e) for e in reversed(self.entries)]
