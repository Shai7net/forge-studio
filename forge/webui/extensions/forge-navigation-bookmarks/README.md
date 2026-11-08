# Forge Navigation Bookmarks

Organize extension tabs on the **Main bar** and in folders while retaining the existing Forge interface. Frequently used extensions stay directly visible beside folder buttons; there is no separate Favorites folder. The scope is **organization only**: no title customization, font changes, color changes, generation-control changes, or theme redesign.

## Enable and use

This extension is already placed in `webui/extensions/forge-navigation-bookmarks`. For an update, restart Forge using its existing launcher and refresh the page with **Ctrl+F5**. It loads automatically unless external extensions are disabled. The small Python API is part of the extension; it adds no package download, API key, remote service or additional dependency.

- Initially every available top-level tab remains on the original top bar. **All tabs are movable**, including built-in tabs and separate instances that share a native ID (both Wildcards Manager entries and both Cleaner entries). Organize remains available even when every native tab is in a folder.
- Click **Organize…**, enter a folder name and choose **Create folder**.
- Use each extension's **Destination** dropdown to put it on **Main bar** or in a named folder. Main-bar items are the original tab buttons, directly visible beside the folders. After Save, a folder-assigned extension disappears from the main bar, including when its native button is selected or its CSS classes are refreshed.
- Drag items between groups or use the up/down arrows. Folder headings can be dragged to reorder folders; their arrows provide the same operation.
- **Save** applies the draft; **Cancel**, **Close**, and Escape discard it. The live bar is unchanged while editing.
- Click a folder on the main bar to open its dropdown list. Choosing an extension opens the original tab and closes the list. Escape, clicking outside, and page scrolling close it too. Opening a folder does not add a row or move the generation controls. The count includes currently available tabs.
- Hold an extension for about half a second, or right-click it, to choose **Main bar** or an existing folder. This works on main-bar tabs and folder contents. The move is saved immediately and does not activate the extension. **Cancel** leaves the arrangement unchanged. Creating new folders is done in Organize.
- Drag a top-level extension onto a folder, or drag a folder item onto a main-bar item to insert it before that item. Blank top-bar space appends to Main bar. Drops on folder items insert before them; blank folder space appends. Main-bar items and folders can be reordered by dragging. These moves save immediately; the post-drag click guard prevents unwanted activation.
- Deleting a folder, after confirmation, returns its extensions to Main bar. **Restore defaults** resets only the organizer draft, after confirmation; Save commits it.
- Keyboard: focus a folder and press ArrowDown/ArrowUp to open its list and focus the first/last item; navigate with arrows, Home and End. Enter opens an extension, Escape closes the list. Shift+F10 on an extension opens the move picker. The dialogs contain focus; use dropdowns and up/down buttons in Organize for ordering and assignment on touch devices too.

To disable: uncheck `forge-navigation-bookmarks` in Forge's Extensions tab, apply/restart, and refresh. Native navigation returns; browser preferences remain for later use.

## Repository findings and integration

Verified locally on 2026-10-13:

| Finding | Local evidence |
| --- | --- |
| Forge source version | `modules_forge/forge_version.py`: `0.0.17v1.8.0rc`; checkout HEAD `dfdcbab6`, with extensive pre-existing local changes |
| Installed runtimes | Packaged Python 3.10.6 and complete venv Python 3.10.11; both have Gradio 3.41.2. The full nine-extension verification uses `webui/venv/Scripts/python.exe` |
| Main navigation construction | `modules/ui.py`, around lines 1128–1168: core interface tuples plus `script_callbacks.ui_tabs_callback()`, then Settings/Extensions; `gr.Tabs(elem_id="tabs")` and `gr.TabItem(..., elem_id="tab_<ifid>")` |
| Initial ordering/visibility | `ui_tab_order` sorts interface labels; `hidden_tabs` omits them before render. Neither setting is modified |
| Extension assets | `modules/ui_gradio_extensions.py` loads active extensions' JavaScript and `style.css`; `modules/scripts.py` honors metadata dependency ordering |
| Frontend | Gradio's Svelte frontend; existing custom scripts use `gradioApp()`, `onUiLoaded()`, `onAfterUiUpdate()` and scoped DOM selectors |
| Important compatibility constraints | `javascript/ui.js`, Cleaner and Replacer use tab button indices; `script.js` expects `#tabs > .tab-nav > button.selected` and direct `#tabs > .tabitem` panels |
| Existing persistence | `javascript/localStorage.js`, Hebrew UI and other scripts use browser storage. The organizer now uses its own Forge extension API and fixed data file, leaving the shared settings file untouched |
| Existing drag support | `javascript/dragdrop.js` handles file/image drops, not tab/folder sorting. No new sorting library is necessary |
| Localization/theme | Default tab labels remain untouched; new labels consult `window.localization` and remain accessible to existing Hebrew translation. New controls reuse Gradio theme classes/variables |

