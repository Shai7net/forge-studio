# Direction tools next to Deforum's Motion schedules (Keyframes -> Motion): ⇄ flips the direction of a schedule, and
# − ↔N + switch it every N frames (off = one direction). The speeds stay as they are; the logic is in camera_directing.py.

import re
import traceback

import gradio as gr
import numpy as np
from modules.ui_components import ToolButton

from . import audio_keyframes as ak
from . import camera_directing as cd

OFF_LABEL = "↔ off"


def nosave(component):
    component.do_not_save_to_config = True
    return component


def every_label(every):
    return OFF_LABEL if every <= 0 else f"↔ {every}"


def every_from_label(label):
    match = re.search(r"\d+", str(label or ""))
    return int(match.group(0)) if match else 0


def direction_tools(key):
    """The small buttons for one Motion schedule. Call it inside the FormRow of the schedule's textbox."""
    return {
        f"{key}_dir_flip": nosave(ToolButton(value="⇄", elem_id=f"{key}_dir_flip",
                                             tooltip="Flip the direction (+ becomes −). The speed stays.")),
        f"{key}_dir_less": nosave(ToolButton(value="−", elem_id=f"{key}_dir_less",
                                             tooltip="Switch the direction more often (every N − 1 frames)")),
        f"{key}_dir_every": nosave(ToolButton(value=OFF_LABEL, elem_id=f"{key}_dir_every",
                                              tooltip="Switch the direction every N frames (off = one direction). "
                                                      "Click to apply it again.")),
        f"{key}_dir_more": nosave(ToolButton(value="+", elem_id=f"{key}_dir_more",
                                             tooltip="Switch the direction less often (every N + 1 frames)")),
    }


def expand_schedule(text, frame_count):
    """Per-frame values of a Deforum schedule, parsed by Deforum itself (math like sin(t) included)."""
    from .animation_key_frames import FrameInterpolater
    return FrameInterpolater(int(frame_count)).parse_inbetweens(text, "motion").to_numpy(dtype=np.float64)


def on_direction_tool(key, action, text, every_text, max_frames):
    every = every_from_label(every_text)
    if action == "less":
        every = max(every - 1, 0)
    elif action == "more":
        every += 1
    try:
        target = ak.TARGETS_BY_KEY[key]
        text = text if str(text or "").strip() else f"0: ({ak.format_number(target.neutral, target.decimals)})"
        frames = max(int(float(max_frames or 0)), cd.last_key_frame(text) + 1, 1)
        values = expand_schedule(text, frames)
        if action == "flip":
            values = cd.flip_values(values, target.neutral)
        else:
            values = cd.switch_values(values, target.neutral, every)
        return gr.update(value=ak.schedule_text(values, target.decimals)), gr.update(value=every_label(every))
    except Exception as err:
        print(f"Deforum Motion direction tools ({key}): {err}")
        traceback.print_exc()
        return gr.update(), gr.update()


def tool_handler(key, action):
    def handler(text, every_text, max_frames):
        return on_direction_tool(key, action, text, every_text, max_frames)
    return handler


def wire_direction_tools(l):
    """Connects the Motion tab's direction tools; `l` is setup_deforum_left_side_ui's locals() with every tab built."""
    for key in cd.MOTION_KEYS:
        if f"{key}_dir_every" not in l:
            continue
        inputs = [l[key], l[f"{key}_dir_every"], l["max_frames"]]
        outputs = [l[key], l[f"{key}_dir_every"]]
        for suffix, action in (("dir_flip", "flip"), ("dir_less", "less"), ("dir_every", "apply"), ("dir_more", "more")):
            l[f"{key}_{suffix}"].click(fn=tool_handler(key, action), inputs=inputs, outputs=outputs,
                                       queue=False, show_progress=False)
