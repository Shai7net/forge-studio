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

import gradio as gr
import modules.paths as ph
from .general_utils import get_os
from .upscaling import process_ncnn_upscale_vid_upload_logic
from .video_audio_utilities import extract_number, get_quick_vid_info, get_ffmpeg_params
from .frame_interpolation import process_interp_vid_upload_logic, process_interp_pics_upload_logic, gradio_f_interp_get_fps_and_fcount
from .vid2depth import process_depth_vid_upload_logic
from .deforum_mediator import mediator_try_setValues

f_models_path = ph.models_path + '/Deforum'

def change_together(trigger, inputs, rules):
    # One event for all (function, output) rules of a trigger, answered directly instead of through the queue.
    # Each function gets as many input values as it takes, the trigger's value first; a failing rule leaves its field as it is.
    def update_all(*values):
        updates = []
        for fn, _ in rules:
            try:
                updates.append(fn(*values[:fn.__code__.co_argcount]))
            except Exception as e:
                print(f"Deforum UI: {fn.__name__} failed ({e}), its field keeps its state")
                updates.append(gr.update())
        return updates if len(updates) > 1 else updates[0]

    trigger.change(fn=update_all, inputs=inputs, outputs=[output for _, output in rules], queue=False, show_progress=False)

# Picking one of these samplers by hand sets its usual Steps and CFG. They are sent to DeforumationQT too, because while it
# is ON its Sampler Steps slider (and its CFG slider, while its CFG switch is on) decides what the render uses.
SAMPLER_PRESETS = {"LCM": {"steps": 8, "cfg": 1.5}}

def apply_sampler_preset(sampler_name):
    preset = SAMPLER_PRESETS.get(sampler_name)
    if preset is None:
        return gr.update(), gr.update()
    mediator_try_setValues({"steps": preset["steps"], "cfg": preset["cfg"]})
    return gr.update(value=preset["steps"]), gr.update(value=f"0: ({preset['cfg']:g})")

