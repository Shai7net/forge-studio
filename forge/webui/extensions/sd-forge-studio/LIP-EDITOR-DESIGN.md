# Lip editor design guide

## Purpose

Make a character speak at an exact moment without leaving the shot. The video and speech timeline are the workspace. Controls support that workspace rather than competing with it.

## Hierarchy

1. Compact source controls: method, language, voice, delivery and expression. A short text box and one speech-generation button.
2. Central video, visible from opening the dialog. Original/result switch and one transport shared with the speech timeline.
3. Video strip above editable speech waveforms. Drag the waveform to place speech; edge handles trim; split/delete and silence removal are close to the tracks.
4. A quiet status line and clear Generate lip sync / Apply to timeline actions. Preview never changes the main edit.

## Space and appearance

- Dark neutral surfaces, subtle separators, one teal accent for primary actions, blue-green speech clips, clear selected edges.
- A 4/8-pixel spacing rhythm, 12-pixel labels, 28–32-pixel controls and restrained corner radii. No decorative emoji on every control.
- Use available width before adding height. Keep the shot and timeline visible together. Advanced tuning folds into a compact panel.
- Slider plus small numerical value for expression, speed, pitch, tone and gain. Sliders update values immediately; audio refreshes after adjustment.
- Voice samples live inside the voice dropdown, beside their respective names. Opening the menu reveals audition buttons; closing it stops samples. No permanent side column.
- At narrow widths controls wrap and the editor scrolls; primary actions remain reachable. Timeline time always runs left to right, including Hebrew UI.

## Interaction

- One shared playhead. Scrubbing the ruler or tracks updates the picture and speech playback.
- Edits are non-destructive segment instructions. Splits, trims, moves, deletions and silence removal have local Undo/Redo. Main-timeline Undo remains untouched until Apply.
- Window capture shortcuts must defer to dialogs. Space, S, Delete and Ctrl+Z/Y belong to the local speech editor while it is open, including when the main shot remains selected.
- Silence removal keeps a little breathing room around detected speech, closes removed pauses within the selected recording and is undoable.
- No overlapping speech pieces or hidden clipping beyond shot/source limits. Invalid or stale audio cannot be used for lip generation.
- Changing pitch/tone/volume preserves cuts. Speed changes map cuts to the same words at their new timing, within shot limits.
- During generation prevent conflicting edits. Show progress in place. Failure leaves the source and edit intact.
- After extension generation offer Combine clips / Keep separate. Combining creates a new rendered take and replaces only the two adjacent visual clips after success; original media stays available and Undo restores both.

## Verification

Check actual waveform positions, recorded sample playback, local shortcuts, exact rendered audio timing, original/result playback, explicit Apply/Cancel, merge decline/failure/stale guards and undo. Review screenshots at desktop and narrow widths, not only DOM assertions.
