"""Register QR controls, LoRA style actions and generation compatibility guards."""
from pathlib import Path
import sys
from fastapi import Body
from modules import script_callbacks

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import forge_workflow as workflow


def started(_demo, app):
    app.add_api_route('/forge-workflow/catalog', workflow.catalog, methods=['GET'])
    app.add_api_route('/forge-workflow/family', lambda title: {'family': workflow.checkpoint_family(title)}, methods=['GET'])
    def save(lora: str = Body(...), name: str = Body(''), weight: float = Body(1), overwrite: bool = Body(False)):
        return workflow.save_lora_style(lora, name, weight, overwrite)
    app.add_api_route('/forge-workflow/lora-style', save, methods=['POST'])
    from modules import processing
    original = processing.process_images
    if getattr(original, '_forge_workflow_guard', False):
        return
    def process(p):
        family = workflow.guard_processing(p)
        result = original(p)
        if family == 'fibo':
            from forge_img2img_hires import native_result
            result = native_result(p, result)
        return result
    process._forge_workflow_guard = True
    processing.process_images = process
    for name in ('modules.img2img', 'modules.txt2img', 'modules.api.api'):
        module = sys.modules.get(name)
        if module and getattr(module, 'process_images', None) is original:
            module.process_images = process


script_callbacks.on_before_ui(workflow.install_qr)
script_callbacks.on_app_started(started)
