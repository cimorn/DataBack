import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
const url=new URL('../src/inputLimits.ts',import.meta.url);
const limits=existsSync(url)?await import(url.href):{};

test('new notes are bounded while unchanged long legacy text remains exact',()=>{
 assert.equal(limits.MAX_NOTES_CHARS,2000);
 assert.equal(limits.MAX_RECORDS_PER_BATCH,20);
 assert.equal(limits.notesForSave('  正常备注  '),'正常备注');
 assert.throws(()=>limits.notesForSave('文'.repeat(2001)),/2000/);
 const original='  '+ '旧'.repeat(3000)+'  ';
 assert.equal(limits.notesForSave(original,original),original);
 assert.throws(()=>limits.notesForSave(original+'变',original),/2000/);
});
