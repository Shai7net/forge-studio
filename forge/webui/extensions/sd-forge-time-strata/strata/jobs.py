"""The queue of slow work the page asks for. One job runs at a time, on one worker thread: cutting frames out
uses the graphics card, and so does Deforum. The page gets a job's id at once and asks about it until it is
done; the answers carry how far it has come and a small preview of the frame it is on."""

import base64
import io
import threading
import time
import traceback
import uuid

JOBS_KEPT = 30

_jobs = {}
_order = []
_queue = []
_wake = threading.Condition()
_worker = None
_handlers = {}


class JobError(Exception):
    """Something the user should read as it is."""


class Cancelled(JobError):
    def __init__(self):
        super().__init__("cancelled")


class Job:
    def __init__(self, kind, params):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.params = params
        self.status = "waiting"         # waiting, running, done, failed, cancelled
        self.stage = ""
        self.progress = None            # 0..1 when known
        self.preview = None             # a data URL of the frame it is on
        self.result = None
        self.error = None
        self.created = time.time()
        self.started = None
        self.finished = None
        self.cancel_asked = False
        self.on_cancel = None           # what stops work that is not ours (Deforum)
        self.live = None                # frames already made while the job runs (Deforum's, shown as they come)

    def step(self, stage=None, progress=None, preview=None):
        """Called by the work as it goes; raises when the user cancelled."""
        if stage is not None:
            self.stage = stage
        if progress is not None:
            self.progress = min(max(float(progress), 0.0), 1.0)
        if preview is not None:
            self.preview = preview
        if self.cancel_asked:
            raise Cancelled()

    def describe(self, with_preview=True):
        answer = {"id": self.id, "kind": self.kind, "status": self.status, "stage": self.stage,
                  "progress": self.progress, "result": self.result, "error": self.error,
                  "seconds": round((self.finished or time.time()) - (self.started or self.created), 1),
                  "position": _queue.index(self) + 1 if self in _queue else 0}
        if with_preview and self.preview and self.status == "running":
            answer["preview"] = self.preview
        if self.live is not None:
            answer["live"] = dict(self.live)
        return answer


def handler(kind):
    def register(function):
        _handlers[kind] = function
        return function
    return register


def submit(kind, params):
    global _worker
    if kind not in _handlers:
        raise JobError(f"unknown job: {kind}")
    job = Job(kind, params)
    with _wake:
        _jobs[job.id] = job
        _order.append(job.id)
        while len(_order) > JOBS_KEPT:
            old = _jobs.get(_order[0])
            if old is not None and old.status in ("waiting", "running"):
                break
            _jobs.pop(_order.pop(0), None)
        _queue.append(job)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="time-strata-jobs", daemon=True)
            _worker.start()
        _wake.notify()
    return job


def get(job_id):
    job = _jobs.get(job_id)
    if job is None:
        raise KeyError("no such job")
    return job


def cancel(job_id):
    job = get(job_id)
    with _wake:
        if job in _queue:
            _queue.remove(job)
            job.status = "cancelled"
            job.finished = time.time()
            return job
    if job.status == "running":
        job.cancel_asked = True
        if job.on_cancel:
            try:
                job.on_cancel()
            except Exception:
                traceback.print_exc()
    return job


def _work():
    while True:
        with _wake:
            while not _queue:
                _wake.wait(timeout=60)
                if not _queue:
                    return
            job = _queue.pop(0)
        job.status = "running"
        job.started = time.time()
        try:
            job.result = _handlers[job.kind](job)
            job.status = "cancelled" if job.cancel_asked else "done"
        except Cancelled:
            job.status = "cancelled"
        except JobError as e:
            job.error = str(e)
            job.status = "failed"
        except Exception as e:
            traceback.print_exc()
            job.error = f"{type(e).__name__}: {e}"
            job.status = "failed"
        finally:
            job.finished = time.time()
            job.preview = None


def preview_of(image, side=256):
    """A small JPEG of a picture (an RGBA one is shown over a checkerboard, so its cut can be judged)."""
    from PIL import Image
    image = image.copy()
    image.thumbnail((side, side))
    if image.mode == "RGBA":
        board = Image.new("RGB", image.size, (200, 200, 200))
        dark = Image.new("RGB", (8, 8), (150, 150, 150))
        for y in range(0, image.size[1], 8):
            for x in range((y // 8) % 2 * 8, image.size[0], 16):
                board.paste(dark, (x, y))
        board.paste(image, mask=image.split()[3])
        image = board
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
