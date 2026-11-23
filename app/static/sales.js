/* 云游旅行 · 销售工作台 / 客户报价页 */
const $ = (id) => document.getElementById(id);
const SALES_USER = localStorage.getItem('travel_sales_user') || '1';
let CITIES = [];
let currentPlan = null;
let currentQuote = null;

const fmt = (n) => '¥' + Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (!opts.public) headers['X-User-Id'] = SALES_USER;
  if (opts.body && !(opts.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const r = await fetch(path, {
    method: opts.method || 'GET',
    headers,
    body: opts.body ? (opts.body instanceof FormData ? opts.body : JSON.stringify(opts.body)) : undefined,
  });
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) {
    const detail = data && data.detail ? data.detail : `请求失败（${r.status}）`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}

function setStatus(el, message, kind = '') {
  const node = typeof el === 'string' ? $(el) : el;
  if (!node) return;
  node.textContent = message || '';
  node.className = 'status-line' + (kind ? ' ' + kind : '');
}

function renderPlan(plan, container) {
  const days = plan?.days_plan || [];
  if (!days.length) {
    container.innerHTML = '<div class="empty"><p>行程没有返回逐日安排，请重试。</p></div>';
    return;
  }
  container.innerHTML = days.map((d) => {
    const recs = (d.recommendations || []).map((x) =>
      `<span class="rec">${esc(x.name || '')} · ${fmt(x.price)}</span>`).join('');
    return `<article class="day">
      <div class="day-head"><span class="day-no">D${esc(d.day)}</span><h3>${esc(d.title || '')}</h3></div>
      <p><b>上午</b> ${esc(d.morning || '')}</p>
      <p><b>下午</b> ${esc(d.afternoon || '')}</p>
      <p><b>晚上</b> ${esc(d.evening || '')}</p>
      ${recs ? `<div class="recs">${recs}</div>` : ''}
    </article>`;
  }).join('');
}

/* ==================== 销售工作台 ==================== */
async function loadCities() {
  try {
    const data = await api('/api/meta/cities');
    CITIES = data.cities || [];
    const select = $('city');
    if (!select) return;
    select.innerHTML = CITIES.map((c) => `<option value="${c.id}" data-name="${esc(c.name)}">${esc(c.name)}</option>`).join('');
    const bj = CITIES.find((c) => c.name.includes('北京')) || CITIES[0];
    if (bj) select.value = String(bj.id);
  } catch (e) {
    setStatus('generate-status', '城市列表加载失败：' + e.message, 'error');
  }
}

function recalcPrice() {
  const base = Number($('base-cost')?.value || 0);
  const markup = Number($('markup')?.value || 0);
  const final = Math.max(0, base + markup);
  if ($('final-price')) $('final-price').value = final.toFixed(2);
  return { base, markup, final };
}

async function generatePlan() {
  const name = $('customer-name').value.trim();
  if (!name) { setStatus('generate-status', '先填写客户姓名。', 'error'); return; }
  const cityOption = $('city').selectedOptions[0];
  const city = cityOption?.dataset.name || cityOption?.textContent || '';
  const areaId = Number($('city').value || 2);
  const days = Number($('days').value || 3);
  const budget = Number($('budget').value || 0);
  const prefs = $('preferences').value.trim();
  const btn = $('btn-generate');
  btn.disabled = true;
  $('plan-empty').classList.add('hidden');
  $('plan-content').classList.remove('hidden');
  setStatus('generate-status', 'AI 正在结合平台商品与攻略库规划行程，通常需要 8–15 秒…');
  try {
    const plan = await api('/api/ai/plan', {
      method: 'POST',
      body: { areaId, city, days, budget, preferences: prefs },
    });
    currentPlan = plan;
    $('plan-title').textContent = `${city} · ${days} 天 AI 行程草稿`;
    $('plan-summary').textContent = plan.summary || 'AI 已根据客户偏好生成逐日安排。';
    renderPlan(plan, $('plan-days'));
    const base = Number(plan.estimated_cost || 0);
    $('base-cost').value = base.toFixed(2);
    $('markup').value = Math.round(base * 0.1 * 100) / 100;
    recalcPrice();
    setStatus('generate-status', plan.ai_powered ? 'AI 行程已生成。请核对成本并调整报价。' : '已生成本地推荐行程（AI 服务降级）。', 'ok');
    $('plan-content').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) {
    setStatus('generate-status', '生成失败：' + e.message, 'error');
  } finally {
    btn.disabled = false;
  }
}

function showCreatedQuote(quote) {
  currentQuote = quote;
  const box = $('created-quote');
  if (!box) return;
  const fullUrl = `${location.origin}${quote.quoteUrl}`;
  box.classList.remove('hidden');
  box.innerHTML = `<h3>报价单已生成</h3>
    <div class="quote-no">${esc(quote.quoteNo)}</div>
    <div class="share-link"><input id="share-url" value="${esc(fullUrl)}" readonly><button id="btn-copy" class="btn">复制</button></div>
    <a class="btn primary block" href="${esc(quote.quoteUrl)}" target="_blank">打开客户确认页</a>
    <div class="hint">客户看到 ${fmt(quote.finalPrice)} · 有效 ${quote.validHours} 小时</div>`;
  $('btn-copy').onclick = async () => {
    try { await navigator.clipboard.writeText(fullUrl); $('btn-copy').textContent = '已复制'; }
    catch { $('share-url').select(); document.execCommand('copy'); $('btn-copy').textContent = '已复制'; }
  };
}

async function createQuote() {
  if (!currentPlan) { setStatus('quote-status', '先生成 AI 行程。', 'error'); return; }
  const { base, markup, final } = recalcPrice();
  if (final <= 0) { setStatus('quote-status', '报价必须大于 0。', 'error'); return; }
  const cityOption = $('city').selectedOptions[0];
  const payload = {
    customerName: $('customer-name').value.trim(),
    customerPhone: $('customer-phone').value.trim(),
    city: cityOption?.dataset.name || cityOption?.textContent || '',
    days: Number($('days').value || 3),
    budget: Number($('budget').value || 0),
    baseCost: base,
    markupAmount: markup,
    validHours: Number($('valid-hours').value || 48),
    plan: currentPlan,
    notes: $('quote-notes').value.trim(),
  };
  const btn = $('btn-quote');
  btn.disabled = true;
  setStatus('quote-status', '正在生成报价单…');
  try {
    const quote = await api('/api/quotes', { method: 'POST', body: payload });
    showCreatedQuote(quote);
    setStatus('quote-status', '报价单已生成，可复制链接发给客户。', 'ok');
    await loadHistory();
  } catch (e) {
    setStatus('quote-status', '生成失败：' + e.message, 'error');
  } finally {
    btn.disabled = false;
  }
}

async function loadHistory() {
  try {
    const data = await api('/api/quotes?limit=20');
    const list = $('quote-list');
    if (!data.items?.length) { list.innerHTML = '<div class="muted">暂无报价</div>'; return; }
    list.innerHTML = data.items.map((q) => `<div class="quote-item" data-url="${esc(q.quoteUrl)}">
      <div class="quote-item-top"><b>${esc(q.customerName)} · ${esc(q.city)}</b><span class="pill ${esc(q.status)}">${esc(q.statusLabel)}</span></div>
      <small>${esc(q.quoteNo)} · ${fmt(q.finalPrice)} · ${esc(q.days)} 天${q.orderNo ? ' · 订单 ' + esc(q.orderNo) : ''}${q.paymentStatus === 'paid' ? ' · 已支付' : ''}${q.supplierStatus === 'confirmed' ? ' · 供应商已确认' : ''}</small>
      ${q.paymentStatus === 'paid' && q.supplierStatus !== 'confirmed' ? `<button class="btn supplier-btn" data-supplier="${esc(q.token)}">供应商确认</button>` : ''}
    </div>`).join('');
    list.querySelectorAll('.quote-item').forEach((el) => {
      el.onclick = () => window.open(el.dataset.url, '_blank');
    });
    list.querySelectorAll('.supplier-btn').forEach((el) => {
      el.onclick = (event) => { event.stopPropagation(); supplierConfirm(el.dataset.supplier); };
    });
  } catch (e) {
    $('quote-list').innerHTML = `<div class="muted">${esc(e.message)}</div>`;
  }
}

async function supplierConfirm(token) {
  try {
    await api(`/api/quotes/${encodeURIComponent(token)}/supplier-confirm`, { method: 'POST' });
    await loadHistory();
  } catch (e) {
    alert(e.message);
  }
}

function initSales() {
  loadCities();
  $('btn-generate').onclick = generatePlan;
  $('btn-quote').onclick = createQuote;
  $('btn-refresh').onclick = loadHistory;
  $('base-cost').oninput = recalcPrice;
  $('markup').oninput = recalcPrice;
  loadHistory();
}

/* ==================== 客户报价页 ==================== */
async function initCustomer(token) {
  $('sales-workspace').classList.add('hidden');
  $('customer-view').classList.remove('hidden');
  document.querySelector('.page-head').classList.add('hidden');
  try {
    const q = await api(`/api/quotes/${encodeURIComponent(token)}`, { public: true });
    currentQuote = q;
    $('q-customer-title').textContent = `${q.customerName}，这是您的专属行程`;
    $('q-no').textContent = q.quoteNo;
    $('q-price').textContent = fmt(q.finalPrice);
    $('q-status').textContent = q.statusLabel;
    $('q-status').className = 'status-pill ' + q.status;
    $('q-meta').innerHTML = `<div class="meta-row"><span>目的地</span><b>${esc(q.city)}</b></div>
      <div class="meta-row"><span>行程天数</span><b>${esc(q.days)} 天</b></div>
      <div class="meta-row"><span>有效至</span><b>${esc(q.expiresAt || '')}</b></div>
      ${q.orderNo ? `<div class="meta-row"><span>订单号</span><b>${esc(q.orderNo)}</b></div>` : ''}`;
    $('q-notes').textContent = q.notes || '含行程内所列项目，最终以顾问确认的出行细节为准。';
    renderPlan(q.plan, $('q-plan-panel'));
    $('q-plan-panel').insertAdjacentHTML('afterbegin', `<div class="panel-title"><div><h2>${esc(q.city)} · ${esc(q.days)} 天行程</h2><p>${esc(q.plan?.summary || '')}</p></div><span class="tag success">专属报价</span></div>`);
    if (q.status === 'confirmed') {
      $('btn-confirm').classList.add('hidden');
      if (q.paymentStatus === 'paid') {
        $('q-status').textContent = '支付成功，待供应商确认';
        $('q-status').className = 'status-pill confirmed';
        $('q-success').classList.remove('hidden');
        $('q-success-text').textContent = `订单 ${q.orderNo || ''} 已支付 · 已通知销售与供应商确认。`;
      } else {
        showPayment(q);
      }
    } else if (q.status === 'expired') {
      $('btn-confirm').disabled = true;
      $('btn-confirm').textContent = '报价已过期';
      $('q-confirm-status').textContent = '请联系顾问重新报价。';
    }
    $('btn-confirm').onclick = confirmQuote;
  } catch (e) {
    $('q-plan-panel').innerHTML = `<div class="empty"><h2>报价单不可用</h2><p>${esc(e.message)}</p></div>`;
    $('btn-confirm').disabled = true;
  }
}

function showPayment(q) {
  $('pay-panel').classList.remove('hidden');
  $('q-pay-amount').textContent = fmt(q.finalPrice);
  $('q-confirm-status').textContent = `报价已确认，订单 ${q.orderNo || ''} 已生成，请完成支付。`;
  const meta = $('q-meta');
  if (meta && q.orderNo && !meta.innerText.includes(q.orderNo)) {
    meta.insertAdjacentHTML('beforeend', `<div class="meta-row"><span>订单号</span><b>${esc(q.orderNo)}</b></div>`);
  }
  $('btn-pay').onclick = payQuote;
}

async function payQuote() {
  if (!currentQuote) return;
  const btn = $('btn-pay');
  btn.disabled = true;
  setStatus('q-confirm-status', '正在创建支付单并模拟渠道回调…');
  try {
    await api(`/api/quotes/${encodeURIComponent(currentQuote.token)}/pay`, {
      method: 'POST',
      public: true,
      body: { paymentMethodCode: $('pay-method').value },
    });
    const q = await api(`/api/quotes/${encodeURIComponent(currentQuote.token)}/pay/confirm`, {
      method: 'POST', public: true,
    });
    currentQuote = q;
    $('pay-panel').classList.add('hidden');
    $('btn-confirm').classList.add('hidden');
    $('q-status').textContent = '支付成功，待供应商确认';
    $('q-status').className = 'status-pill confirmed';
    $('q-success').classList.remove('hidden');
    $('q-success-text').textContent = `订单 ${q.orderNo || ''} 已支付 · 已通知销售与供应商确认。`;
    setStatus('q-confirm-status', '支付成功。', 'ok');
  } catch (e) {
    setStatus('q-confirm-status', e.message, 'error');
    btn.disabled = false;
  }
}

async function confirmQuote() {
  if (!currentQuote) return;
  const btn = $('btn-confirm');
  btn.disabled = true;
  setStatus('q-confirm-status', '正在确认…');
  try {
    const q = await api(`/api/quotes/${encodeURIComponent(currentQuote.token)}/confirm`, { method: 'POST', public: true });
    currentQuote = q;
    $('q-status').textContent = '已确认，待支付';
    $('q-status').className = 'status-pill confirmed';
    $('btn-confirm').classList.add('hidden');
    setStatus('q-confirm-status', '报价已确认，订单已生成。', 'ok');
    showPayment(q);
  } catch (e) {
    setStatus('q-confirm-status', e.message, 'error');
    btn.disabled = false;
  }
}

/* ==================== 入口 ==================== */
const path = location.pathname;
if (path.startsWith('/quote/')) {
  const small = document.querySelector('.brand small');
  if (small) small.textContent = '客户报价';
  const nav = document.querySelector('.topbar nav');
  if (nav) nav.innerHTML = '<span class="env">客户专属报价</span>';
  initCustomer(decodeURIComponent(path.split('/quote/')[1].split('/')[0]));
} else {
  initSales();
}