The core tab IDs are `txt2img`, `img2img`, `extras`, `pnginfo`, `modelmerger`, `train`, `settings`, and `extensions`. These are now eligible for organization at the user's explicit request. Extension registration includes bundled **SVD/Z123** (`extensions-builtin/sd_forge_svd`, `sd_forge_z123`) as well as installed third-party tabs.

Screenshot entries traced to local registration code include Infinite Zoom (`iz_interface`), System Info (`system_info`), Deforum (`deforum_interface`), Simulation 360 (`sim360`), Live Portrait (`live_portrait`), Panorama Viewer (`panorama-3dviewer`), NextView, Replacer, Image Browser, Cleaner, Batch Cleaner and its additional localized tabs. Wildcards Manager belongs to installed Dynamic Prompts. Extension-specific menus and sub-tabs remain owned by their extensions. Only direct top-level `tab_*` panels are eligible.

The implementation discovers actual direct Gradio panel/button pairs, rather than matching visible/translated labels or guessing extension filenames. Forge registers these tabs while building the Python UI; registration changes normally require a UI restart. The organizer also reconciles added/removed DOM pairs and Gradio's selected-button replacement. Unsupported DOM structures fail open, exposing original navigation. Duplicate native IDs are supported using separate organizer keys: the original untranslated Gradio registration label and occurrence distinguish instances. `window.gradio_config` supplies these labels; its numeric component IDs are only used to traverse the current layout and are never persisted. Dynamically inserted DOM pairs use a cached initial label if they have no Gradio layout registration. Original panel IDs, labels, DOM order and handlers remain unchanged.

## Plan followed and compatibility decisions

1. Inspect supplied instructions, screenshot, logs, runtime, registration, extension assets, index-sensitive callers and persistence conventions before editing.
2. Package the feature as an independent Forge extension; keep all production changes in this new folder.
3. Model single-level folders and stable panel-ID assignments separately from rendering. Preserve original button identities, order in the DOM, labels, colors, fonts, IDs and click handlers. Use CSS `order` for main-bar visual order and visibility only for folder assignments. New folder/organizer controls follow the complete original button list.
4. Add staged management, scoped drag targets and keyboard/touch alternatives, server-backed persistence with a browser fallback, then test with the exact installed Gradio version.

Folder dropdowns use small native-styled activation buttons that resolve and click the current original Gradio button. They never toggle panel visibility themselves or copy its functionality. The anchored popup is outside `#tabs`, so it does not become a second direct `#tabs > .tab-nav`, change direct panel selectors, or shift the page. Native folder assignments use a dedicated data attribute as well as a class, so a class rewrite cannot reveal a folder item on the main bar. Gradio itself replaces selected/unselected button nodes; the organizer observes those changes and binds to the replacements. Its own initialization and drag/hold listeners are idempotent; abort controllers cancel listeners and pending hold timers when their nodes are refreshed.

No original Forge file, installed third-party extension, launcher script, or settings file was edited.

## Persistence

Preferences are stored by the Forge extension at `webui/forge-navigation-bookmarks.json` through same-origin routes under `/forge-navigation-bookmarks/config`. The server writes a bounded JSON document atomically via a temporary file and `os.replace`, so the arrangement is shared by Edge, Chrome, Opera, separate profiles and normal Forge restarts without touching `config.json` or unrelated settings. The live arrangement updates immediately after validation; a failed server request is reported and the browser copy remains available as a recovery fallback.

Existing `forge-navigation-bookmarks.v1` browser data is migrated automatically the first time a browser reaches a Forge instance with no server arrangement. The browser copy is retained for offline recovery and backward compatibility, but a valid server arrangement is authoritative on subsequent loads. A server request has a short timeout and never blocks Forge's native tabs; if the endpoint is unavailable, the original browser fallback is used.

