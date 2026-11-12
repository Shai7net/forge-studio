"""The 🌐 prompt writer (the hebrew-translator extension) asked over Forge's own HTTP routes, so its model, its
card sharing and what it learned from 👍 / 👎 are the same ones the prompt fields use. Its targets for this tab:
"ch_character" -> "character" (the character as JSON), "ch_video" -> "wan_ingredients" (a Wan prompt that names
the character as "... from picture 1")."""

import json
import random
import urllib.error
import urllib.request


class WriterError(RuntimeError):
    pass


def _base():
    try:
        from modules import shared
        port = getattr(shared.cmd_opts, "port", None) or 7860
    except Exception:
        port = 7860
    return f"http://127.0.0.1:{port}"


def write(text, field, timeout=240, video_mode=""):
    """The writer's answer for `text` written for `field`'s target: {"prompt", "meaning", ...}."""
    body = json.dumps({"text": text, "field": field, "video_mode": video_mode, "again": False,
                       "seed": random.randint(0, 10 ** 9)}).encode()
    req = urllib.request.Request(_base() + "/translator/write", body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise WriterError("The prompt writer (the hebrew-translator extension) is not installed: write the "
                              "character in English instead.") from None
        try:
            message = json.load(e).get("error") or str(e)
        except Exception:
            message = str(e)
        raise WriterError(message) from None
    except urllib.error.URLError as e:
        raise WriterError(f"Could not reach the prompt writer: {e.reason}") from None


def video_request(idea, character):
    """What the writer gets for a video: the user's words, and who picture 1 is."""
    who = character.get("name") or "the character"
    look = character.get("appearance") or character.get("summary") or ""
    style = character.get("style") or ""
    return (f"{idea.strip()}\n\n(Picture 1 is {who}. Its look: {look} Call it by 2 to 6 words of that look, the "
            f"most visible clothes or features, for example \"the old man in the green sweater from picture 1\". "
            f"Style: {style}.)")
