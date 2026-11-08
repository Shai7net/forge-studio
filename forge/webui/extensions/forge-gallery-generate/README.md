# Gallery Generate shortcut

Adds one **G** button below the output gallery in **txt2img** and **img2img**, visually before the existing folder/save/send tools. The button is 46 × 46 pixels, with white text on **#F46913** and an English **Generate** tooltip. It turns gray and disables while its tab is generating, then returns to orange. Other controls, navigation, colors and fonts are unchanged.

## Enable and use

This folder is already installed under `webui/extensions`. Refresh Forge with **Ctrl+F5**. If it is not listed/loaded yet, restart Forge using its normal launcher and refresh. Click G or activate it with Enter/Space. It uses the same prompts, settings, img2img mode and extension callbacks as the original Generate button. Existing Interrupt/Skip controls continue to handle stopping/skipping.

To disable, uncheck `forge-gallery-generate` in Forge's Extensions page and apply/restart. It stores no settings and requires no packages, Python production code, downloads or remote assets.

## Integration and scope

Local source identifies Forge `0.0.17v1.8.0rc`, Gradio 3.41.2. `modules/ui_common.py::create_output_panel()` creates `image_buttons_txt2img` and `image_buttons_img2img`. `modules/ui_toprow.py` creates the original `<tab>_generate` buttons; `modules/ui.py` binds their Gradio callbacks to the full generation input/output lists. `javascript/ui.js::submit()` and `submit_img2img()` manage the original Interrupt/Skip visibility and call `javascript/progressbar.js::requestProgress()`.

The shortcut calls the original button's `.click()` rather than duplicating generation functions, inputs or endpoints. Mutation observers follow the native interrupt controls, native disabled state and the progress element's lifecycle. A progress element counts as busy even when its display is disabled in Settings. This also covers jobs started by the main button, prompt submission, hires upscale and restored progress. Pending clicks briefly disable the shortcut before the native lifecycle begins; a rejected/no-task click recovers after two seconds. Generation completion, interruption and the existing progress error handler release the shortcut through the same native signals.

The new button copies only a native tool button's CSS classes. Its ID is separate from the native generate IDs and does not match Forge's generic `button[id$=_generate]` selector. It is appended after the original tools in the DOM, with scoped CSS `order: -1` to place it first visually. This preserves the original tools' relative DOM positions and Cleaner's behavior of cloning the first original tool. The 46px minimum overrides Forge's `.tool` font-based minimum only on these new buttons. No original Forge file, third-party extension, bookmark preference or settings file is edited.

Initialization uses `onUiLoaded`/`onAfterUiUpdate`. Per-tab listeners have abort controllers; observers and timers are cleaned up when original nodes are replaced. Repeated refreshes do not add duplicate buttons or handlers. Missing/unsupported output structures leave the original controls available. The two modes track their own state independently; the shortcut is disabled throughout the native task lifecycle and has an accessible Generate label, busy state, keyboard activation and visible focus ring.

## Files

| File | Purpose |
| --- | --- |
| `javascript/gallery_generate.js` | Shortcut placement, native activation forwarding and lifecycle synchronization |
| `style.css` | Only the two new buttons: requested orange/white, gray disabled state, size and focus |
| `metadata.ini` | Independent Forge extension registration |
| `tests/gradio_fixture.py` | Actual Gradio and production Forge submit/progress JavaScript with deterministic test-only generation backend |
| `tests/browser.test.cjs` | Styling, single submission, lifecycle, prompt/main button, keyboard/touch, errors, rerendering and mobile/theme checks |
| `tests/run_forge_integration.py` | Actual Forge with copies of settings and temporary output paths; no dependency installers/model downloads |
| `tests/forge.test.cjs` | Two small real GPU generations through the shortcuts and the existing Send to img2img tool |
| `.gitignore` | Exclude generated test outputs and caches |

## Validation commands

Run from `C:\ForgeStudio\forge`. Playwright is preinstalled development tooling, not an extension dependency.

