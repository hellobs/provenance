// 无头浏览器截图(用于"布局到底对不对"的目视验收)。
//
// 为什么要有它:5010 右栏那张结果卡片只有 ~380px 宽,靠肉眼或看 HTML 都判断不了
// 九块在里面是否挤坏;而 `msedge --headless --screenshot --window-size=390,900`
// **给不了 390 的视口**(实测 innerWidth=504,截出来右边被裁掉,看着像布局坏了)。
// 所以这里用 CDP 的 Emulation.setDeviceMetricsOverride **强制**一个精确视口再截图。
//
// 浏览器:默认找 Edge / Chrome;可用环境变量 BROWSER 指定可执行文件。
// 用法(需要目标服务在跑):
//   node case01/tools/webshot.js <url> <宽> <高> <输出.png> [等待毫秒]
// 例:
//   node case01/tools/webshot.js "http://127.0.0.1:5010/review?embed=1" 372 900 shots/card.png
//   node case01/tools/webshot.js "http://127.0.0.1:5010/" 1680 1000 shots/home.png 3000
//
// 退出码:0=截好了;2=找不到浏览器;1=CDP 出错。
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const CANDIDATES = [
  process.env.BROWSER,
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
].filter(Boolean);

function findBrowser() {
  for (const p of CANDIDATES) { try { if (fs.existsSync(p)) return p; } catch (e) { /* ignore */ } }
  return '';
}

const [url, wArg, hArg, outArg, waitArg] = process.argv.slice(2);
if (!url || !wArg || !hArg || !outArg) {
  console.error('用法: node case01/tools/webshot.js <url> <宽> <高> <输出.png> [等待毫秒]');
  process.exit(2);
}
const width = parseInt(wArg, 10), height = parseInt(hArg, 10), waitMs = parseInt(waitArg || '2500', 10);
const browser = findBrowser();
if (!browser) {
  console.error('找不到 Edge/Chrome;可用 BROWSER=<可执行文件> 指定。');
  process.exit(2);
}
const port = 9401 + (process.pid % 200);
const child = spawn(browser, [
  '--headless=new', '--disable-gpu', '--no-first-run', '--hide-scrollbars',
  `--remote-debugging-port=${port}`,
  `--user-data-dir=${path.join(os.tmpdir(), 'webshot-' + port)}`,
  'about:blank',
], { stdio: 'ignore' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function wsUrl() {
  for (let i = 0; i < 40; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      const page = list.find((t) => t.type === 'page');
      if (page) return page.webSocketDebuggerUrl;
    } catch (e) { /* 还没起来 */ }
    await sleep(300);
  }
  throw new Error('CDP 没起来');
}

(async () => {
  const ws = new WebSocket(await wsUrl());
  let id = 0; const pending = new Map();
  const send = (method, params) => new Promise((res) => {
    const mid = ++id; pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); }
  };
  await new Promise((r) => { ws.onopen = r; });
  await send('Page.enable');
  // 关键:精确强制视口,否则 --window-size 会被系统最小窗口宽度改掉
  await send('Emulation.setDeviceMetricsOverride',
             { width, height, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url });
  await sleep(waitMs);
  const shot = await send('Page.captureScreenshot', { format: 'png' });
  fs.mkdirSync(path.dirname(path.resolve(outArg)), { recursive: true });
  fs.writeFileSync(outArg, Buffer.from(shot.data, 'base64'));
  const m = await send('Runtime.evaluate', {
    expression: 'JSON.stringify({vw:window.innerWidth,doc:document.documentElement.scrollWidth})',
    returnByValue: true });
  console.log(`已截图 ${outArg} (${width}x${height}, 视口 ${m.result.value})`);
  ws.close(); child.kill(); process.exit(0);
})().catch((e) => { console.error('FAIL', e.message); child.kill(); process.exit(1); });
