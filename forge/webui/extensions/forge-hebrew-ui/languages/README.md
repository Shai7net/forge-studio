# Interface languages

Every folder here is one more interface language: `languages/<code>/` (for example `languages/es/`) holds the files
an AI returned from the translation kit (`../translation-kit/`), in any number and under any names. A single file
`languages/<code>.json` works too, and so does a plain `{"English": "translation"}` table.

The language button's menu (▾, top right) lists them, and **Add a language…** there saves new ones here.
Whatever a language does not translate stays in English. Delete a folder to remove its language.
