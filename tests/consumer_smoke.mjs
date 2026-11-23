/* C 端主路径：首页 → AI 行程 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(__dirname, '..')
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
const PORT = 9345
const PROFILE = path.join(ROOT, '..', '.edge-consumer-smoke')
const BASE = process.env.APP_URL || 'http://127.0.0.1:8000/'
const wait = (ms) => new Promise(r => setTimeout(r, ms))
if (fs.existsSync(PROFILE)) fs.rmSync(PROFILE, { recursive: true, force: true })
const edge = spawn(EDGE, ['--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run', '--disable-extensions',
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${PROFILE}`, '--window-size=1440,1000', BASE], { stdio: 'ignore' })
let target
for (let i = 0; i < 100; i++) {
  try { const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); target = list.find(t => t.type === 'page'); if (target) break } catch {}
  await wait(150)
}
if (!target) throw new Error('浏览器页面未启动')
const ws = new WebSocket(target.webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
let seq = 0
const pending = new Map()
const exceptions = []
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data)
  if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result) }
  if (m.method === 'Runtime.exceptionThrown') exceptions.push(m.params.exceptionDetails.text)
}
const send = (method, params = {}) => new Promise((resolve, reject) => { const id = ++seq; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params })) })
const evaluate = async (expression) => {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, userGesture: true })
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text)
  return r.result?.value
}
const waitFor = async (fn, timeout = 15000, label = '') => {
  const start = Date.now()
  while (Date.now() - start < timeout) { try { if (await fn()) return true } catch {} await wait(200) }
  throw new Error(`等待超时：${label}`)
}
const check = (name, ok, extra = '') => {
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${extra ? ' · ' + extra : ''}`)
  if (!ok) throw new Error(name)
}
const screenshot = async (name) => {
  const shot = await send('Page.captureScreenshot', { format: 'png' })
  fs.mkdirSync(path.join(ROOT, 'screenshots'), { recursive: true })
  fs.writeFileSync(path.join(ROOT, 'screenshots', `${name}.png`), Buffer.from(shot.data, 'base64'))
}
await send('Runtime.enable')
await wait(1200)
await waitFor(() => evaluate("document.readyState==='complete' && !!document.querySelector('#agent-query')"), 12000, 'home')
check('首页是 AI 旅行规划', await evaluate("document.body.innerText.includes('AI 智能行程助手') && document.title.includes('AI 旅行规划')"))
check('游客端没有销售工作台', await evaluate("!document.body.innerText.includes('销售工作台')"))
check('提供目的地/天数/预算/偏好', await evaluate("!!document.getElementById('ai-days') && !!document.getElementById('ai-budget') && !!document.getElementById('ai-pref')"))

await evaluate(`(() => {
  const q=document.getElementById('agent-query');
  q.value='去成都玩3天，预算3000，喜欢美食和历史，不想太赶';
  q.dispatchEvent(new Event('input',{bubbles:true}));
  document.getElementById('btn-agent').click();
  return true;
})()`)
await waitFor(() => evaluate("!!document.querySelector('.ai-day')"), 60000, 'AI plan')
check('AI 行程生成', await evaluate("document.querySelectorAll('.ai-day').length >= 2"))
check('结果不再出现找顾问入口', await evaluate("!document.body.innerText.includes('找真人顾问')"))
await screenshot('consumer-home')
check('无未捕获 JavaScript 异常', exceptions.length === 0, exceptions.join(' | '))
console.log('\nCONSUMER SMOKE OK · 6 checks')
ws.close(); edge.kill(); process.exit(0)
