"""Save a captured PNG to the user's chosen location; does not import it into any project."""
import base64
import io
import os
import tempfile
from PIL import Image
from . import projects


def pick_path(filename):
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        return filedialog.asksaveasfilename(parent=root, title='Save frame as a picture',
            initialfile=filename, defaultextension='.png', filetypes=[('PNG picture', '*.png')]) or ''
    finally:
        root.destroy()


def export(image, filename='frame.png'):
    if not isinstance(image, str) or not image.startswith('data:image/png;base64,') or len(image) > 90_000_000:
        raise ValueError('Choose a captured PNG frame')
    try:
        raw = base64.b64decode(image.split(',', 1)[1], validate=True)
        with Image.open(io.BytesIO(raw)) as opened:
            if opened.format != 'PNG' or opened.width * opened.height > 32_000_000:
                raise ValueError('The captured frame is too large')
            picture = opened.copy()
    except (OSError, ValueError) as exc:
        raise ValueError('Could not read the captured PNG frame') from exc
    filename = projects.BAD.sub('_', os.path.basename(filename or 'frame.png'))
    if not filename.lower().endswith('.png'):
        filename += '.png'
    chosen = pick_path(filename)
    if not chosen:
        return {'cancelled': True}
    chosen = os.path.abspath(chosen)
    if not chosen.lower().endswith('.png'):
        raise ValueError('Save the frame with a .png extension')
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=os.path.dirname(chosen), suffix='.png', delete=False) as f:
            tmp = f.name
        picture.save(tmp, 'PNG')
        os.replace(tmp, chosen)
        return {'saved': True, 'path': chosen}
    finally:
        if tmp and os.path.isfile(tmp):
            os.remove(tmp)
