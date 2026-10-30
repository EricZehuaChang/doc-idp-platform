import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
const repo=path.resolve(import.meta.dirname,'..');
const require=createRequire(path.join(repo,'frontend/package.json'));
const {parse,compileTemplate}=require('@vue/compiler-sfc');

const fixture=`<template>
  <!-- 中文注释 stays byte-for-byte -->
  <div><a href="/">菜单</a><a href="/help">帮助</a></div>
  <p>普通文本</p>
  <input placeholder="请输入" :title="ready ? '可用' : '不可用'" />
  <p>请看 <a href="/help">帮助</a>。</p>
  <p>地址 @ 示例 | {literal}</p>
</template>
<script setup lang="ts">
// 脚本注释 preserved
const ready = true;
const labels = { queued: '排队中' };
function formatCount(n: number) { return '条目'; }
const concat = '合计' + 3;
const interpolated = \`共 \${count} 个\`;
async function save() { await Promise.resolve(); toast.ok('保存完成'); }
</script>
<style scoped>/* 样式注释 retained */ p { color: red; }</style>
`;

test('extracts AST nodes, preserves comments, reports unsafe cases and is idempotent',()=>{
 const dir=fs.mkdtempSync(path.join(repo,'scripts/i18n/.fixture-'));
 try {
  const sourceDir=path.join(dir,'frontend/src');fs.mkdirSync(sourceDir,{recursive:true});
  const file=path.join(sourceDir,'Fixture.vue');fs.writeFileSync(file,fixture);
  const run=mode=>{const p=spawnSync(process.execPath,[path.join(repo,'scripts/i18n_extract.mjs'),'--root',dir,mode],{encoding:'utf8'});assert.equal(p.status,0,p.stderr+p.stdout);return p;};
  run('--collect');
  const report=JSON.parse(fs.readFileSync(path.join(dir,'scripts/i18n/manual-review.json')));
  assert.deepEqual(new Set(report.map(r=>r.reason)),new Set(['rich-text','eager-setup-value','pure-function-message','concatenation','interpolated-template']));
  assert.equal(report.filter(r=>r.reason==='rich-text').length,1,'navigation containers must not be marked rich text');
  const inventory=JSON.parse(fs.readFileSync(path.join(dir,'scripts/i18n/inventory.json')));
  const plan=JSON.parse(fs.readFileSync(path.join(dir,'scripts/i18n/extraction-plan.json')))[0];
  assert(plan.edits.some(e=>e.kind==='attribute:placeholder'));
  assert.equal(plan.edits.filter(e=>e.kind==='expression').length,2);
  assert(plan.edits.some(e=>e.kind==='script'));
  const cache=Object.fromEntries(Object.entries(inventory).map(([key,value])=>[key,value==='地址 @ 示例 | {literal}'?'Address @ example | {literal}':'same slug']));
  fs.writeFileSync(path.join(dir,'scripts/i18n/translations.json'),JSON.stringify(cache));
  run('--apply');
  const output=fs.readFileSync(file,'utf8');
  assert(output.includes('<!-- 中文注释 stays byte-for-byte -->'));
  assert(output.includes('// 脚本注释 preserved'));
  assert(output.includes('/* 样式注释 retained */'));
  assert(output.includes("const labels = { queued: '排队中' };"),'eager constants stay manual');
  assert(output.includes("return '条目'"),'pure formatter stays manual');
  assert(output.includes("'合计' + 3"));
  assert(output.includes('共 ${count} 个'));
  assert(output.includes("toast.ok(t('fixture."));
  const {descriptor,errors}=parse(output,{filename:file});assert.deepEqual(errors,[]);
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:file,id:'fixture'}).errors,[]);
  const zh=JSON.parse(fs.readFileSync(path.join(sourceDir,'i18n/locales/zh.json'))).fixture;
  assert.equal(Object.keys(zh).length,plan.edits.length,'slug collisions retain every distinct message');
  assert(Object.values(zh).some(v=>v.includes("{'@'}")&&v.includes("{'|'}")&&v.includes("{'{'}literal{'}'}")));
  run('--apply');assert.equal(fs.readFileSync(file,'utf8'),output,'reapply has no changes');
 } finally {fs.rmSync(dir,{recursive:true,force:true});}
});