```json
{
  "version": 1,
  "folders": [{"id": "folder-<uuid>", "name": "Video"}],
  "items": [{"id": "tab_deforum_interface", "destination": "folder-<uuid>"}]
}
```

Folder array order and item array order provide ordering. IDs are stored independently of native display labels. Loading normalizes invalid destinations to Main bar, removes duplicate assignments, bounds folder names, rejects invalid IDs, and retains missing tab IDs. New tabs default to Main bar near their native neighbors, while existing saved items retain their relative order. Missing tabs remain in the manager, marked unavailable, with their assignments preserved. The storage identifier `favorites` still means Main bar internally, so arrangements saved before the terminology update are preserved without a schema migration or reset.

Each available item additionally records `registration: {panelId, label, occurrence}`. This optional metadata extends version 1 without invalidating legacy arrangements. Existing IDs and destinations are retained; extra instances receive distinct organizer keys. Saved instances are matched before allocating new keys, so disabling Batch Cleaner does not assign its folder to Cleaner. Unique native IDs survive registration title changes. Two registrations identical in both native ID and original label necessarily use their relative occurrence: if an extension changes the relative registration order of otherwise indistinguishable instances, Forge supplies no further persistent identity to distinguish them. Their native activation behavior, including any pre-existing effects of shared IDs, is preserved.

Malformed and unknown-version data is left untouched on load, with a visible message and usable default navigation. Explicit replacement requires confirmation and saves the old value in `forge-navigation-bookmarks.v1.recovery`. A browser storage event updates other windows; an open draft warns of a concurrent change and requires confirmation before replacing it. Cancel reloads the current saved state.

Malformed browser data is left untouched, and malformed server data is ignored without replacing it automatically. Explicit replacement from Organize remains guarded by confirmation. Clearing browser storage no longer removes a valid server arrangement; deleting the server file is the reset operation for all browsers. The API accepts only version 1 objects, enforces a 1 MiB limit, uses a fixed path under Forge's data directory, and never accepts a client-provided filename or path.

## Added files

| File | Purpose |
| --- | --- |
| `javascript/nav_bookmarks_model.js` | Pure schema validation, normalization, moves and folder operations |
| `javascript/nav_bookmarks_ui.js` | Original main-bar integration, anchored folder lists, mouse/touch/keyboard move picker, management, drag-and-drop and persistence |
| `style.css` | Only organizer-specific layout, visibility, drag/focus states and new dialog styling; existing theme is read, never changed |
| `metadata.ini` | Forge extension metadata and explicit model-before-UI load dependency |
| `tests/model.test.cjs` | Eleven Node tests for configuration integrity, legacy preferences, native core placement and duplicate identities |
| `tests/browser.test.cjs` | Real Gradio browser interaction and regression checks |
| `tests/restart.test.cjs` | Real test-server restart, persistence and core/settings file-integrity checks |
| `tests/gradio_fixture.py` | Lightweight app built with installed Gradio and the repository's real `script.js`; no Forge models or user settings loaded |
| `tests/other-folder.test.cjs` | Exact Other 🔻 nine-tab workflow in Edge, including save visibility, reloads and SVD out/back |
| `tests/all-tabs.test.cjs` | Full installed Forge: all 26 tabs, duplicate activation, legacy Other (10), independent moves and all core tabs |
| `tests/run_forge_integration.py` | Start the actual Forge code with test copies of UI/settings files, no installers or model downloads |
| `scripts/forge_navigation_bookmarks_api.py` | Same-origin Forge API and atomic server-side arrangement persistence |
| `.gitignore` | Exclude test screenshots and Python caches |
| `README.md` | Usage, evidence, decisions, validation and limitations |

`REVIEW.diff` is a generated unified addition diff for these source files. The parent repository ignores `extensions`, so normal `git diff` does not show this local extension; no ignore rules or Git index were changed.

### Cross-browser and startup follow-up

The reported Edge-only lockup was treated as a browser-local persistence and initialization problem. The UI now starts with the native Forge navigation immediately, loads the shared arrangement asynchronously with a 2.5-second timeout, and fails open to the original tabs if the extension or API encounters an error. A bounded startup watchdog also checks the same-origin API after 45 seconds when an app window still has no native tabs; if Forge has become reachable, it performs one guarded reload so an Edge PWA opened during model startup can recover without user data loss. The API was loaded by the installed Forge process and returned HTTP 200. The full installed-Forge 26-tab regression passed after the change, and the lightweight browser suite passed all 15 groups with zero page errors. The temporary test arrangement was removed after validation; no user `config.json`, `ui-config.json`, core `script.js`, `style.css` or `modules/ui.py` was modified.