def handle_change_functions(l_vars):
    # Deforum used to register one queued event per field: a 2D/3D switch sent 30 websocket jobs, the page froze redrawing
    # every answer, the queue dropped jobs whose handshake came back late, and Gradio never sent those again (they stayed
    # "pending"), so the Motion tab showed 2D and 3D fields together. Now each trigger shows/hides all its fields in one event.
    def rules(fn, *outputs):
        return [(fn, l_vars[name]) for name in outputs]

    def on_change(trigger, *groups, extra_inputs=()):
        inputs = [l_vars[trigger]] + [l_vars[name] for name in extra_inputs]
        change_together(l_vars[trigger], inputs, [rule for group in groups for rule in group])

    on_change('override_settings_with_file', rules(hide_if_false, 'custom_settings_file'))
    on_change('sampler', rules(show_when_ddim, 'enable_ddim_eta_scheduling'), rules(show_when_ancestral_samplers, 'enable_ancestral_eta_scheduling'))
    # .input fires only for a pick made by hand, so loading settings never overwrites their Steps and CFG
    l_vars['sampler'].input(fn=apply_sampler_preset, inputs=l_vars['sampler'], outputs=[l_vars['steps'], l_vars['cfg_scale_schedule']],
                            queue=False, show_progress=False)
    on_change('enable_ancestral_eta_scheduling', rules(hide_if_false, 'ancestral_eta_schedule'))
    on_change('enable_ddim_eta_scheduling', rules(hide_if_false, 'ddim_eta_schedule'))
    on_change('animation_mode',
              rules(change_max_frames_visibility, 'max_frames'),
              rules(change_diffusion_cadence_visibility, 'diffusion_cadence', 'guided_images_accord', 'optical_flow_cadence_row', 'cadence_flow_factor_schedule',
                    'optical_flow_redo_generation', 'redo_flow_factor_schedule', 'diffusion_redo'),
              rules(disble_3d_related_stuff, 'only_3d_motion_column', 'depth_warp_row_1', 'depth_warp_row_2', 'depth_warp_row_3', 'depth_warp_row_4',
                    'depth_warp_row_5', 'depth_warp_row_6', 'depth_warp_row_7'),
              rules(per_flip_handle, 'per_f_th_row', 'per_f_ph_row', 'per_f_ga_row', 'per_f_f_row'),
              rules(only_show_in_non_3d_mode, 'depth_warp_msg_html'),
              rules(enable_2d_related_stuff, 'only_2d_motion_column'),
              rules(disable_by_interpolation, 'color_force_grayscale', 'noise_tab_column'),
              rules(disable_pers_flip_accord, 'enable_per_f_row', 'both_anim_mode_motion_params_column'),
              rules(show_hybrid_html_msg, 'hybrid_msg_html'),
              rules(change_hybrid_tab_status, 'hybrid_sch_accord', 'hybrid_settings_accord', 'humans_masking_accord'),
              extra_inputs=['enable_perspective_flip'])
    on_change('enable_perspective_flip', rules(hide_if_false, 'per_f_th_row', 'per_f_ph_row', 'per_f_ga_row', 'per_f_f_row'))
    on_change('aspect_ratio_use_old_formula', rules(hide_if_true, 'aspect_ratio_schedule'))
    on_change('optical_flow_redo_generation', rules(hide_if_none, 'redo_flow_factor_schedule_column'))
    on_change('optical_flow_cadence', rules(hide_if_none, 'cadence_flow_factor_schedule_column'))
    on_change('seed_behavior', rules(change_seed_iter_visibility, 'seed_iter_N_row'), rules(change_seed_schedule_visibility, 'seed_schedule_row'))
    on_change('color_coherence', rules(change_color_coherence_video_every_N_frames_visibility, 'color_coherence_video_every_N_frames_row'),
              rules(change_color_coherence_image_path_visibility, 'color_coherence_image_path_row'))
    on_change('noise_type', rules(change_perlin_visibility, 'perlin_row'))
    on_change('diffusion_cadence', rules(hide_optical_flow_cadence, 'optical_flow_cadence_row'))
    on_change('depth_algorithm', rules(legacy_3d_mode, 'midas_weight'), rules(show_leres_html_msg, 'leres_license_msg'))
    on_change('fps', rules(change_gif_button_visibility, 'make_gif'))
    # These read videos or take the trigger as a later input, so they stay separate queued events
    l_vars['r_upscale_model'].change(fn=update_r_upscale_factor, inputs=l_vars['r_upscale_model'], outputs=l_vars['r_upscale_factor'])
    l_vars['ncnn_upscale_model'].change(fn=update_r_upscale_factor, inputs=l_vars['ncnn_upscale_model'], outputs=l_vars['ncnn_upscale_factor'])
    l_vars['ncnn_upscale_model'].change(update_upscale_out_res_by_model_name, inputs=[l_vars['ncnn_upscale_in_vid_res'], l_vars['ncnn_upscale_model']],
                                          outputs=l_vars['ncnn_upscale_out_vid_res'])
    l_vars['ncnn_upscale_factor'].change(update_upscale_out_res, inputs=[l_vars['ncnn_upscale_in_vid_res'], l_vars['ncnn_upscale_factor']], outputs=l_vars['ncnn_upscale_out_vid_res'])
    l_vars['vid_to_upscale_chosen_file'].change(vid_upscale_gradio_update_stats, inputs=[l_vars['vid_to_upscale_chosen_file'], l_vars['ncnn_upscale_factor']],
                                                  outputs=[l_vars['ncnn_upscale_in_vid_fps_ui_window'], l_vars['ncnn_upscale_in_vid_frame_count_window'], l_vars['ncnn_upscale_in_vid_res'],
                                                           l_vars['ncnn_upscale_out_vid_res']])
    on_change('hybrid_comp_mask_type', rules(hide_if_none, 'hybrid_comp_mask_row', 'hybrid_comp_mask_blend_alpha_schedule_row', 'hybrid_comp_mask_contrast_schedule_row',
                                             'hybrid_comp_mask_auto_contrast_cutoff_high_schedule_row', 'hybrid_comp_mask_auto_contrast_cutoff_low_schedule_row'))
    on_change('hybrid_motion', rules(disable_by_non_optical_flow, 'hybrid_flow_method', 'hybrid_flow_factor_schedule', 'hybrid_flow_consistency', 'hybrid_consistency_blur',
                                     'hybrid_motion_use_prev_img'))
    on_change('hybrid_flow_consistency', rules(hide_if_false, 'hybrid_consistency_blur'))
    on_change('hybrid_composite', rules(disable_by_hybrid_composite_dynamic, 'hybrid_comp_mask_row'),
              rules(hide_if_false, 'humans_masking_accord', 'hybrid_sch_accord', 'hybrid_comp_mask_type', 'hybrid_use_first_frame_as_init_image', 'hybrid_use_init_image'),
              extra_inputs=['hybrid_comp_mask_type'])
    # End of hybrid related
    on_change('skip_video_creation', rules(change_visibility_from_skip_video, 'fps_out_format_row', 'soundtrack_row', 'store_frames_in_ram', 'make_gif', 'r_upscale_row',
                                           'delete_imgs', 'delete_input_frames'))
    on_change('frame_interpolation_slow_mo_enabled', rules(hide_if_false, 'frame_interp_slow_mo_amount_column'))
    on_change('frame_interpolation_engine', rules(change_interp_x_max_limit, 'frame_interpolation_x_amount'),
              rules(hide_interp_by_interp_status, 'frame_interpolation_slow_mo_enabled', 'frame_interpolation_keep_imgs', 'frame_interpolation_use_upscaled',
                    'frame_interp_amounts_row', 'interp_existing_video_row'),
              extra_inputs=['frame_interpolation_x_amount'])
    # Populate the FPS and FCount values as soon as a video is uploaded to the FileUploadBox (vid_to_interpolate_chosen_file)
    l_vars['vid_to_interpolate_chosen_file'].change(gradio_f_interp_get_fps_and_fcount,
                                                      inputs=[l_vars['vid_to_interpolate_chosen_file'], l_vars['frame_interpolation_x_amount'], l_vars['frame_interpolation_slow_mo_enabled'],
                                                              l_vars['frame_interpolation_slow_mo_amount']],
                                                      outputs=[l_vars['in_vid_fps_ui_window'], l_vars['in_vid_frame_count_window'], l_vars['out_interp_vid_estimated_fps']])
    l_vars['vid_to_interpolate_chosen_file'].change(fn=hide_interp_stats, inputs=[l_vars['vid_to_interpolate_chosen_file']], outputs=[l_vars['interp_live_stats_row']])