```powershell
rtk proxy node --check webui/extensions/forge-gallery-generate/javascript/gallery_generate.js
rtk proxy .\system\python\python.exe webui/extensions/forge-gallery-generate/tests/gradio_fixture.py
rtk proxy node webui/extensions/forge-gallery-generate/tests/browser.test.cjs %USERPROFILE%/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright
rtk proxy .\webui\venv\Scripts\python.exe webui/extensions/forge-gallery-generate/tests/run_forge_integration.py
rtk proxy node webui/extensions/forge-gallery-generate/tests/forge.test.cjs %USERPROFILE%/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright --baseline
rtk proxy node webui/extensions/forge-gallery-generate/tests/forge.test.cjs %USERPROFILE%/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright
```

Browser fixture: seven groups passed with zero unhandled JavaScript errors. Real Gradio callbacks received the current prompts, and repeat clicks on the disabled shortcut did not submit another task. Tests verified orange/white and gray states, keyboard/Space and touch, synchronization from the main button and prompt, hidden progress-bar mode, progress-error recovery, native disabled state, tooltip localization recovery, element replacement, pending/no-task recovery, reload, light/dark themes and 390px viewport. Screenshots are under `output/playwright`.

Full installed Forge verification passed: a real **256×256, four-step txt2img generation**, transfer through the existing **Send to img2img** button, and a real **256×256, four-step img2img generation**. Each shortcut dispatched the native Generate handler exactly once, stayed gray/disabled during the task, and returned to orange afterward. Both shortcuts measured 46px, the English tooltip and requested colors matched, the existing bookmarks organizer remained present, and the original Generate button's text/classes/style were unchanged. Test settings were copies, image output/temporary paths were isolated under this extension's `output`, and automatic image/grid saving was disabled only in the copied config. Hashes of original `config.json`, `ui-config.json`, core `script.js`, core `style.css` and `modules/ui.py` matched their pre-test values.

The full installation emits two existing `Cannot read properties of undefined (reading 'props')` errors from Gradio's `Blocks-b3881047.js:45:1828` while setting txt2img inputs. The initial strict zero-error assertion failed. The **same workflow without this shortcut script** reproduced both errors with identical stacks, and both native generations still completed. The final integration test passes only if the shortcut run's messages and stacks exactly match that native-only baseline, in addition to the generation/state/style assertions; it does not suppress errors or claim a zero-error full installation. Structured evidence and screenshots are in `output/playwright/actual-forge`, with reference data in `actual-forge-baseline`. Existing third-party dependency import warnings also remain outside this change's scope.

The first test fixture omitted Forge's ToolButton component-name override and CSS classes; that test-only mismatch was corrected to match the local implementation before accepting results. JavaScript syntax and Python AST checks supplement the browser tests. Repository ESLint/pytest were found unavailable in earlier navigation work; no dependency was installed for this change. Native submit/progress integration is verified for the installed Forge/Gradio version; newer versions or other extensions that replace generation controls may require adaptation.

## Instruction checklist

Before editing, the supplied AGENTS instructions and `%USERPROFILE%/.codex/RTK.md` were reviewed. Relevant rules: inspect logs first, understand existing architecture, preserve existing functionality, make minimal changes, and recheck before finalizing. No on-disk AGENTS.md was found in this checkout. Existing integration logs and source were inspected first. Shell commands use `rtk`. Pinokio home resolves to `C:/ForgeStudio/pinokio`; this task changes a Forge extension rather than a Pinokio launcher, so launcher destinations, examples, URL capture and menu/start/install/reset rules do not apply. Production destination is `C:/ForgeStudio/forge/webui/extensions/forge-gallery-generate`.

Exit scope: original Generate IDs/callbacks untouched; only two added buttons styled; no settings/persistence writes; bookmark arrangement untouched; no new runtime dependencies; source diff available as `REVIEW.diff`; tests, evidence and limits documented.
