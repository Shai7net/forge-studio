"""How FLUX 3 plugs into this Forge (f0.0.17, A1111 1.8 based) without changing anything for the other models.

A FLUX 3 file in models/Stable-diffusion is recognised by its weight names. Selecting it unloads Forge's model,
loads FLUX 3 with its text encoder and VAE from their folders, and installs a light stand-in as `shared.sd_model`
(`Flux3SdModel`) so that Forge's UI, infotext, scripts and API keep working. Every patch below first asks
`active()`; with an SD 1.5 / SDXL checkpoint selected they call straight through to Forge's own code.

Where FLUX 3 takes over:
- sd_models.load_model: the FLUX 3 loader (and freeing it again when another checkpoint is chosen);
- the conditioning setup: FLUX 3 encodes its prompts itself (Qwen3-VL), with a cache;
- txt2img sample (with Hires. fix), img2img init + sample (classic img2img, inpainting, and FLUX 3's own editing
  with the picture as a reference): all return decoded images, the path Forge already has for that;
- the Lora network loader (FLUX 3 LoRAs), the token counter, VAE reloads, and Forge's memory manager (FLUX 3
  leaves the card when Forge needs it).
"""

import gc
import json
import os
import struct
import sys
import time
import types

import numpy as np
import torch
from PIL import Image, ImageOps

from modules import devices, errors, images, masking, paths, processing, prompt_parser, rng, script_callbacks, sd_models, sd_vae, shared
from modules.shared import opts, state

from . import engine as feng
from . import lora as flora

ENGINE = None
DEFAULTS = {"steps": 16, "cfg": 1.0, "sampler": "Euler", "base_steps": 50, "base_cfg": 3.5}
MODELS_DIR = os.path.join(paths.models_path)
TE_DIR = os.path.join(MODELS_DIR, "text_encoder")
VAE_DIR = os.path.join(MODELS_DIR, "VAE")
_keys_cache = {}


def log(msg):
    feng.log(msg)


# ---------------------------------------------------------------------- recognising FLUX 3 files

