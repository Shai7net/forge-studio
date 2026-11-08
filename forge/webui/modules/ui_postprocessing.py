import html
import json
import os

import gradio as gr
from modules import scripts, shared, ui_common, postprocessing, postprocessing_video, call_queue, ui_toprow
import modules.infotext_utils as parameters_copypaste
from modules.ui_components import FormRow, ResizeHandleRow, ToolButton

VIDEO_TAB = 3

video_guide = (
    "<p>Upload a video and choose below what to do with its frames: Hires. fix, an upscaler, face restoration. "
    "<b>Preview a frame</b> renders the one frame at “Preview from second”, <b>Preview</b> a short part from there. "
    "Both are shown with a line to drag across the picture: the original on one side, "
    "the result on the other. After a click on the picture the mouse wheel zooms in, and dragging then moves it. "
    "<b>Generate</b> renders the whole video. The result is an MP4 with the original sound.</p>"
    "<p>RTX Video Super Resolution is made for video and is by far the fastest; "
    "for a video from a phone or the web, its (compressed video) version also cleans away the compression blocks. "
    "R-ESRGAN redraws more and takes a second or more a frame; at ×2, R-ESRGAN 2x+ is about three times faster than 4x+. "
    "DAT is only for a few seconds of video.</p>"
)


class SavedVideo(gr.Video):
    """A gr.Video that plays a file where it is. Gradio's own copies every video into its temp folder, which for
    finished videos means gigabytes that nothing cleans up, and converts a source it thinks a browser cannot
    play (HEVC, which Chrome plays) to H.264 in full before showing it."""

    def get_block_name(self):
        return "video"

    def postprocess(self, y):
        if not y:
            return None
        self.temp_files.add(str(gr.utils.abspath(y)))      # the files Gradio agrees to serve
        # the time in the address, as Forge does for pictures: a new video under an old name would otherwise be
        # played from the browser's cache, and fail
        return {"name": f"{y}?{os.path.getmtime(y)}", "data": None, "is_file": True, "orig_name": os.path.basename(y)}, None


class UploadedVideo(gr.File):
    """A gr.File that hands over the path of the upload itself. Gradio's own reads the whole file again on every
    click, to make and then find a second copy of it: seconds and gigabytes with a long video."""

    def get_block_name(self):
        return "file"

    def preprocess(self, x):
        return x["name"] if x else None


def show_source(video):
    """An uploaded video gets a player, to find the moment for a preview, and a line saying what it is."""
    if not video:
        return gr.update(value=None, visible=False), ""
    return gr.update(value=video, visible=True), postprocessing_video.describe(video)


def video_outputs(path, pictures, line, compare):
    """What the right side of the tab shows after a video ran: the plain player, or for a comparison preview the
    before / after viewer, which javascript/extras_compare.js builds from the data in this html."""
    if not path:
        return gr.update(), gr.update(), pictures, line, ''
    if not compare:
        return gr.update(value=path, visible=True), "", pictures, line, ''
    address = "file=" + path.replace(os.sep, "/") + f"?{os.path.getmtime(path)}"       # the time: see SavedVideo
    data = html.escape(json.dumps({**compare, "video": address}), quote=True)
    return gr.update(visible=False), f"<div class='extras-compare-data' data-compare='{data}'></div>", pictures, line, ''


def run_generate(id_task, tab_index, image, image_batch, input_dir, output_dir, show_results, video, preview_start, preview_length, preview_compare, *args):
    if tab_index != VIDEO_TAB:
        return (gr.update(visible=False), "", *postprocessing.run_postprocessing(tab_index, image, image_batch, input_dir, output_dir, show_results, *args))

    return video_outputs(*postprocessing_video.run(video, *args))


def run_preview(id_task, tab_index, image, image_batch, input_dir, output_dir, show_results, video, preview_start, preview_length, preview_compare, *args):
    return video_outputs(*postprocessing_video.run(video, *args, preview=(preview_start, preview_length, preview_compare)))


def run_preview_frame(id_task, tab_index, image, image_batch, input_dir, output_dir, show_results, video, preview_start, preview_length, preview_compare, *args):
    # no length: one frame, and always beside the original, which is what one frame is rendered for
    return video_outputs(*postprocessing_video.run(video, *args, preview=(preview_start, 0, True)))


