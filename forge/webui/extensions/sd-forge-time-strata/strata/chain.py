"""A source made into a chain: every frame cut out, cleaned, aligned and written as the page will show it.

Two passes, so that the frames can be aligned again (another ground line, another size) without cutting
them out again: the first cuts every frame and keeps its alpha and its measures, the second places every
frame in the chain's frame and writes it.
"""

import itertools
import os
import time

import cv2
import numpy as np
from PIL import Image

from . import cut, jobs, media, store, upscale

DEFAULTS = {
    "start": 0.0, "end": None, "fps": None, "max_frames": 600, "max_side": 1280,
    "matte": "auto", "model": None, "key_colour": None, "key_near": 9.0, "key_far": 26.0, "crop": "auto",
    "low": 0.06, "high": 0.94, "keep_parts": 0.03, "holes": 0.0006, "prior_reach": 0.03,
    "align": "centre", "scale": "steady", "baseline": 0.95, "figure": 0.84, "smooth": 3.0, "ground": 12,
    "height": 768, "width": None,
    "upscale": None,        # Real-ESRGAN before the cut, as "model:scale" (see upscale.CHOICES), for small footage
}


def _options(params):
    options = dict(DEFAULTS)
    options.update({k: v for k, v in (params.get("options") or {}).items() if k in DEFAULTS and v is not None})
    return options


def _frames(project, source, options):
    """(count, a function that yields the source frames again)"""
    kind = source.get("kind")
    if kind == "video":
        path = store.resolve(project, source["path"])
        total = media.count_video(path, options["start"], options["end"], options["fps"])
        count = min(total, int(options["max_frames"] or total))
        # black bars around the picture would be taken for its backdrop (an upright video of a wide film)
        crop = media.find_letterbox(path, options["start"], options["end"]) if options.get("crop", "auto") == "auto" else None

        def again():
            for n, frame in enumerate(media.read_video(path, options["start"], options["end"], options["fps"], options["max_side"], crop)):
                if n >= count:
                    break
                yield frame
        return count, again, media.probe(path)["fps"] if not options["fps"] else float(options["fps"])
    if kind in ("pictures", "zip"):
        folder = os.path.join(store.project_dir(project), source["path"])
        if kind == "zip":
            target = folder + "-pictures"
            pictures = media.list_pictures(target) if os.path.isdir(target) else media.unpack_zip(store.resolve(project, source["path"]), target)
        else:
            pictures = media.list_pictures(folder)
        if not pictures:
            raise jobs.JobError("no pictures were found in what was given")
        pictures = pictures[:int(options["max_frames"] or len(pictures))]
        return len(pictures), (lambda: media.read_pictures(pictures, options["max_side"])), float(source.get("fps") or 24)
    raise jobs.JobError(f"unknown source: {kind}")


LOOK_AHEAD = 48         # frames the background is judged on (two seconds): a first frame alone may be a black fade-in


def _decide(frames, options, video=False):
    """How the background is taken away, decided on the first frames when the user left it to us: the frames'
    own transparency, a flat backdrop's colour, or the best picture model here. A flat backdrop must be flat in
    every frame that shows something (a dark fade-in looked like one and the whole picture was kept). The video
    model (RVM) is ten times faster but only on request: on a white dress before a white curtain it left holes
    in the dress and kept pieces of the curtain, where BiRefNet-lite cut it clean, with less fog and less flicker
    (measured 2026-10-18)."""
    frames = frames if isinstance(frames, (list, tuple)) else [frames]
    first = frames[0]
    matte = options["matte"]
    seen = [(cut.border_colour(f), float(f[..., :3].mean())) for f in frames[::4]]
    lit = [(colour, spread) for (colour, spread), bright in seen if bright > 14] or [seen[0][0]]
    colour, spread = max(lit, key=lambda pair: pair[1])       # the busiest border of the frames that show something
    if matte == "auto":
        if cut.has_alpha(first):
            matte = "alpha"
        elif spread < 4.5:
            matte = "key"
        else:
            matte = "model"
    if matte in ("key", "keymodel") and options["key_colour"]:
        text = str(options["key_colour"]).lstrip("#")
        colour = np.array([int(text[i:i + 2], 16) for i in (0, 2, 4)], np.float32)
    if matte == "rvm":
        model = options["model"] if options["model"] in cut.RVM_FILES else "rvm"
    elif matte in ("model", "keymodel"):
        model = options["model"] if options["model"] in cut.REMBG_MODELS else cut.best_ready_model()
    else:
        model = None
    return {"matte": matte, "key_colour": [float(c) for c in colour], "border_spread": round(spread, 2), "model": model}


