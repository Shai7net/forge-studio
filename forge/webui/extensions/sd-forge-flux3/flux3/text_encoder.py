"""Qwen3-VL-4B-Instruct, text only: FLUX 3's text encoder, and the prompt writer ("upsampler").

Encoding follows the reference exactly (diffusers `Flux3TextEncoderStep`, ComfyUI `Flux3TEModel`):
- the prompt goes through the chat template "<|im_start|>user\\n{prompt}<|im_end|>\\n<|im_start|>assistant\\n";
- it is right-padded with <|endoftext|> to a multiple of 80 tokens; padding is masked inside Qwen but its states
  are kept (the image model attends to them), and the padding sits at rope position 1 as in transformers 4.57;
- the hidden states after layers 4, 8, ..., 32 (of 36) are stacked per token: 8 x 2560 = 20480 features.

With text only, Qwen3-VL's interleaved multimodal RoPE has the same position on all three axes, which is plain
1-D RoPE (theta 5e6). The weights are tied, so the token embedding also scores the next token when writing.

Layers stream through the card one at a time (about 125 MB each at int8), so the text encoder never needs more
than a few hundred MB of video memory next to the image model.
"""

import json
import os

import torch
import torch.nn.functional as F

from . import ops

PAD_ID = 151643  # <|endoftext|>
IM_END = 151645
LAYERS_OUT = (4, 8, 12, 16, 20, 24, 28, 32)
PAD_MULTIPLE = 80
TEMPLATE = "<|im_start|>user\n{}<|im_end|>\n<|im_start|>assistant\n"


def _find_prefix(sd):
    for k in sd:
        if k.endswith("layers.0.self_attn.q_proj.weight"):
            return k[: -len("layers.0.self_attn.q_proj.weight")]
    raise ValueError("this file has no Qwen3 text layers")


class Layer:
    def __init__(self, sd, p):
        q, k, v = (ops.take_linear(sd, f"{p}self_attn.{n}_proj.") for n in ("q", "k", "v"))
        self.qkv, self.qkv_sizes = ops.fuse([q, k, v])
        if self.qkv is None:
            self.parts = [q, k, v]
        self.o = ops.take_linear(sd, f"{p}self_attn.o_proj.")
        gate, up = ops.take_linear(sd, f"{p}mlp.gate_proj."), ops.take_linear(sd, f"{p}mlp.up_proj.")
        self.gu, self.gu_sizes = ops.fuse([gate, up])
        if self.gu is None:
            self.gu_parts = [gate, up]
        self.down = ops.take_linear(sd, f"{p}mlp.down_proj.")
        self.ln1 = ops.Weight(sd[f"{p}input_layernorm.weight"].float())
        self.ln2 = ops.Weight(sd[f"{p}post_attention_layernorm.weight"].float())
        self.q_norm = ops.Weight(sd[f"{p}self_attn.q_norm.weight"].float())
        self.k_norm = ops.Weight(sd[f"{p}self_attn.k_norm.weight"].float())

    def linears(self):
        out = [self.qkv] if self.qkv is not None else list(self.parts)
        out += [self.gu] if self.gu is not None else list(self.gu_parts)
        return out + [self.o, self.down]

    def weights(self):
        out = [self.ln1, self.ln2, self.q_norm, self.k_norm]
        for l in self.linears():
            out += l.weights()
        return out


