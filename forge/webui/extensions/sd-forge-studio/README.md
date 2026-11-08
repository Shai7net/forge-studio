# Forge Studio

A video editor inside Forge, in the spirit of CapCut and Higgsfield, where everything Forge can make is part of
the edit: a shot made from words drops into the timeline, a clip is continued or made again with a click, a frame
is repainted and the change carried through the clip, a person is cut out onto a new background, narration, music,
sound effects and captions are made where they are needed. Hebrew throughout (right to left), English when Forge is

## The editor

- **Top bar:** the project (open, new, with its shape), the shape of the video (9:16, 16:9, 1:1, 4:5, 3:4),
  ⛶ to lay the editor over all of Forge (Esc returns), Export.
- **Side panels:** Media (every file in Forge's output folders, newest first; drag to the timeline, double-click
  or + to add at the playhead; ⤒ brings files from the computer), AI, Sound, Text.
- **A bigger preview:** ⇔ under the picture (or P) folds the side panels away (remembered); ⛶ next to it (or F)
  shows the picture alone on the whole screen (Esc returns). The line above the timeline drags it taller or shorter.
- **Picking clips:** click one, Ctrl-click more (Ctrl-click again leaves one out), Shift-click adds; right-clicking
  one of the picked clips keeps them all picked. Two shots picked → "🔗 Join these two shots (AI)" in the menu.
- **Right-click a clip:** its menu, with a Speed slider (drag left: slower and longer, right: faster and shorter,
  0.1×-8×, the clips change as it moves, one undo; double-click: 1×) and quick buttons (0.25× to 4×, or … for any
  speed) on videos and sounds, 🔇 Mute / 🔊 Sound back on, and 🎵 Separate the sound from the video: the same stretch of the file plays faster or slower, so the clip gets shorter or longer (all the selected
  clips together when it is one of them); a speed ramp on it gives way to the steady speed.
- **Player:** every layer drawn on one canvas. Click a clip in the picture to select it, drag to move it (the centre
  lines pull), drag a corner or use the wheel to size it, Alt+wheel to turn it. While a shot's frame is still being
  found (scrubbing, or the next shot at a cut; a seeking video gives Chrome an empty picture although it says it is
  ready), the last whole picture stays up for up to 1.5 s instead of a black hole, and the picture is drawn again as
  soon as the frame is there (scrubbing "ROYAL FRAM": 26 of 50 points black before, none after).
- **Cuts in the export:** each layer runs two frames past its end and the overlay's time window ends it exactly;
  before, overlay never showed a layer's own last frame, so a cut that fell between two frames had one black frame
  (three in the first 40 s of "SKIRA KLALIT"; none now; `test_no_black_frame_at_a_cut_between_frames`).
- **Inspector:** the selected clip's size, place, turn, opacity, colour (brightness, contrast, saturation), fades,
  dissolve from the clip before, speed, volume, text and style, and its AI actions; with nothing selected, the video's size, frame rate and background.
- **Keyframes (Motion):** a ◆ next to size, left/right, up/down, turn and opacity puts a keyframe at the playhead;
  the value then moves between its keyframes (eased in and out, or evenly), and any change at the playhead (in the
  inspector, by dragging or with the wheel in the picture) makes or moves the keyframe there. ◀◆ ◆▶ jump between
  them, and the timeline shows them as small diamonds on the clip. Ready-made moves: slow zoom in or out, pan right,
  left, up or down (sized so the picture fills the frame while it moves), rise in from below, turn in. Trimming,
  splitting and changing the speed keep each keyframe on the same moment of the picture. Volume has a ◆ too: the
  sound rises and falls between its keyframes (a yellow volume line on the clip, 100% halfway up), in the player
  and in the export (an ffmpeg volume expression of the same eased steps).
- **Colour and looks (🎨):** brightness, contrast, saturation, warmth (blue … amber) and tint (green … magenta),
  and ready-made looks that set all five: natural, warm, cool, black and white, vintage, film, vivid, night. "The
  same colour on every shot" copies a clip's colour to every video and picture. The player draws warmth and tint
  as an SVG colour matrix in sRGB, and the export as `colorchannelmixer` gains after the rest, so they match.
- **Crop:** a video's or picture's left, right, top and bottom edges can be hidden (in %); the part that shows
  stays where it was and as big as it was, so it can then be moved and sized (picture in picture). Opposite sides
  leave at least 5% between them. The player draws only that part of the source; the export crops before scaling
  (and moving clips are cropped as they are drawn).