The exact Edge PWA command supplied for this installation was exercised against `http://127.0.0.1:7860/`: a cold launch reached the full Forge UI after the server/model startup window, and a subsequent PWA reload reached the same UI within five seconds. Captures are kept in `output/playwright/edge-pwa-after30.png` and `output/playwright/edge-pwa-reload5.png`. A separate restart diagnostic reproduced Edge's “page not responding” prompt only when the server was deliberately stopped during reload; the watchdog is designed for that recovery boundary and does not alter a healthy navigation.

## Validation actually executed

Commands below were run from `C:\ForgeStudio\forge` with the required `rtk` wrapper. The Playwright path points to an already installed development runtime, not an extension dependency.

```powershell
rtk proxy node --test webui/extensions/forge-navigation-bookmarks/tests/model.test.cjs
rtk proxy node --check webui/extensions/forge-navigation-bookmarks/javascript/nav_bookmarks_model.js
rtk proxy node --check webui/extensions/forge-navigation-bookmarks/javascript/nav_bookmarks_ui.js
rtk proxy .\system\python\python.exe webui/extensions/forge-navigation-bookmarks/tests/gradio_fixture.py
rtk proxy node webui/extensions/forge-navigation-bookmarks/tests/browser.test.cjs %USERPROFILE%/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright
rtk proxy node webui/extensions/forge-navigation-bookmarks/tests/restart.test.cjs %USERPROFILE%/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright C:/ForgeStudio/forge/system/python/python.exe
rtk proxy .\webui\venv\Scripts\python.exe webui/extensions/forge-navigation-bookmarks/tests/run_forge_integration.py
rtk proxy node webui/extensions/forge-navigation-bookmarks/tests/other-folder.test.cjs %USERPROFILE%/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright http://127.0.0.1:7860 --actual-forge
rtk proxy node webui/extensions/forge-navigation-bookmarks/tests/all-tabs.test.cjs %USERPROFILE%/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright http://127.0.0.1:7860
```

Results: eleven model tests passed; JavaScript syntax passed; fifteen browser scenario groups passed with zero browser JavaScript errors; actual Gradio server-process restart persistence passed; hashes confirmed `config.json`, `ui-config.json`, core `script.js`, core `style.css` and `modules/ui.py` stayed byte-for-byte unchanged during validation. Dark, light and 390px-wide screenshots were rendered and inspected. The dialog's width and dark input readability are also asserted, to catch Gradio reset CSS conflicts. Folder dropdown and quick-move screenshots were inspected too.

The lightweight fixture now loads **production Forge CSS in production order**. It reproduced the reported failure: Forge globally hides every `<footer>`, and the old organizer's Save actions were inside one. The regression test failed before the fix. The organizer now uses a normal div for actions, and constrains only the list to scrolling so Save stays visible. No global stylesheet was changed.

The final strict **full Forge** test passed with all nine tabs from the user's screenshot: SVD, Z123, Image Browser, Replacer - video, Panorama Viewer, וידאו, ביצ', NextView and Replacer. It created Other 🔻, saved, reloaded, verified all nine were hidden from main and listed in Other, moved SVD to main and back with reloads, and compared original tab text/color/font values. There were zero missing tabs and zero unhandled browser errors. The test used an isolated Edge profile and copied settings, preserving existing user preferences. See `output/playwright/other-folder-actual-forge/PROOF.md` and its five screenshots and `proof.json` for the evidence.

Browser scenarios cover original client/server activation, native appearance preservation, Main bar labeling and visibility, class-refresh hiding, folder dropdowns without layout shifts, genuine mouse and touchscreen holds, right-click/Shift+F10 moves, menu arrow keys and outside/Escape dismissal, move cancellation followed by keyboard activation, folder creation/validation/rename/delete, real drag/drop between folders/main bar, reorder and invalid drops, reloads, unavailable/dynamic tabs, multi-window conflicts, storage failures, many/long tabs, modal focus, invalid/outdated schema recovery and incompatible DOM fail-open behavior.

