"""Weights that live in system memory and visit the graphics card one block at a time.

An 8 GB card cannot hold a 14B video model, not even at 3 bits a weight, next to the activations of an 81-frame
clip. So every linear layer keeps its weight where it was loaded (quantised GGUF bytes, fp8 or 16-bit, in pinned
system memory) and a block's weights are copied to the card on a side stream while the block before it computes.
The copy of a 14B block (about 180 MB at Q3) takes ~15 ms on PCIe 4; computing it at 480p takes over a second, so
the stream costs nothing measurable and the card only ever holds two blocks of weights.

Quantised weights are dequantised on the card right before their matmul and dropped after it. LoRAs are applied
on the side (y += up(down(x)) * scale), so they work on quantised weights without merging anything.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F

QK_K = 256

# ggml type id -> (name, block size, bytes per block)
GGML_TYPES = {
    0: ("F32", 1, 4), 1: ("F16", 1, 2), 2: ("Q4_0", 32, 18), 3: ("Q4_1", 32, 20), 6: ("Q5_0", 32, 22),
    7: ("Q5_1", 32, 24), 8: ("Q8_0", 32, 34), 10: ("Q2_K", 256, 84), 11: ("Q3_K", 256, 110),
    12: ("Q4_K", 256, 144), 13: ("Q5_K", 256, 176), 14: ("Q6_K", 256, 210), 30: ("BF16", 1, 2),
}


def _split(blocks, sizes):
    out, start = [], 0
    for size in sizes:
        out.append(blocks[:, start:start + size])
        start += size
    out.append(blocks[:, start:])
    return out


def _f16(x, dtype):
    return x.contiguous().view(torch.float16).to(dtype)


def _scale_min_k4(scales):
    n = scales.shape[0]
    scales = scales.reshape(n, 3, 4)
    d, m, m_d = scales[:, 0], scales[:, 1], scales[:, 2]
    sc = torch.cat([d & 0x3F, (m_d & 0x0F) | ((d >> 2) & 0x30)], dim=-1)
    mn = torch.cat([m & 0x3F, (m_d >> 4) | ((m >> 2) & 0x30)], dim=-1)
    return sc, mn


def _shifts(values, device):
    return torch.tensor(values, device=device, dtype=torch.uint8)


def _deq_q8_0(b, dtype):
    d, x = _split(b, [2])
    return _f16(d, dtype) * x.view(torch.int8).to(dtype)


def _deq_q4_0(b, dtype):
    n = b.shape[0]
    d, qs = _split(b, [2])
    qs = qs.reshape(n, -1, 1, 16) >> _shifts([0, 4], b.device).reshape(1, 1, 2, 1)
    qs = (qs & 0x0F).reshape(n, -1).to(torch.int8) - 8
    return _f16(d, dtype) * qs.to(dtype)


def _deq_q4_1(b, dtype):
    n = b.shape[0]
    d, m, qs = _split(b, [2, 2])
    qs = qs.reshape(n, -1, 1, 16) >> _shifts([0, 4], b.device).reshape(1, 1, 2, 1)
    qs = (qs & 0x0F).reshape(n, -1)
    return _f16(d, dtype) * qs.to(dtype) + _f16(m, dtype)


def _deq_q5_0(b, dtype):
    n = b.shape[0]
    d, qh, qs = _split(b, [2, 4])
    qh = qh.contiguous().view(torch.int32).reshape(n, 1) >> torch.arange(32, device=b.device, dtype=torch.int32).reshape(1, 32)
    ql = qs.reshape(n, -1, 1, 16) >> _shifts([0, 4], b.device).reshape(1, 1, 2, 1)
    qh = (qh & 1).to(torch.uint8)
    ql = (ql & 0x0F).reshape(n, -1)
    q = (ql | (qh << 4)).to(torch.int8) - 16
    return _f16(d, dtype) * q.to(dtype)


def _deq_q5_1(b, dtype):
    n = b.shape[0]
    d, m, qh, qs = _split(b, [2, 2, 4])
    qh = qh.contiguous().view(torch.int32).reshape(n, 1) >> torch.arange(32, device=b.device, dtype=torch.int32).reshape(1, 32)
    ql = qs.reshape(n, -1, 1, 16) >> _shifts([0, 4], b.device).reshape(1, 1, 2, 1)
    qh = (qh & 1).to(torch.uint8)
    ql = (ql & 0x0F).reshape(n, -1)
    q = ql | (qh << 4)
    return _f16(d, dtype) * q.to(dtype) + _f16(m, dtype)


def _deq_q2_k(b, dtype):
    n = b.shape[0]
    scales, qs, d, dmin = _split(b, [QK_K // 16, QK_K // 4, 2])
    d, dmin = _f16(d, dtype), _f16(dmin, dtype)
    dl = (d * (scales & 0xF).to(dtype)).reshape(n, QK_K // 16, 1)
    ml = (dmin * (scales >> 4).to(dtype)).reshape(n, QK_K // 16, 1)
    qs = (qs.reshape(n, -1, 1, 32) >> _shifts([0, 2, 4, 6], b.device).reshape(1, 1, 4, 1)) & 3
    qs = qs.reshape(n, QK_K // 16, 16).to(dtype)
    return (dl * qs - ml).reshape(n, -1)


def _deq_q3_k(b, dtype):
    n = b.shape[0]
    hmask, qs, scales, d = _split(b, [QK_K // 8, QK_K // 4, 12])
    d = _f16(d, dtype)
    lscales, hscales = scales[:, :8], scales[:, 8:]
    lscales = (lscales.reshape(n, 1, 8) >> _shifts([0, 4], b.device).reshape(1, 2, 1)).reshape(n, 16)
    hscales = (hscales.reshape(n, 1, 4) >> _shifts([0, 2, 4, 6], b.device).reshape(1, 4, 1)).reshape(n, 16)
    scales = ((lscales & 0x0F) | ((hscales & 0x03) << 4)).to(torch.int8) - 32
    dl = (d * scales.to(dtype)).reshape(n, 16, 1)
    ql = qs.reshape(n, -1, 1, 32) >> _shifts([0, 2, 4, 6], b.device).reshape(1, 1, 4, 1)
    qh = hmask.reshape(n, -1, 1, 32) >> _shifts(list(range(8)), b.device).reshape(1, 1, 8, 1)
    ql = ql.reshape(n, 16, QK_K // 16) & 3
    qh = (qh.reshape(n, 16, QK_K // 16) & 1) ^ 1
    q = ql.to(torch.int8) - (qh << 2).to(torch.int8)
    return (dl * q.to(dtype)).reshape(n, QK_K)


def _deq_q4_k(b, dtype):
    n = b.shape[0]
    d, dmin, scales, qs = _split(b, [2, 2, 12])
    d, dmin = _f16(d, dtype), _f16(dmin, dtype)
    sc, m = _scale_min_k4(scales)
    d = (d * sc.to(dtype)).reshape(n, -1, 1)
    dm = (dmin * m.to(dtype)).reshape(n, -1, 1)
    qs = qs.reshape(n, -1, 1, 32) >> _shifts([0, 4], b.device).reshape(1, 1, 2, 1)
    qs = (qs & 0x0F).reshape(n, -1, 32).to(dtype)
    return (d * qs - dm).reshape(n, QK_K)


def _deq_q5_k(b, dtype):
    n = b.shape[0]
    d, dmin, scales, qh, qs = _split(b, [2, 2, 12, QK_K // 8])
    d, dmin = _f16(d, dtype), _f16(dmin, dtype)
    sc, m = _scale_min_k4(scales)
    d = (d * sc.to(dtype)).reshape(n, -1, 1)
    dm = (dmin * m.to(dtype)).reshape(n, -1, 1)
    ql = qs.reshape(n, -1, 1, 32) >> _shifts([0, 4], b.device).reshape(1, 1, 2, 1)
    qh = qh.reshape(n, -1, 1, 32) >> _shifts(list(range(8)), b.device).reshape(1, 1, 8, 1)
    ql = (ql & 0x0F).reshape(n, -1, 32)
    qh = (qh & 0x01).reshape(n, -1, 32)
    q = (ql | (qh << 4)).to(dtype)
    return (d * q - dm).reshape(n, QK_K)


def _deq_q6_k(b, dtype):
    n = b.shape[0]
    ql, qh, scales, d = _split(b, [QK_K // 2, QK_K // 4, QK_K // 16])
    scales = scales.contiguous().view(torch.int8).to(dtype)
    d = (_f16(d, dtype) * scales).reshape(n, QK_K // 16, 1)
    ql = ql.reshape(n, -1, 1, 64) >> _shifts([0, 4], b.device).reshape(1, 1, 2, 1)
    ql = (ql & 0x0F).reshape(n, -1, 32)
    qh = qh.reshape(n, -1, 1, 32) >> _shifts([0, 2, 4, 6], b.device).reshape(1, 1, 4, 1)
    qh = (qh & 0x03).reshape(n, -1, 32)
    q = (ql | (qh << 4)).to(torch.int8) - 32
    return (d * q.reshape(n, QK_K // 16, -1).to(dtype)).reshape(n, QK_K)


DEQUANT = {
    "Q8_0": _deq_q8_0, "Q4_0": _deq_q4_0, "Q4_1": _deq_q4_1, "Q5_0": _deq_q5_0, "Q5_1": _deq_q5_1,
    "Q2_K": _deq_q2_k, "Q3_K": _deq_q3_k, "Q4_K": _deq_q4_k, "Q5_K": _deq_q5_k, "Q6_K": _deq_q6_k,
}


class Weight:
    """One stored weight: raw quantised bytes (qtype set) or a float tensor (fp8 with an optional scale, or
    16/32-bit). `cpu` stays in system memory; `gpu` is filled by the streamer only while its block runs."""

    __slots__ = ("cpu", "gpu", "qtype", "shape", "scale")

    def __init__(self, cpu, qtype=None, shape=None, scale=None):
        self.cpu = cpu
        self.gpu = None
        self.qtype = qtype
        self.shape = torch.Size(shape) if shape is not None else cpu.shape
        self.scale = scale

    @property
    def nbytes(self):
        return self.cpu.numel() * self.cpu.element_size()

    def source(self, device):
        if self.gpu is not None:
            return self.gpu
        return self.cpu.to(device, non_blocking=True)

    def materialize(self, device, dtype):
        """The weight as a dense `dtype` tensor on `device`."""
        raw = self.source(device)
        if self.qtype is not None:
            # Dequantise in fp16 (the scales are fp16 in the file), then cast: bf16 arithmetic would round the
            # per-block scale products to 8 bits. Compiled, Q3_K takes 1.6 ms for a 13824x5120 matrix (8.2 eager).
            return _make_deq(self.qtype, dtype)(raw, self.shape)
        if raw.dtype in (torch.float8_e4m3fn, torch.float8_e5m2):
            out = raw.to(torch.float16 if dtype == torch.float16 else torch.float32)
            if self.scale is not None:
                out = out * self.scale
            return out.to(dtype)
        return raw.to(dtype)

    def int8(self, device):
        """(int8 weight [N, K], fp32 per-row scale [N]) on `device`, quantised on the card in one fused pass."""
        raw = self.source(device)
        if self.qtype is not None:
            return _make_deq_int8(self.qtype)(raw, self.shape)
        return _quant_weight(self.materialize(device, torch.float16))

GGML_TYPES_BY_NAME = {name: (tid, block, size) for tid, (name, block, size) in GGML_TYPES.items()}


def _make_deq(qtype, dtype):
    size = GGML_TYPES_BY_NAME[qtype][2]
    deq = DEQUANT[qtype]

    def fn(raw, shape):
        return deq(raw.reshape(-1, size), torch.float16).reshape(shape).to(dtype)
    return fn


def _make_deq_int8(qtype):
    size = GGML_TYPES_BY_NAME[qtype][2]
    deq = DEQUANT[qtype]

    def fn(raw, shape):
        return _quant_weight(deq(raw.reshape(-1, size), torch.float16).reshape(shape))
    return fn


INT8 = {"on": False, "rows": 4096}
try:
    import torch._dynamo
    torch._dynamo.config.cache_size_limit = 64
except Exception:  # noqa: BLE001
    pass
COMPILE = {"on": True, "failed": None}
_compiled = {}


def fast(key, fn):
    """torch.compile'd `fn` (one fused Triton kernel instead of a chain of elementwise passes), or `fn` itself
    when compiling is off or has failed once (no Triton, no compiler): the eager path gives the same numbers."""
    if not COMPILE["on"]:
        return fn
    c = _compiled.get(key)
    if c is None:
        c = _compiled[key] = torch.compile(fn, dynamic=True)

    def call(*args):
        if not COMPILE["on"]:
            return fn(*args)
        try:
            return c(*args)
        except Exception as e:  # noqa: BLE001 - any compiler failure falls back to eager
            COMPILE["on"] = False
            COMPILE["failed"] = f"{type(e).__name__}: {str(e)[:200]}"
            return fn(*args)
    return call


def _quant_weight(w):
    """Per output channel int8. Eager on purpose: compiled per weight shape it recompiled for every new matrix
    and stalled for minutes at full clip size (2026-10-24); in fp16 it costs ~6 ms for a 13824x5120 matrix."""
    w = w.half()
    sw = w.abs().amax(dim=1).float().clamp_(min=1e-8) / 127.0
    return (w * (1.0 / sw).half()[:, None]).round_().clamp_(-127, 127).to(torch.int8), sw


def _quant_rows(x):
    sx = x.abs().amax(dim=1, keepdim=True).float().clamp(min=1e-8) / 127.0
    return torch.round(x.float() / sx).clamp(-127, 127).to(torch.int8), sx


def _dequant_out(acc, sx, sw):
    return (acc.float() * sx * sw[None, :]).to(torch.bfloat16)


def _dequant_out_bias(acc, sx, sw, b):
    return (acc.float() * sx * sw[None, :] + b.float()).to(torch.bfloat16)


def int8_linear(x, wq, sw, b):
    """W8A8 matmul: weight per output channel, activations per token, int32 accumulate on the int8 tensor cores.

    On the RTX 3070 Laptop an 8192x5120 @ 5120x13824 slice takes 16 ms + 2.6 ms of fused (de)quantisation this
    way against 44.7 ms in bf16; a whole 14B forward at 480p measured 46 s against 71 s (2026-10-24). The weight
    goes to _int_mm column-major (wq.t() of a row-major [N, K]), which is the fast kernel (214 ms otherwise).
    Rows go in slices so the int32 result stays small. Output error against bf16: cos 0.9993."""
    shape = x.shape
    x2 = x.reshape(-1, shape[-1])
    wt = wq.t()
    out = torch.empty(x2.shape[0], wq.shape[0], device=x.device, dtype=x.dtype)
    rows = INT8["rows"]
    quant = fast("quant_rows", _quant_rows)
    deq = fast("dequant_out_bias", _dequant_out_bias) if b is not None else fast("dequant_out", _dequant_out)
    for s in range(0, x2.shape[0], rows):
        xs = x2[s:s + rows]
        if xs.shape[0] <= 16:
            out[s:s + rows] = (F.linear(xs.float(), wq.float() * sw[:, None], b.float() if b is not None else None)).to(x.dtype)
            continue
        xq, sx = quant(xs)
        acc = torch._int_mm(xq, wt)
        out[s:s + rows] = deq(acc, sx, sw, b) if b is not None else deq(acc, sx, sw)
        del acc, xq
    return out.reshape(*shape[:-1], wq.shape[0])


class StreamLinear(nn.Module):
    """A Linear whose weight is a `Weight`. LoRA pairs (down, up, scale) live on the card for the whole run."""

    def __init__(self, in_features, out_features, bias=True):
        super().__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.weight = None
        self.bias = None
        self.has_bias = bias
        self.loras = []

    def forward(self, x):
        b = self.bias.materialize(x.device, x.dtype) if self.bias is not None else None
        tokens = x.numel() // x.shape[-1]
        if INT8["on"] and x.dtype == torch.bfloat16 and tokens >= 1024 and self.in_features % 8 == 0 and self.out_features % 8 == 0:
            wq, sw = self.weight.int8(x.device)
            y = int8_linear(x, wq, sw, b)
            del wq, sw
        else:
            w = self.weight.materialize(x.device, x.dtype)
            y = F.linear(x, w, b)
            del w
        for down, up, scale in self.loras:
            # A big LoRA waits in pinned system memory and comes to the card with its layer (see lora.apply).
            down = down.to(x.device, x.dtype, non_blocking=True)
            up = up.to(x.device, x.dtype, non_blocking=True)
            # In place: `y + lora(x)` held two outputs at once (0.7 GB more at 480p with the lightx2v LoRA on every
            # layer, which ran Wan Animate out of memory, 2026-10-25).
            y.view(-1, y.shape[-1]).addmm_(F.linear(x, down).view(-1, down.shape[0]), up.t(), alpha=scale)
        return y

    def weights(self):
        return [w for w in (self.weight, self.bias) if w is not None]


class StreamParam(nn.Module):
    """A loose tensor (modulation, norm weight) streamed like a weight."""

    def __init__(self):
        super().__init__()
        self.weight = None

    def get(self, device, dtype):
        return self.weight.materialize(device, dtype)

    def weights(self):
        return [self.weight] if self.weight is not None else []


def stream_weights(module):
    found = []
    for m in module.modules():
        if isinstance(m, (StreamLinear, StreamParam)):
            found.extend(m.weights())
    return found


class BlockStreamer:
    """Copies block i+1's weights to the card on a side stream while block i computes, and drops block i's
    weights after. `resident` blocks (the first ones) stay on the card for the whole run when memory allows."""

    def __init__(self, blocks, device, resident=0):
        self.blocks = list(blocks)
        self.device = device
        self.stream = torch.cuda.Stream(device=device) if device.type == "cuda" else None
        self.events = {}
        self.resident = set(range(min(resident, len(self.blocks))))
        self.weights = [stream_weights(b) for b in self.blocks]
        for i in self.resident:
            for w in self.weights[i]:
                w.gpu = w.cpu.to(device)

    def block_bytes(self, i):
        return sum(w.nbytes for w in self.weights[i])

    def prefetch(self, i):
        if i >= len(self.blocks) or i in self.resident or i in self.events:
            return
        if self.stream is None:
            for w in self.weights[i]:
                w.gpu = w.cpu.to(self.device)
            self.events[i] = None
            return
        with torch.cuda.stream(self.stream):
            for w in self.weights[i]:
                w.gpu = w.cpu.to(self.device, non_blocking=True)
            event = torch.cuda.Event()
            event.record(self.stream)
        self.events[i] = event

    def ready(self, i):
        self.prefetch(i)
        event = self.events.get(i)
        if event is not None:
            torch.cuda.current_stream(self.device).wait_event(event)
            for w in self.weights[i]:
                w.gpu.record_stream(torch.cuda.current_stream(self.device))

    def release(self, i):
        if i in self.resident:
            return
        for w in self.weights[i]:
            w.gpu = None
        self.events.pop(i, None)

    def run(self, fn):
        """Call fn(i, block) for every block in order with its weights on the card."""
        self.prefetch(0)
        for i, block in enumerate(self.blocks):
            self.ready(i)
            self.prefetch(i + 1)
            fn(i, block)
            self.release(i)

    def close(self):
        for i in range(len(self.blocks)):
            for w in self.weights[i]:
                w.gpu = None
        self.events.clear()
        self.resident.clear()


def pin(tensor):
    """Pinned copy for asynchronous copies; plain tensor when pinning fails (e.g. no room for page-locked RAM)."""
    try:
        return tensor.pin_memory()
    except RuntimeError:
        return tensor.contiguous()