def _alpha_of(frame, decided, options, prior=None, matter=None):
    matte = decided["matte"]
    if matte == "alpha":
        raw = frame[..., 3].astype(np.float32) / 255.0
    elif matte == "key":
        raw = cut.key_matte(frame, decided["key_colour"], options["key_near"], options["key_far"])
    elif matte == "keymodel":
        # a figure drawn on a known flat backdrop: the key says what differs from the backdrop, the model what is
        # a figure; together they keep a see-through figure (water, mist) whole where the key alone left holes
        raw = np.maximum(cut.key_matte(frame, decided["key_colour"], options["key_near"], options["key_far"]),
                         cut.model_matte(frame, decided["model"]))
    elif matte == "rvm":
        raw = matter(frame)
    else:
        raw = cut.model_matte(frame, decided["model"])
    if prior is not None:
        # a frame drawn over a known figure (Deforum, from a motion chain) keeps that figure's body solid where
        # the key is unsure (clothes close to the backdrop's colour), but not where the drawing shows the backdrop
        # itself: a dress drawn with a slit where the filmed one was whole would otherwise keep a grey patch
        # (60 of 171 frames of the first lifetime chain did)
        core = cv2.erode((prior >= 0.5).astype(np.uint8), cut._disk(0.02 * prior.shape[0])).astype(np.float32)
        raw = np.where(raw > 0.02, np.maximum(raw, core), raw)
    return cut.clean_alpha(raw, options["low"], options["high"], options["keep_parts"], options["holes"], prior=prior,
                           prior_reach=options["prior_reach"])


def _save_alpha(path, alpha):
    if not media.imwrite(path, (np.clip(alpha, 0, 1) * 255).round().astype(np.uint8)):
        raise jobs.JobError(f"the cut of a frame could not be saved: {path}")


