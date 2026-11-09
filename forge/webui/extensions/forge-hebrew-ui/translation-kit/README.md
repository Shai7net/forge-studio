# Forge Studio translation kit

Forge Studio comes in English and Hebrew. This kit lets any AI translate it into another language:
17 files with 5054 interface texts and 63 help texts, in English.

1. Give your AI (ChatGPT, Claude, Gemini, ...) one or more of the `part-*.json` files at a time, with this request:

   > Translate this Forge Studio interface file into <your language>. Follow the "instructions" inside the file exactly. Return the complete file as valid JSON (same keys, every value translated), with nothing before or after it.

2. Save every file it returns (the name does not matter, `.json`).
3. In Forge Studio, open the language menu (▾ next to the language button, top right) → **Add a language...** →
   **Load the translated files**, and choose all of them. Or copy them into
   `forge/webui/extensions/forge-hebrew-ui/languages/<code>/` (for example `languages/es/`) and reload the page.

A language does not have to be complete: whatever is missing stays in English. After an update, a new kit may have
new texts; translate the new parts and load them into the same language.

An AI coding agent can do the whole kit in one go: ask it to translate every part in this folder by these rules and
to save the results in `forge/webui/extensions/forge-hebrew-ui/languages/<code>/`.
