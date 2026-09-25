// case01 成品记录审阅面板的无头自查(不需要浏览器)。
//
// 作用:把面板页面里的 <script> 抽出来,用最小 DOM stub 在 Node 里真跑一遍九个 pane 的
// 渲染函数,喂真实 run.json(默认把 /api/review/runs 里**每一条**记录都跑一遍),抓:
//   - pane 抛出的运行期异常(光查语法是抓不到的)
//   - 输出里的 undefined / [object Object] / NaN   ← 典型是"字段形状变了但面板没跟上"
// 页面 JS 的语法用 `node --check` 单独查;本脚本查的是**运行期**。
//
// 用法(需要先起着 5010;它现在同时是小镇与结果面板的单一界面):
//   node case01/tools/review_panel_probe.js
//   node case01/tools/review_panel_probe.js --base http://127.0.0.1:5010 260917-demo-case01-mavis-C 260905-demo-case01-old-C
//
// 退出码:0=全部通过;1=有 pane 抛异常或输出可疑。
const vm = require('vm');
const fs = require('fs');

const argv = process.argv.slice(2);
let base = 'http://127.0.0.1:5010';
let pageFile = '';
let onlyDeeplink = false;
const ids = [];
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === '--base') { base = argv[++i]; }
  else if (argv[i] === '--page-file') { pageFile = argv[++i]; }   // 不连服务:用本地渲染好的 HTML
  else if (argv[i] === '--only-deeplink') { onlyDeeplink = true; }
  else { ids.push(argv[i]); }
}

function makeSandbox(opts) {
  opts = opts || {};
  const el = () => ({ innerHTML: '', style: {}, onchange: null, dataset: {}, value: 'stub' });
  // 元素按 id 复用同一个对象(以前每次 getElementById 都返回新对象,于是
  // `main` 渲染出来的内容拿不到 —— 深链/协议这类**行为**就查不了)。
  const els = {};
  // fetch stub 必须对 /api/review/runs 返回一份**合法**列表,否则页面末尾的 boot()
  // 会在 d.runs.map 上抛,把探针自己的失败误报成面板的失败。
  const fakeRuns = {
    count: 1,
    runs: [{ run_id: 'probe', engine: 'mavis', branch: 'A', branch_summary: '',
             start_date: '', end_date: '', n_turns: 0, n_retrievals: 0,
             n_events: 0, n_issues: 0, has_reflection: true }],
  };
  const sandbox = {
    console,
    setInterval: () => 0,
    clearInterval: () => {},
    setTimeout: (f) => { try { f(); } catch (e) { /* noop */ } return 0; },
    fetch: async (url) => ({
      ok: true, status: 200, text: async () => '',
      // 实时那条也要给合法 JSON:页面 boot() 会同时取 /api/review/live。
      json: async () => (String(url).includes('/api/review/live')
        ? { ok: true, live: false }
        : (String(url).includes('/api/review/runs') ? fakeRuns : {})),
    }),
    // 页面用 location 与 URLSearchParams 读嵌入/深链参数,用 document.body 挂 embed 类;
    // stub 缺了它们会在脚本初始化阶段就抛(那是探针自己的问题,不是被测 pane 的问题)。
    location: { search: opts.search || '', pathname: opts.pathname || '/review',
                host: '127.0.0.1:5010', protocol: 'http:' },
    URLSearchParams,
    // 宿主协议(2026-09-24 落地)给页面加了 window.addEventListener("message") 与
    // window.parent.postMessage —— stub 没跟上,这个自查工具**静默坏了**:
    // 初始化就抛 "window.addEventListener is not a function"(2026-09-25 第十五轮体检发现)。
    addEventListener: () => {},
    removeEventListener: () => {},
    postMessage: () => {},
    parent: null,
    document: {
      getElementById: (id) => (els[id] || (els[id] = el())),
      querySelectorAll: () => [],
      createElement: el,
      body: { classList: { add: () => {}, remove: () => {} } },
      scrollingElement: { scrollTop: 0 },
      addEventListener: () => {},
    },
  };
  sandbox.globalThis = sandbox;
  sandbox.window = sandbox;
  sandbox.window.parent = sandbox;   // 页面里会判 window.parent === window(非嵌入时不发消息)
  sandbox.__els = els;               // 供探针断言"渲染出来的是什么"
  return sandbox;
}

// 页面末尾会自己调 boot();它的异步失败不该以未捕获拒绝的形式把探针搞崩。
process.on('unhandledRejection', (e) => {
  console.error('页面异步路径(boot)抛了: ' + (e && e.message ? e.message : e));
  process.exit(1);
});