# START gradio-to-frame-interoplation/ upscaling functions
def upload_vid_to_interpolate(file, engine, x_am, sl_enabled, sl_am, keep_imgs, in_vid_fps):
    # print msg and do nothing if vid not uploaded or interp_x not provided
    if not file or engine == 'None':
        return print("Please upload a video and set a proper value for 'Interp X'. Can't interpolate x0 times :)")
    f_location, f_crf, f_preset = get_ffmpeg_params()

    process_interp_vid_upload_logic(file, engine, x_am, sl_enabled, sl_am, keep_imgs, f_location, f_crf, f_preset, in_vid_fps, f_models_path, file.orig_name)

def upload_pics_to_interpolate(pic_list, engine, x_am, sl_enabled, sl_am, keep_imgs, fps, add_audio, audio_track):
    from PIL import Image

    if pic_list is None or len(pic_list) < 2:
        return print("Please upload at least 2 pics for interpolation.")
    f_location, f_crf, f_preset = get_ffmpeg_params()
    # make sure all uploaded pics have the same resolution
    pic_sizes = [Image.open(picture_path.name).size for picture_path in pic_list]
    if len(set(pic_sizes)) != 1:
        return print("All uploaded pics need to be of the same Width and Height / resolution.")

    resolution = pic_sizes[0]

    process_interp_pics_upload_logic(pic_list, engine, x_am, sl_enabled, sl_am, keep_imgs, f_location, f_crf, f_preset, fps, f_models_path, resolution, add_audio, audio_track)

