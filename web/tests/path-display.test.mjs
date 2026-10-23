import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';

const moduleURL = new URL('../src/pathDisplay.ts', import.meta.url);
const paths = existsSync(moduleURL) ? await import(moduleURL.href) : {};
const display = value => { assert.equal(typeof paths.displayPath, 'function'); return paths.displayPath(value); };

test('path display replaces only backslashes and preserves UNC, drives, URLs and repeated slashes', () => {
  const cases = [
    [String.raw`\\fixture-nas\personal_folder`, '//fixture-nas/personal_folder'],
    [String.raw`D:\Photos`, 'D:/Photos'],
    ['https://example.test/a//b', 'https://example.test/a//b'],
    ['/a//b/', '/a//b/'],
    [String.raw`relative\folder`, 'relative/folder'],
    ['  /path  ', '  /path  '], ['', ''],
  ];
  for (const [input, expected] of cases) assert.equal(display(input), expected);
});

test('new or changed paths use forward slashes while an untouched legacy path stays raw', () => {
  assert.equal(typeof paths.pathForSave, 'function');
  const original = String.raw`\\fixture-nas\personal_folder`;
  assert.equal(paths.pathForSave('//fixture-nas/personal_folder', original), original);
  assert.equal(paths.pathForSave(String.raw` D:\Photos `), 'D:/Photos');
  assert.equal(paths.pathForSave(String.raw`\\fixture-nas\new`, original), '//fixture-nas/new');
  assert.equal(paths.pathForSave('', original), '');
});
