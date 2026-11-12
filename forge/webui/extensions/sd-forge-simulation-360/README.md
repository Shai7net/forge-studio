# Simulation 360

Build and explore panoramic worlds, place objects, connect portals and record camera routes inside Forge.

## Capture a viewpoint

The 📷 button in the project toolbar captures the current view at 1920 pixels wide, preserving the viewport
aspect ratio (bounded to eight megapixels). Camera yaw, pitch, roll, position, current weather, objects and
portals are rendered by the same WebGL engine as the viewport. Editor selection outlines and guides are omitted.
The world is not changed by a capture.

With Forge Studio installed, the shared frame dialog offers Save As to a location of your choice, img2img,
Inpaint, native FIBO/FLUX references, Video Studio Image to Video and Ingredients to Video. Each video/reference
send uses the next empty slot, so captures from different angles or different worlds accumulate in order.
Occupied slots are preserved; a full destination reports that a slot must be cleared. Sending does not Generate.

For integration, `await Sim360.engine.captureFrame()` returns `{canvas, state}` for the current clean view.
`await forgeFrameShare.open({canvas, filename, host: Sim360.ui.root})` presents the shared dialog;
`await forgeFrameShare.send(canvas.toDataURL('image/png'), 'ingredients')` appends a reference directly.

## Validation

Run Python tests with Forge's existing venv: `python -m unittest discover -s tests`.
Run browser-independent geometry/string tests with `node --test tests/core.test.js tests/strings.test.js tests/solid.test.js`.
Live viewport checks require WebGL2. The named Codex test project uses a synthetic panorama to check that
different camera angles produce different captured PNGs without modifying user worlds.
