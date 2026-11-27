"""The LoRA chips → the prompt (no Forge imports, so it can be tested on its own: tests/test_workspace_lora.py)."""

import json


def parse_loras(value):
    """The hidden field's JSON → [{"alias", "weight", "trigger"}], unusable entries left out."""
    try:
        items = json.loads(value or "[]")
    except ValueError:
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        alias = str(it.get("alias") or it.get("name") or "").strip()
        if not alias or any(ch in alias for ch in "<>:"):
            continue
        try:
            weight = float(it.get("weight", 1.0))
        except (TypeError, ValueError):
            weight = 1.0
        out.append({"alias": alias, "weight": max(-5.0, min(5.0, weight)), "trigger": str(it.get("trigger") or "").strip()})
    return out


def add_to_prompt(prompt, loras):
    """The prompt with each chip's trigger words and LoRA tag after it. A LoRA already typed in the prompt by hand is
    left as typed (no second tag, no second set of trigger words)."""
    prompt = prompt or ""
    parts = [prompt.rstrip(" ,")] if prompt.strip() else []
    for it in loras:
        if f"<lora:{it['alias']}:" in prompt:
            continue
        if it["trigger"]:
            parts.append(it["trigger"])
        parts.append(f"<lora:{it['alias']}:{round(it['weight'], 2):g}>")
    return ", ".join(parts)
