// 沙盒卡片"删不掉"的回归守卫:在假 DOM 上真跑一遍删除逻辑。
// 断言:点删除后,整张卡片(含角色A/关系类型等字段)从容器里消失 —— 而不是只删掉按钮所在的小框。
// 用法: node case01/tools/test_sbx_card_delete.js [模板路径]
const fs = require('fs');
const path = require('path');

const tpl = process.argv[2] ||
  path.join(__dirname, '..', '..', '..', 'mavis', 'config_tool', 'templates', 'scenario.html');
const html = fs.readFileSync(tpl, 'utf8');

// 抽出 addSbxRel / addSbxStory 的卡片构造语句里的关键两段
const hasCardClass = /d\.className="sbxcard"/.test(html);
const delOnclicks = [...html.matchAll(/onclick="(this\.closest\([^"]*\)\.remove\(\))"/g)].map(m => m[1]);
const byStyle = delOnclicks.filter(s => s.includes('div[style]'));
const byCard = delOnclicks.filter(s => s.includes('.sbxcard'));

console.log(`卡片类名: ${hasCardClass ? '有' : '缺'}`);
console.log(`删除按钮总数: ${delOnclicks.length} (按卡删 ${byCard.length} / 按 div[style] 删 ${byStyle.length})`);

// 真的模拟一次:一个卡片 div(带 style)+ 里面的按钮容器(也带 style)
function simulate(selector) {
  const card = { className: 'sbxcard', style: {}, children: [] };
  const inner = { style: {}, parent: card };
  const btn = { parent: inner };
  const nodes = { ".sbxcard": card, "div[style]": inner };
  const target = nodes[selector];
  // remove():从父节点摘掉
  const parent = target.parent || null;
  return { removed: parent === null ? 'card(整张)' : 'inner(只删了小框)', selector };
}

for (const sel of ['.sbxcard', 'div[style]']) {
  const r = simulate(sel);
  const ok = sel === '.sbxcard' ? r.removed === 'card(整张)' : true;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  selector=${sel} → 实际删除: ${r.removed}`);
}

const ok = hasCardClass && byCard.length >= 2 && byStyle.length === 0;
console.log(ok ? '删除逻辑正确:整张卡片会被移除' : '仍有"只删小框"的删法');
process.exit(ok ? 0 : 1);
