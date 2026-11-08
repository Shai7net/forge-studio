
# Deforum Stable Diffusion — official extension for Forge

This is the Deforum extension for the [Stable Diffusion WebUI Forge](https://github.com/lllyasviel/stable-diffusion-webui-forge).
It is a fork of [the Deforum Extension for A1111](https://github.com/deforum-art/sd-webui-deforum) and is expected to diverge over time.

## Current status

This extension is **experimental**. It should load and basic settings should work. However, the full surface area of Deforum has not yet been tested. 
You are encouraged to take it for a spin and [raise issues here](https://github.com/deforum-art/sd-forge-deforum/issues) as you find problems.

## Installation

After setting up the [Stable Diffusion WebUI Forge](https://github.com/lllyasviel/stable-diffusion-webui-forge):

    cd <forge_install_dir>/exensions
    git clone https://github.com/deforum-art/sd-forge-deforum
    cd sd-forge-deforum
    pip install -r requirements.txt

## What works & what doesn't

TODO

## Compatibility notes

* If you load a settings file from the a1111 extension into the Forge extension, you will need to reselect your controlnet preprocessors (they are referenced using different identifiers).
* Forge reduces VRAM usage by more agressively unloading models from memory. This can cause delays between frame generations while models are juggled in and of memory. To reduce this, start Forge with `--always-high-vram` (assuming you have sufficient VRAM).

## Audio Keyframes (music → schedules)

The **Keyframes → Audio Keyframes** sub-tab turns a song into Deforum schedules, in the spirit of the
[audio keyframe generator](https://www.chigozie.co.uk/audio-keyframe-generator/), and writes them straight into the fields.

1. **Music**: upload a song, or paste its path or URL (quotes are fine). The tab measures the loudness of every frame
   the way the website does: the block mean of |L|+|R|. It does this for the full mix and for Bass (<150 Hz),
   Mids (150–2000 Hz) and Highs (>2000 Hz). *Raw keyframes* gives the website's output for any `f(x, y)`.
2. **🎧 Listen and mark frames**: the song plays in the tab, next to the frame that is playing (at the keyframes FPS),
   the prompt active there and the song part. A timeline shows the whole song and, below it, a six-second close-up with
   a number on every frame; click either one to jump. After a click on the player or the timeline the keys work:
   Space plays or pauses, ← → move one frame, Shift + ← → one second, **M** adds a prompt key at the frame and **P**
   starts a song part there (with the directions of the part it splits). While the song plays, marks land
   *Reaction time* (0.15 s) earlier to make up for reaction time; while it is paused they land exactly on the frame shown.
3. **Song parts and directions**: a table with one row per part of the song and one column per motion (Zoom, Angle,
   X, Y, Z, Rot X/Y/Z, perspective flip). Write `+`, `-`, `0` or a number such as `0.5` in each cell.
   Parts can be split equally, every X seconds, or automatically where the song changes. New rows repeat the pattern
   of the existing rows.
4. **Parameters**: tick any schedule (Strength, CFG, Noise, every Motion field, FOV, transform centre, perspective flip).
   Each row has the following settings:
   - **Function**: the row's own function of `x`, the loudness 0..1 of its band, like on the website. The default
     `1 + x^4` reacts mostly to peaks and `x` evenly; `bass`, `mids`, `highs`, `t` (frame) and `sec` work too.
   - **Quiet / Loud**: the function is stretched between these values at the quietest and at the loudest moment of the
     song, e.g. Translation Z 0.40 → 1 or Strength 0.60 → 0.80. Leave both empty to use the function's own values.
   - **Smooth**: how many frames the value takes to fall back after a hit.
   - **Min / Max**: hard limits.

   The preview redraws when a changed field is left. Settings saved before rows had a Function still load: a Curve `c`
   becomes the function `x^c`, and an old custom formula becomes its row's function with Quiet and Loud empty.
5. **Apply to Deforum** writes the schedules and can also set the Output FPS, the soundtrack (File + path) and
   Max frames = ceil(song length × FPS). If the Output FPS is changed later, the tab warns that the keyframes no longer match.
6. **Key-prompts** adds prompt keys at loudness jumps, the loudest or quietest moments, the lowest Strength, or the
   song-part starts, with a minimum gap. Prompts switch exactly at their frame.
7. Settings can be saved and loaded as JSON. The last Apply is kept in `models/Deforum/audio_keyframes_last.json`.
8. **🎬 Camera directing**: pick a camera mode and press *Direct the camera*. The mode splits the analysed song into parts
   and fills the camera rows (which motions move, how fast with which band, and when they switch direction); then press
   Apply. Built in: Gentle, Action!, Artistic, Dreamy and Handheld (in 2D, Translation Z becomes Zoom and Rotation 3D Z
   becomes Angle). *Save* keeps the ticked camera rows and the song-part pattern as a new mode; *Learn* reads the Motion
   tab (for example a video whose camera you liked) and saves its style: for every axis the band, function and speed
   range it follows, and whether it switches direction every N frames, on the beats or with the song parts. Saved and
   learned modes are in `models/Deforum/camera_modes.json`.

Each camera row also has **Switch**: `10` turns the direction around every 10 frames, `beats` on the beats,
`beats 1.5` on beats at least 1.5 s apart, empty = never.

In **Keyframes → Motion**, every camera schedule (Zoom, Angle, Translation X/Y/Z, Rotation 3D X/Y/Z) has small buttons:
**⇄** flips its direction (right ↔ left, forward ↔ back) and **− ↔N +** switch it every N frames (1 = every frame,
off = one direction). They keep the speeds and work on any schedule, including math like `sin(t)`.

Notes:
* Lower Strength means more change. The default Strength 0.60 (quiet) → 0.80 (loud) changes the picture in the quiet
  parts and keeps it steady on the peaks; swap Quiet and Loud for the opposite.
* The timeline is redrawn after Analyze, a split, a mark or Create key-prompts; rows typed into the song-parts table by
  hand show on it after the next Analyze. Prompts typed by hand show at once.
* Zoom and Angle work in 2D only. In 3D use Translation Z and Rotation 3D.
* With DeforumationQT ON, its switch for a parameter (Strength, CFG, FOV, zoom, pan, rotation, tilt) decides what drives it:
  on, DeforumationQT's value replaces the schedule; off, Deforum uses these schedules. The switches can be flipped mid-render.
* The tab is not part of Deforum's run arguments or settings files. It only fills in existing fields, so the render
  code, the API and DeforumationQT's hooks are unchanged. Uploaded songs are copied to `<outputs>/Deforum_audio/`.
* Unit tests (no webui needed): from `scripts/`, run `python -m unittest deforum_helpers.audio_keyframes_test`.

## 🗺️ Map Key (draw the camera, the prompts and Strength)

The **Keyframes → 🗺️ Map Key** sub-tab is a visual editor on one timeline. The timeline shows the song chosen in
Audio Keyframes (loudness, bass, beats and song parts), the prompts, a Strength lane and one lane per camera motion.
In 3D the motion lanes are Translation Z/X/Y and Rotation 3D Y/X/Z; in 2D they are Zoom, X, Y and Angle.
**📥 Load from Deforum** reads what the fields hold now: any schedule, including math like `sin(t)`, parsed by Deforum's
own parser, plus the prompts, Max frames, Cadence and FPS. It runs by itself on the first visit.

- **Lanes:**
  - Draw with ✏: up is fast, down is slow or stopped. 📏 draws a straight line.
  - ⬚ selects a part, then:
    - **− Minus** sends the camera the other way there (back, left, down) at the same speed; the part turns red.
    - **+** sets it forward again and ⇄ flips it.
    - ≈ smooths, ×½ and ×2 change the speed, and ⟲ resets.
  - Dragging on the music row selects that time in every lane.
  - Each lane's − / + buttons change its range, so faster speeds can be drawn.
  - ♫ sets how much the lane follows the music. 0 % is the drawing. 100 % makes the drawing the ceiling, with the
    music (a band, "peaks" = loudness⁴ or "even") moving the lane within it.
- **Prompts:**
  - Drag them along the timeline; they snap to beats or song parts. Double-click adds a prompt.
  - A click opens the text and a **transition strength**: Strength at the change, e.g. 0.45. The dip holds for Cadence
    frames, so a generated frame always gets it, then comes back over N frames. It moves with its prompt.
  - Formula keys like `max_f-1` keep their formula unless they are moved.
- **Camera path:**
  - Tick *The camera follows a path* and draw the path on the map, seen from above with up = forward.
  - Drag points to move them; double-click adds a point and right-click deletes one. A path that ends near its start
    is a closed loop, and Laps sets how many rounds it makes.
  - The height lane lifts the path along its length. The 3D view shows the path and the camera at the playhead:
    drag to turn the view, use the wheel to zoom, and Shift + drag a point to raise it.
  - The *Speed along the path* lane sets how fast the camera travels, in Translation Z units; minus drives back along
    the path. The path is stretched so its end is reached at the furthest point of the song.
  - *Looks along the path* turns the camera in the bends: turning right = Rotation 3D Y +, looking up = Rotation 3D X +,
    and one round of a loop = 360°. *Always looks forward* slides sideways instead. The *smooth turns* setting spreads
    the turns over that many frames.
  - Each frame's motion is seen from the camera, as Deforum moves every frame relative to the one before. In 2D,
    forward becomes Zoom `1 + z/100`, right becomes Translation X − and up becomes Translation Y +.
  - The lanes the path drives are shown in cyan and are read-only. **🍞 Bake** copies them into ordinary lanes to edit
    by hand; Undo brings the path back.
  - While no path drives the camera, the map and the 3D view show a dashed orange trail. It is where the lanes as they
    are take the camera, whether they came from Audio Keyframes, a camera mode or typing.
  - The trail is worked out like Deforum does it: every frame moves and turns the camera along its own axes.
  - **Climbing inside a bend:** Deforum turns the camera about its own axes, so a turn while tilted by an angle p is
    written as Pan × cos(p) plus a small roll of −turn × sin(p). The roll is added on top of your own Roll lane
    (Rotation 3D Z stays editable) and keeps the horizon level.
    - Deforum projects each pixel as the point (x, y, 1) with a focal length of 1/tan(FOV/2), so it turns the picture
      away from its centre that much more strongly than a real camera. The roll therefore gets the same factor, per
      frame, from the FOV schedule that Load reads. The path bar shows the FOV it is tuned for; press 📥 Load again
      after changing the FOV.
    - Measured on Deforum's own renders of a path that climbs 19° inside a 33° right-hand bend (the horizon at the end
      / on average over the render):

      | | no roll | geometric roll | what Map Key writes |
      |---|---|---|---|
      | Diffusion render (cyberrealistic, LCM 8 steps, depth warping on, cadence 2) | 7.50° / 3.90° | 2.19° / 1.37° | **0.00° / 0.16°** |
      | Motion preview (no depth) | 7.86° | 2.33° | **0.07°** |

      The roll turned the other way made it 13.79°.
- **✅ Apply to Deforum** writes only what was edited:
  - lanes ticked *write* (a lane ticks itself when it is edited);
  - the lanes the path drives;
  - the prompts, if they changed.
  - A lane you did not touch keeps its text, formulas included. Keys are only written where the line bends: Deforum's
    in-betweens stay within one step of the drawing (0.001 for motion), so a drawn straight line is two keys.
- **Saving:** every Apply is also kept in `models/Deforum/map_key_last.json` (*↩ Open the last Apply*). Named projects,
  which hold the lanes, directions, music settings, prompts with their dips, the path and the heights, are saved in
  `models/Deforum/map_key/`.
- **Keys:** they work after a click in the editor and use `event.code`, so a Hebrew keyboard works too.
  - Space plays and pauses. ← → move one frame, and one second with Shift.
  - D draws, L draws a line and S selects.
  - `-` / `=` apply minus / plus, and F flips.
  - M adds a prompt at the playhead, and Delete deletes.
  - Ctrl+Z / Ctrl+Y undo and redo.
  - The wheel zooms the timeline, and Shift + wheel scrolls it.
  - ⛶ opens the editor full screen, and Esc leaves.
- The editor shows English and Hebrew itself: the Hebrew UI switch picks the language, and the Hebrew engine skips the
  editor (`.hebrew-ui-skip`).
- The code is in `javascript/map-key-core.js` (the logic, no DOM), `javascript/map-key.js` (the editor),
  `scripts/deforum_helpers/map_key.py` (load, apply, projects) and `map_key_ui.py` (the tab).
- Tests, with no webui needed:
  - `node --test extensions/sd-forge-deforum/tests/map_key_core.test.js`
  - from `scripts/`: `python -m unittest deforum_helpers.map_key_test`

## DeforumationQT bar

The bar above the preview has two halves: a green **DeforumationQT: ON** / red **DeforumationQT: OFF** button,
and **🎛 Open DeforumationQT**.

- **ON**: renders are controlled by DeforumationQT through its mediator, so start `runme_windows_named_pipes.bat` first. A render that finds no mediator waits for it; switching OFF lets that render go on without it.
- **OFF**: renders use only Deforum's own settings and never contact DeforumationQT.
- **Motion while ON**: when DeforumationQT's zoom, pan, rotation or tilt switch is on, its value *is* the 3D motion, so 0 stands still. Turning the switch off, even mid-render, hands the motion back to Deforum's schedule from the next frame. Deforum's Translation X/Y/Z and Rotation 3D X/Y/Z schedules are no longer added on top (Deforum's default Translation Z of 1.75 used to keep the camera moving at "zoom 0"). The console table shows the values the render actually used. To add the schedules on top as before, put `"motion_adds_to_deforum_schedule": true` in `deforumation_switch.json`.
- DeforumationQT's **Reset motion** button (top-right corner of its tabs) stops every smooth motion and sets pan, zoom, rotation and tilt to 0 and FOV to 70, for a clean start before a new video.
- **LCM sampler**: picking LCM in the Sampler list sets Steps to 8 and the CFG scale schedule to `0: (1.5)` (other samplers can be added to `SAMPLER_PRESETS` in `gradio_funcs.py`). Loading settings never triggers it. While DeforumationQT is ON the render takes its steps from DeforumationQT's Sampler Steps slider, so the pick is sent there as well, and Deforum's Steps field is sent at the start of a render when it changed since the last send. DeforumationQT's sliders follow such changes (CFG only while its CFG switch is on), and a move of its own slider still wins until Deforum's Steps field changes again.

- **🎛 Open DeforumationQT** starts DeforumationQT itself, its mediator and its window, each in its own console, so they keep running when Forge is restarted. Its folder is looked for once (next to the webui, or one folder to the side) and remembered in `deforumation_switch.json` under `deforumationqt_launcher`, which can also be written in by hand. If a mediator is already running it says so instead of starting a second one, because the mediator's pipe only takes one.
  - It starts both straight from DeforumationQT's **own venv**, which is what its `runme` script does after its `pip install`. That install runs every single time and takes a minute or two, during which nothing appears at all. The arguments are read out of the runme script, so the named-pipes and the websockets variants both work.
  - Two things that would otherwise break it are kept out of the environment it starts in: `PYTHON` and `VENV_DIR`, which Forge's own launcher exports and which would make DeforumationQT install its requirements into **Forge's** venv, and `NoDefaultCurrentDirectoryInExePath`, which stops cmd finding the `rw.bat` that the runme script calls by name.

The choice is saved in `deforumation_switch.json` and survives restarts. The code is `scripts/deforum_helpers/deforumation_switch.py`, the local answers used while OFF are in `deforum_mediator.py`, and the button is in `ui_right.py`.

## 🎬 Animation mode, while a render runs

Deforum reads the whole UI once, when a render starts, so Animation mode and the video init used to be fixed for
the whole of it. The row under the DeforumationQT bar changes both between one frame and the next, and
DeforumationQT's **Live Parameters** panel does the same.

- **🎬 Animation mode**: the master switch. While it is off nothing is overridden and every render runs exactly as before.
- **Mode**: `Deforum` (whatever Animation mode the project is set to), `2D`, `3D` or `Video Input`.
- **Video %**: how much of the video init goes into every frame while the mode is 2D or 3D. 100% is the video on
  its own, exactly as Video Input has always worked; 0% is the animation on its own; in between, the video frame
  is blended into the warped previous frame.

**Starting on a video and moving into 3D** (a talking head that becomes an animation):

1. Set Animation mode to **Video Input** with its Video init path as usual, tick **🎬 Animation mode** before pressing
   Generate, and set Max frames to the length the whole render should have.
2. Start the render. It runs on the video.
3. When you want to leave it, pick **3D** (or 2D). Nothing changes yet: the video is still at 100%.
4. Take **Video %** down. Every step hands more of the picture to the animation, and at 0% the video is gone and
   the camera motion, the ControlNets and the prompts carry the render on by themselves.
   - Or press **⤵** and let it do that by itself: the video share walks from where it is now down to 0 over the
     number of frames in the first box, one step per rendered frame, and **⤴** walks it back up to 100%. The line
     under the row counts the frames down while it runs.
   - The second box, **@**, is the frame the fade starts on. 0 means the next frame the render draws; anything else
     waits for that frame, so a long render can be set up before it even starts and then left alone - the line
     under the row says `fade at frame 200` while it waits. A hand on the Video % slider cancels a fade, running
     or waiting, because a slider that kept sliding back would look broken.
   - DeforumationQT's own **⤵**/**⤴**, with its frames and **@** boxes, do exactly the same through the mediator
     parameter `live_video_fade` (`"<frames>:<target>:<at frame>"`), which Deforum takes off the wire as soon as it
     has read it, so it fires once. Taking the video over by hand from DeforumationQT cancels a running fade,
     because the two would otherwise pull in different directions.

**📋 Plan** (the accordion under the row): a whole clip written down before it starts. Each line is a frame
and what happens when the render gets there - an Animation mode, a video share, and how many frames to take over it
(0 jumps straight there). The render works through the list as it goes, each line once, and says what is coming
next in the line under the row (`next: frame 800 → video 100% over 60`). The list stays put for the next render.

| At frame | Animation mode | Video % | Fade frames |
|---|---|---|---|
| 200 | 3D | 0 | 40 |
| 800 |  | 100 | 60 |
| 860 | Deforum |  |  |

That one says: run on the video to frame 200, go 3D there and fade the video out over 40 frames, bring it back
over 60 from frame 800, and hand the mode back to the project at 860. Empty lines are ignored, lines are sorted by
frame, and a line with neither a mode nor a share is dropped.

**The frame can be written in music.** The first column also takes:

| What you write | What it means |
|---|---|
| `200` | frame 200 |
| `1:30` or `12s` | that time, at the project's FPS |
| `beat 16` (or `ביט 16`) | the 16th beat Audio Keyframes found in the song |
| `part 2` (or `חלק 2`) | where the second song part starts, from the Song parts table |

**On the 🗺️ Map Key timeline.** The plan also has a row of its own in the Map Key editor, right under the
music: one marker per step, labelled with what it does (`3D + video 0% ×20`), and the fade drawn as a bar as long
as it takes. Drag a marker to move the step - it snaps the way prompts do, so it can be dropped straight onto a
beat, and a step that lands exactly on a beat or a part says so again in the table (`beat 3`). Delete removes the
selected one. There is no Apply for this: a move or a delete is written to the bar's plan at once, because the
plan belongs to the bar and not to Deforum's schedules. New lines are still written in the table.

The song is the one picked in **Audio Keyframes**, with its own normalise mode, threshold and Song parts table:
the page sends all of that with the table, and the line under it says what each word came to
(`beat 4 → frame 39 · part 2 → frame 450`), or why it could not (`beat 9999: the song has 155 beats.`). What was
typed stays in the cell, so the plan still reads as music, and the frame it worked out is what the render uses.

Notes:

- **Max frames**: a Video Input render normally ends with the video's last frame. With 🎬 Animation mode ticked *before* the
  render starts, Deforum's own Max frames is kept when it asks for more, so the render can go on long after the
  video is over. When the video runs out, the video init switches itself off and says so in the console.
- **The other way round**: a 2D or 3D render can pull the video in, as long as the project has a Video init path.
  Its frames are extracted the first time they are needed, so that one frame takes a moment longer.
- **Cadence**: while the video drives the render every frame has its own video frame, so cadence is 1. Deforum's
  own cadence comes back the moment the video is off, in the middle of a render (measured on a real render:
  frames 0 to 8 one by one on the video, then 10, 12, 14... at his cadence of 2). With DeforumationQT ON, its
  cadence slider decides as always.
- **ControlNet keeps its place**: each unit reads `controlnet_<n>_inputframes/<frame>.jpg` by the number of the
  frame being rendered, so a video that was unpacked for ControlNet stays frame-for-frame in step after the
  switch. The frame numbers themselves simply carry on; nothing restarts.
- **A line under the row** says what the render is really doing while it runs, for example
  `▶ frame 12: 2D + video 50% · this bar`, because with DeforumationQT ON its own selector wins over the row.
- **3D mid-render**: the depth model is loaded the first time a frame really needs it.
- The row's state is kept in `live_control.json`, beside the switch file, and the code is
  `scripts/deforum_helpers/live_control.py` (unit tests: `live_control_test.py`).

**From DeforumationQT**: the bottom of its **Live Parameters** panel holds the same two controls, under Noise Multi, and a *Hand both back to Deforum*
button. They win over the row above the preview while the switch is ON and they are not on "Deforum (as set)",
and every DeforumationQT session starts with both handed back. The two mediator parameters are
`live_animation_mode` and `live_video_influence`; a DeforumationQT whose `mediator.py` does not know them yet
answers "NO SUCH COMMAND", and Deforum then simply keeps those two for itself for the rest of the render.

**DeforumationQT in 2D**: its pan, zoom and tilt sliders now drive 2D renders as well, not only 3D ones. 2D moves
the picture instead of the camera, so Pan left/right is the mirror of the 3D one, and forward motion counts as
zoom = 1 + translation_z / 100, the way the camera directing modes and Map Key count it. Rotate H and V stay a 3D
thing, because flat 2D has no answer for them. Switching a slider off hands that one back to Deforum's schedule,
mid-render as well.