class QwenText:
    def __init__(self, sd, tokenizer_dir, cfg):
        self.cfg = cfg
        p = _find_prefix(sd)
        self.n_layers = cfg["num_hidden_layers"]
        self.heads, self.kv_heads, self.head_dim = cfg["num_attention_heads"], cfg["num_key_value_heads"], cfg["head_dim"]
        self.eps = cfg["rms_norm_eps"]
        self.theta = float(cfg["rope_theta"])
        self.layers = [Layer(sd, f"{p}layers.{i}.") for i in range(self.n_layers)]
        self.embed = ops.take_linear(sd, f"{p}embed_tokens.")
        self.norm = ops.Weight(sd[f"{p}norm.weight"].float()) if f"{p}norm.weight" in sd else None
        from tokenizers import Tokenizer
        self.tok = Tokenizer.from_file(os.path.join(tokenizer_dir, "tokenizer.json"))
        self.streamer = None
        self.device = None
        self._embed_gpu = None

    def pin(self):
        self.arena = ops.PinnedArena([w for layer in self.layers for w in layer.weights()] + self.embed.weights()
                                     + ([self.norm] if self.norm is not None else []))

    def release(self):
        self.offload()
        if getattr(self, "arena", None) is not None:
            self.arena.release()
            self.arena = None

    def total_bytes(self):
        return sum(w.nbytes for l in self.layers for w in l.weights()) + sum(w.nbytes for w in self.embed.weights())

    def to_device(self, device, resident_layers=0):
        self.device = device
        if self.streamer is None:
            self.streamer = ops.BlockStreamer([l.weights() for l in self.layers], device)
        self.streamer.set_resident(resident_layers)

    def offload(self):
        if self.streamer is not None:
            self.streamer.offload()
        self._embed_gpu = None
        for w in self.embed.weights():
            w.gpu = None

    # ------------------------------------------------------------------ pieces

    def _rms(self, x, w):
        return F.rms_norm(x.float(), (x.shape[-1],), w.on(x.device), eps=self.eps).to(x.dtype)

    def _rope(self, pos):
        inv = 1.0 / (self.theta ** (torch.arange(0, self.head_dim, 2, dtype=torch.float64, device=pos.device) / self.head_dim))
        ang = pos.double()[..., None] * inv
        ang = torch.cat([ang, ang], dim=-1)
        return torch.cos(ang).float(), torch.sin(ang).float()

    @staticmethod
    def _apply_rope(x, cos, sin):
        # rotate_half convention (Qwen): x [B, H, L, D], cos/sin [B, L, D]
        half = x.shape[-1] // 2
        rot = torch.cat([-x[..., half:], x[..., :half]], dim=-1)
        return (x.float() * cos[:, None] + rot.float() * sin[:, None]).to(x.dtype)

    def _embed_rows(self, ids):
        e = self.embed
        dev = ids.device
        if not e.int8:
            return F.embedding(ids, e.weight.on(dev)).to(torch.bfloat16)
        rows = F.embedding(ids, e.weight.on(dev)).float() * F.embedding(ids, e.scale.on(dev)[:, None])
        if e.group:
            rows = ops.rotate(rows, e.group)
        return rows.to(torch.bfloat16)

    def _layer(self, layer, x, cos, sin, mask, cache=None, start=0):
        B, L, _ = x.shape
        h = self._rms(x, layer.ln1)
        if layer.qkv is not None:
            q, k, v = torch.split(layer.qkv(h), layer.qkv_sizes, dim=-1)
        else:
            q, k, v = (l(h) for l in layer.parts)
        q = q.view(B, L, self.heads, self.head_dim)
        k = k.view(B, L, self.kv_heads, self.head_dim)
        v = v.view(B, L, self.kv_heads, self.head_dim).transpose(1, 2)
        q = F.rms_norm(q.float(), (self.head_dim,), layer.q_norm.on(x.device), eps=self.eps).to(x.dtype).transpose(1, 2)
        k = F.rms_norm(k.float(), (self.head_dim,), layer.k_norm.on(x.device), eps=self.eps).to(x.dtype).transpose(1, 2)
        q = self._apply_rope(q, cos, sin)
        k = self._apply_rope(k, cos, sin)
        if cache is not None:
            if "k" in cache:
                k = torch.cat([cache["k"], k], dim=2)
                v = torch.cat([cache["v"], v], dim=2)
            cache["k"], cache["v"] = k, v
        rep = self.heads // self.kv_heads
        k = k.repeat_interleave(rep, dim=1)
        v = v.repeat_interleave(rep, dim=1)
        a = F.scaled_dot_product_attention(q, k, v, attn_mask=mask)
        a = a.transpose(1, 2).reshape(B, L, -1)
        x = x + layer.o(a)
        h = self._rms(x, layer.ln2)
        if layer.gu is not None:
            g, u = torch.split(layer.gu(h), layer.gu_sizes, dim=-1)
        else:
            g, u = (l(h) for l in layer.gu_parts)
        return x + layer.down(F.silu(g) * u)

    # ------------------------------------------------------------------ encode

    def tokens(self, text):
        return self.tok.encode(text, add_special_tokens=False).ids

    @torch.no_grad()
    def encode(self, prompt):
        """[1, T, 20480] bf16 on the device, T a multiple of 80."""
        dev = self.device
        ids = self.tokens(TEMPLATE.format(prompt))
        n = len(ids)
        total = max(PAD_MULTIPLE, -(-n // PAD_MULTIPLE) * PAD_MULTIPLE)
        ids = ids + [PAD_ID] * (total - n)
        ids_t = torch.tensor([ids], device=dev)
        valid = torch.zeros(1, total, dtype=torch.bool, device=dev)
        valid[:, :n] = True
        pos = torch.arange(total, device=dev)[None].masked_fill(~valid, 1)
        cos, sin = self._rope(pos)
        causal = torch.ones(total, total, dtype=torch.bool, device=dev).tril()
        mask = (causal[None] & valid[:, None, :])[:, None]  # [1, 1, T, T]: causal, never a padding key
        x = self._embed_rows(ids_t)
        outs = {}
        need = max(LAYERS_OUT)

        def run(i):
            nonlocal x
            x = self._layer(self.layers[i], x, cos, sin, mask)
            if i + 1 in LAYERS_OUT:
                outs[i + 1] = x

        self.streamer.run(list(range(need)), run)
        out = torch.stack([outs[k] for k in LAYERS_OUT], dim=1)  # [1, 8, T, 2560]
        return out.permute(0, 2, 1, 3).reshape(1, total, -1)

    # ------------------------------------------------------------------ write (prompt upsampler)

    def _layer_static(self, layer, x, cos, sin, mask, kc, vc, idx):
        """A layer over a fixed-size KV cache: writes this call's keys/values at positions `idx` and attends over
        the whole cache under `mask` ([1, 1, L, MAXL]). Shapes never change, so a decode step can be a CUDA graph."""
        B, L, _ = x.shape
        h = self._rms(x, layer.ln1)
        q, k, v = torch.split(layer.qkv(h), layer.qkv_sizes, dim=-1)
        q = F.rms_norm(q.float().view(B, L, self.heads, self.head_dim), (self.head_dim,), layer.q_norm.on(x.device), eps=self.eps).to(x.dtype).transpose(1, 2)
        k = F.rms_norm(k.float().view(B, L, self.kv_heads, self.head_dim), (self.head_dim,), layer.k_norm.on(x.device), eps=self.eps).to(x.dtype).transpose(1, 2)
        v = v.view(B, L, self.kv_heads, self.head_dim).transpose(1, 2)
        q = self._apply_rope(q, cos, sin)
        k = self._apply_rope(k, cos, sin)
        kc.index_copy_(2, idx, k)
        vc.index_copy_(2, idx, v)
        a = F.scaled_dot_product_attention(q, kc, vc, attn_mask=mask, enable_gqa=True)
        x = x + layer.o(a.transpose(1, 2).reshape(B, L, -1))
        h = self._rms(x, layer.ln2)
        g, u = torch.split(layer.gu(h), layer.gu_sizes, dim=-1)
        return x + layer.down(F.silu(g) * u)

    @staticmethod
    def chat_text(system, user, turns=()):
        """The chat template; `turns` are earlier (user, assistant) exchanges, placed before `user` as examples."""
        text = f"<|im_start|>system\n{system}<|im_end|>\n"
        for u, a in turns or ():
            text += f"<|im_start|>user\n{u}<|im_end|>\n<|im_start|>assistant\n{a}<|im_end|>\n"
        return text + f"<|im_start|>user\n{user}<|im_end|>\n<|im_start|>assistant\n"

    @staticmethod
    def _gumbel(rows, top_k, seed, device):
        """Noise for sampling, made up front so a CUDA graph can replay it: argmax(logits / T + gumbel) over the
        top_k best tokens draws from their softmax at temperature T."""
        g = torch.Generator().manual_seed(int(seed) & 0x7FFFFFFF)
        u = torch.rand(rows, top_k, generator=g).clamp_(1e-10, 1 - 1e-7)
        return (-torch.log(-torch.log(u))).to(device)

    @torch.no_grad()
    def generate(self, system, user, max_new=700, presence_penalty=1.5, stop=None, turns=(), temperature=0.0,
                 top_k=40, seed=0):
        """Greedy, as the upsampler's card says (presence penalty 1.5 for text-to-image), all layers on the card.
        With `temperature` > 0 each token is drawn from the `top_k` best instead (another version of the same text).

        The prompt is read in one pass into a fixed-size KV cache, then each new token is one replay of a CUDA graph
        of the whole step (36 layers, scoring, the presence penalty and argmax): launching ~1,500 small kernels per
        token from Python cost ~200 ms a token, the graph ~15-25 ms (RTX 3070 Laptop)."""
        if any(l.qkv is None or l.gu is None for l in self.layers):
            return self._generate_eager(system, user, max_new, presence_penalty, stop, turns, temperature, top_k, seed)
        dev = self.device
        ids = self.tokens(self.chat_text(system, user, turns))
        n = len(ids)
        maxl = -(-(n + max_new + 1) // 256) * 256
        emb_full = self.embed.dense(dev, torch.bfloat16)  # tied: also the output head
        kcs = [torch.zeros(1, self.kv_heads, maxl, self.head_dim, device=dev, dtype=torch.bfloat16) for _ in self.layers]
        vcs = [torch.zeros_like(k) for k in kcs]
        keys = torch.arange(maxl, device=dev)
        seen = torch.zeros(emb_full.shape[0], device=dev)
        sampling = temperature > 0
        gumbel = self._gumbel(max_new + 2, top_k, seed, dev) if sampling else None

        def pick(lg, row):
            if not sampling:
                return torch.argmax(lg).reshape(1)
            v, i = torch.topk(lg, top_k)
            # gather, not i[j]: indexing with a tensor reads it back to Python, which a CUDA graph cannot capture
            return i.gather(0, torch.argmax(v / temperature + gumbel.index_select(0, row)[0]).reshape(1))

        # prefill
        idx = torch.arange(n, device=dev)
        cos, sin = self._rope(idx[None])
        mask = (keys[None, :] <= idx[:, None])[None, None]
        x = F.embedding(torch.tensor([ids], device=dev), emb_full)
        for i, layer in enumerate(self.layers):
            x = self._layer_static(layer, x, cos, sin, mask, kcs[i], vcs[i], idx)
        logits = F.linear(self._rms(x[:, -1:], self.norm), emb_full)[0, -1].float()
        tok = pick(logits, torch.zeros(1, device=dev, dtype=torch.long))
        pos = torch.full((1,), n, device=dev, dtype=torch.long)
        penalty = float(presence_penalty or 0.0)

        def step():
            seen.index_fill_(0, tok, 1.0)
            c, s = self._rope(pos[None])
            m = (keys <= pos)[None, None, None, :]
            h = F.embedding(tok[None], emb_full)
            for i, layer in enumerate(self.layers):
                h = self._layer_static(layer, h, c, s, m, kcs[i], vcs[i], pos)
            lg = F.linear(self._rms(h, self.norm), emb_full)[0, -1].float() - penalty * seen
            tok.copy_(pick(lg, (pos - (n - 1)).clamp_(max=max_new + 1)))
            pos.add_(1)

        out_ids = []
        graph = None
        try:
            side = torch.cuda.Stream(dev)
            side.wait_stream(torch.cuda.current_stream(dev))
            # warm up on copies of the state, so the real cache and token stay untouched
            save = (tok.clone(), pos.clone(), seen.clone())
            with torch.cuda.stream(side):
                step()
            torch.cuda.current_stream(dev).wait_stream(side)
            tok.copy_(save[0]); pos.copy_(save[1]); seen.copy_(save[2])
            graph = torch.cuda.CUDAGraph()
            with torch.cuda.graph(graph):
                step()
            tok.copy_(save[0]); pos.copy_(save[1]); seen.copy_(save[2])
        except Exception as e:  # noqa: BLE001 - no graph: the same step, launched eagerly
            print(f"[FLUX 3] prompt writer without CUDA graph ({type(e).__name__}: {e})")
            graph = None
        for _ in range(max_new):
            nxt = int(tok)
            if nxt in (IM_END, PAD_ID) or (stop and nxt in stop):
                break
            out_ids.append(nxt)
            if graph is not None:
                graph.replay()
            else:
                step()
        del graph, kcs, vcs, emb_full
        return self.tok.decode(out_ids).strip()

    @torch.no_grad()
    def _generate_eager(self, system, user, max_new=700, presence_penalty=1.5, stop=None, turns=(), temperature=0.0,
                        top_k=40, seed=0):
        dev = self.device
        ids = self.tokens(self.chat_text(system, user, turns))
        gumbel = self._gumbel(max_new + 1, top_k, seed, dev) if temperature > 0 else None
        caches = [dict() for _ in self.layers]
        emb_full = self.embed.dense(dev, torch.bfloat16)  # [V, 2560] for scoring (tied head)
        final_norm = self.norm
        out_ids = []
        seen = torch.zeros(emb_full.shape[0], dtype=torch.bool, device=dev)
        cur = torch.tensor([ids], device=dev)
        pos0 = 0
        for step in range(max_new):
            L = cur.shape[1]
            pos = torch.arange(pos0, pos0 + L, device=dev)[None]
            cos, sin = self._rope(pos)
            if L > 1:
                mask = torch.ones(L, L, dtype=torch.bool, device=dev).tril()[None, None]
            else:
                mask = None
            x = F.embedding(cur, emb_full)

            def run(i):
                nonlocal x
                x = self._layer(self.layers[i], x, cos, sin, mask, caches[i])

            self.streamer.run(list(range(self.n_layers)), run)
            h = self._rms(x[:, -1:], final_norm)
            logits = F.linear(h, emb_full)[0, -1].float()
            if presence_penalty:
                logits = logits - presence_penalty * seen.float()
            if gumbel is not None:
                v, i = torch.topk(logits, top_k)
                nxt = int(i[torch.argmax(v / temperature + gumbel[step])])
            else:
                nxt = int(torch.argmax(logits))
            if nxt in (IM_END, PAD_ID) or (stop and nxt in stop):
                break
            out_ids.append(nxt)
            seen[nxt] = True
            pos0 += L
            cur = torch.tensor([[nxt]], device=dev)
        del emb_full, caches
        return self.tok.decode(out_ids).strip()


def load_config(tokenizer_dir):
    with open(os.path.join(tokenizer_dir, "config.json"), encoding="utf8") as f:
        cfg = json.load(f)
    return cfg.get("text_config", cfg)
