"""Linear layers for the FLUX 3 files as ComfyUI stores them, run the fast way an RTX 30 card allows.

The int8 files ("int8-convrot") keep every big matrix as int8 with one scale per output row, after a fixed
rotation of the input side: W_rot = W @ H, where H is a block-diagonal "regular" Hadamard matrix of 256x256
blocks (symmetric and orthogonal). A matmul then needs the same rotation on the activations,
x @ W.T == (x @ H) @ W_rot.T, which also spreads the outliers of x so that one int8 scale per token is enough.
Activations are quantised per token and the product runs on the int8 tensor cores (torch._int_mm): on the RTX 3070
Laptop that is about twice the bf16 speed (measured for the Video Studio engine, 2026-10-24). Plain bf16/fp16/fp32
weights are run with F.linear.

Weights stay where they were loaded (pinned system memory) unless a streamer puts them on the card: `BlockStreamer`
copies the next block's weights on a side stream while the current block computes.
"""

import json
import math

import torch
import torch.nn.functional as F

_HADAMARD = {}


def hadamard(size, device, dtype):
    """The normalised regular Hadamard matrix ComfyUI's ConvRot uses (kron powers of a symmetric 4x4)."""
    key = (size, str(device), dtype)
    h = _HADAMARD.get(key)
    if h is None:
        if size < 4 or math.log(size, 4) % 1 != 0:
            raise ValueError(f"ConvRot group size must be a power of 4, got {size}")
        h4 = torch.tensor([[1, 1, 1, -1], [1, 1, -1, 1], [1, -1, 1, 1], [-1, 1, 1, 1]], dtype=torch.float32)
        h = h4
        while h.shape[0] < size:
            h = torch.kron(h, h4)
        h = (h / math.sqrt(size)).to(device=device, dtype=dtype)
        _HADAMARD[key] = h
    return h