def _load_alpha(path):
    image = media.imread(path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise jobs.JobError(f"the cut of a frame is missing: {os.path.basename(path)}")
    return image.astype(np.float32) / 255.0


def _cut_pass(job, project, chain_id, count, frames, options, priors=None, video=False):
    folder = os.path.join(store.chain_dir(project, chain_id, create=True), "alpha")
    os.makedirs(folder, exist_ok=True)
    for name in os.listdir(folder):
        os.remove(os.path.join(folder, name))
    override = None
    try:
        for attempt in range(3):
            try:
                measures, decided = _cut_frames(job, folder, count, frames, options, priors, video, [], time.time(), override)
                # (frames drawn over a known figure need no steadying from their neighbours: the figure they were
                # drawn over already says where it is, and drawn frames far apart in time are not alike)
                return (measures if priors else _steady_suspects(job, folder, measures)), decided
            except cut.NoRoom as e:
                # never a chain cut half with one model and half with another: start again from its first frame
                cut.release_models()
                cut.free_graphics_memory()
                if attempt == 0:
                    job.step(f"not enough graphics memory ({e}): freeing it and starting again", 0.03)
                    continue
                fallback = "rvm" if video and cut.rvm_ready("rvm") else "u2net"
                job.step(f"not enough graphics memory ({e}): cutting with {fallback} instead", 0.03)
                override = {"matte": "rvm" if fallback == "rvm" else "model", "model": fallback,
                            "fallback": f"{e}; cut with {fallback} instead"}
        raise jobs.JobError("the graphics card has too little free memory for any cut-out model")
    finally:
        # a cut-out model holds up to 4-6 GB of the graphics card (BiRefNet); Forge needs it back for drawing
        cut.release_models()


def _cut_frames(job, folder, count, frames, options, priors, video, measures, started, override=None):
    decided, matter = None, None
    source = iter(frames())
    ahead = list(itertools.islice(source, LOOK_AHEAD))
    for n, frame in enumerate(itertools.chain(ahead, source)):
        if decided is None:
            decided = _decide(ahead, options, video)
            ahead = None
            if override and decided["matte"] == "keymodel":
                # (the backdrop's key stays; only the model is a smaller one)
                decided.update(model="u2net", fallback=override["fallback"].replace("rvm", "u2net"))
            elif override:
                decided.update(override)
            if decided["matte"] in ("rvm", "model", "keymodel"):
                cut.free_graphics_memory()
            if decided["matte"] == "rvm":
                matter = cut.VideoMatter(decided["model"])
            elif decided["matte"] in ("model", "keymodel"):
                cut.prepare_model(decided["model"], job.step)
            job.step(f"cutting out with {decided['model'] or decided['matte']}", 0.04)
        prior = priors(n, frame.shape[:2]) if priors else None
        alpha = _alpha_of(frame, decided, options, prior, matter)
        _save_alpha(os.path.join(folder, f"{n:06d}.png"), alpha)
        measures.append(cut.measure(alpha))
        preview = None
        if n % 8 == 0:
            preview = jobs.preview_of(Image.fromarray(np.dstack([frame[..., :3], (alpha * 255).astype(np.uint8)]), "RGBA"))
        left = (time.time() - started) / (n + 1) * (count - n - 1)
        job.step(f"cutting out frame {n + 1} of {count} (about {int(left)} s left)", 0.05 + 0.6 * (n + 1) / count, preview)
    if not measures:
        raise jobs.JobError("the source gave no frames")
    return measures, decided


def _steady_suspects(job, folder, measures, reach=0.04, around=3):
    """A frame whose cut jumps from its neighbours (cut.suspects) usually kept a piece of the background: a lit
    pillar beside the figure, a sign. In a stack one such frame flashes as a sliver, and its height shrank the
    whole chain (his disco clip, 2026-10-20). Such a frame keeps only what lies near where the figure is in the
    good frames around it (`around` on each side), `reach` of the height further at most; then it is measured
    again. A real movement (an arm swung out) is covered by the neighbours; something that is there for one or
    two frames only is not."""
    bad = set(cut.suspects(measures))
    if not bad:
        return measures
    count = len(measures)
    cleaned = 0
    for k in sorted(bad):
        if measures[k].get("empty"):
            continue
        neighbours = [j for j in range(k - around, k + around + 1)
                      if 0 <= j < count and j != k and j not in bad and not measures[j].get("empty")]
        if not neighbours:
            continue
        union = None
        for j in neighbours:
            solid = _load_alpha(os.path.join(folder, f"{j:06d}.png")) >= 0.5
            union = solid if union is None else union | solid
        alpha = _load_alpha(os.path.join(folder, f"{k:06d}.png"))
        h = alpha.shape[0]
        allowed = cv2.dilate(union.astype(np.uint8), cut._disk(reach * h)).astype(np.float32)
        allowed = cv2.GaussianBlur(allowed, (0, 0), max(1.0, reach / 4 * h))
        steadied = alpha * np.clip(allowed * 2.0, 0.0, 1.0)
        steadied[steadied < 1.5 / 255] = 0.0
        _save_alpha(os.path.join(folder, f"{k:06d}.png"), steadied)
        measures[k] = cut.measure(steadied)
        cleaned += 1
    job.step(f"steadied {cleaned} frames whose cut jumped", 0.66)
    return measures


def _place_pass(job, project, chain_id, frames, measures, decided, options, drawn=False):
    """Places every frame. Returns the make, its size, the frames that look wrongly cut and those with no figure,
    the plan and the figure's height. `drawn`: the frames were drawn on a flat backdrop (Deforum), so a cut is
    judged by how it follows the drawing; a drawing is free to be larger than its neighbours (water, a cape)."""
    folder = store.chain_dir(project, chain_id)
    bad = cut.suspects(measures)        # (not believed when placing)
    plan, width = cut.plan_alignment(measures, int(options["height"]), options["align"], options["baseline"], options["figure"],
                                     smooth=options["smooth"], ground=options["ground"], scale=options["scale"],
                                     out_w=options["width"], bad=bad)
    height = int(options["height"])
    make, out_folder = store.next_make(project, chain_id)
    background = decided["key_colour"] if decided["matte"] in ("key", "keymodel") else None
    count = len(measures)
    figure_heights = []
    agreements = []
    for n, frame in enumerate(frames()):
        if n >= count:
            break
        alpha = _load_alpha(os.path.join(folder, "alpha", f"{n:06d}.png"))
        if drawn and background is not None:
            agreements.append(cut.agreement(frame, alpha, background))
        placed = cut.render_frame(frame, alpha, plan[n], width, height, background, straight=decided["matte"] == "alpha")
        Image.fromarray(placed, "RGBA").save(os.path.join(out_folder, f"{n:06d}.webp"), "WEBP", quality=90, alpha_quality=100, method=2)
        if not measures[n].get("empty"):
            s, _dx, dy = plan[n]
            figure_heights.append(s * (measures[n]["feet_y"] - measures[n]["top"]))
        preview = jobs.preview_of(Image.fromarray(placed, "RGBA")) if n % 8 == 0 else None
        job.step(f"placing frame {n + 1} of {count}", 0.65 + 0.35 * (n + 1) / count, preview)
    store.drop_old_makes(project, chain_id, make)
    empty = cut.figureless(measures)
    wrong = cut.disagreeing(agreements, empty) if agreements else [k for k in bad if k not in set(empty)]
    return make, width, height, wrong, empty, plan, float(np.median(figure_heights)) if figure_heights else height * options["figure"]


def build(job, project, chain_id, source, options, realign=False, priors=None, extra=None):
    """Cuts a source out (unless `realign`) and places its frames; writes chain.json; returns what the page
    keeps of the chain. `priors(n, shape)`: where the figure of frame n is known to be (or None)."""
    earlier = store.read_chain(project, chain_id) if realign else None
    if realign:
        if not earlier:
            raise jobs.JobError("this chain has not been cut out yet")
        kept = dict(earlier.get("options") or {})
        kept.update({k: v for k, v in options.items() if v is not None})
        kept["upscale"] = (earlier.get("options") or {}).get("upscale")     # (the cuts were measured on those frames)
        options = _options({"options": kept})
    job.step("reading the source", 0.01)
    count, frames, fps = _frames(project, source, options)
    if options.get("upscale"):
        # made larger once with Real-ESRGAN and kept beside the chain, so placing again reads the same frames
        frames = upscale.upscaled_frames(job, os.path.join(store.chain_dir(project, chain_id, create=True), "upscale"),
                                         count, frames, options["upscale"], (0.01, 0.04 if realign else 0.2))
    if realign:
        measures, decided = earlier["measures"], earlier["method"]
        if len(measures) != count:
            raise jobs.JobError("the source has changed since it was cut: cut it out again")
    else:
        measures, decided = _cut_pass(job, project, chain_id, count, frames, options, priors, video=source.get("kind") == "video")
    if extra is None and earlier:
        extra = earlier.get("extra")            # what made the chain (Deforum's settings) outlives a new placing
    drawn = bool(priors) or bool((extra or {}).get("deforum"))
    make, width, height, bad, empty, plan, figure_px = _place_pass(job, project, chain_id, frames, measures, decided, options, drawn)
    record = {"source": source, "options": options, "method": decided, "frames": len(measures), "fps": fps,
              "width": width, "height": height, "make": make, "suspects": bad, "figureless": empty, "measures": measures,
              "plan": [[round(v, 3) for v in p] for p in plan], "figure_px": figure_px, "made": time.time()}
    if extra:
        record["extra"] = extra
    store.write_chain(project, chain_id, record)
    return {"chain": chain_id, "make": make, "frames": len(measures), "fps": fps, "width": width, "height": height,
            "suspects": bad, "figureless": empty, "method": decided, "figure": figure_px / height, "baseline": options["baseline"]}


def _known_figure(project, chain_id):
    """The motion a drawn chain was drawn over (its prior folder), as build()'s priors - or None."""
    folder = os.path.join(store.chain_dir(project, chain_id), "prior")
    if not os.path.isdir(folder):
        return None

    def priors(n, shape):
        path = os.path.join(folder, f"{n:06d}.png")
        if not os.path.isfile(path):
            return None
        prior = np.asarray(Image.open(path).convert("L"), np.float32) / 255.0
        if prior.shape != tuple(shape):
            prior = cv2.resize(prior, (shape[1], shape[0]), interpolation=cv2.INTER_LINEAR)
        return prior
    return priors


@jobs.handler("sharpen")
def sharpen(job):
    """A chain cut again from its own source with Real-ESRGAN first, at a taller frame (a drawn chain keeps its
    backdrop key and the motion it was drawn over). params: project, chain, upscale ("model:scale"), height"""
    params = job.params
    project = store.clean_name(params["project"])
    chain_id = store.clean_id(params["chain"], "chain id")
    record = store.read_chain(project, chain_id)
    if not record or not record.get("source"):
        raise jobs.JobError("this chain has not been made yet")
    choice = params.get("upscale")
    upscale.parse(choice)
    options = dict(record.get("options") or {})
    options["upscale"] = choice
    if params.get("height"):
        options["height"] = int(min(2048, max(256, int(params["height"]))))
    return build(job, project, chain_id, record["source"], _options({"options": options}),
                 priors=_known_figure(project, chain_id), extra=record.get("extra"))


@jobs.handler("chain")
def make_chain(job):
    """params: project, chain (id), source {kind: video|pictures|zip, path, fps}, options (see DEFAULTS),
    realign (only place the frames again, with the cuts already made)"""
    params = job.params
    project = store.clean_name(params["project"])
    chain_id = store.clean_id(params["chain"], "chain id")
    realign = bool(params.get("realign"))
    source = params.get("source")
    if realign:
        earlier = store.read_chain(project, chain_id) or {}
        source = earlier.get("source") or source
    if not source:
        raise jobs.JobError("this chain has no source to make it from")
    options = dict(params.get("options") or {}) if realign else _options(params)
    return build(job, project, chain_id, source, options, realign=realign)
