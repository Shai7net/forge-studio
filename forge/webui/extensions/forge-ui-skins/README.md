# Forge UI Skins

Re-dress Forge as you work, and keep the extension cards in the order you want.

## Skins

**Hold the mouse** (about two thirds of a second) on any button, tab or card title. A green ring fills while you hold;
then a small editor opens beside it:

- background colour, text colour, border colour
- a picture on it (png, jpg, webp, gif, svg; *Fill* or *Whole picture*) - a button can look like a graphic
- roundness, text size, bold, a shadow under the text (helps on a picture)
- other words for it (empty: its own words)
- hide it (it comes back from *Skins… → What this skin changes*)

Every change shows at once. **Save** writes into the skin in use; **Save as…** makes a new skin from what you see.
**Classic** is Forge as it is and never changes: saving while on Classic always asks for a new skin's name. Changes not
saved yet are remembered by a small reminder at the bottom of the page (*Save* / *Discard*).

**Skins…** lists them: *Use*, *Rename*, *Export* (one `.forgeskin.json` file with its pictures inside, to share),
*Delete* (moves it to `webui/ui-skins/_trash`), and *Import a skin file…*.

Settings → **UI Skins**: the skin in use, holding to edit on or off, how long to hold.

How elements are found: an element with an id of its own by its id; a tab's button by its tabs and its English words;
an extension card by its English title (so the Hebrew interface and other languages keep the same skin); anything
else by its place under the nearest element with an id (that can move if an extension changes its layout).

## Extension cards (txt2img, img2img)

- They **start closed** (some extensions open theirs).
- Hover a card's title: **⠿** drags it to another place (a green line shows where), **⤓** puts it into **More**, a
  box after the last card for the ones you seldom use; inside More, **⤒** brings one back. A card can also be dragged
  onto More's title.
- The order and More are yours, apart from the skins: switching skins never moves cards.
- Settings → UI Skins turns either part off.

## Files

- `webui/ui-skins/<skin>/skin.json` and its pictures; `webui/ui-skins/_drafts` (pictures put on while on Classic,
  moved into the skin when it is saved); `webui/ui-skins/layout.json` (the cards).
- `scripts/ui_skins.py` routes and settings, `uiskins/store.py` storage, `javascript/ui-skins.js` the editor,
  `javascript/ui-cards.js` the cards, `style.css`.

## API

All under `/ui-skins/`:

| Route | Body / query | Returns |
|---|---|---|
| `GET state` | | `active`, `skins`, `long_press`, `long_press_ms`, `cards_closed`, `cards_organize` |
| `GET skin?name=` | | `{name, rules}` |
| `POST save` | `name`, `rules`, `from` (the skin it started from), `activate` | `{name, rules}` |
| `POST active` | `name` | `{active}` |
| `POST image?skin=&filename=` | the picture's bytes | `{file}` |
| `GET file/{skin}/{file}` | | the picture |
| `POST rename` / `POST delete` | `name`, `to` / `name` | |
| `GET export?name=` / `POST import` | / the exported JSON | the shareable file / `{name}` |
| `GET layout` / `POST layout` | / `{layout: {txt2img: {order, more}, img2img: {...}}}` | the cards' layout |

```bash
curl -s http://127.0.0.1:7860/ui-skins/state
```

```python
import requests
skin = requests.get("http://127.0.0.1:7860/ui-skins/skin", params={"name": "Neon"}).json()
skin["rules"]["#txt2img_generate"] = {"bg": "#16a34a", "color": "#ffffff", "radius": 18}
requests.post("http://127.0.0.1:7860/ui-skins/save", json={"name": "Neon", "rules": skin["rules"]})
```

```javascript
const state = await (await fetch('/ui-skins/state')).json();
```

A rule's fields: `bg`, `color`, `border` (CSS colours), `image` (a file of the skin), `fit` (`contain` or empty),
`radius`, `size` (px), `bold`, `shadow`, `hidden` (true), `text`.

## Tests

```bash
..\..\venv\Scripts\python.exe -m unittest discover -s tests
```