def rotate(x, group):
    """x @ H_block for the last dimension, in groups of `group` features."""
    shape = x.shape
    h = hadamard(group, x.device, x.dtype)
    return torch.matmul(x.reshape(-1, shape[-1] // group, group), h).reshape(shape)


def quant_rows(x):
    """Symmetric int8 per row (token): (int8 [M, K], fp32 scale [M, 1]).

    Few passes over x on purpose (the elementwise work around the int8 matmuls was half of a step, profiled
    2026-10-26): the row max is one reduction, and x * (127 / max) goes straight to fp32."""
    amax = torch.linalg.vector_norm(x, ord=float("inf"), dim=1, keepdim=True, dtype=torch.float32).clamp_(min=1e-8)
    q = torch.mul(x, 127.0 / amax).round_().clamp_(-127, 127).to(torch.int8)
    return q, amax / 127.0


# _int_mm wants more than 16 rows; fewer rows are padded (one generated token of the prompt writer is one row)
_MIN_ROWS = 17
ROWS_PER_SLICE = 8192
SLICE_BYTES = 192 << 20


def int8_matmul(x, wq, sw, out_dtype):
    """x [M, K] (already rotated if the weight is) @ wq [N, K].T, int8 x int8 -> int32 on the tensor cores.

    wq.t() is a column-major [K, N] view, the layout of the fast kernel. Rows go in slices so the int32 result
    stays small at 2-4 megapixels."""
    m = x.shape[0]
    out = torch.empty(m, wq.shape[0], device=x.device, dtype=out_dtype)
    wt = wq.t()
    # an int32 slice result of at most SLICE_BYTES (the fused q/k/v/MLP matrix is 27648 wide)
    rows = max(256, min(ROWS_PER_SLICE, SLICE_BYTES // (wq.shape[0] * 4)))
    for s in range(0, m, rows):
        xs = x[s:s + rows]
        n = xs.shape[0]
        if n < _MIN_ROWS:
            xs = torch.cat([xs, xs.new_zeros(_MIN_ROWS - n, xs.shape[1])])
        xq, sx = quant_rows(xs)
        acc = torch._int_mm(xq, wt)
        del xq
        # int32 * per-column scale -> fp32 in one pass, then * per-row scale written straight into the bf16 output
        torch.mul(torch.mul(acc[:n], sw[None, :]), sx[:n], out=out[s:s + n])
        del acc, sx
    return out


class Weight:
    """One stored tensor. `cpu` stays in system memory; `gpu` is set while it is on the card."""

    __slots__ = ("cpu", "gpu")

    def __init__(self, cpu):
        self.cpu = cpu
        self.gpu = None

    @property
    def nbytes(self):
        return self.cpu.numel() * self.cpu.element_size()

    def on(self, device):
        if self.gpu is not None:
            return self.gpu
        return self.cpu.to(device, non_blocking=True)


class Linear:
    """A bias-free linear layer: int8 ConvRot (weight int8 [N, K], scale fp32 [N], rotation group) or float."""

    def __init__(self, weight, scale=None, group=0):
        self.weight = Weight(weight)
        self.scale = Weight(scale.float().reshape(-1)) if scale is not None else None
        self.group = group
        self.out_features, self.in_features = weight.shape
        self.loras = []  # (down [r, K], up [N, r], strength) in bf16, applied on the side

    @property
    def int8(self):
        return self.scale is not None

    def weights(self):
        return [w for w in (self.weight, self.scale) if w is not None]

    def __call__(self, x, rotated=None):
        """`rotated` is x already rotated (and flattened) when several layers share one input."""
        shape = x.shape
        x2 = x.reshape(-1, shape[-1])
        dev = x.device
        if self.int8:
            xr = rotated if rotated is not None else (rotate(x2, self.group) if self.group else x2)
            y = int8_matmul(xr, self.weight.on(dev), self.scale.on(dev), x.dtype)
        else:
            y = F.linear(x2, self.weight.on(dev).to(x.dtype))
        for down, up, strength in self.loras:
            y.addmm_(F.linear(x2, down.to(dev, x.dtype)), up.to(dev, x.dtype).t(), alpha=strength)
        return y.reshape(*shape[:-1], self.out_features)

    def dense(self, device, dtype):
        """The weight as a float matrix in the original basis (for the token embedding and checks)."""
        w = self.weight.on(device)
        if not self.int8:
            return w.to(dtype)
        w = w.float() * self.scale.on(device)[:, None]
        if self.group:
            w = rotate(w, self.group)  # W = W_rot @ H (H is symmetric and its own inverse)
        return w.to(dtype)


def fuse(linears):
    """One Linear that computes several layers sharing an input (q, k, v and the MLP input): one rotation, one
    activation quantisation and one big int8 matmul instead of four. Returns (Linear, output sizes)."""
    sizes = [l.out_features for l in linears]
    first = linears[0]
    same = all(l.int8 == first.int8 and l.group == first.group and l.in_features == first.in_features for l in linears)
    if not same:
        return None, sizes
    w = torch.cat([l.weight.cpu for l in linears])
    s = torch.cat([l.scale.cpu for l in linears]) if first.int8 else None
    return Linear(w, s, first.group), sizes


def split_rows(lin, sizes):
    """A Linear cut into several by output rows (per-row scales make this exact)."""
    out, start = [], 0
    for n in sizes:
        w = lin.weight.cpu[start:start + n]
        s = lin.scale.cpu[start:start + n] if lin.int8 else None
        out.append(Linear(w, s, lin.group))
        start += n
    return out


def read_quant_conf(sd, prefix):
    raw = sd.get(prefix + "comfy_quant")
    if raw is None:
        return None
    return json.loads(raw.numpy().tobytes())


def take_linear(sd, prefix):
    """Build a Linear from `prefix`weight (+ ComfyUI quantisation keys) in a state dict."""
    w = sd[prefix + "weight"]
    conf = read_quant_conf(sd, prefix)
    if conf is None and w.dtype != torch.int8:
        return Linear(w)
    fmt = (conf or {}).get("format", "int8_tensorwise")
    if fmt != "int8_tensorwise":
        raise ValueError(f"{prefix}: unsupported quantisation '{fmt}' (this card runs the int8 or bf16 files)")
    params = (conf or {}).get("params", {}) if isinstance((conf or {}).get("params", {}), dict) else {}
    convrot = (conf or {}).get("convrot", params.get("convrot", False))
    group = int((conf or {}).get("convrot_groupsize", params.get("convrot_groupsize", 256))) if convrot else 0
    scale = sd[prefix + "weight_scale"].float()
    if scale.numel() == 1:
        scale = scale.reshape(1).expand(w.shape[0]).contiguous()
    return Linear(w, scale, group)


class BlockStreamer:
    """Keeps the first `resident` blocks on the card and streams the others in: block i+1's weights are copied on
    a side stream while block i computes, and block i's are dropped after it."""

    def __init__(self, blocks_weights, device):
        self.weights = blocks_weights  # list (per block) of lists of Weight
        self.device = device
        self.stream = torch.cuda.Stream(device=device) if device.type == "cuda" else None
        self.events = {}
        self.resident = set()

    def block_bytes(self, i):
        return sum(w.nbytes for w in self.weights[i])

    def set_resident(self, n):
        n = max(0, min(n, len(self.weights)))
        want = set(range(n))
        for i in self.resident - want:
            for w in self.weights[i]:
                w.gpu = None
        for i in want - self.resident:
            for w in self.weights[i]:
                w.gpu = w.cpu.to(self.device)
        self.resident = want

    def prefetch(self, i):
        if i >= len(self.weights) or i in self.resident or i in self.events:
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
            cur = torch.cuda.current_stream(self.device)
            cur.wait_event(event)
            for w in self.weights[i]:
                w.gpu.record_stream(cur)

    def release(self, i):
        if i in self.resident:
            return
        for w in self.weights[i]:
            w.gpu = None
        self.events.pop(i, None)

    def run(self, indices, fn):
        """fn(i) for the blocks `indices` in order, each with its weights on the card."""
        if not indices:
            return
        self.prefetch(indices[0])
        for n, i in enumerate(indices):
            self.ready(i)
            if n + 1 < len(indices):
                self.prefetch(indices[n + 1])
            fn(i)
            self.release(i)

    def offload(self):
        self.set_resident(0)
        for i in list(self.events):
            self.release(i)


def pin(t):
    try:
        return t.pin_memory()
    except RuntimeError:
        return t.contiguous()


class PinnedArena:
    """All weights of a model in one exactly-sized block of system memory, page-locked with cudaHostRegister so
    copies to the card run asynchronously at full speed.

    tensor.pin_memory() goes through PyTorch's host caching allocator, which rounds every block up to a power of two
    and keeps freed blocks: FLUX 3 took 18 GB of RAM for 9.2 GB of weights, and 5 GB stayed after unloading
    (measured 2026-10-26)."""

    ALIGN = 256

    def __init__(self, weights):
        uniq, seen = [], set()
        for w in weights:
            if id(w) not in seen:
                seen.add(id(w))
                uniq.append(w)
        total = sum(-(-w.nbytes // self.ALIGN) * self.ALIGN for w in uniq)
        self.buf = torch.empty(total, dtype=torch.uint8)
        off = 0
        for w in uniq:
            t = w.cpu.contiguous()
            n = t.numel() * t.element_size()
            view = self.buf[off:off + n].view(t.dtype).view(t.shape)
            view.copy_(t)
            w.cpu = view
            off += -(-n // self.ALIGN) * self.ALIGN
        self.owners = uniq
        self.registered = False
        try:
            err = torch.cuda.cudart().cudaHostRegister(self.buf.data_ptr(), self.buf.numel(), 0)
            self.registered = int(err) == 0 if not hasattr(err, "value") else err.value == 0
        except Exception:  # noqa: BLE001 - not page-locked: copies are synchronous but still work
            self.registered = False
        self.nbytes = total

    def release(self):
        if self.registered:
            try:
                torch.cuda.cudart().cudaHostUnregister(self.buf.data_ptr())
            except Exception:  # noqa: BLE001
                pass
            self.registered = False
        # the weights hold views into the block: while anything still holds a weight (an old model object kept
        # somewhere), the whole block stayed in memory - 14 GB of Forge's after an "unload" (2026-11-09)
        for w in getattr(self, "owners", ()):
            w.cpu = None
        self.owners = []
        self.buf = None
