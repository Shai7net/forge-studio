"""The writer's logic without the model: targets, cleaning answers, and what is learned from ratings.

  venv\\Scripts\\python.exe extensions\\hebrew-translator\\tests\\test_prompt_writer.py   (or with pytest)
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prompt_writer  # noqa: E402
from prompt_writer import memory as pmem, styles  # noqa: E402


def test_targets():
    assert styles.target_for("txt2img_prompt", "sd15") == "sd15"
    assert styles.target_for("img2img_prompt", "sdxl", "juggernautXL_v9.safetensors") == "sdxl"
    assert styles.target_for("txt2img_prompt", "sdxl", "ponyDiffusionV6XL.safetensors") == "pony"
    assert styles.target_for("txt2img_prompt", "sdxl", "waiNSFWIllustrious_v140.safetensors") == "booru"
    assert styles.target_for("txt2img_prompt", "flux", "flux3-image-klein") == "flux"
    assert styles.target_for("txt2img_neg_prompt", "flux") == "negative"
    assert styles.target_for("vs_prompt", "sd15", "", "t2v") == "wan_t2v"
    assert styles.target_for("vs_prompt", "sd15", "", "i2v") == "wan_i2v"
    assert styles.target_for("vs_part2", "sd15", "", "i2v") == "wan_i2v"
    assert styles.target_for("txt2img_prompt", "fibo", "FIBO 2.0 Dev (INT8) [063dd39739]") == "fibo"
    assert styles.target_for("img2img_prompt", "fibo", "FIBO 2.0 Dev (INT8) [063dd39739]") == "fibo_edit"
    assert styles.target_for("txt2img_prompt", "fibo", "FIBO 2.0 Edit Turbo (INT8) [1f6fa7c499]") == "fibo_edit"
    assert styles.target_for("txt2img_neg_prompt", "fibo") == "negative"
    assert set(styles.TARGETS) == set(styles.GUIDES) == set(styles.EXAMPLES) == set(styles.FAMILY)


def test_tidy():
    assert styles.tidy('"a cat, sitting"', "sd15") == "a cat, sitting"
    assert styles.tidy("Prompt: a cat", "flux") == "a cat"
    # the two-line answer: the meaning is shown to the user, the prompt goes into the box
    two = "Meaning: A fox in the snow.\nPrompt: A red fox stands in deep snow."
    assert styles.parse(two) == ("A fox in the snow.", "A red fox stands in deep snow.")
    assert styles.tidy(two, "flux") == "A red fox stands in deep snow."
    assert styles.parse("Meaning: A fox.\nA red fox, snow")[1] == "A red fox, snow"
    assert styles.parse("just a prompt") == ("", "just a prompt")
    # FIBO's caption: JSON across lines (or in a code fence) comes back as valid, readable JSON
    fibo = 'Meaning: A fox.\nPrompt: ```json\n{"short_description": "A fox in snow.",\n "objects": []}\n```'
    assert json.loads(styles.tidy(fibo, "fibo")) == {"short_description": "A fox in snow.", "objects": []}
    try:
        styles.tidy("Prompt: a fox in snow", "fibo")
        raise AssertionError("plain text must not pass as a FIBO caption")
    except ValueError:
        pass
    assert json.loads(styles.example("fibo")[1].split("Prompt: ", 1)[1])["style_medium"] == "photograph"
    # a weight that lost its brackets gets them back; correct ones and times are left alone
    assert styles.tidy("fox in snow:1.2, forest", "sd15") == "(fox in snow:1.2), forest"
    assert styles.tidy("(fox:1.2), at 10:30, forest", "sd15") == "(fox:1.2), at 10:30, forest"
    assert styles.tidy("A fox.\nIt runs.", "flux") == "A fox. It runs."
    assert styles.tidy("a fox\nsnow", "sd15") == "a fox, snow"
    # the user's LoRA tags always come back unchanged
    text, tags = styles.split_tags("שועל בשלג <lora:LCM_SD15:1> ")
    assert text == "שועל בשלג" and tags == ["<lora:LCM_SD15:1>"]
    assert styles.tidy("a fox in snow", "sd15", tags) == "a fox in snow <lora:LCM_SD15:1>"
    assert styles.tidy("a fox <lora:LCM_SD15:1>", "sd15", tags) == "a fox <lora:LCM_SD15:1>"


def test_similarity():
    assert pmem.similarity("נחש מצייר ציור", "נחש מצייר ציור") > 0.99
    assert pmem.similarity("נחש מצייר ציור", "הנחש מצייר ציור יפה") > 0.6
    assert pmem.similarity("נחש מצייר ציור", "סבתא מבשלת קוסקוס") < 0.1


def test_learning(tmp_path):
    mem = pmem.Memory(str(tmp_path / "learned.json"))
    # nothing learned yet: the built-in example, no notes
    examples, notes, avoid, words = mem.lessons("נחש מצייר ציור", "sd15")
    assert examples == [styles.example("sd15")] and notes == [] and avoid == [] and words == []
    assert examples[0][1].startswith("Meaning: A red-haired girl") and "\nPrompt: a red-haired girl" in examples[0][1]

    a = mem.rate("דרקון ישן על ערימת זהב", "sd15", "a dragon", "(sleeping dragon:1.2), gold", 1,
                 meaning="A dragon sleeps on a pile of gold.")
    mem.rate("ינשוף קורא ספר", "sd15", "an owl", "(owl reading:1.2), library", 1)
    mem.rate("נחש בשמש", "flux", "A snake.", "A snake basks in the sun.", 1)  # another family: not an example
    bad = mem.rate("נחש מצייר ציור", "sd15", "a snake, masterpiece", "", -1)
    mem.note(bad["id"], "בלי masterpiece")
    examples, notes, avoid, words = mem.lessons("נחש מצייר ציור", "sd15")
    assert [e[1] for e in examples] == ["Meaning: A dragon sleeps on a pile of gold.\nPrompt: (sleeping dragon:1.2), gold",
                                        "Prompt: (owl reading:1.2), library"]
    assert notes == ["בלי masterpiece"]
    assert avoid == ["a snake, masterpiece"]

    # rating the same entry again changes it instead of adding one; the 👍 keeps the corrected text
    again = mem.rate("דרקון ישן על ערימת זהב", "sd15", "a dragon", "(sleeping dragon:1.3), gold", 1, entry_id=a["id"])
    assert again["id"] == a["id"] and again["final"] == "(sleeping dragon:1.3), gold"
    assert mem.counts() == {"liked": 3, "disliked": 1}

    # kept on disk, and forgotten on request
    mem2 = pmem.Memory(str(tmp_path / "learned.json"))
    assert len(mem2.listing()) == 4
    assert mem2.forget(bad["id"]) and mem2.counts() == {"liked": 3, "disliked": 0}


def test_words(tmp_path):
    assert pmem.glossary_pairs("צב = turtle; ג'חנון = jachnun") == [("צב", "turtle"), ("ג'חנון", "jachnun")]
    assert pmem.glossary_pairs("קצר יותר") == [] and pmem.glossary_pairs("turtle = צב") == []
    assert pmem.mentions("הצב מטפס על עץ", "צב") and pmem.mentions("והצב", "צב")
    assert not pmem.mentions("צבע אדום", "צב") and not pmem.mentions("מצב רוח", "צב")
    assert pmem.mentions("חייל בתחנה מרכזית", "תחנה מרכזית") and pmem.mentions("ובג'חנון", "ג'חנון")

    mem = pmem.Memory(str(tmp_path / "learned.json"))
    bad = mem.rate("הצב מטפס על עץ", "flux", "A deer climbs.", "", -1)
    mem.note(bad["id"], "צב = turtle")
    mixed = mem.rate("סבתא מבשלת ג'חנון", "sd15", "challah", "", -1)
    mem.note(mixed["id"], "ג'חנון = jachnun\nקצר יותר")
    # a word lesson reaches every target whose request has the word, and only those
    *_, words = mem.lessons("צב קטן בים", "wan_t2v")
    assert words == [("צב", "turtle")]
    assert mem.lessons("צבע אדום", "wan_t2v")[3] == []
    examples, notes, avoid, words = mem.lessons("ג'חנון עם ביצה", "sd15")
    assert words == [("ג'חנון", "jachnun")] and notes == ["ג'חנון = jachnun\nקצר יותר"]
    # a note that is only word lessons is not repeated as a general note
    assert mem.lessons("צב", "flux")[1] == []
    system, *_ = prompt_writer.compose("צב קטן", "flux", mem)
    assert "- צב = turtle" in system


def test_compose(tmp_path):
    mem = pmem.Memory(str(tmp_path / "learned.json"))
    system, turns, user, tags = prompt_writer.compose("נחש מצייר ציור <lora:x:0.8>", "wan_t2v", mem, "Faithful")
    assert user == "נחש מצייר ציור" and tags == ["<lora:x:0.8>"]
    assert "Wan 2.2" in system and styles.CREATIVITY["Faithful"] in system and "Meaning:" in system
    assert turns == [styles.example("wan_t2v")]
    # a negative prompt never gets enriched
    system, *_ = prompt_writer.compose("טשטוש", "negative", mem, "Creative")
    assert styles.CREATIVITY["Faithful"] in system and styles.CREATIVITY["Creative"] not in system


def test_broken_file_is_kept(tmp_path):
    path = tmp_path / "learned.json"
    path.write_text("{not json", encoding="utf8")
    mem = pmem.Memory(str(path))
    assert mem.listing() == []
    assert any(p.name.startswith("learned.json.broken-") for p in tmp_path.iterdir())


def test_characters_targets():
    """sd-forge-characters' fields, and Video Studio's Ingredients sub-tab."""
    assert styles.target_for("ch_character", "flux") == "character"
    assert styles.target_for("ch_video", "flux") == "wan_ingredients"
    assert styles.target_for("vs_prompt", "flux", "", "ingredients") == "wan_ingredients"
    assert styles.target_for("vs_prompt", "flux", "", "t2v") == "wan_t2v"
    answer = ('Meaning: a skater girl\nPrompt: ```json\n{"name": "Luna", "appearance": "A skater girl.", '
              '"extra": 1}\n```')
    value = json.loads(styles.tidy(answer, "character"))
    assert list(value) == list(styles.CHARACTER_KEYS) and value["name"] == "Luna" and value["style"] == ""
    try:
        styles.tidy('Prompt: {"name": "Luna"}', "character")
        raise AssertionError("a character without appearance must be refused")
    except ValueError:
        pass
    for target in ("character", "wan_ingredients"):  # every target has its guide, example and family
        assert styles.GUIDES[target] and styles.example(target) and styles.FAMILY[target]


if __name__ == "__main__":  # Forge's venv has no pytest
    import inspect
    import pathlib
    import tempfile

    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            with tempfile.TemporaryDirectory() as tmp:
                fn(*([pathlib.Path(tmp)] if inspect.signature(fn).parameters else []))
            print(f"ok  {name}")
