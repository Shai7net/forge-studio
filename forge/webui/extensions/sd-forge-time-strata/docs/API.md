# Time Strata API

Routes are registered on Forge's existing FastAPI app at `/time-strata`. Replace `http://127.0.0.1:7860` with your actual Forge URL, including a proxy path if applicable. These routes do not require `--api` or `--deforum-api`.

Use the extension with your trusted local Forge session. The routes do not implement a separate authentication layer; host/proxy access controls determine who can reach them.

## Quick examples

Create a project before uploading to it. Uploads return a relative `path`; use that returned value because filenames are sanitised and duplicates are renamed.

### curl

POSIX shell syntax (`curl.exe` can be used on Windows with equivalent quoting):

```bash
curl --fail-with-body -X POST http://127.0.0.1:7860/time-strata/project/create \
  -H 'Content-Type: application/json' -d '{"name":"My strata"}'

curl --fail-with-body -X POST \
  'http://127.0.0.1:7860/time-strata/upload?project=My%20strata&filename=dance.mp4' \
  -H 'Content-Type: application/octet-stream' --data-binary @dance.mp4

# Use the actual path returned by the upload above.
curl --fail-with-body -X POST http://127.0.0.1:7860/time-strata/job/start \
  -H 'Content-Type: application/json' \
  -d '{"kind":"chain","params":{"project":"My strata","chain":"c1","source":{"kind":"video","path":"uploads/dance.mp4"},"options":{"fps":24,"end":6,"matte":"key"}}}'

# Substitute the returned job id.
curl --fail-with-body 'http://127.0.0.1:7860/time-strata/job?id=JOB_ID&preview=false'
```

This example uses colour keying; choose a suitable plain-background clip. For natural footage, configure a matting model as described in [compatibility setup](COMPATIBILITY.md).

### JavaScript

Run in the Forge page's browser console to create a project and inspect available options:

```javascript
const base = './time-strata';
async function strata(path, body) {
  const response = await fetch(`${base}/${path}`, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
console.log(await strata('options'));
const project = await strata('project/create', {name: 'Browser demo'});
console.log(project);
```

### Python

Uses the standard library. Set `SOURCE` to a short clip with a flat backdrop. This creates a project, uploads the video, builds a chain and polls its job with a timeout.

```python
import json
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = 'http://127.0.0.1:7860/time-strata'
PROJECT = 'Python demo'
SOURCE = Path('dance.mp4')

def call(route, body=None, raw=False):
    data = body if raw else (json.dumps(body).encode() if body is not None else None)
    headers = {'Content-Type': 'application/octet-stream' if raw else 'application/json'}
    with urlopen(Request(f'{BASE}/{route}', data=data, headers=headers), timeout=120) as response:
        return json.load(response)

call('project/create', {'name': PROJECT})
upload = call('upload?' + urlencode({'project': PROJECT, 'filename': SOURCE.name}),
              SOURCE.read_bytes(), raw=True)
job = call('job/start', {'kind': 'chain', 'params': {
    'project': PROJECT, 'chain': 'c1',
    'source': {'kind': 'video', 'path': upload['path']},
    'options': {'fps': 12, 'end': 3, 'height': 512, 'matte': 'key'}
}})
deadline = time.monotonic() + 1800
while True:
    state = call('job?' + urlencode({'id': job['id'], 'preview': 'false'}))
    print(state['status'], state['stage'])
    if state['status'] == 'done':
        print(state['result'])
        break
    if state['status'] in ('failed', 'cancelled'):
        raise RuntimeError(state.get('error') or state['status'])
    if time.monotonic() > deadline:
        call('job/cancel', {'id': job['id']})
        raise TimeoutError('Chain extraction exceeded 30 minutes')
    time.sleep(1)
```

Job creation writes prepared chain files. The browser owns the scene document: to place a programmatically built chain in the editor, its scene entry must also be added to `doc.chains` using the same schema as an editor-created chain. Back up and fetch the latest document before using `project/save` so you do not overwrite unsaved browser edits.

## Routes

All names below are relative to `/time-strata/`. POST bodies are JSON unless noted.

