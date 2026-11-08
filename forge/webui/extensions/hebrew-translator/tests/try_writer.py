"""Writes prompts for a few ideas and every target, outside Forge (the GPU must be free).

  venv\\Scripts\\python.exe extensions\\hebrew-translator\\tests\\try_writer.py [--targets sd15,flux] [--memory file]
      [--engine gguf|qwen] [--gguf other.gguf] [--ideas "...|..."] [--temperature 0.8]
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EXT = os.path.dirname(HERE)
sys.path.insert(0, EXT)

import torch  # noqa: E402

import prompt_writer  # noqa: E402
from prompt_writer import llama as pllama, memory as pmem, model as pmodel, styles  # noqa: E402

IDEAS = ["נחש מצייר ציור", "חתול אסטרונאוט צף בחלל, בסגנון אנימה", "סבתא מרוקאית מבשלת קוסקוס במטבח קטן, יום שישי",
         "רחוב בתל אביב אחרי גשם בלילה, אורות ניאון משתקפים בשלוליות"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", default="sd15,sdxl,flux,wan_t2v")
    ap.add_argument("--ideas", default="")
    ap.add_argument("--memory", default="")
    ap.add_argument("--creativity", default="Rich")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--out", default="")
    ap.add_argument("--engine", default="gguf", choices=["gguf", "qwen"])
    ap.add_argument("--gguf", default="", help="a .gguf in models/LLM (default: the writer's own choice)")
    args = ap.parse_args()
    models_dir = os.path.join(os.path.dirname(os.path.dirname(EXT)), "models")
    if args.engine == "gguf":
        path = os.path.join(models_dir, "LLM", args.gguf) if args.gguf else pllama.find_model(models_dir)
        lw = pllama.LlamaWriter(pllama.find_server(), path, os.path.join(HERE, "llama-server.log"))
        lw.start()

        class W:
            @staticmethod
            def write(device, system, user, turns, temperature, seed):
                return lw.chat(system, user, turns, temperature, seed)
        writer = W()
    else:
        writer = pmodel.Writer(*pmodel.find_files(models_dir))
    mem = pmem.Memory(args.memory) if args.memory else None
    ideas = args.ideas.split("|") if args.ideas else IDEAS
    results = []
    for target in args.targets.split(","):
        for idea in ideas:
            system, turns, user, tags = prompt_writer.compose(idea, target, mem, args.creativity)
            t0 = time.time()
            answer = writer.write(torch.device("cuda"), system, user, turns, args.temperature, seed=7)
            prompt = styles.tidy(answer, target, tags)
            meaning = styles.parse(answer)[0]
            dt = time.time() - t0
            print(f"\n[{target}] {idea}  ({dt:.1f} s, {len(prompt.split())} words)\nMEANING {meaning}\n{prompt}",
                  flush=True)
            results.append({"target": target, "idea": idea, "meaning": meaning, "prompt": prompt,
                            "seconds": round(dt, 1)})
    if args.engine == "gguf":
        lw.stop()
    else:
        print(f"\npeak card memory {torch.cuda.max_memory_allocated() / 2**30:.2f} GB")
    if args.out:
        with open(args.out, "w", encoding="utf8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