(async () => {
  const html = pageFile ? fs.readFileSync(pageFile, 'utf8')
                        : await (await fetch(base + '/review')).text();
  const m = html.match(/<script>([\s\S]*?)<\/script>/);
  if (!m) { console.error('FAIL: 页面里找不到 <script>'); process.exit(1); }

  const sandbox = makeSandbox();
  try {
    vm.runInNewContext(m[1] + '\n;globalThis.__t = { PANES, TABS };', sandbox);
  } catch (e) {
    console.error('FAIL: 脚本初始化抛异常: ' + e.message);
    process.exit(1);
  }
  const { PANES, TABS } = sandbox.__t;

  let bad = 0;
  let target = ids;
  if (!onlyDeeplink && !target.length) {
    const list = await (await fetch(base + '/api/review/runs')).json();
    target = list.runs.map((r) => r.run_id);
  }
  if (onlyDeeplink) { target = []; }

  for (const rid of target) {
    const res = await fetch(`${base}/api/review/run/${encodeURIComponent(rid)}`);
    if (!res.ok) { console.log(`[${rid}] 取记录失败 HTTP ${res.status}`); bad++; continue; }
    const run = await res.json();
    const engine = ('injector' in run) ? 'mavis' : '旧引擎';
    console.log(`\n=== ${rid} (${engine}, 顶层键 ${Object.keys(run).length}) ===`);
    for (const [id, label] of TABS) {
      let out = '', err = '';
      try { out = String(PANES[id](run)); } catch (e) { err = e.message; }
      if (err) { console.log(`  ${label.padEnd(6)} 抛异常: ${err}`); bad++; continue; }
      const sus = [];
      if (out.includes('undefined')) sus.push('含 undefined');
      if (out.includes('[object Object]')) sus.push('含 [object Object]');
      if (out.includes('NaN')) sus.push('含 NaN');
      if (sus.length) bad++;
      const vis = out.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 76);
      console.log(`  ${label.padEnd(6)} ${String(out.length).padStart(6)} 字符 ${sus.length ? '⚠ ' + sus.join('/') : 'ok'}  | ${vis}`);
    }
  }
  console.log(bad ? `\n自查未过:${bad} 处可疑` : '\n自查通过:所有 pane 都能渲染且无 undefined/[object Object]/NaN');

  // ---- 深链页签行为(2026-09-25 第十五轮体检加) ----
  // 文档《给平台侧_嵌入与数据接入》§五 承诺:tab 非法 → 页面上黄条说明并回到默认页签。
  // 实测此前 /embed/review?tab=injector 会把**主区域**渲染成注入器面板(导航里却没有这个页签),
  // 而那句 SAFE 守卫写在读 WANT_TAB 之前 = 死代码。这里把行为钉住。
  async function renderPage(opts) {
    const sb = makeSandbox(opts);
    vm.runInNewContext(m[1] + '\n;globalThis.__t2 = { TAB };', sb);
    for (let i = 0; i < 12; i++) { await new Promise((r) => setImmediate(r)); }
    return { sb, main: (sb.__els.main || {}).innerHTML || '', nav: (sb.__els.nav || {}).innerHTML || '' };
  }
  const checks = [
    { name: '专家面 ?tab=injector 不许渲染注入器面板',
      opts: { search: '?tab=injector', pathname: '/embed/review' },
      want: '嵌入面没有该页签', forbid: '无注入器记录' },
    { name: '专家面 ?tab=nope 要有黄条说明',
      opts: { search: '?tab=nope', pathname: '/embed/review' },
      want: '不存在', forbid: '' },
    { name: '内部面 ?tab=states 正常切换',
      opts: { search: '?tab=states', pathname: '/review' },
      want: '', forbid: '深链页签' },
  ];
  console.log('\n-- 深链页签行为 --');
  for (const c of checks) {
    let r;
    try { r = await renderPage(c.opts); } catch (e) {
      console.log(`  FAIL ${c.name}: 初始化抛异常 ${e.message}`); bad++; continue;
    }
    const problems = [];
    if (c.want && !r.main.includes(c.want)) problems.push('缺少说明:' + c.want);
    if (c.forbid && r.main.includes(c.forbid)) problems.push('不该出现:' + c.forbid);
    if (c.opts.pathname.startsWith('/embed/') && r.nav.includes('注入器')) problems.push('导航里有注入器页签');
    if (problems.length) { console.log(`  FAIL ${c.name}: ${problems.join('; ')}`); bad++; }
    else { console.log(`  ok   ${c.name}`); }
  }
  console.log(bad ? `\n合计未过:${bad} 处` : '\n合计通过:渲染与深链行为都符合契约');
  // 退出码要可信:Node 在某些 Windows 版本上,`process.exit()` 撞上还没关干净的
  // fetch 连接会触发 libuv 断言(win/async.c),进程以 0xC0000409 结束——
  // 明明自查通过,退出码却像崩了(实测踩过)。所以先设 exitCode 让事件循环自然排空,
  // 只在真的卡住时才强制退。
  process.exitCode = bad ? 1 : 0;
  const hard = setTimeout(() => process.exit(bad ? 1 : 0), 4000);
  hard.unref();
})();
