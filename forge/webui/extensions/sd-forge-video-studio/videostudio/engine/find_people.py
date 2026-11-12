"""Numbers the people on one frame of a video (and in the character picture, when given), for choosing who is
replaced: `python find_people.py video.mp4 start_seconds width height out.png det.onnx pose.onnx [picture]`
prints {"people": n, "picture_people": m}; out.png shows the frame and the picture side by side.

Runs on the CPU (one frame takes a second or two), so it never competes with a render on the card.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))


def main():
    from PIL import Image

    from videostudio.engine import animate_prep
    from videostudio import WEBUI_DIR, sheets
    import numpy as np
    video, start, width, height, out, det, pose = sys.argv[1:8]
    picture = sys.argv[8] if len(sys.argv) > 8 and os.path.isfile(sys.argv[8]) else None
    finder = animate_prep.Pose(det, pose, gpu=False)
    frame = animate_prep.read_frames(video, int(width), int(height), 16, float(start), 0.25)[0]
    found = animate_prep.people(finder, frame)
    shown = animate_prep.mark_people(frame, found)
    picture_people = 0
    sheet = False
    if picture:
        views = sheets.split(picture, sheets.matte_file(os.path.join(WEBUI_DIR, "models")), cpu=True)
        sheet = bool(views)
        with Image.open(picture) as im:
            image = np.asarray(views[0][1] if views else im.convert("RGB"))
        in_picture = animate_prep.people(finder, image)
        picture_people = 1 if sheet else len(in_picture)
        marked = Image.fromarray(animate_prep.mark_people(image, in_picture))
        marked = marked.resize((max(1, marked.width * shown.shape[0] // marked.height), shown.shape[0]))
        gap = np.full((shown.shape[0], 16, 3), 40, np.uint8)
        shown = np.concatenate([shown, gap, np.asarray(marked)], axis=1)
    Image.fromarray(shown).save(out)
    print(json.dumps({"people": len(found), "picture_people": picture_people, "character_sheet": sheet}))


if __name__ == "__main__":
    main()
