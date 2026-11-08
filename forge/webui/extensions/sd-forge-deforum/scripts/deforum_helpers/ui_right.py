# Copyright (C) 2023 Deforum LLC
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

# Contact the authors: https://deforum.github.io/

from .args import DeforumOutputArgs, get_component_names, get_settings_component_names
from modules.shared import opts, state
from modules.ui import create_output_panel, wrap_gradio_call
from modules.call_queue import wrap_gradio_gpu_call
from .run_deforum import run_deforum
from .settings import save_settings, load_all_settings, load_video_settings
from .general_utils import get_deforum_version
from .ui_left import setup_deforum_left_side_ui
from . import deforumation_switch
from . import live_control
from scripts.deforum_extend_paths import deforum_sys_extend
import gradio as gr

def on_ui_tabs():
    # extend paths using sys.path.extend so we can access all of our files and folders
    deforum_sys_extend()
    # set text above generate button
    i1_store_backup = f"<p style=\"text-align:center;font-weight:bold;margin-bottom:0em\">Deforum extension for auto1111 — version 3.0 | Git commit: {get_deforum_version()}</p>"
    i1_store = i1_store_backup

    with gr.Blocks(analytics_enabled=False) as deforum_interface:
        components = {}
        dummy_component = gr.Label(visible=False)
        with gr.Row(elem_id='deforum_progress_row').style(equal_height=False, variant='compact'):
            with gr.Column(scale=1, variant='panel'):
                # setting the left side of the ui:
                components = setup_deforum_left_side_ui()
            with gr.Column(scale=1, variant='compact'):
                with gr.Row(variant='compact'):
                    btn = gr.Button("Click here after the generation to show the video")
                    components['btn'] = btn
                    close_btn = gr.Button("Close the video", visible=False)
                with gr.Row(variant='compact'):
                    i1 = gr.HTML(i1_store, elem_id='deforum_header')
                    components['i1'] = i1
                    def show_vid(): # Show video button related func
                        from .run_deforum import last_vid_data # get latest vid preview data (this import needs to stay inside the function!)
                        return {
                            i1: gr.update(value=last_vid_data, visible=True),
                            close_btn: gr.update(visible=True),
                            btn: gr.update(value="Update the video", visible=True),
                        }
                    btn.click(
                        fn=show_vid,
                        inputs=[],
                        outputs=[i1, close_btn, btn],
                        )
                    def close_vid(): # Close video button related func
                        return {
                            i1: gr.update(value=i1_store_backup, visible=True),
                            close_btn: gr.update(visible=False),
                            btn: gr.update(value="Click here after the generation to show the video", visible=True),
                        }
                    
                    close_btn.click(
                        fn=close_vid,
                        inputs=[],
                        outputs=[i1, close_btn, btn],
                        )
                id_part = 'deforum'
                with gr.Row(elem_id=f"{id_part}_generate_box", variant='compact'):
                    skip = gr.Button('Pause/Resume', elem_id=f"{id_part}_skip", visible=False)
                    interrupt = gr.Button('Interrupt', elem_id=f"{id_part}_interrupt", visible=True)
                    interrupting = gr.Button('Interrupting...', elem_id=f"{id_part}_interrupting", elem_classes="generate-box-interrupting", tooltip="Interrupting generation...")
                    submit = gr.Button('Generate', elem_id=f"{id_part}_generate", variant='primary')

                    skip.click(
                        fn=lambda: state.skip(),
                        inputs=[],
                        outputs=[],
                    )

                    interrupt.click(
                        fn=lambda: state.interrupt(),
                        inputs=[],
                        outputs=[],
                    )

                    interrupting.click(
                        fn=lambda: state.interrupt(),
                        inputs=[],
                        outputs=[],
                    )
                
                # The DeforumationQT bar above Deforum's preview. One half is the ON/OFF switch, green while renders
                # go through DeforumationQT and red while Deforum renders alone; the other half starts DeforumationQT.
                with gr.Row(variant='compact', elem_id='deforumation_switch_row'):
                    deforumation_on = gr.Button("DeforumationQT: ON", elem_id="deforumation_switch_on", visible=deforumation_switch.is_enabled(),
                                                tooltip="DeforumationQT controls the render. Click to render with Deforum's own settings only.")
                    deforumation_off = gr.Button("DeforumationQT: OFF", elem_id="deforumation_switch_off", visible=not deforumation_switch.is_enabled(),
                                                 tooltip="Deforum renders on its own. Click to let DeforumationQT control the render (start DeforumationQT first).")
                    deforumation_sync = gr.Button("DeforumationQT sync", elem_id="deforumation_switch_sync", visible=False)
                    deforumation_open = gr.Button("\U0001F39B Open DeforumationQT", elem_id="deforumation_open",
                                                  tooltip="Start DeforumationQT itself: its mediator and its window. Its folder is remembered in deforumation_switch.json.")
                    for switch_button in (deforumation_on, deforumation_off, deforumation_sync, deforumation_open):
                        switch_button.do_not_save_to_config = True
                deforumation_message = gr.HTML("", elem_id="deforumation_message")

                # Live control, for while a render is running: which Animation mode the next frame uses, and how much
                # of the video init goes into it. Both the bar and DeforumationQT can change them (live_control.py).
                with gr.Row(variant='compact', elem_id='live_control_row'):
                    live_enabled = gr.Checkbox(label="\U0001F3AC Animation mode", value=live_control.is_enabled(), elem_id="live_control_enabled",
                                               tooltip="Change Animation mode and the video init while a render runs. Off: the render follows the project, exactly as before.")
                    live_mode = gr.Radio(list(live_control.MODES), value=live_control.current_mode(), show_label=False, interactive=True,
                                         elem_id="live_control_mode",
                                         tooltip="What the next frame does: the project's own Animation mode, 2D, 3D, or the video init on its own.")
                    live_video = gr.Slider(label="Video %", minimum=0, maximum=100, step=1, value=live_control.current_video_percent(),
                                           elem_id="live_control_video",
                                           tooltip="How much of the video init is mixed into every frame while the mode is 2D or 3D. 100% is the video alone, 0% is the animation alone.")
                    live_fade_frames = gr.Number(value=30, precision=0, show_label=False, elem_id="live_control_fade_frames",
                                                 tooltip="How many frames the fade takes.")
                    live_fade_at = gr.Number(value=0, precision=0, show_label=False, elem_id="live_control_fade_at",
                                             tooltip="Which frame the fade starts on. 0 starts it on the next frame the render draws, so a long render can be set up before it starts.")
                    live_fade_out = gr.Button("\u2935", elem_id="live_control_fade_out",
                                              tooltip="Fade the video init out to 0% over those frames, by itself, from the next frame on.")
                    live_fade_in = gr.Button("\u2934", elem_id="live_control_fade_in",
                                             tooltip="Fade the video init back in to 100% over those frames.")
                    for live_component in (live_enabled, live_mode, live_video, live_fade_frames, live_fade_at, live_fade_out, live_fade_in):
                        live_component.do_not_save_to_config = True
                # filled by javascript/deforumation-switch.js from live_control_status.json while a render runs:
                # with DeforumationQT ON its own selector wins over this row, so the row alone can be misleading
                live_status = gr.HTML("", elem_id="live_control_status")

                # The plan: a whole clip written down before it starts. Each line is a frame and what happens there,
                # and the render works through them as it goes (live_control.py).
                with gr.Accordion("\U0001F4CB Plan", open=False, elem_id="live_control_plan_box"):
                    live_plan = gr.Dataframe(value=live_control.plan_rows(), headers=list(live_control.PLAN_COLUMNS),
                                             datatype=["number", "str", "number", "number"], type="array",
                                             row_count=(1, "dynamic"), col_count=(4, "fixed"), interactive=True,
                                             elem_id="live_control_plan",
                                             label="At that frame: switch to that mode, and send the video init to that share over that many frames (0 jumps straight there). Mode: Deforum, 2D, 3D or Video. The frame can also be a time (1:30) or a point in the song picked in Audio Keyframes (beat 16, part 2).")
                    live_plan.do_not_save_to_config = True
                    live_plan_notes = gr.HTML("", elem_id="live_control_plan_notes")

                def show_deforumation_switch(enabled):
                    return gr.update(visible=enabled), gr.update(visible=not enabled)

                def show_live_control():
                    state = live_control.read_state()
                    return (gr.update(value=state["enabled"]), gr.update(value=state["mode"]),
                            gr.update(value=round(state["video"] * 100)), gr.update(value=live_control.plan_rows()))

                def start_deforumationqt():
                    started, message = deforumation_switch.launch()
                    colour = "var(--primary-500)" if started else "var(--error-text-color, #e74c3c)"
                    return gr.update(value=f"<div class='deforumation-message' style='color:{colour}'>{message}</div>")

                deforumation_on.click(fn=lambda: show_deforumation_switch(deforumation_switch.set_enabled(False)), inputs=[], outputs=[deforumation_on, deforumation_off], queue=False)
                deforumation_off.click(fn=lambda: show_deforumation_switch(deforumation_switch.set_enabled(True)), inputs=[], outputs=[deforumation_on, deforumation_off], queue=False)
                deforumation_open.click(fn=start_deforumationqt, inputs=[], outputs=[deforumation_message], queue=False, show_progress=False)
                # clicked by javascript/deforumation-switch.js after a page reload, because the page is built once at
                # startup: both the switch and the live control row then show what the files really say
                deforumation_sync.click(fn=lambda: show_deforumation_switch(deforumation_switch.is_enabled()) + show_live_control(), inputs=[],
                                        outputs=[deforumation_on, deforumation_off, live_enabled, live_mode, live_video, live_plan], queue=False)

                # .input, not .change, so only a real click or drag writes the file: a value the sync button sets
                # must not bounce back. Every one of these is direct (queue=False), so it lands mid-render as well.
                live_enabled_event = getattr(live_enabled, "input", live_enabled.change)
                live_enabled_event(fn=live_control.set_enabled, inputs=[live_enabled], outputs=[], queue=False, show_progress=False)
                # the plan's first column may be written in music, so the song picked in Audio Keyframes comes with it
                music_names = ("fps", "audio_kf_path", "audio_kf_normalize", "audio_kf_threshold", "audio_kf_parts")
                music_inputs = [components[name] for name in music_names if name in components]
                if len(music_inputs) != len(music_names):
                    music_inputs = []   # Audio Keyframes is not there: frames and times still work

                def write_plan(rows, fps=15, path="", normalize="peak", threshold=0.0, parts_rows=None):
                    plan, notes = live_control.set_plan(rows, fps, path, normalize, threshold, parts_rows)
                    lines = notes or [f"frame {entry['at']}: {live_control.plan_text(entry)}" for entry in plan]
                    text = " \u00b7 ".join(line.replace("&", "&amp;").replace("<", "&lt;") for line in lines)
                    return gr.update(value=f"<div class='live-plan-notes'>{text}</div>" if text else "")

                live_plan_event = getattr(live_plan, "input", live_plan.change)
                live_plan_event(fn=write_plan, inputs=[live_plan] + music_inputs, outputs=[live_plan_notes],
                                queue=False, show_progress=False)
                live_mode_event = getattr(live_mode, "input", live_mode.change)
                live_mode_event(fn=live_control.set_mode, inputs=[live_mode], outputs=[], queue=False, show_progress=False)
                # the fade walks the video share down (or up) on its own, one frame at a time, while the render runs
                live_fade_out.click(fn=lambda frames, at_frame: live_control.start_fade(frames, 0, at_frame),
                                    inputs=[live_fade_frames, live_fade_at], outputs=[], queue=False, show_progress=False)
                live_fade_in.click(fn=lambda frames, at_frame: live_control.start_fade(frames, 100, at_frame),
                                   inputs=[live_fade_frames, live_fade_at], outputs=[], queue=False, show_progress=False)

                for event_name in ("input", "release"):
                    live_video_event = getattr(live_video, event_name, None)
                    if live_video_event is not None:
                        live_video_event(fn=live_control.set_video_percent, inputs=[live_video], outputs=[], queue=False, show_progress=False)

                res = create_output_panel("deforum", opts.outdir_img2img_samples)
                
                #deforum_gallery, generation_info, html_info, _ 

                generation_info = res.generation_info
                html_info= res.html_log
                deforum_gallery = res.gallery

                with gr.Row(variant='compact'):
                    settings_path = gr.Textbox("deforum_settings.txt", elem_id='deforum_settings_path', label="Settings File", info="settings file path can be relative to webui folder OR full - absolute")
                with gr.Row(variant='compact'):
                    save_settings_btn = gr.Button('Save Settings', elem_id='deforum_save_settings_btn')
                    load_settings_btn = gr.Button('Load All Settings', elem_id='deforum_load_settings_btn')
                    load_video_settings_btn = gr.Button('Load Video Settings', elem_id='deforum_load_video_settings_btn')

        component_list = [components[name] for name in get_component_names()]

        submit.click(
                    fn=wrap_gradio_gpu_call(run_deforum),
                    _js="submit_deforum",
                    inputs=[dummy_component, dummy_component] + component_list,
                    outputs=[
                         deforum_gallery,
                         components["resume_timestring"],
                         generation_info,
                         html_info                 
                    ],
                )
        
        settings_component_list = [components[name] for name in get_settings_component_names()]
        video_settings_component_list = [components[name] for name in list(DeforumOutputArgs().keys())]

        save_settings_btn.click(
            fn=wrap_gradio_call(save_settings),
            inputs=[settings_path] + settings_component_list + video_settings_component_list,
            outputs=[],
        )
        
        load_settings_btn.click(
            fn=wrap_gradio_call(lambda *args, **kwargs: load_all_settings(*args, ui_launch=False, **kwargs)),
            inputs=[settings_path] + settings_component_list,
            outputs=settings_component_list,
        )

        load_video_settings_btn.click(
            fn=wrap_gradio_call(load_video_settings),
            inputs=[settings_path] + video_settings_component_list,
            outputs=video_settings_component_list,
        )
        
    # handle persistent settings - load the persistent file upon UI launch
    def trigger_load_general_settings():
        print("Loading general settings...")
        wrapped_fn = wrap_gradio_call(lambda *args, **kwargs: load_all_settings(*args, ui_launch=True, **kwargs))
        inputs = [settings_path.value] + [component.value for component in settings_component_list]
        outputs = settings_component_list
        updated_values = wrapped_fn(*inputs, *outputs)[0]
        settings_component_name_to_obj = {name: component for name, component in zip(get_settings_component_names(), settings_component_list)}
        for key, value in updated_values.items():
            settings_component_name_to_obj[key].value = value['value']
    # actually check persistent setting status
    if opts.data.get("deforum_enable_persistent_settings", False):
        trigger_load_general_settings()
        
    return [(deforum_interface, "Deforum", "deforum_interface")]