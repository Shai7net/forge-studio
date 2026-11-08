"""Optional local VGL expansion using the already loaded Qwen3-VL, without cloud calls."""

import json

SYSTEM = """You write visual captions for the FIBO 2.0 image model. Translate the user's visual request into English,
preserving the requested subjects, counts, identities and composition. Return ONLY a valid JSON object, no markdown.
Translate Hebrew object nouns carefully: ספל means a mug/cup with a handle, never a spoon (כף) or teaspoon (כפית).
Use short_description, objects (description, location, relative_size, appearance_details), background_setting,
lighting (conditions, direction), aesthetics (composition, mood_atmosphere), photographic_characteristics
(camera_angle, focus), style_medium, artistic_style, context. Keep it concise: at most 300 words.
Include text_render (entries with text, location, color, font) ONLY when the user specifies exact lettering;
preserve that lettering in its original language. Do not invent slogans or change requested text.
When images are provided this is an EDIT: describe the desired FINAL image, keep every unrequested property
of image_1 unchanged, and add edit_instruction as a direct imperative command describing the requested changes.
References are numbered image_1, image_2, etc. Use every reference requested by the user.
Never add aesthetic_score, preference_score, markdown, commentary, or a chain of thought."""


def wants_writer(request):
    if request.prompt_writer == "off" or request.prompt.lstrip().startswith("{"):
        return False
    return request.prompt_writer == "always" or len(request.prompt.split()) < 40 or any(ord(c) > 0x24F for c in request.prompt)


def extract_json(text, editing=False):
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("The local writer did not return a VGL object. Disable the writer or provide VGL JSON directly.")
    value = json.loads(text[start:end + 1])
    if not isinstance(value, dict) or not isinstance(value.get("short_description"), str):
        raise ValueError("The local writer returned an invalid caption")
    if editing and not isinstance(value.get("edit_instruction"), str):
        raise ValueError("The local writer returned no edit instruction")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def expand(clip, request, references):
    import numpy as np
    import torch
    from comfy.text_encoders.qwen3vl import Qwen3VLTokenizer

    images = []
    for reference in references:
        image = reference.copy().convert("RGB")
        image.thumbnail((512, 512))
        images.append(torch.from_numpy(np.asarray(image).astype("float32") / 255.0)[None])
    # The same weights generate text; the regular chat tokenizer avoids FIBO's caption-only template.
    tokenizer = Qwen3VLTokenizer(model_type="qwen3vl_4b")
    tokens = tokenizer.tokenize_with_weights(request.prompt, images=images, system_prompt=SYSTEM, min_length=1)
    generated = clip.generate(tokens, do_sample=False, max_length=1000, repetition_penalty=1.05, mtp=False)
    return extract_json(clip.decode(generated), editing=request.mode == "edit")
