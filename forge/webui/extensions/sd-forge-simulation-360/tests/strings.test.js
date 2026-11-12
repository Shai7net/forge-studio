// node --test extensions/sd-forge-simulation-360/tests/strings.test.js
// Every text the page shows has its Hebrew: the texts are found in the page's own source.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const folder = path.join(__dirname, '..', 'javascript');
global.window = { Sim360: {} };
global.document = { documentElement: { classList: { contains: () => true } } };
require(path.join(__dirname, '..', '..', 'forge-hebrew-ui', 'javascript', 'hebrew-ui-dictionary.js'));
require(path.join(folder, 'sim360-3-strings.js'));
const S = global.window.Sim360;

const STRING = `'((?:[^'\\\\]|\\\\.)*)'`;
const unescape = s => s.replace(/\\'/g, "'").replace(/\\\\/g, '\\');

function shownTexts() {
    const found = new Map();
    const add = (text, where) => { text = unescape(text); if (text && /[A-Za-z]{2}/.test(text)) found.set(text, where); };
    for (const name of ['sim360-4-engine.js', 'sim360-5-ui.js']) {
        const source = fs.readFileSync(path.join(folder, name), 'utf8');
        const patterns = [
            new RegExp(`\\b(?:T|button|field|section|say|E\\.say)\\(\\s*${STRING}`, 'g'),            // the first text of a call
            new RegExp(`\\bnew Error\\(\\s*${STRING}`, 'g'),                                         // what a failure says
            new RegExp(`\\b(?:t|tt|tp|placeholder):\\s*${STRING}`, 'g'),                            // texts of an element
            new RegExp(`\\bcheck\\([^,()]+,\\s*'[^']*',\\s*${STRING}`, 'g'),                        // a checkbox's label
            new RegExp(`\\bcheck\\([^,()]+,\\s*'[^']*',\\s*'(?:[^'\\\\]|\\\\.)*',\\s*${STRING}`, 'g'), // and its hint
            new RegExp(`\\bfield\\(\\s*'[^']*',[^\\n]*?\\),\\s*${STRING}\\)`, 'g'),                    // a field's hint
            new RegExp(`\\[\\s*'[^']*',\\s*${STRING}\\s*\\]`, 'g'),                                 // a choice of a list
        ];
        for (const pattern of patterns) for (const match of source.matchAll(pattern)) add(match[1], name);
    }
    return found;
}

test('every text of the page has its Hebrew', () => {
    const texts = shownTexts();
    assert.ok(texts.size > 150, `only ${texts.size} texts were found: the patterns no longer match the source`);
    // what is not a text for people: names of things in the program
    const notForPeople = /^(s360-|\.|#|[a-z0-9_-]+$|image\/|application\/|[A-Z][a-z]+[A-Z]|Key[A-Z]$|Arrow|Shift|Numpad)/;
    const missing = [];
    for (const [text, where] of texts) {
        if (Object.prototype.hasOwnProperty.call(S.he, text)) continue;
        if (Object.prototype.hasOwnProperty.call(window.hebrewUiDictionary.texts, text)) continue;
        if (notForPeople.test(text) && !/ /.test(text)) continue;
        missing.push(`${where}: ${text}`);
    }
    assert.deepStrictEqual(missing, []);
});

test('the values of a text survive the translation', () => {
    for (const [english, hebrew] of Object.entries(S.he)) {
        const wanted = (english.match(/\{\d\}/g) || []).sort().join();
        assert.strictEqual((hebrew.match(/\{\d\}/g) || []).sort().join(), wanted, english);
    }
    assert.strictEqual(S.T('World {0}', 3), 'עולם 3');
    assert.match(S.T('Capture this view and save or send the frame'), /[א-ת]/);
    assert.strictEqual(S.helpText().length, 5);
});
