"""Music for a stretch of the timeline, made by ACE-Step 1.5 (turbo, no language model) in its own Python.

usage: <ACE-Step venv python> music_make.py request.json
request: {"home": ACE-Step folder, "caption": str, "seconds": float, "seed": int, "out": wav path,
          "work": scratch folder, "instrumental": bool, "lyrics": str}
Prints PROGRESS <0..1> lines, then DONE <path> or FAILED <why>.
The "thinking" language model is left out: it stops at an Enter prompt unless an instruction.txt exists, and on
this card one take with it costs more than the take itself.
"""

import json
import os
import shutil
import sys


def main():
    with open(sys.argv[1], encoding="utf-8") as f:
        req = json.load(f)
    home = req["home"]
    sys.path.insert(0, home)
    os.chdir(req["work"])
    print("PROGRESS 0.05", flush=True)
    from acestep.handler import AceStepHandler
    from acestep.inference import GenerationConfig, GenerationParams, generate_music
    from acestep.llm_inference import LLMHandler

    dit = AceStepHandler()
    message, ok = dit.initialize_service(project_root=home, config_path="acestep-v15-turbo", device="cuda",
                                         use_flash_attention=False, compile_model=False, offload_to_cpu=True)
    if ok is False:
        print("FAILED " + str(message)[:300], flush=True)
        return 1
    print("PROGRESS 0.3", flush=True)
    params = GenerationParams(caption=req["caption"], lyrics=req.get("lyrics") or "[Instrumental]",
                              instrumental=bool(req.get("instrumental", True)), duration=float(req["seconds"]),
                              inference_steps=8, seed=int(req.get("seed", -1)), thinking=False,
                              use_cot_metas=False, use_cot_caption=False, use_cot_language=False)
    config = GenerationConfig(batch_size=1, use_random_seed=int(req.get("seed", -1)) < 0,
                              seeds=None if int(req.get("seed", -1)) < 0 else [int(req["seed"])], audio_format="wav")
    result = generate_music(dit, LLMHandler(), params, config, save_dir=req["work"])
    made = [a.get("path") for a in (getattr(result, "audios", None) or []) if a.get("path")]
    if not made or not os.path.isfile(made[0]):
        print("FAILED " + str(getattr(result, "error", None) or getattr(result, "status_message", "") or "no audio")[:300], flush=True)
        return 1
    shutil.copyfile(made[0], req["out"])
    print("PROGRESS 1", flush=True)
    print("DONE " + req["out"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
