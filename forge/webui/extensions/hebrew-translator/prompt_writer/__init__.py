"""The Hebrew prompt writer: an idea in Hebrew (or any language) becomes an English prompt written the way the
target model reads best, and it learns from the user's 👍 and 👎 (see memory.py)."""

from . import styles


def compose(source, target, memory=None, creativity="Rich"):
    """What the model is asked: (system, example turns, the request, the LoRA tags kept aside)."""
    text, tags = styles.split_tags(source)
    examples, notes, avoid, words = (memory.lessons(text, target) if memory is not None
                                     else ([styles.example(target)], [], [], []))
    system = styles.system_prompt(target, creativity, notes, avoid, words)
    return system, examples, text, tags