- **Blurred background (🌫):** a picture or video whose shape is not the frame's (a wide shot in a vertical film)
  can have a blurred copy of itself behind it instead of black: the same source covering the frame and a margin of
  three blur widths (so the blur's edges fall outside), blurred (3% of the frame's height, or as chosen), at 85%
  brightness, fading with the clip. "On every shot that does not fill the frame" gives it to all of them at once.
  The player blurs on the canvas; the export with ffmpeg's gblur on its own reading of the file (`render.fill_of`).
- **Chroma key (🟩):** a picture or video shot or made on a green (or any one-colour) background has that colour
  taken away: "Take a colour away" with the colour, or 🎯 "Pick it in the picture" and click it; "Similar colours"
  (how far from it is still taken), "Soft edge", and "Remove the colour cast" (the key's own channel pulled down
  towards the others, so the edges lose the green tint). The player keys on its own canvas; the export draws keyed
  clips frame by frame with the same numbers (`render.chroma`, the page's `FS.applyKey`), then colours them.
- **Shape (◯):** the part that shows can be cut to a circle (the ellipse that fits it) or a rectangle with rounded
  corners, with a soft edge if wanted (the shape drawn smaller by half the softness, then blurred by half). The
  player cuts it on a canvas of its own; the export multiplies a mask picture (`render.mask_array`, the same
  drawing) into the clip's transparency, and moving clips take it as they are drawn.
- **Sizes stay sane:** a number typed into the inspector stays inside its field's range, a moving clip is never
  prepared at more than twice the frame, and a mask is drawn at most 16 million pixels (a size of 60 typed by
  mistake once made an export take 24 GB).
- **Freeze frame and backwards (❄ ⏪):** in a video's Time section. "Freeze frame at the playhead" splits the clip
  there and puts the frame on screen between the halves as a still (same size, place and colour; 2 s, or the
  seconds next to it), pushing the rest of the track along; one undo takes it all back. "Play it backwards" makes
  the part of the file the clip uses, backwards with its sound, and the clip plays that ("Play it forwards again"
  returns to the original). `forgestudio/clipkit.py`: the still is the exact frame the player shows (transparent
  WebMs stay transparent); backwards is done in 5 s pieces, since ffmpeg's reverse keeps all it reverses in memory.
- **Animation in and out (✨):** any picture, video or text can come in and go out with a fade, a rise (up from 6%
  of the frame lower), a pop (grows from 60% with a small overshoot) or a wipe from the right (for Hebrew) or the
  left, over 0.5 s or the seconds chosen; the way out is the way in played backwards. They work around the part of
  the clip that has something in it: a text's letters (where its picture is not see-through), so a lower third
  pops about itself and the wipe runs across its words. Animated clips are drawn frame by frame for the export
  (`render.anim_at`, the same numbers as the page's `FS.animAt`).
- **Typewriter (text):** "Typewriter" as a text's way in shows its letters one after another, evenly, from the start
  of the reading (the right, in Hebrew), big lines first, then the small ones; as the way out they go from the end.
  A letter with its niqqud is one step, spaces count, and every letter stands where it stands in the whole line, so
  nothing shifts while it types. Picking it sets about 15 letters a second (at most 70% of the clip; up to 30 s by
  hand). For the export the page draws the text with each count of letters a frame shows and sends those pictures
  (`render/text?step=`, `render/typed-<clip>/`); `moving.py` lays the right one on each frame (`render.typed_count`,
  the same arithmetic as `FS.typedCount`). Checked: a Hebrew title cut after 6 of 19 letters is pixel for pixel the
  whole title's right part, and the export frame by frame shows the letters the formula gives.
- **Beats (🥁):** on a music clip, "Find the beats" marks every beat on it (a tall line on each bar's first beat),
  and clip edges snap to them. "Cut to the beat" re-times the main track so each shot ends on a beat: the nearest
  one, every beat, every two, or every bar; a video too short for the next bar ends on the last beat it reaches.
  The beats are found here without extra packages (`forgestudio/beats.py`: onset envelope over log-spaced bands,
  tempo by autocorrelation, dynamic-programming beat tracking, the bar's "one" by strength), in about half a second
  for a two-minute song.
- **Music under speech (🗣):** a sound clip set to "Lowered under speech" (−6, −12 or −18 dB) steps back only
  while a speech clip is really talking, and comes back up in its pauses: down over 0.15 s before the words, up
  over 0.6 s after them; pauses shorter than that stay low, so it does not pump between words. Narration made here
  (`voice-*.wav`) counts as speech by itself; tick "This is speech" on any other clip (a talking character's
  video), or untick it. The timeline draws the music's volume line where it is lowered. Where a file talks is found
  by its loudness in 20 ms windows (`forgestudio/duck.py`, cached); the player and the export use the same curve.
- **Timeline:** layers above the main track, sound tracks below. The main track is magnetic (no gaps, like
  CapCut's; 🧲 turns that off). Clips show their own frames or their sound's outline; drag to move (also to another
  track, or to the empty row above/below for a new one), drag an edge to trim, edges snap to the playhead and to
  other clips. A ✨ at every cut on the main track offers a transition (dissolve, dip to black, slide in, push),
  an AI transition between the two shots, a continuation or a new shot.
- **Transitions:** in the inspector's Time section, a clip that follows another chooses how it comes in and for how
  long: dissolve, a dip through black or white (the cut at the middle), slide in from any side over the clip
  before, or push it out to the other side (eased in and out). The clip before holds its last frame meanwhile. The
  timeline marks each one at the start of the clip; the export does the same (overlay places that move with the
  time, a colour layer for the dips), checked frame by frame against the player. A 🎞 badge on a clip switches between its takes.
- **Speed ramps (🏎):** the ◆ next to Speed makes the speed move between keyframes, and ready-made ramps set it
  in one go: hero (fast, slow, fast), slowing down, speeding up, flash (slow, fast, slow), montage. The clip keeps
  its length; the file second it shows is `in` plus the integral of the speed (summed in 1/240 s steps the same
  way in the page and in `render.source_span`), and a ramp is scaled down when the file is too short for it.
  Splitting and trimming keep each moment of the picture. The export draws a ramped video frame by frame (the file
  read in order, fast enough for the ramp's fastest part) and plays its sound along the ramp like a tape (faster is
  higher), cached as a WAV.
- **Soft sound at a cut (🔉):** in the Time section of a clip that follows another on its track, "Soft sound from
  the clip before" crosses the two sounds over that many seconds with equal-power curves (a quarter sine in, its
  mirror out), so the loudness does not dip in the middle. The clip before goes on under the new one from its own
  file past its end (its picture stays held, as in a transition) when the file has that much more; otherwise it fades
  out before the cut. Until it is set it follows the picture transition (a 1 s dissolve crosses the sound for 1 s);
  0 turns it off. Only between two clips that both sound (`FS.soundCross` / `FS.soundOut` / `FS.soundGain`,
  `render.soft_cuts`). Every other hard cut gets a 6 ms fade against clicks, except between the two halves of a split,
  where the sound simply goes on. Measured in the export: 0.951 / 0.309 at a fifth of the way, 0.707 / 0.707 halfway,
  the summed power 1.00 throughout.
- **Separate the sound (🔊):** in a video's Sound section, its sound becomes a clip of its own on a free sound
  track below (same file, place, trim, speed, volume, fades, volume keyframes and lowering), and the video goes
  quiet; then it can be moved, trimmed, lowered under speech or cut to the beat on its own. One undo.
- **Save the frame (📷, top bar):** the frame at the playhead, every layer, at the edit's full size, as the player
  draws it, saved as a PNG next to the exports (`<project>-frame-<time>-<clock>.png`) and put in the library.
- **A marked part (I / O):** I marks the start of a part at the playhead and O its end (by the key's place, so a
  Hebrew keyboard works too); the ruler shows it as a blue band, and with nothing selected the inspector shows it
  with ✕ to clear it. The export window's "Part" can then take just that part: the page cuts a copy of the edit to
  it the way a split does (`FS.docInRange`: each clip's place in the file, keyframes, speed ramp and fades kept,
  transitions from clips left out dropped) and exports the copy; the edit itself is not touched.
- **Markers (🚩):** M puts a marker at the playhead (M on it again takes it away). Markers show as flags on the
  ruler with a line through the tracks; clip edges and the playhead snap to them. With nothing selected, the
  inspector lists them: jump to one, name it, take it away.
- **Copy and paste style (🖌 📋):** a clip's colour and look, crop and shape, motion (keyframes stretched to the
  other clip's length), volume and lowering under speech, and the transition into it, onto the selected clips in
  one undo; each part only where it means something (no colour on sound, no motion on sound).
- **Keys:** Space play/pause, S split, Del delete, Ctrl+Z / Ctrl+Y undo/redo, Ctrl+D duplicate, ← → a frame
  (Shift: a second), Home / End, Ctrl+wheel zoom the timeline, M marker, Ctrl+Alt+C / V copy / paste style (M and
  Ctrl+Alt+C / V go by the key's place, so they work on a Hebrew keyboard too).
- **Trimming from the keyboard:** Q trims the clip's start to the playhead and W its end (the picked clips, or the
  one under the playhead on the main track): a split there that keeps one side with the clip's own outer edge (its
  fade or transition in, its fade out), the gap on the main track closing, one undo; after Q the playhead stands on
  the new start. ↑ / ↓ jump to the cut before / after. , / . (or Alt+← / →) nudge the picked clips a frame earlier or
  later (Shift: a second), each only where it covers nothing; on the main track with the magnet on clips stay end to
  end. By the key's place, so they work on a Hebrew keyboard too (`FS.trimTo`, `FS.nudge`, `FS.cutNear`).

## AI in the edit

- **Prompts in Hebrew:** every prompt box takes Hebrew or English; the 🌐 prompt writer (Gemma) writes the
  English prompt for the model before the request goes out, and the request shows what it wrote.
- **Shots (Video Studio, Wan 2.2):** a new shot at the playhead from words, from the frame under the playhead, a
  continuation of the shot before it, a transition between two shots at a cut, another take of a clip. A striped
  clip of the right length goes on the timeline at once, shows its progress, and becomes the clip when it is ready
  (also after a reload: the requests are kept in the project's `generations.json`). Made with the settings last
  chosen in Video Studio's own tab, in the shape of the edit.
- **Continue with the tool that made the clip:** "Continue this shot" reads what made the clip and continues it
  the same way, from its last frame, so the continuation looks like it: Wan for Video Studio's clips (and anything
  else), **AnimateDiff** for AnimateDiff's (the settings in its frames: model, LoRAs and their trigger words, sampler,
  steps, size, motion module, fps, upscale; img2img from the last frame with AnimateDiff's image-to-video, the last
  frame also an IP-Adapter reference; ControlNets that followed a driving video are left out), **Deforum** for
  Deforum's (its `<timestring>_settings.txt` again, the last frame as the init picture, the new words from frame 0).
  The box shows which and can choose another. AnimateDiff and Deforum draw even their first frame again, so their
  continuation fades in from the last frame over its first half second (Wan keeps that frame exact), and gets AI
  sound like Wan's clips when sound is on. A continuation of a continuation keeps going the same way.
- **Join two clips, frame to frame (🔗 in the clip menu, ✨ at the cut):** a new clip from the last frame of the first
  to the first frame of the second, by Wan (first + last frame) or by AnimateDiff (image-to-video with a first and a
  last picture, in the AnimateDiff clip's own settings) - AnimateDiff when either clip is AnimateDiff's. The box sets
  the join's length (1-7.5 s, 2 s first, remembered). Its first and last half seconds fade from and into the two
  frames, so both cuts are seamless. The join goes right after the first shot.
- **Edit the frame:** paint where to change it and Forge draws there (the checkpoint chosen at the top; FLUX 3 gets
  Euler / 16 steps / CFG 1), or the whole frame again. Back into the video: carried through the clip along the
  optical flow (the clip gets the result as a new take, same trim), made again by Wan from it, or as a still.
- **Auto reframe (🎯):** for a video whose shape is not the frame's (a wide shot in a vertical film), "Fill the
  frame and follow the subject": BiRefNet finds the subject four times a second (its matte's weighted centre), and
  the clip is made to fill the frame with x / y keyframes every half second that keep the subject in the middle,
  smoothed over a second (narrower at the ends so the window does not lean into the clip) and never past the
  picture's edge. One undo; the keyframes can be changed by hand afterwards. Measured on a real shot: the subject at
  0.50-0.51 across the exported frame, where only filling the frame left it at about 0.66.
- **Cut out:** BiRefNet removes the background; the cut-out (VP8 WebM with transparency) lies on the first free
  layer above, at the same place.
- **Sound:** narration (Chatterbox with niqqud for Hebrew), music (ACE-Step), sound effects from a clip's picture
  (MMAudio); each lands on a free sound track.
- **Lip sync (👄 in a video's or picture's menu):** the clip is made to say something, three ways. To a sound
  already in the edit (the part of it that plays with the clip, starting where it starts in the clip), or to written
  words that a voice of the list says (Chatterbox with niqqud, or Piper; the speech also lands on a free sound track
  under the clip): MuseTalk draws the mouth again over the whole clip, so the skin looks the same before, during and
  after the words, and a clip shorter than the words plays back and forth past its end. Like a face video (someone
  filmed saying it): LivePortrait (the sd-webui-live-portrait extension, called in-process) moves the character's
  lips (or, when asked, the whole face and head: livelier, but the edge of the pasted face can show on a still portrait) like the video's, and the video's sound comes with it; a still picture goes
  to it as a portrait, a video clip frame by frame. The face video has to be a close-up from the front, like a
  selfie: from a full-body shot (a face about 100 pixels wide) the mouth hardly moved whatever the settings. The clip
  plays the talking take from its start at normal speed (a 👄 on the timeline); "Back to the original" returns the
  file, trim, speed and ramp it had. `forgestudio/lipsync.py`; the clip's part is cut out under a plain name first.
  Measured on the RTX 3070 Laptop: written words to a 5 s clip 117 s, a sound 61 s, a 7 s face video about 40 s.
- **Text and captions:** text clips in six looks (title, title at the top, lower third, caption, full-screen card,
  the SHAI TIRI signature), edited in the inspector; captions from the edit's own sound by Whisper large-v2, one
  caption clip per sentence on a track of their own.
- **Templates (🎬, in the Text panel):** ready titles with their animation and length, put at the playhead in one
  undo: Opening (the film's name typed in, fading out), Lower third (wiped in from the right with its box),
  Chapter (a full-screen card fading in and out), Quote (typed in a soft box) and Ending ("תודה שצפיתם" on a card,
  the SHAI TIRI signature popping in a second later on a layer above it). Each part lies on a layer above the part
  before it, even where a lower layer is free for part of the time (`FS.TEMPLATES`, `FS.addTemplate`).
- **Stickers and shapes (⭐, in the Text panel):** a rectangle, rounded rectangle, circle, arrow (pointing right; turn
  the clip for any other way), line, star or heart, filled or as an outline, its colour, size, width and thickness in
  the inspector; and 24 emoji stickers drawn in colour (Segoe UI Emoji). Both are text clips (`template: 'shape'`,
  `FS.drawShape`; a sticker is a big emoji in the middle), so they are dragged in the picture, keyed, animated and
  exported like any text. Checked: a star, an outlined heart, a turned arrow and a 🔥 export as the player shows
  them (an average difference of 0.46 of 255 over the frame).

## How it is built

- The edit is one document, `project.json` in `webui\forge-studio\projects\<name>\` (settings, tracks, clips,
  times in seconds); the last 40 saves are kept in `history\`. `studio.json` names each file the project uses and
  keeps where it really is. Projects of the first version (FilmCraft's `project.fcproj`) are converted the first
  time they are opened; the old file stays.
- The page draws the player and the timeline itself (`javascript/forge-studio-a … j`). Text is drawn by the page
  for the player and, for the export, sent as pictures, so Hebrew looks the same in both.
- **The export window** (⬆ Export): the file's name and folder (📁 Choose… opens Windows' folder picker; ↺ back to
  `outputorge-studio\<date>`; a file is never written over), the format, and finishing steps after the edit is
  rendered (`forgestudio/finish.py`, run with Forge's Python, frames streamed through ffmpeg): **Upscale** ×1.5-×4
  with RTX Video Super Resolution (NVIDIA's own video upscaler, the `nvidia-vfx` that Forge's RTX upscaler uses;
  high-bitrate or compressed-video mode) or Lanczos; **Frames** ×2 / ×4 drawn by RIFE 4.6 (Deforum's code and
  `models\Deforum\RIFE46.pkl`, about a gigabyte of VRAM) or FILM (finer, slower; at 1080 × 1920 it filled the 8 GB
  card); **HDR10** by [sdr2hdr](https://github.com/jpneagle/sdr2hdr) (kept as is in `C:\ForgeStudio\sdr2hdr`,
  run with Video Studio's Python 3.11; HEVC Main 10, BT.2020 / PQ, its style, tone and AI strength). The window
  shows what comes out and about how long it takes, the progress with Stop, then Watch and Show in the folder; the
  choices are remembered. Measured on the RTX 3070 Laptop: 2 s at 1080 × 1920 → 60 fps, 2160 × 3840, HDR10 in 77 s
  (HDR at 4K about 2.3 frames a second is most of it).
- **Export** (`forgestudio/render.py`): the same document becomes one ffmpeg filter graph (each clip fitted,
  scaled, turned, moved, coloured, faded, dissolved, sped up, layered in track order; sound mixed with its volumes and fades, and the
  loudness set, -14 LUFS by default) encoded by the graphics card (NVENC). A 15.6 s edit with text and sound
  exported in 5.2 s on the RTX 3070 Laptop. A clip with keyframes is drawn frame by frame by `forgestudio/moving.py`
  with the player's own geometry (OpenCV, into a transparent UT Video file of the whole frame) and then layered like
  any other, because ffmpeg's scale and rotate cannot change a layer's size from frame to frame. Results go to `output\forge-studio\<date>\` and into the library.
- Media pictures for the timeline (`forgestudio/media.py`): a strip of frames over the whole file and the sound's
  outline, cached in `webui\forge-studio\cache`.

## API

All routes are under `/forge-studio/` on Forge's address.

```bash
curl "http://127.0.0.1:7860/forge-studio/project?name=My%20film"
curl -X POST http://127.0.0.1:7860/forge-studio/render/start -H "Content-Type: application/json" -d "{\"name\": \"My film\", \"doc\": {...}, \"preset\": \"h264\", \"loudness\": -14}"
curl "http://127.0.0.1:7860/forge-studio/render?id=<job id>"
curl -X POST http://127.0.0.1:7860/forge-studio/lips -H "Content-Type: application/json" -d "{\"name\": \"My film\", \"web\": \"actor.mp4\", \"start\": 0, \"seconds\": 5, \"mode\": \"text\", \"options\": {\"text\": \"שלום\", \"language\": \"Hebrew\", \"voice\": \"Hebrew man 1\"}}"
curl -X POST http://127.0.0.1:7860/forge-studio/generate -H "Content-Type: application/json" -d "{\"name\": \"My film\", \"kind\": \"text\", \"prompt\": \"a fox runs through snow\", \"seconds\": 3}"
```

```python
import requests
base = "http://127.0.0.1:7860/forge-studio/"
p = requests.get(base + "project", params={"name": "My film"}).json()
job = requests.post(base + "render/start", json={"name": "My film", "doc": p["doc"], "preset": "h264", "loudness": -14}).json()
print(requests.get(base + "render", params={"id": job["id"]}).json()["state"])
```

```javascript
// in the Forge page
await ForgeStudio.addFromLibrary((await ForgeStudio.api('library?kind=video&limit=1')).items[0]);
await ForgeStudio.exportNow('h264', -14);
```

## Tests

From this folder with Forge's Python (`..\..\venv\Scripts\python.exe tests\test_<name>.py`):
`test_core` (library, projects with a Hebrew name), `test_render` (exports checked by pixels and loudness,
transitions, crop, shape masks, chroma key, a blurred background, animations in and out, speed ramps (picture and pitch), a separated sound and a saved
frame, bounded memory for huge
sizes, music lowered under speech, volume keyframes, freeze frames and backwards, soft sound at cuts, typewriter text, converting an old
project), `test_generate` (requests with a stand-in Video Studio; the frame a continuation starts from, also at
the very end of a 15 fps clip on a 30 fps timeline), `test_continuers` (which tool made a clip, AnimateDiff's request
and Deforum's settings for a continuation), `test_frames` (carrying an edit through a clip).

`node tests/test_core_js.js` runs the page's core without a browser (keyframes, splitting with keyframes, cutting
to the beat, undo, music under speech, transitions, freeze frames, volume keyframes, looks, crop, masks, markers,
copying style, animations, separating the sound, speed ramps, chroma key, blurred background, auto reframe, a marked
part, taking a lip sync and going back, soft sound at a cut, typewriter counts, templates, shapes, keyboard trimming); `tests/test_beats.py` checks the beat finder on music made with known beats.