def ncnn_upload_vid_to_upscale(vid_path, in_vid_fps, in_vid_res, out_vid_res, upscale_model, upscale_factor, keep_imgs):
    if vid_path is None:
        print("Please upload a video :)")
        return
    f_location, f_crf, f_preset = get_ffmpeg_params()
    current_user = get_os()
    process_ncnn_upscale_vid_upload_logic(vid_path, in_vid_fps, in_vid_res, out_vid_res, f_models_path, upscale_model, upscale_factor, keep_imgs, f_location, f_crf, f_preset, current_user)

def upload_vid_to_depth(vid_to_depth_chosen_file, mode, thresholding, threshold_value, threshold_value_max, adapt_block_size, adapt_c, invert, end_blur, midas_weight_vid2depth, depth_keep_imgs):
    # print msg and do nothing if vid not uploaded
    if not vid_to_depth_chosen_file:
        return print("Please upload a video :()")
    f_location, f_crf, f_preset = get_ffmpeg_params()

    process_depth_vid_upload_logic(vid_to_depth_chosen_file, mode, thresholding, threshold_value, threshold_value_max, adapt_block_size, adapt_c, invert, end_blur, midas_weight_vid2depth,
                                   vid_to_depth_chosen_file.orig_name, depth_keep_imgs, f_location, f_crf, f_preset, f_models_path)

# END gradio-to-frame-interoplation/ upscaling functions

def change_visibility_from_skip_video(choice):
    return gr.update(visible=False) if choice else gr.update(visible=True)

def update_r_upscale_factor(choice):
    return gr.update(value='x4', choices=['x4']) if choice != 'realesr-animevideov3' else gr.update(value='x2', choices=['x2', 'x3', 'x4'])

def change_perlin_visibility(choice):
    return gr.update(visible=choice == "perlin")

def legacy_3d_mode(choice):
    return gr.update(visible=choice.lower() in ["midas+adabins (old)", 'zoe+adabins (old)'])

def change_color_coherence_image_path_visibility(choice):
    return gr.update(visible=choice == "Image")

def change_color_coherence_video_every_N_frames_visibility(choice):
    return gr.update(visible=choice == "Video Input")

def change_seed_iter_visibility(choice):
    return gr.update(visible=choice == "iter")

def change_seed_schedule_visibility(choice):
    return gr.update(visible=choice == "schedule")

def disable_pers_flip_accord(choice):
    return gr.update(visible=True) if choice in ['2D', '3D'] else gr.update(visible=False)

def per_flip_handle(anim_mode, per_f_enabled):
    if anim_mode in ['2D', '3D'] and per_f_enabled:
        return gr.update(visible=True)
    return gr.update(visible=False)

def change_max_frames_visibility(choice):
    return gr.update(visible=choice != "Video Input")

def change_diffusion_cadence_visibility(choice):
    return gr.update(visible=choice not in ['Video Input', 'Interpolation'])

def disble_3d_related_stuff(choice):
    return gr.update(visible=False) if choice != '3D' else gr.update(visible=True)

def only_show_in_non_3d_mode(choice):
    return gr.update(visible=False) if choice == '3D' else gr.update(visible=True)

def enable_2d_related_stuff(choice):
    return gr.update(visible=True) if choice == '2D' else gr.update(visible=False)