def read_keys(path):
    st = os.stat(path)
    key = (path, st.st_size, st.st_mtime)
    hit = _keys_cache.get(key)
    if hit is None:
        with open(path, "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            header = json.loads(f.read(n))
        hit = _keys_cache[key] = [k for k in header if k != "__metadata__"]
    return hit


def is_flux3_file(path):
    if not path or not str(path).lower().endswith(".safetensors") or not os.path.isfile(path):
        return False
    try:
        keys = read_keys(path)
    except Exception:  # noqa: BLE001
        return False
    has_single = any(k.endswith("single_blocks.0.mlp_in.weight") for k in keys)
    has_modes = any("content_mode_blocks.image.0." in k for k in keys)
    return has_single and has_modes


def is_flux3_info(info):
    return info is not None and is_flux3_file(getattr(info, "filename", None))


def flux3_titles():
    return [title for title, info in sd_models.checkpoints_list.items() if is_flux3_info(info)]


def _first(folder, prefixes, exts=(".safetensors",)):
    if not os.path.isdir(folder):
        return None
    names = sorted(os.listdir(folder))
    for p in prefixes:
        for n in names:
            if n.lower().startswith(p) and n.lower().endswith(exts):
                return os.path.join(folder, n)
    return None


def components(info):
    """Where the text encoder, its tokenizer and the VAE of this checkpoint are: from <checkpoint>.json
    ("flux3_text_encoder", "vae") when given, else the usual names in models/text_encoder and models/VAE."""
    meta = {}
    side = os.path.splitext(info.filename)[0] + ".json"
    if os.path.isfile(side):
        try:
            with open(side, encoding="utf8") as f:
                meta = json.load(f)
        except Exception:  # noqa: BLE001
            meta = {}
    te = meta.get("flux3_text_encoder")
    te = os.path.join(TE_DIR, te) if te and not os.path.isabs(te) else te
    te = te or _first(TE_DIR, ("qwen3vl_4b_int8", "qwen3vl_4b"))
    tok = meta.get("flux3_tokenizer") or os.path.join(TE_DIR, "qwen3vl_4b_tokenizer")
    tok = os.path.join(TE_DIR, tok) if not os.path.isabs(tok) else tok
    vae = meta.get("vae")
    vae = os.path.join(VAE_DIR, vae) if vae and not os.path.isabs(vae) else vae
    vae = vae or _first(VAE_DIR, ("flux2-vae", "flux2_vae", "ae."))
    missing = [n for n, p in (("text encoder (models/text_encoder/qwen3vl_4b_*.safetensors)", te),
                              ("tokenizer (models/text_encoder/qwen3vl_4b_tokenizer)", tok),
                              ("VAE (models/VAE/flux2-vae.safetensors)", vae)) if not p or not os.path.exists(p)]
    if missing:
        raise FileNotFoundError("FLUX 3 needs: " + "; ".join(missing))
    return {"model": info.filename, "text_encoder": te, "tokenizer_dir": tok, "vae": vae, "meta": meta}


# ---------------------------------------------------------------------- the stand-in model

class _Stub(types.SimpleNamespace):
    def shallow_copy(self):
        return self


class _CondStub:
    """What the prompt token counter and the textual inversion loader ask the text model."""

    def __init__(self, sd_model):
        self.sd_model = sd_model

    def process_texts(self, texts):
        eng = ENGINE
        if eng is None or eng.te is None:
            return None, 0
        return None, max(len(eng.te.tokens(clean_prompt(t))) for t in texts)

    def get_target_prompt_token_count(self, n):
        return max(80, -(-(n + 5) // 80) * 80)

    def encode_embedding_init_text(self, text, nvpt):
        return torch.zeros(nvpt, 1)


class Flux3SdModel:
    is_flux3 = True
    is_sdxl = False
    is_sd2 = False
    is_sd1 = False
    is_ssd = False
    cond_stage_key = "crossattn"
    parameterization = "flow"
    latent_channels = 128
    tiling_enabled = False
    current_lora_hash = None

    def __init__(self, info, engine):
        self.engine = engine
        self.filename = info.filename
        self.sd_model_checkpoint = info.filename
        self.sd_checkpoint_info = info
        self.sd_model_hash = info.calculate_shorthash()
        self.forge_objects = _Stub(unet=_Stub(model_options={}, model=_Stub(model_sampling=None)), clip=None, vae=None, clipvision=None)
        self.forge_objects_original = self.forge_objects
        self.forge_objects_after_applying_lora = self.forge_objects
        self.model = _Stub(conditioning_key="crossattn", diffusion_model=None, state_dict=lambda: {})
        self.cond_stage_model = _CondStub(self)
        self.cond_stage_model_empty_prompt = None
        self.first_stage_model = None
        self.device = devices.device
        self.dtype = torch.bfloat16
        self.lowvram = False

    def children(self):
        return []

    def state_dict(self):
        return {}

    def to(self, *args, **kwargs):
        return self

    def get_learned_conditioning(self, prompts):
        raise RuntimeError("FLUX 3 encodes its prompts itself")

    @torch.no_grad()
    def decode_first_stage(self, x):
        return self.engine.decode(x).to(x)

    @torch.no_grad()
    def encode_first_stage(self, x):
        return self.engine.encode_image(x)

    def get_first_stage_encoding(self, x):
        return x


def current():
    m = sd_models.model_data.sd_model
    return m if isinstance(m, Flux3SdModel) else None


def active(sd_model=None):
    m = sd_model if sd_model is not None else sd_models.model_data.sd_model
    return isinstance(m, Flux3SdModel)


# ---------------------------------------------------------------------- loading

_orig = {}


def _free_engine():
    global ENGINE
    if ENGINE is not None:
        ENGINE.unload()
        ENGINE = None
        gc.collect()
        torch.cuda.empty_cache()
        log("unloaded")


def load_model(checkpoint_info=None, already_loaded_state_dict=None):
    info = checkpoint_info or sd_models.select_checkpoint()
    cur = sd_models.model_data.sd_model
    if not is_flux3_info(info):
        if isinstance(cur, Flux3SdModel):
            sd_models.model_data.sd_model = None
            sd_models.model_data.loaded_sd_models = []
            sd_vae.loaded_vae_file = None
            _free_engine()
        return _orig["load_model"](checkpoint_info, already_loaded_state_dict)

    global ENGINE
    if isinstance(cur, Flux3SdModel) and cur.filename == info.filename:
        return cur
    from ldm_patched.modules import model_management
    timer = time.time()
    if cur is not None:
        sd_models.model_data.sd_model = None
        sd_models.model_data.loaded_sd_models = []
        model_management.unload_all_models()
        model_management.soft_empty_cache()
        gc.collect()
    _free_engine()
    paths_ = components(info)
    log(f"loading {os.path.basename(info.filename)} with {os.path.basename(paths_['text_encoder'])} and {os.path.basename(paths_['vae'])}")
    eng = feng.Flux3Engine(paths_)
    eng.load(devices.device)
    ENGINE = eng
    sd_model = Flux3SdModel(info, eng)
    shared.opts.data["sd_checkpoint_hash"] = info.sha256
    sd_vae.delete_base_vae()
    sd_vae.clear_loaded_vae()
    sd_vae.loaded_vae_file = paths_["vae"]
    sd_models.model_data.set_sd_model(sd_model)
    sd_models.model_data.was_loaded_at_least_once = True
    script_callbacks.model_loaded_callback(sd_model)
    log(f"ready in {time.time() - timer:.1f} s (sampler: flow-map Euler, {'16 steps, no CFG' if eng.model.flow_map else '50 steps, CFG 3.5'})")
    return sd_model


def reload_vae_weights(sd_model=None, vae_file=sd_vae.unspecified):
    if active():
        return sd_models.model_data.sd_model
    return _orig["reload_vae_weights"](sd_model, vae_file)


def free_memory(memory_required, device, keep_loaded=[]):
    from ldm_patched.modules import model_management
    _orig["free_memory"](memory_required, device, keep_loaded)
    eng = ENGINE
    if eng is not None and eng.on_card_bytes() > 0 and model_management.get_free_memory(device) < memory_required:
        if eng.lock.acquire(blocking=False):
            try:
                eng.offload_all()
                log("left the card for Forge")
            finally:
                eng.lock.release()


def load_networks(names, te_multipliers=None, unet_multipliers=None, dyn_dims=None):
    sd_model = sd_models.model_data.get_sd_model()
    if not isinstance(sd_model, Flux3SdModel):
        return _orig["load_networks"](names, te_multipliers, unet_multipliers, dyn_dims)
    networks = sys.modules["networks"]
    networks.loaded_networks.clear()
    on_disk = [networks.available_network_aliases.get(n) for n in names]
    if any(x is None for x in on_disk):
        networks.list_available_networks()
        on_disk = [networks.available_network_aliases.get(n) for n in names]
    wanted = [(nd.filename, float(m)) for nd, m in zip(on_disk, unet_multipliers or [1.0] * len(names)) if nd is not None]
    key = str(wanted)
    for n, nd in zip(names, on_disk):
        if nd is None:
            log(f"LoRA not found: {n}")
            continue
        nd.read_hash()
        networks.loaded_networks.append(types.SimpleNamespace(mentioned_name=n, network_on_disk=nd, name=n))
    if sd_model.current_lora_hash == key:
        return
    from safetensors.torch import load_file
    loras = [(load_file(f), m) for f, m in wanted]
    applied, missed = flora.apply(sd_model.engine.model, loras)
    sd_model.current_lora_hash = key
    if wanted:
        log(f"LoRA: {applied} layers{f', {missed} keys not for FLUX 3' if missed else ''}")


# ---------------------------------------------------------------------- prompts

def clean_prompt(text):
    """FLUX 3 reads plain language: prompt editing is resolved to its final prompt, (emphasis:1.2) is dropped
    to its words and BREAK becomes a new line."""
    try:
        text = prompt_parser.get_learned_conditioning_prompt_schedules([text], 100)[0][-1][1]
    except Exception:  # noqa: BLE001
        pass
    try:
        parts = prompt_parser.parse_prompt_attention(text)
        text = "".join("\n" if w < 0 and t == "BREAK" else t for t, w in parts)
    except Exception:  # noqa: BLE001
        pass
    return text.strip()


def wants_writer(p, prompt):
    mode = getattr(p, "flux3_writer", None) or opts.data.get("flux3_writer", "Auto")
    if mode == "Off" or not prompt.strip():
        return False  # an empty prompt stays empty: the writer would invent a scene
    if mode == "Always":
        return True
    words = len(prompt.split())
    non_latin = any(ord(c) > 0x24F for c in prompt)
    return words < 40 or non_latin


def final_prompt(p, prompt, width, height, mode="t2i"):
    text = clean_prompt(prompt)
    if mode == "t2i" and wants_writer(p, text):
        caption = ENGINE.upsample(text, width, height, "t2i")
        p.extra_generation_params["FLUX 3 prompt writer"] = "Qwen3-VL-4B"
        if caption and len(getattr(p, "flux3_captions", [])) < 4:
            p.flux3_captions = getattr(p, "flux3_captions", []) + [caption]
            log(f"caption: {caption}")
        return caption or text
    return text


# ---------------------------------------------------------------------- sampling helpers

def _size16(w, h):
    return max(16, int(round(w / 16)) * 16), max(16, int(round(h / 16)) * 16)


def _noise(p, seeds, subseeds, h, w):
    g = rng.ImageRNG((128, h, w), seeds, subseeds=subseeds, subseed_strength=p.subseed_strength)
    return g.next().float()


def _progress(steps):
    state.sampling_steps = steps
    state.sampling_step = 0

    def cb(i, n, x0):
        state.sampling_step = i + 1
        shared.total_tqdm.update()
        every = max(1, int(opts.data.get("show_progress_every_n_steps", 10) or 10))
        if opts.data.get("live_previews_enable", True) and every > 0 and (i + 1) % every == 0 and i + 1 < n:
            try:
                state.assign_current_image(feng.latent_preview(x0[0]))
            except Exception:  # noqa: BLE001
                pass
    return cb


def _should_stop():
    return state.interrupted or state.skipped or getattr(state, "stopping_generation", False)


def _note(p, eng):
    p.sampler_name = "Euler"
    p.extra_generation_params["FLUX 3 sampler"] = "flow-map Euler, shift 6.93" if eng.model.flow_map else "Euler, shift 6.93"
    if eng.model.flow_map and p.cfg_scale != 1:
        p.cfg_scale = 1.0  # the distilled model has no guidance


def _to_pil(img):
    arr = ((img.float().clamp(-1, 1) + 1) * 127.5).round().byte().permute(1, 2, 0).cpu().numpy()
    return Image.fromarray(arr)


def _from_pil(im, w, h):
    im = im.convert("RGB")
    if im.size != (w, h):
        im = im.resize((w, h), Image.LANCZOS)
    a = np.asarray(im).astype(np.float32) / 127.5 - 1
    return torch.from_numpy(a).permute(2, 0, 1)[None]


def _fit(img, w, h):
    """[3, h16, w16] -> [3, h, w] when the requested size is not a multiple of 16."""
    if img.shape[-1] == w and img.shape[-2] == h:
        return img
    return _from_pil(_to_pil(img), w, h)[0]


def _run(eng, p, noise, emb, steps, **kw):
    with torch.autocast("cuda", enabled=False):
        return eng.sample(noise, emb, steps, callback=_progress(steps), should_stop=_should_stop, **kw)


# ---------------------------------------------------------------------- txt2img

def make_setup_conds(original):
    """One wrapper per class that defines setup_conds (txt2img's calls the base one through super())."""
    def setup_conds(self):
        if not active():
            return original(self)
        self.c = self.uc = None
        self.step_multiplier = 1
        self.firstpass_steps = self.steps
    return setup_conds


def txt2img_sample(self, conditioning, unconditional_conditioning, seeds, subseeds, subseed_strength, prompts):
    if not active():
        return _orig["txt2img_sample"](self, conditioning, unconditional_conditioning, seeds, subseeds, subseed_strength, prompts)
    eng = ENGINE
    _note(self, eng)
    with eng.lock:
        w16, h16 = _size16(self.width, self.height)
        noise = _noise(self, seeds, subseeds, h16 // 16, w16 // 16)
        out = processing.DecodedSamples()
        for b, prompt in enumerate(prompts):
            with torch.autocast("cuda", enabled=False):
                emb = eng.encode(final_prompt(self, prompt, w16, h16))
            lat = _run(eng, self, noise[b:b + 1], emb, self.steps)
            if state.interrupted:
                out.append(_fit(eng.decode(lat)[0], self.width, self.height))
                continue
            img = eng.decode(lat)[0]
            if self.enable_hr and not state.skipped:
                img = _hires(self, eng, img, b, seeds, subseeds)
            out.append(img if self.enable_hr else _fit(img, self.width, self.height))
        return out


def _hires(p, eng, img, b, seeds, subseeds):
    p.is_hr_pass = True
    tw, th = p.hr_upscale_to_x, p.hr_upscale_to_y
    w16, h16 = _size16(tw, th)
    first = _to_pil(img)
    if p.hr_upscaler in shared.latent_upscale_modes or not p.hr_upscaler or p.hr_upscaler == "None":
        up = first.resize((w16, h16), Image.LANCZOS)
    else:
        up = images.resize_image(0, first, w16, h16, upscaler_name=p.hr_upscaler)
    with torch.autocast("cuda", enabled=False):
        init = eng.encode_image(_from_pil(up, w16, h16))
    prompt = p.hr_prompts[b] if getattr(p, "hr_prompts", None) else p.prompts[b]
    with torch.autocast("cuda", enabled=False):
        emb = eng.encode(final_prompt(p, prompt, w16, h16))
    steps = p.hr_second_pass_steps or p.steps
    noise = _noise(p, seeds[b:b + 1], subseeds[b:b + 1] if subseeds else None, h16 // 16, w16 // 16)
    lat = _run(eng, p, noise, emb, steps, denoise=float(p.denoising_strength), init=init)
    out = eng.decode(lat)[0]
    p.is_hr_pass = False
    return _fit(out, tw, th)


# ---------------------------------------------------------------------- img2img

EDIT_MODES = ("Edit with reference (FLUX 3)", "Classic img2img (denoise)")


def img2img_init(self, all_prompts, all_seeds, all_subseeds):
    if not active():
        return _orig["img2img_init"](self, all_prompts, all_seeds, all_subseeds)
    eng = ENGINE
    self.extra_generation_params["Denoising strength"] = self.denoising_strength
    self.image_cfg_scale = None
    self.sampler = None
    crop_region = None
    image_mask = self.image_mask
    if image_mask is not None:
        image_mask = processing.create_binary_mask(image_mask, round=self.mask_round)
        if self.inpainting_mask_invert:
            image_mask = ImageOps.invert(image_mask)
            self.extra_generation_params["Mask mode"] = "Inpaint not masked"
        import cv2
        if self.mask_blur_x > 0:
            np_mask = np.array(image_mask)
            k = 2 * int(2.5 * self.mask_blur_x + 0.5) + 1
            image_mask = Image.fromarray(cv2.GaussianBlur(np_mask, (k, 1), self.mask_blur_x))
        if self.mask_blur_y > 0:
            np_mask = np.array(image_mask)
            k = 2 * int(2.5 * self.mask_blur_y + 0.5) + 1
            image_mask = Image.fromarray(cv2.GaussianBlur(np_mask, (1, k), self.mask_blur_y))
        if self.mask_blur_x > 0 or self.mask_blur_y > 0:
            self.extra_generation_params["Mask blur"] = self.mask_blur
        if self.inpaint_full_res:
            self.mask_for_overlay = image_mask
            mask = image_mask.convert("L")
            crop_region = masking.get_crop_region(mask, self.inpaint_full_res_padding)
            crop_region = masking.expand_crop_region(crop_region, self.width, self.height, mask.width, mask.height)
            x1, y1, x2, y2 = crop_region
            mask = mask.crop(crop_region)
            image_mask = images.resize_image(2, mask, self.width, self.height)
            self.paste_to = (x1, y1, x2 - x1, y2 - y1)
            self.extra_generation_params["Inpaint area"] = "Only masked"
            self.extra_generation_params["Masked area padding"] = self.inpaint_full_res_padding
        else:
            image_mask = images.resize_image(self.resize_mode, image_mask, self.width, self.height)
            np_mask = np.clip(np.array(image_mask).astype(np.float32) * 2, 0, 255).astype(np.uint8)
            self.mask_for_overlay = Image.fromarray(np_mask)
        self.overlay_images = []

    latent_mask = self.latent_mask if self.latent_mask is not None else image_mask
    add_color_corrections = opts.img2img_color_correction and self.color_corrections is None
    if add_color_corrections:
        self.color_corrections = []
    imgs = []
    for img in self.init_images:
        image = images.flatten(img, opts.img2img_background_color)
        if crop_region is None and self.resize_mode != 3:
            image = images.resize_image(self.resize_mode, image, self.width, self.height)
        if image_mask is not None:
            image_masked = Image.new("RGBa", (image.width, image.height))
            image_masked.paste(image.convert("RGBA").convert("RGBa"), mask=ImageOps.invert(self.mask_for_overlay.convert("L")))
            self.overlay_images.append(image_masked.convert("RGBA"))
        if crop_region is not None:
            image = image.crop(crop_region)
            image = images.resize_image(2, image, self.width, self.height)
        if image_mask is not None and self.inpainting_fill != 1:
            image = masking.fill(image, latent_mask)
        if add_color_corrections:
            self.color_corrections.append(processing.setup_color_correction(image))
        imgs.append(image)

    if len(imgs) == 1:
        imgs = imgs * self.batch_size
        if self.overlay_images is not None:
            self.overlay_images = self.overlay_images * self.batch_size
        if self.color_corrections is not None and len(self.color_corrections) == 1:
            self.color_corrections = self.color_corrections * self.batch_size
    elif len(imgs) <= self.batch_size:
        self.batch_size = len(imgs)
    else:
        raise RuntimeError(f"bad number of images passed: {len(imgs)}; expecting {self.batch_size} or less")

    w16, h16 = _size16(self.width, self.height)
    self.flux3_size = (w16, h16)
    batch = torch.cat([_from_pil(im, w16, h16) for im in imgs])
    with torch.autocast("cuda", enabled=False):
        self.init_latent = eng.encode_image(batch)
    self.flux3_mask = None
    if image_mask is not None:
        lm = latent_mask.convert("L").resize((w16 // 16, h16 // 16), Image.BILINEAR)
        # kept soft: rounded at 16 px per cell the edge turns into blocks
        self.flux3_mask = m = torch.from_numpy(np.asarray(lm).astype(np.float32) / 255.0)[None, None]
        if self.inpainting_fill == 3:
            self.init_latent = self.init_latent * (1 - m.to(self.init_latent))
            self.extra_generation_params["Masked content"] = "latent nothing"
        elif self.inpainting_fill == 2:
            self.extra_generation_params["Masked content"] = "latent noise"
    self.image_conditioning = None
    self.mask = self.nmask = None
    # Other extensions may give their own reference pictures (`p.flux3_references`, PIL images): each keeps its own
    # shape (see the sizes below) instead of being stretched to the output like the init picture.
    # Video Studio's Ingredients draws a character from a reference sheet this way (2026-11-09).
    refs = getattr(self, "flux3_references", None)
    self.flux3_ref_latents = None
    if refs:
        latents = []
        for k, im in enumerate(refs):
            # the first reference leads (the pose sheet of a character sheet, the main character of a scene) and
            # keeps its detail; the others share about half as many tokens again. With both at 1024²/2 a pose
            # sheet's small heads came out as broken figures (2026-11-09).
            area = REF_AREA if k == 0 else max(512 * 512, REF_AREA / 2 / (len(refs) - 1))
            im = images.flatten(im, opts.img2img_background_color)
            scale = (area / (im.width * im.height)) ** 0.5
            w, h = (max(16, round(v * scale / 16) * 16) for v in im.size)
            with torch.autocast("cuda", enabled=False):
                latents.append(eng.encode_image(_from_pil(im, w, h)))
        self.flux3_ref_latents = latents


REF_AREA = 1024 * 1024


def img2img_sample(self, conditioning, unconditional_conditioning, seeds, subseeds, subseed_strength, prompts):
    if not active():
        return _orig["img2img_sample"](self, conditioning, unconditional_conditioning, seeds, subseeds, subseed_strength, prompts)
    eng = ENGINE
    _note(self, eng)
    mode = getattr(self, "flux3_edit_mode", None) or opts.data.get("flux3_edit_mode", EDIT_MODES[0])
    # inpainting always takes the classic path: an edit redraws the whole frame and does not keep its geometry
    # exactly, so the pasted-back mask area did not line up (a second car in the sky, 2026-10-26)
    edit = mode == EDIT_MODES[0] and self.flux3_mask is None
    self.extra_generation_params["FLUX 3 img2img"] = "edit with reference" if edit else "classic"
    with eng.lock:
        w16, h16 = self.flux3_size
        noise = _noise(self, seeds, subseeds, h16 // 16, w16 // 16)
        out = processing.DecodedSamples()
        for b, prompt in enumerate(prompts):
            init = self.init_latent[b:b + 1]
            mask = self.flux3_mask
            if mask is not None and self.inpainting_fill == 2:
                init = init * (1 - mask.to(init)) + noise[b:b + 1].to(init) * mask.to(init)
            text = clean_prompt(prompt)
            with torch.autocast("cuda", enabled=False):
                emb = eng.encode(text)
            if edit:
                # FLUX 3's own editing: the picture goes in as reference 0 and the whole image is drawn from noise,
                # so the change sits in a coherent scene. With a mask, Forge then pastes only the masked area (soft,
                # with "Mask blur") over the original; forcing the known area during sampling at 16 px per latent
                # cell left a visible edge (2026-10-26)
                refs = getattr(self, "flux3_ref_latents", None) or [self.init_latent[b:b + 1]]
                lat = _run(eng, self, noise[b:b + 1], emb, self.steps, refs=refs)
            else:
                lat = _run(eng, self, noise[b:b + 1], emb, self.steps, denoise=float(self.denoising_strength),
                           init=init, mask=mask)
            out.append(_fit(eng.decode(lat)[0], self.width, self.height))
        return out


# ---------------------------------------------------------------------- install

def install():
    if _orig:
        return
    from ldm_patched.modules import model_management
    _orig["load_model"] = sd_models.load_model
    sd_models.load_model = load_model
    _orig["reload_vae_weights"] = sd_vae.reload_vae_weights
    sd_vae.reload_vae_weights = reload_vae_weights
    _orig["free_memory"] = model_management.free_memory
    model_management.free_memory = free_memory
    for cls in (processing.StableDiffusionProcessing, processing.StableDiffusionProcessingTxt2Img):
        original = cls.__dict__["setup_conds"]
        _orig[f"setup_conds:{cls.__name__}"] = original
        cls.setup_conds = make_setup_conds(original)
    _orig["txt2img_sample"] = processing.StableDiffusionProcessingTxt2Img.sample
    processing.StableDiffusionProcessingTxt2Img.sample = txt2img_sample
    _orig["img2img_init"] = processing.StableDiffusionProcessingImg2Img.init
    processing.StableDiffusionProcessingImg2Img.init = img2img_init
    _orig["img2img_sample"] = processing.StableDiffusionProcessingImg2Img.sample
    processing.StableDiffusionProcessingImg2Img.sample = img2img_sample
    networks = sys.modules.get("networks")
    if networks is not None and hasattr(networks, "load_networks"):
        _orig["load_networks"] = networks.load_networks
        networks.load_networks = load_networks
    log("ready: FLUX 3 checkpoints in models/Stable-diffusion load with their text encoder and VAE")