def create_ui():
    dummy_component = gr.Label(visible=False)
    tab_index = gr.Number(value=0, visible=False)

    with ResizeHandleRow(equal_height=False, variant='compact'):
        with gr.Column(variant='compact'):
            with gr.Tabs(elem_id="mode_extras"):
                with gr.TabItem('Single Image', id="single_image", elem_id="extras_single_tab") as tab_single:
                    extras_image = gr.Image(label="Source", source="upload", interactive=True, type="pil", elem_id="extras_image")

                with gr.TabItem('Batch Process', id="batch_process", elem_id="extras_batch_process_tab") as tab_batch:
                    image_batch = gr.Files(label="Batch Process", interactive=True, elem_id="extras_image_batch")

                with gr.TabItem('Batch from Directory', id="batch_from_directory", elem_id="extras_batch_directory_tab") as tab_batch_dir:
                    extras_batch_input_dir = gr.Textbox(label="Input directory", **shared.hide_dirs, placeholder="A directory on the same machine where the server is running.", elem_id="extras_batch_input_dir")
                    extras_batch_output_dir = gr.Textbox(label="Output directory", **shared.hide_dirs, placeholder="Leave blank to save images to the default path.", elem_id="extras_batch_output_dir")
                    show_extras_results = gr.Checkbox(label='Show result images', value=True, elem_id="extras_show_extras_results")

                with gr.TabItem('Video', id="video", elem_id="extras_video_tab") as tab_video:
                    # a file upload, not gr.Video: that one keeps the whole video in the page as base64 text and
                    # sends it again with every click, which a phone's 4K video does not survive
                    extras_video = UploadedVideo(label="Source", file_types=["video"], interactive=True, elem_id="extras_video")
                    video_player = SavedVideo(show_label=False, visible=False, interactive=False, elem_id="extras_video_player")
                    video_about = gr.HTML(elem_id="extras_video_about")
                    with FormRow():
                        preview_start = gr.Number(label="Preview from second", value=0, elem_id="extras_video_preview_start")
                        preview_from_player = ToolButton('📍', elem_id="extras_video_preview_from_player", tooltip="Take the moment the source video is standing on")
                        preview_length = gr.Slider(label="Preview length (seconds)", minimum=0.5, maximum=30, step=0.5, value=3, elem_id="extras_video_preview_length")
                    with FormRow():
                        preview_compare = gr.Checkbox(label="Compare with the original", value=True, elem_id="extras_video_preview_compare")
                        preview_frame = gr.Button('Preview a frame', elem_id="extras_video_preview_frame")
                        preview = gr.Button('Preview', elem_id="extras_video_preview")
                    gr.HTML(video_guide, elem_id="extras_video_guide")

            script_inputs = scripts.scripts_postproc.setup_ui()

        with gr.Column():
            toprow = ui_toprow.Toprow(is_compact=True, is_img2img=False, id_part="extras")
            toprow.create_inline_toprow_image()
            submit = toprow.submit

            result_video = SavedVideo(label="Video", visible=False, interactive=False, elem_id="extras_video_result")
            compare_view = gr.HTML(elem_id="extras_video_compare")
            output_panel = ui_common.create_output_panel("extras", shared.opts.outdir_extras_samples)

    tab_single.select(fn=lambda: 0, inputs=[], outputs=[tab_index])
    tab_batch.select(fn=lambda: 1, inputs=[], outputs=[tab_index])
    tab_batch_dir.select(fn=lambda: 2, inputs=[], outputs=[tab_index])
    tab_video.select(fn=lambda: VIDEO_TAB, inputs=[], outputs=[tab_index])

    inputs = [
        dummy_component,
        tab_index,
        extras_image,
        image_batch,
        extras_batch_input_dir,
        extras_batch_output_dir,
        show_extras_results,
        extras_video,
        preview_start,
        preview_length,
        preview_compare,
        *script_inputs
    ]
    # the gallery stays third from the end: submit_extras() empties that slot so old pictures are not sent back
    outputs = [
        result_video,
        compare_view,
        output_panel.gallery,
        output_panel.generation_info,
        output_panel.html_log,
    ]

    submit.click(
        fn=call_queue.wrap_gradio_gpu_call(run_generate, extra_outputs=[gr.update(), gr.update(), None, '']),
        _js="submit_extras",
        inputs=inputs,
        outputs=outputs,
        show_progress=False,
    )

    preview.click(
        fn=call_queue.wrap_gradio_gpu_call(run_preview, extra_outputs=[gr.update(), gr.update(), None, '']),
        _js="submit_extras",
        inputs=inputs,
        outputs=outputs,
        show_progress=False,
    )

    preview_frame.click(
        fn=call_queue.wrap_gradio_gpu_call(run_preview_frame, extra_outputs=[gr.update(), gr.update(), None, '']),
        _js="submit_extras",
        inputs=inputs,
        outputs=outputs,
        show_progress=False,
    )

    extras_video.change(fn=show_source, inputs=[extras_video], outputs=[video_player, video_about], show_progress=False, queue=False)
    preview_from_player.click(fn=None, _js="extras_video_player_time", inputs=[preview_start], outputs=[preview_start])

    parameters_copypaste.add_paste_fields("extras", extras_image, None)

    extras_image.change(
        fn=scripts.scripts_postproc.image_changed,
        inputs=[], outputs=[]
    )