| Method and route | Parameters / purpose |
| --- | --- |
| `GET options` | Version, data locations, matting models, export kinds and the Real-ESRGAN upscaler (`upscale: {ready, models}`) |
| `GET deforum/options` | Deforum presence, the ControlNet model and preprocessor found for each unit (`pose`, `depth`, `edges`, `shape`, `look`), the checkpoints, the loaded one and the last generation settings |
| `POST job/start` with kind `sharpen` | `{project, chain, upscale: "model:scale", height}`: the chain cut again from its source with Real-ESRGAN first |
| `GET dqt` | DeforumationQT's mediator, read only: `{running, values: {translation_x, translation_y, translation_z, rotation_x, rotation_y, fov}}` |
| `GET projects` | Project list |
| `GET project?name=...` | Read the scene document |
| `POST project/create` | `{name}` |
| `POST project/save` | `{name, doc}`; complete scene document |
| `POST project/delete` | `{name}`; moves the project to `_deleted` |
| `POST upload?project=...&filename=...[&folder=...]` | Raw file bytes; returns `{path, size, ...}` |
| `POST job/start` | `{kind, params}`; kinds `chain` and `deforum` |
| `GET job?id=...[&preview=false]` | Status, progress, error and result; optional preview |
| `POST job/cancel` | `{id}` |
| `GET chain?project=...&chain=...[&measures=true]` | Prepared chain metadata |
| `POST chain/delete` | `{project, chain}`; removes the chain's generated files |
| `GET file/{project}/{path}` | Project media; supports byte ranges |
| `POST export/start` | Recording options; see below |
| `POST export/frame?id=...[&jpeg=true]` | Raw bottom-first RGBA bytes, or JPEG with `jpeg=true` |
| `POST export/finish` | `{id}`; returns filename, size, frame count and download URL |
| `POST export/cancel` | `{id}`; discards an unfinished recording |
| `GET export/file/{name}` | Download an export |
| `POST open-folder` | `{project}` or `{exports:true}`; Explorer on Windows |
| `GET sim360/worlds` | Available Simulation 360 panoramas |
| `GET sim360/thumb/{project}/{world}/{name}` | World preview |
| `POST sim360/take` | `{project, from_project, world, file}`; copy a panorama |

URL-encode query values and path segments. Job states are `waiting`, `running`, `done`, `failed` and `cancelled`. A running `deforum` job also reports `live: {frames, total, width, height, fps, path}`: the frames Deforum has drawn so far, already cut, at `file/{project}/{path}/{000000}.webp`.

A `deforum` job's `options` may hold `scenes` (`[{from, text, texture, look}]`, seconds of the motion; `texture` and `look` are project paths), `blend` (seconds), `prefix`, `suffix`, `negative`, `fill` (`{amount, texture}`), `look`, `units` (`{pose|depth|edges|shape|look: {on, weight, start, end}}`), `steady` (`{on, weight}`: anchor frames per scene held by IP-Adapter), `redraw` (frame numbers of the drawn chain `chain` to draw again; the rest is kept) with `redraw_seed` (moved past every seed the chain was drawn with; the seeds used are kept in its record), `upscale` (`"realesr-animevideov3:2"`, `"realesrgan-x4plus:2"`, `"realesrgan-x4plus-anime:2"` or ×4), `checkpoint`, `sampler`, `steps`, `cfg`, `strength`, `seed`, `background`, `first`, `last` and `every`, and `skip_empty` (default true: motion frames with no figure are not drawn; the drawn chain's record keeps `extra.deforum.motion_frames`, the motion frame of each drawn frame, and the job result lists the `skipped` ones). A chain's result and record list `suspects` (frames that look wrongly cut) and `figureless` (frames with no figure). Jobs are in memory, survive only for the current server process and retain up to 30 recent records subject to active jobs.

## Recording protocol

`export/start` requires `width` and `height`; optional fields are `project`, `name`, `fps` (30), `kind` (`mp4-h264`), `quality` (17), `supersample` (1), `music` (relative project path) and `music_start` (0). Kinds are `mp4-h264`, `mp4-nvenc` and `webm-vp9`.

The response contains `id`, `drawn` and `path`. Send **one frame per request**, waiting for each response. The raw frame dimensions are `drawn[0] × drawn[1]`, with four bytes per pixel, bottom row first. JPEG input is decoded and flipped to the same internal convention. Call `export/finish` after the last frame, or `export/cancel` on failure.

Rendering the WebGL scene is the browser's responsibility. Starting an export session alone does not render frames or create a finished movie. `quality` is CRF for software H.264/VP9 and CQ for NVENC; lower values generally increase quality and file size.
