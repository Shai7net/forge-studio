/* The actual window capture handler must let local dialog shortcuts reach their target. */
'use strict';
const assert=require('assert'),fs=require('fs'),path=require('path');
const source=fs.readFileSync(path.join(__dirname,'../javascript/forge-studio-e-shell.js'),'utf8');
const fn=source.slice(source.indexOf('function keys(e)'),source.indexOf('    /* ---- building and starting ---- */')).trim();
let modal=true,played=0,undone=0,removed=0;
const UI={root:{offsetParent:{},classList:{contains:()=>false},querySelector:()=>modal?{}:null}};
const S={doc:{},selected:['main-shot']};
const F={player:{toggle:()=>played++},undo:()=>undone++,remove:()=>removed++};
const keys=new Function('UI','S','FS','T','return ('+fn+');')(UI,S,F,x=>x);
function event(key,ctrlKey=false){return {key,code:key===' '?'Space':'Key'+key.toUpperCase(),ctrlKey,target:{tagName:'BUTTON'},preventDefault(){},stopPropagation(){}};}
keys(event(' '));keys(event('z',true));keys(event('Delete'));
assert.deepStrictEqual([played,undone,removed],[0,0,0]);
console.log('ok modal shortcuts cannot play, undo or delete on the main timeline');
modal=false;keys(event(' '));keys(event('z',true));keys(event('Delete'));
assert.deepStrictEqual([played,undone,removed],[1,1,1]);
console.log('ok the main timeline retains its shortcuts when no dialog is open');
console.log('2 passed');
