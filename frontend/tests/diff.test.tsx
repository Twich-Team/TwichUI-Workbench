import {render} from '@testing-library/react';
import {describe,it,expect} from 'vitest';
import {DiffText,diffLines} from '../src/DiffText';
const patch='--- a/sample.json\n+++ b/sample.json\n@@ -1,2 +1,2 @@\n {\n-  "value": "<img src=x onerror=alert(1)>"\n+  "value": true, "count": 12, "empty": null\n';
describe('untrusted diff presentation',()=>{
 it('separates headers, hunks, context and changes including code beginning ++/--',()=>{
  expect(diffLines(patch).map(l=>l.kind)).toEqual(['metadata','metadata','hunk','context','removed','added']);
  expect(diffLines('@@ -1 +1 @@\n---code\n+++code\n').map(l=>l.kind)).toEqual(['hunk','removed','added']);
 });
 it('preserves exact text, whitespace and trailing newline while decorating JSON safely',()=>{
  const {container}=render(<pre><DiffText text={patch} path="sample.json"/></pre>);
  expect(container.textContent).toBe(patch);expect(container.querySelector('img')).toBeNull();
  for(const kind of ['key','string','number','literal','punctuation'])expect(container.querySelector('.json-'+kind)).not.toBeNull();
 });
 it('keeps unsupported types readable and preserves incomplete content',()=>{
  const text='@@ -1 +1 @@\n-\t old  \n+\t new  ';
  const {container}=render(<pre><DiffText text={text} path="file.lua"/></pre>);
  expect(container.textContent).toBe(text);expect(container.querySelector('.diff-added')).not.toBeNull();
  expect(container.querySelector('[class^="json-"]')).toBeNull();
 });
});
