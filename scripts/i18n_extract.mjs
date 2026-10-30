/** Conservative Vue/TypeScript i18n codemod. Review exclusions before applying.
 * node scripts/i18n_extract.mjs --collect
 * python scripts/i18n_machine_translate.py --inventory scripts/i18n/inventory.json
 * node scripts/i18n_extract.mjs --apply
 * node scripts/i18n_extract.mjs --scan
 */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';
const toolingRoot = path.resolve(import.meta.dirname, '..');
const rootFlag = process.argv.indexOf('--root');
const root = rootFlag >= 0 ? path.resolve(process.argv[rootFlag + 1]) : toolingRoot;
const require = createRequire(path.join(toolingRoot, 'frontend/package.json'));
const { parse } = require('@vue/compiler-sfc');
const ts = require('typescript');
const src = path.join(root, 'frontend/src');
const reportDir = path.join(root, 'scripts/i18n');
const han = /[\p{Script=Han}]/u;
const exclude = new Set(['main.ts', 'labels.ts', 'toast.ts', 'resultExport.ts', 'api.ts', 'session.ts']);
const walk = dir => fs.readdirSync(dir, { withFileTypes: true }).flatMap(e => e.isDirectory() ? walk(path.join(dir, e.name)) : /\.(vue|ts|css|json)$/.test(e.name) && path.join(dir,e.name) !== path.join(src,'i18n/locales/zh.json') ? [path.join(dir, e.name)] : []);
const files = walk(src);
const plans = [];
const manual = [];
const inventory = {};
function remember(text) { const id = crypto.createHash('sha256').update(text).digest('hex').slice(0, 16); inventory[id] = text; return id; }
function namespace(file) { let base = path.basename(file, path.extname(file)).replace(/View$/, ''); return base[0].toLowerCase() + base.slice(1); }
function analyse(file) {
  const source = fs.readFileSync(file, 'utf8');
  const edits = [];
  const ns = namespace(file);
  const relative = path.relative(root, file);
  const issue = (reason, start, end, text) => { if (!han.test(text)) return; manual.push({ file: relative, line: source.slice(0, start).split('\n').length, reason, start, end, source: text, id: remember(text) }); };
  const add = (start, end, text, kind) => { if (!han.test(text)) return; const id = remember(text); edits.push({start, end, text, kind, id}); };
  const script = (code, offset, template = false) => {
    const ast = ts.createSourceFile(file + '.ts', code, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
    const visit = (node, inFunction = false, pure = false) => {
      if (ts.isTemplateExpression(node)) { if (han.test(node.getText(ast))) { issue('interpolated-template', offset + node.getStart(ast), offset + node.end, node.getText(ast)); return; } }
      if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken && han.test(node.getText(ast))) { issue('concatenation', offset + node.getStart(ast), offset + node.end, node.getText(ast)); return; }
      if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
        if (!han.test(node.text)) return;
        const start = offset + node.getStart(ast), end = offset + node.end;
        const p = node.parent;
        if (ts.isPropertyAssignment(p) && p.name === node || ts.isLiteralTypeNode(p)) { issue('data-key-or-type', start, end, node.text); return; }
        if (!template && (exclude.has(path.basename(file)) || path.extname(file) === '.ts')) { issue('module-or-pure-function', start, end, node.text); return; }
        if (!template && !inFunction) { issue('eager-setup-value', start, end, node.text); return; }
        if (!template && pure) { issue('pure-function-message', start, end, node.text); return; }
        add(start, end, node.text, template ? 'expression' : 'script');
        return;
      }
      const fn = ts.isFunctionLike(node);
      const fnPure = fn && (ts.isFunctionDeclaration(node) || ts.isArrowFunction(node)) && node.body && !/\b(?:await|throw|\.value\s*=|fetch|api\.|toast\.|console\.)/.test(node.body.getText(ast)) && !/\b(?:computed|watch|watchEffect|onMounted)\s*\(/.test(node.parent?.getText(ast)?.slice(0, 40) || '');
      ts.forEachChild(node, child => visit(child, inFunction || fn, fn ? fnPure : pure));
    };
    visit(ast);
  };
  if (file.endsWith('.vue')) {
    const { descriptor, errors } = parse(source, { filename: file });
    if (errors.length) throw new Error(`${file}: ${errors.join(', ')}`);
    for (const block of [descriptor.script, descriptor.scriptSetup].filter(Boolean)) script(block.content, block.loc.start.offset);
    const block = descriptor.template;
    if (block) {
      // compiler-sfc template AST locations are absolute within the SFC.
      const offset = 0;
      const visit = node => {
        if (node.type === 3) return; // Vue comment
        if (node.type === 1) {
          const rich = node.children?.some(c => c.type === 1 && ['a','b','strong','code','em'].includes(c.tag)) && node.children?.some(c => c.type === 2 && han.test(c.content));
          if (rich) { issue('rich-text', offset + node.loc.start.offset, offset + node.loc.end.offset, node.loc.source); return; }
          for (const prop of node.props || []) {
            if (prop.type === 6 && prop.value && han.test(prop.value.content)) {
              const value = prop.value;
              add(offset + prop.loc.start.offset, offset + prop.loc.end.offset, value.content, 'attribute:' + prop.name);
            } else if (prop.type === 7 && prop.exp) script(prop.exp.content, offset + prop.exp.loc.start.offset, true);
          }
        }
        if (node.type === 2) {
          const raw = node.loc.source;
          const text = raw.trim();
          if (han.test(text)) { const leading = raw.length - raw.trimStart().length; add(offset + node.loc.start.offset + leading, offset + node.loc.start.offset + leading + text.length, text, 'text'); }
        }
        if (node.type === 5) script(node.content.content, offset + node.content.loc.start.offset, true);
        for (const child of node.children || []) visit(child);
      };
      visit(block.ast);
    }
  } else if (file.endsWith('.ts')) script(source, 0);
  else if (file.endsWith('.json')) {
    const visitJson = (value) => { if (typeof value === 'string' && han.test(value)) issue('untranslated-json', 0, source.length, value); else if (value && typeof value === 'object') Object.values(value).forEach(visitJson); };
    visitJson(JSON.parse(source));
  }
  else { // CSS: only comments may contain Han; strings/content need manual handling.
    const uncommented = source.replace(/\/\*[\s\S]*?\*\//g, m => ' '.repeat(m.length));
    if (han.test(uncommented)) issue('stylesheet', 0, source.length, uncommented);
  }
  plans.push({file: relative, namespace: ns, hash: crypto.createHash('sha256').update(source).digest('hex'), edits});
}
for (const file of files) analyse(file);
fs.mkdirSync(reportDir, { recursive: true });
if (process.argv.includes('--scan')) {
  const remaining = [...plans.flatMap(p => p.edits.map(e => ({ file: p.file, line: fs.readFileSync(path.join(root,p.file),'utf8').slice(0,e.start).split('\n').length, text:e.text }))), ...manual];
  console.log(JSON.stringify(remaining, null, 2));
  console.error(`Non-comment Chinese: ${remaining.length} occurrences`);
  process.exitCode = remaining.length ? 1 : 0;
} else if (process.argv.includes('--apply')) {
  const cache = JSON.parse(fs.readFileSync(path.join(reportDir, 'translations.json'), 'utf8'));
  for (const p of plans) for (const e of p.edits) if (!cache[e.id]) throw new Error(`Translation missing for ${e.id}`);
  const zhPath = path.join(src, 'i18n/locales/zh.json');
  const enPath = path.join(src, 'i18n/locales/en.json');
  const zh = fs.existsSync(zhPath) ? JSON.parse(fs.readFileSync(zhPath, 'utf8')) : {};
  const en = fs.existsSync(enPath) ? JSON.parse(fs.readFileSync(enPath, 'utf8')) : {};
  const escape = text => text.replace(/[@|{}]/g, c => "{'" + c + "'}");
  for (const plan of plans) {
    if (!plan.edits.length) continue;
    const file = path.join(root, plan.file);
    let source = fs.readFileSync(file, 'utf8');
    const group = zh[plan.namespace] ||= {}, translated = en[plan.namespace] ||= {};
    const replacements = plan.edits.map(edit => {
      const english = cache[edit.id];
      if (!english) throw new Error(`Translation missing for ${edit.id}`);
      let slug = english.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').split('_').slice(0, 9).join('_') || 'message';
      if (group[slug] && group[slug] !== escape(edit.text)) slug += '_' + edit.id.slice(0,6);
      group[slug] = escape(edit.text); translated[slug] = escape(english);
      const key = `${plan.namespace}.${slug}`;
      let value = `t('${key}')`;
      if (edit.kind === 'text') value = `{{ $t('${key}') }}`;
      if (edit.kind === 'expression') value = `$t('${key}')`;
      if (edit.kind.startsWith('attribute:')) value = `:${edit.kind.slice(10)}="$t('${key}')"`;
      return { ...edit, value, key };
    });
    for (const edit of replacements.sort((a,b) => b.start-a.start)) source = source.slice(0, edit.start) + edit.value + source.slice(edit.end);
    if (plan.edits.some(e => e.kind === 'script')) {
      const { descriptor } = parse(source, { filename: file });
      const script = descriptor.scriptSetup || descriptor.script;
      if (!script) throw new Error(`Script missing ${file}`);
      source = source.slice(0, script.loc.start.offset) + '\nimport { useI18n } from "vue-i18n";\nconst { t } = useI18n();\n' + source.slice(script.loc.start.offset);
    }
    fs.writeFileSync(file, source);
  }
  fs.mkdirSync(path.dirname(zhPath), { recursive:true });
  fs.writeFileSync(zhPath, JSON.stringify(zh,null,2)+'\n');
  fs.writeFileSync(enPath, JSON.stringify(en,null,2)+'\n');
  fs.writeFileSync(path.join(reportDir, 'manual-review.json'), JSON.stringify(manual,null,2)+'\n');
  console.log(`Applied ${plans.reduce((n,p)=>n+p.edits.length,0)} replacements; ${manual.length} manual cases.`);
} else {
  fs.writeFileSync(path.join(reportDir,'inventory.json'), JSON.stringify(inventory,null,2)+'\n');
  fs.writeFileSync(path.join(reportDir,'extraction-plan.json'), JSON.stringify(plans,null,2)+'\n');
  fs.writeFileSync(path.join(reportDir,'manual-review.json'), JSON.stringify(manual,null,2)+'\n');
  console.log(`Collected ${Object.keys(inventory).length} unique strings; ${plans.reduce((n,p)=>n+p.edits.length,0)} automatic replacements; ${manual.length} manual cases.`);
}