The all-tabs follow-up verifies **all 26 entries in the installed Forge UI**, including both Wildcards Managers, Batch Cleaner, Cleaner, the Hebrew batch tab and all eight core tabs. It seeds the legacy Other (10) arrangement, checks preservation of its assignments and relative main order, moves both duplicate groups into Other, compares each folder activation with that exact original Gradio button's selection behavior, returns only the second Wildcards Manager to main and back through the move picker, moves all 26 entries into Other, opens Settings from there, and returns txt2img to main. Native IDs, original button order and titles remain unchanged. Evidence is in `output/playwright/all-tabs-actual-forge/proof.json` and the six numbered screenshots. Settings/core file hashes match the original values.

The complete installation has third-party assets of its own: an exploratory test that blocked every external request produced the existing Panorama Viewer's `THREE is not defined` error. Final full-installation validation uses those extensions' normal asset loading. This organizer adds no external assets, dependencies or runtime network access. A cold full-interface load also exceeded the first test's 30-second wait; the integration test now allows 90 seconds for initialization/reload instead of treating partial loading as a pass.

Unavailable checks were attempted honestly:

- `rtk proxy npm exec --offline -- eslint --version` from `webui`: failed `ENOTCACHED`; ESLint is not installed/cached. No package was downloaded. The repository's `npm run lint` requires ESLint and normally ignores the `extensions` directory.
- `rtk proxy ..\system\python\python.exe -m pytest -q test/test_torch_utils.py` from `webui`: could not run because pytest is absent. System Python 3.11 and Forge's `venv` Python also have no pytest. The existing API/generation suite needs a running full Forge instance and model fixtures.
- Python fixture source was parsed with `ast.parse`: passed.

## Limits and remaining real-installation checks

Both the production-CSS Gradio fixture and the full installed Forge UI were tested. The full run used the existing venv, loaded the configured local checkpoint and registered all nine requested extension tabs. Packaged Python alone lacked some third-party dependencies, so that earlier partial run was not treated as the complete proof. Unrelated pre-existing extension import warnings remained in the server log; generation, all send-to operations and newer Gradio versions were not tested. Another extension that changes the native tab DOM may conflict; incompatible structures deliberately fall back to native navigation. Test preferences were created in a separate Edge profile, so an arrangement that was previously discarded must be saved once in the user's own profile using the now-visible Save button.

Folders are single-level. Main bar initially includes every available tab so nothing disappears without an explicit assignment. All available top-level tabs are eligible, including core, bundled SVD/Z123 and duplicate IDs. Tabs hidden through Forge's own Settings are not resurrected; their previous organizer assignments survive until they return.

## Instruction checklist and review scope

The AGENTS instructions were supplied in the chat; scans found no on-disk AGENTS.md or SPEC.md in this checkout. Relevant sections reviewed before edits: **Working With Launchers** (apply only to launcher work), **Development Workflow / Understanding the Project**, **Modifying Existing Launcher Projects / preserve existing functionality**, **Troubleshooting with Logs**, and **Best practices / minimal changes**. The supplied screenshot was examined as UI evidence, not treated as an instruction document. `%USERPROFILE%/.codex/RTK.md` was read; subsequent shell commands use `rtk`.

Pinokio home resolved from `~/.pinokio/config.json` to `C:/ForgeStudio/pinokio`. This task modifies Forge navigation, not a Pinokio launcher. Launcher destination, app/plugin selection, launcher example lock-in, start/install/reset/update/menu checks and URL-capture requirements are therefore not applicable. No launcher was created, moved or modified. The actual intended destination is `C:/ForgeStudio/forge/webui/extensions/forge-navigation-bookmarks`, using Forge's own extension conventions. Local Pinokio documentation and examples were inspected; they do not justify moving Forge UI code into a launcher tree.

No `logs` or `pinokio/logs` folder was present; the existing `webui/log` contained an images folder, with no current launcher/runtime terminal log to diagnose. Runtime checks and the lightweight test-server output supplied validation evidence.

Exit checklist: scope follows the user's organization-only and Main bar corrections; only this organizer extension and its tests/docs edited; original tab IDs/handlers/DOM positions preserved by our code; visual-only main-bar ordering and folder hiding; anchored folder lists and hold-to-move added; existing arrangements preserved; no new runtime dependencies; versioned normalized storage with failure/recovery handling; browser and model checks run; unavailable checks and full-installation limits reported; source diff and usage documentation provided. Launcher-specific checks remain not applicable.
