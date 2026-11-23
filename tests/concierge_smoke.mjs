/* C 端 AI 行程 → 找顾问 → 销售工作台线索 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(__dirname, '..')
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
const PORT = 9343
const PROFILE = path.join(ROOT, '..', '.edge-concierge-smoke')
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
const setInput = async (selector, value) => {
  await evaluate(`(() => { const e=document.querySelector(${JSON.stringify(selector)}); e.value=${JSON.stringify(value)}; e.dispatchEvent(new Event('input',{bubbles:true})); return true })()`)
  await wait(180)
}
await send('Runtime.enable')
await wait(1200)
await waitFor(() => evaluate("document.readyState==='complete' && !!document.querySelector('#agent-query')"), 12000, 'consumer home')
check('游客首页默认是 AI 规划', await evaluate("!!document.querySelector('#btn-agent') && document.body.innerText.includes('AI 行程助手')"))
check('游客导航不出现销售工作台', await evaluate("![...document.querySelectorAll('nav a')].some(a=>a.textContent.includes('销售工作台'))"))

await setInput('#agent-query', '下周末去成都玩3天，预算3000，亲子，不想太赶')
await evaluate("document.getElementById('btn-agent').click(); true")
await waitFor(() => evaluate("!!document.querySelector('#concierge-box') && !!document.querySelector('.ai-day')"), 60000, 'AI plan')
check('AI 行程生成并出现找顾问入口', await evaluate("document.body.innerText.includes('找真人顾问')"))

await evaluate("[...document.querySelectorAll('#ai-result button')].find(b=>b.textContent.includes('找真人顾问')).click(); true")
await setInput('#concierge-name', '王女士')
await setInput('#concierge-phone', '13800000000')
await evaluate("[...document.querySelectorAll('#concierge-box button')].find(b=>b.textContent.includes('提交给顾问')).click(); true")
await waitFor(() => evaluate("document.getElementById('concierge-status').textContent.includes('已提交给顾问')"), 15000, 'inquiry submitted')
check('游客主动提交询价成功', true)
await screenshot('consumer-concierge')

await send('Page.navigate', { url: 'http://127.0.0.1:8000/sales' })
await waitFor(() => evaluate("!!document.querySelector('.inquiry-item')"), 10000, 'sales inquiry')
check('销售工作台收到询价线索', await evaluate("document.querySelector('.inquiry-item').innerText.includes('王女士')"))
await evaluate("document.querySelector('.inquiry-item').click(); true")
await waitFor(() => evaluate("document.getElementById('customer-name').value === '王女士' && !!document.querySelector('.day')"), 10000, 'inquiry applied')
check('点击线索带出行程与客户信息', true)
await screenshot('sales-inquiry')
check('无未捕获 JavaScript 异常', exceptions.length === 0, exceptions.join(' | '))
console.log('\nCONCIERGE SMOKE OK · 6 checks')
ws.close(); edge.kill(); process.exit(0)
