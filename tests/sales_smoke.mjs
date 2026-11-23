/*
 * 销售工作台闭环浏览器测试
 * 前置：uvicorn 运行在 8000，MySQL 可用
 * 运行：node tests/sales_smoke.mjs
 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(__dirname, '..')
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
const PORT = 9341
const PROFILE = path.join(ROOT, '..', '.edge-sales-smoke')
const BASE = process.env.SALES_URL || 'http://127.0.0.1:8000/sales'
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
await waitFor(() => evaluate("document.readyState==='complete' && !!document.querySelector('#customer-name')"), 12000, 'sales page')
check('销售工作台渲染', await evaluate("document.body.innerText.includes('客户需求') && document.body.innerText.includes('生成 AI 行程草稿')"))

await setInput('#customer-name', '王女士')
await setInput('#budget', '3000')
await setInput('#preferences', '亲子，不想太赶，住得舒服一点')
await evaluate("document.getElementById('btn-generate').click(); true")
await waitFor(() => evaluate("!!document.querySelector('.day')"), 60000, 'AI plan')
check('AI 行程草稿生成', await evaluate("document.querySelectorAll('.day').length >= 2"))
await wait(300)
await screenshot('sales-workbench')

await setInput('#markup', '500')
const finalPrice = await evaluate("document.getElementById('final-price').value")
check('销售改价实时计算', Number(finalPrice) > 0, finalPrice)
await evaluate("document.getElementById('btn-quote').click(); true")
await waitFor(() => evaluate("!!document.querySelector('#share-url')"), 15000, 'quote created')
const quoteUrl = await evaluate("document.getElementById('share-url').value")
check('报价单生成并生成客户链接', !!quoteUrl, quoteUrl)
await screenshot('sales-quote-created')

await send('Page.navigate', { url: quoteUrl })
await waitFor(() => evaluate("!!document.querySelector('.day') && !!document.querySelector('#q-price') && document.querySelector('#q-price').textContent.includes('¥')"), 15000, 'customer quote')
check('客户报价页显示行程与价格', await evaluate("document.body.innerText.includes('专属行程') && document.querySelector('.price').textContent.includes('¥')"))
await screenshot('customer-quote')
await evaluate("document.getElementById('btn-confirm').click(); true")
await waitFor(() => evaluate("!document.getElementById('pay-panel').classList.contains('hidden')"), 15000, 'customer confirm')
check('客户确认报价成功', await evaluate("document.body.innerText.includes('待支付')"))
check('确认后生成订单并锁价', await evaluate("document.body.innerText.includes('订单 TR')"))
await evaluate("document.getElementById('btn-pay').click(); true")
await waitFor(() => evaluate("document.body.innerText.includes('支付成功')"), 15000, 'payment success')
check('客户支付成功并通知供应商', await evaluate("document.body.innerText.includes('已通知销售与供应商')"))
await screenshot('customer-confirmed')
await send('Page.navigate', { url: 'http://127.0.0.1:8000/sales' })
await waitFor(() => evaluate("!!document.querySelector('.supplier-btn')"), 10000, 'supplier action')
check('销售端收到支付完成待供应商确认', await evaluate("document.querySelector('.supplier-btn').textContent.includes('供应商确认')"))
await evaluate("document.querySelector('.supplier-btn').click(); true")
await waitFor(() => evaluate("!document.querySelector('.supplier-btn') && document.body.innerText.includes('供应商已确认')"), 10000, 'supplier confirmed')
check('供应商确认接单', true)
check('无未捕获 JavaScript 异常', exceptions.length === 0, exceptions.join(' | '))
console.log('\nSALES SMOKE OK · 11 checks')
ws.close(); edge.kill(); process.exit(0)