def disable_by_interpolation(choice):
    return gr.update(visible=False) if choice in ['Interpolation'] else gr.update(visible=True)

def disable_by_video_input(choice):
    return gr.update(visible=False) if choice in ['Video Input'] else gr.update(visible=True)

def hide_if_none(choice):
    return gr.update(visible=choice != "None")

def change_gif_button_visibility(choice):
    if choice is None or choice == "":
        return gr.update(visible=True)
    return gr.update(visible=False, value=False) if int(choice) > 30 else gr.update(visible=True)

def hide_if_false(choice):
    return gr.update(visible=True) if choice else gr.update(visible=False)

def hide_if_true(choice):
    return gr.update(visible=False) if choice else gr.update(visible=True)

def disable_by_hybrid_composite_dynamic(choice, comp_mask_type):
    if choice in ['Normal', 'Before Motion', 'After Generation']:
        if comp_mask_type != 'None':
            return gr.update(visible=True)
    return gr.update(visible=False)

def disable_by_non_optical_flow(choice):
    return gr.update(visible=False) if choice != 'Optical Flow' else gr.update(visible=True)

# Upscaling Gradio UI related funcs
def vid_upscale_gradio_update_stats(vid_path, upscale_factor):
    if not vid_path:
        return '---', '---', '---', '---'
    factor = extract_number(upscale_factor)
    fps, fcount, resolution = get_quick_vid_info(vid_path.name)
    in_res_str = f"{resolution[0]}*{resolution[1]}"
    out_res_str = f"{resolution[0] * factor}*{resolution[1] * factor}"
    return fps, fcount, in_res_str, out_res_str

def update_upscale_out_res(in_res, upscale_factor):
    if not in_res:
        return '---'
    factor = extract_number(upscale_factor)
    w, h = [int(x) * factor for x in in_res.split('*')]
    return f"{w}*{h}"

def update_upscale_out_res_by_model_name(in_res, upscale_model_name):
    if not upscale_model_name or in_res == '---':
        return '---'
    factor = 2 if upscale_model_name == 'realesr-animevideov3' else 4
    return f"{int(in_res.split('*')[0]) * factor}*{int(in_res.split('*')[1]) * factor}"

def hide_optical_flow_cadence(cadence_value):
    return gr.update(visible=True) if cadence_value > 1 else gr.update(visible=False)

def hide_interp_by_interp_status(choice):
    return gr.update(visible=False) if choice == 'None' else gr.update(visible=True)

def change_interp_x_max_limit(engine_name, current_value):
    if engine_name == 'FILM':
        return gr.update(maximum=300)
    elif current_value > 10:
        return gr.update(maximum=10, value=2)
    return gr.update(maximum=10)

def hide_interp_stats(choice):
    return gr.update(visible=True) if choice is not None else gr.update(visible=False)

def show_hybrid_html_msg(choice):
    return gr.update(visible=True) if choice not in ['2D', '3D'] else gr.update(visible=False)

def change_hybrid_tab_status(choice):
    return gr.update(visible=True) if choice in ['2D', '3D'] else gr.update(visible=False)

def show_leres_html_msg(choice):
    return gr.update(visible=True) if choice.lower() == 'leres' else gr.update(visible=False)

def show_when_ddim(sampler_name):
    return gr.update(visible=True) if sampler_name.lower() == 'ddim' else gr.update(visible=False)

def show_when_ancestral_samplers(sampler_name):
    return gr.update(visible=True) if sampler_name.lower() in ['euler a', 'dpm++ 2s a', 'dpm2 a', 'dpm2 a karras', 'dpm++ 2s a karras'] else gr.update(visible=False)

def change_css(checkbox_status):
    if checkbox_status:
        display = "block"
    else:
        display = "none"

    html_template = f'''
        <style>
            #tab_deforum_interface .svelte-e8n7p6, #f_interp_accord {{
                display: {display} !important;
            }}
        </style>
        '''
    return html_template
