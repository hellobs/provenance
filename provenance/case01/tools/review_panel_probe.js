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

const argv = process.argv.slice(2);
let base = 'http://127.0.0.1:5010';
const ids = [];
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === '--base') { base = argv[++i]; } else { ids.push(argv[i]); }
}

function makeSandbox() {
  const el = () => ({ innerHTML: '', style: {}, onchange: null, dataset: {}, value: 'stub' });
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
    location: { search: '', pathname: '/review', host: '127.0.0.1:5010', protocol: 'http:' },
    URLSearchParams,
    document: {
      getElementById: el,
      querySelectorAll: () => [],
      createElement: el,
      body: { classList: { add: () => {}, remove: () => {} } },
      scrollingElement: { scrollTop: 0 },
    },
  };
  sandbox.globalThis = sandbox;
  sandbox.window = sandbox;
  return sandbox;
}

// 页面末尾会自己调 boot();它的异步失败不该以未捕获拒绝的形式把探针搞崩。
process.on('unhandledRejection', (e) => {
  console.error('页面异步路径(boot)抛了: ' + (e && e.message ? e.message : e));
  process.exit(1);
});

(async () => {
  const pageRes = await fetch(base + '/review');
  const html = await pageRes.text();
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

  let target = ids;
  if (!target.length) {
    const list = await (await fetch(base + '/api/review/runs')).json();
    target = list.runs.map((r) => r.run_id);
  }

  let bad = 0;
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
  process.exit(bad ? 1 : 0);
})();
