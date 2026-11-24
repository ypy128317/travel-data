/* 云游旅行 · 运营管理后台 —— Vue3 (Composition API) */
const { createApp, ref, computed, onMounted } = Vue;
const TOKEN_KEY = 'yunyou_admin_token';
const charts = {};

createApp({
  setup() {
    /* ---------- 登录态 ---------- */
    const logged = ref(false);
    const lgUser = ref('admin');
    const lgPass = ref('123456');
    const lgErr = ref('');
    const lgBusy = ref(false);
    const who = ref('admin');

    /* ---------- 导航 ---------- */
    const navs = [
      { m: 'dash', ic: '📊', t: '数据概览' },
      { m: 'orders', ic: '📋', t: '订单管理' },
      { m: 'users', ic: '👥', t: '用户与会员' },
      { m: 'refunds', ic: '💰', t: '退款管理' },
      { m: 'guides', ic: '🧭', t: '导游管理' },
      { m: 'ai', ic: '🤖', t: 'AI 运营数据' },
      { m: 'site', ic: '🌐', t: '返回游客端', sep: true },
    ];
    const TITLES = {
      dash: '📊 数据概览', orders: '📋 订单管理', users: '👥 用户与会员',
      refunds: '💰 退款管理', guides: '🧭 导游管理', ai: '🤖 AI 运营数据',
    };
    const current = ref('dash');
    const pageTitle = computed(() => TITLES[current.value] || '');
    const todayLine = ref('—');

    /* ---------- 数据 ---------- */
    const cards = ref([]);
    const tables = ref({ orders: '', users: '', refunds: '', guides: '', bookings: '' });
    const aiMini = ref([]);
    const aiCaps = ref([]);
    const aiCities = ref([]);
    const aiModel = ref('—');

    /* ---------- 工具 ---------- */
    function fmtNum(n) {
      const v = Number(n) || 0;
      return v >= 10000 ? v.toLocaleString('zh-CN', { maximumFractionDigits: 0 }) : String(v);
    }
    const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const stCls = s => 'st-' + String(s || '').replace(/\s/g, '_');
    function table(cols, rows, rowFn) {
      if (!rows || !rows.length) return '<div class="empty">暂无数据</div>';
      return '<table><thead><tr>' + cols.map(c => '<th>' + c + '</th>').join('') + '</tr></thead><tbody>' +
        rows.map(rowFn).join('') + '</tbody></table>';
    }
    async function aapi(path, opt = {}) {
      const h = { 'Content-Type': 'application/json' };
      const token = sessionStorage.getItem(TOKEN_KEY);
      if (token) h['Authorization'] = 'Bearer ' + token;
      try {
        const r = await fetch('/api/admin' + path, {
          method: opt.method || 'GET', headers: h,
          body: opt.body ? JSON.stringify(opt.body) : undefined,
        });
        return await r.json();
      } catch (e) { return { ok: false, message: String(e) }; }
    }

    /* ---------- ECharts ---------- */
    const AXIS = {
      axisLine: { lineStyle: { color: '#2a3a5c' } },
      axisLabel: { color: '#94a3b8', fontSize: 10 },
      splitLine: { lineStyle: { color: '#ffffff0d' } },
    };
    function chart(id) {
      if (!window.echarts) return null;
      const el = document.getElementById(id);
      if (!el) return null;
      if (!charts[id]) charts[id] = echarts.init(el, 'dark');
      return charts[id];
    }
    function resizeCharts() { Object.values(charts).forEach(c => { try { c.resize(); } catch (e) {} }); }
    window.addEventListener('resize', resizeCharts);

    /* ---------- 登录 / 退出 ---------- */
    async function doLogin() {
      lgErr.value = ''; lgBusy.value = true;
      const d = await aapi('/login', { method: 'POST', body: { username: lgUser.value.trim(), password: lgPass.value } });
      lgBusy.value = false;
      if (d.ok) { sessionStorage.setItem(TOKEN_KEY, d.token); enter(); }
      else lgErr.value = d.message || '登录失败，请重试';
    }
    function doLogout() {
      sessionStorage.removeItem(TOKEN_KEY);
      logged.value = false;
    }
    function enter() {
      who.value = sessionStorage.getItem('yunyou_admin_user') || 'admin';
      logged.value = true;
      go('dash');
    }

    /* ---------- 导航切换 ---------- */
    function go(m) {
      if (m === 'site') { window.open('/', '_blank'); return; }
      current.value = m;
      if (m === 'dash') loadDash();
      else if (m === 'orders') loadOrders();
      else if (m === 'users') loadUsers();
      else if (m === 'refunds') loadRefunds();
      else if (m === 'guides') loadGuides();
      else if (m === 'ai') loadAI();
    }

    /* ---------- 数据概览 ---------- */
    async function loadDash() {
      const d = await aapi('/overview');
      if (d.cards) {
        cards.value = d.cards;
        if (d.today) todayLine.value = '今日 ' + d.today.orders + ' 单 · ¥' + fmtNum(d.today.amount);
      }
      const t = await aapi('/order-trend?days=14');
      const c1 = chart('chart-trend');
      if (c1 && t.labels) c1.setOption({
        backgroundColor: 'transparent', tooltip: { trigger: 'axis' },
        legend: { data: ['订单量', '营收(元)'], textStyle: { color: '#94a3b8' } },
        grid: { left: 40, right: 50, top: 40, bottom: 30 },
        xAxis: { type: 'category', data: t.labels, ...AXIS },
        yAxis: [{ type: 'value', ...AXIS }, { type: 'value', ...AXIS }],
        series: [
          { name: '订单量', type: 'bar', data: t.counts, itemStyle: { color: '#6366f1', borderRadius: [4, 4, 0, 0] }, barMaxWidth: 18 },
          { name: '营收(元)', type: 'line', yAxisIndex: 1, data: t.amounts, smooth: true, symbolSize: 5,
            lineStyle: { color: '#22d3ee', width: 2 }, itemStyle: { color: '#22d3ee' } },
        ],
      });
      const dist = await aapi('/order-distribution');
      const c2 = chart('chart-type');
      if (c2 && dist.types) c2.setOption({
        backgroundColor: 'transparent', tooltip: { trigger: 'item' },
        legend: { bottom: 0, textStyle: { color: '#94a3b8', fontSize: 11 } },
        series: [{ type: 'pie', radius: ['42%', '68%'], center: ['50%', '44%'], avoidLabelOverlap: true,
          itemStyle: { borderColor: '#1a2740', borderWidth: 2 },
          label: { color: '#e2e8f0', fontSize: 11, formatter: '{b}\n{c}' }, data: dist.types }],
      });
      const c3 = chart('chart-status');
      if (c3 && dist.statuses) c3.setOption({
        backgroundColor: 'transparent', tooltip: { trigger: 'axis' },
        grid: { left: 70, right: 30, top: 20, bottom: 30 },
        xAxis: { type: 'value', ...AXIS },
        yAxis: { type: 'category', data: dist.statuses.map(s => s.name), ...AXIS },
        series: [{ type: 'bar', data: dist.statuses.map(s => s.value), barMaxWidth: 20,
          itemStyle: { color: new echarts.graphic.LinearGradient(0, 0, 1, 0,
            [{ offset: 0, color: '#8b5cf6' }, { offset: 1, color: '#22d3ee' }]), borderRadius: [0, 5, 5, 0] },
          label: { show: true, position: 'right', color: '#e2e8f0' } }],
      });
      setTimeout(resizeCharts, 50);
    }

    /* ---------- 列表模块 ---------- */
    async function loadOrders() {
      const d = await aapi('/orders?limit=100');
      tables.value = {
        ...tables.value,
        orders: table(['订单号', '用户', '类型', '状态', '应付', '实付', '创建时间'], d.items, r =>
          `<tr><td>${esc(r.orderNo)}</td><td>${esc(r.user)}</td><td>${esc(r.type)}</td>
           <td><span class="status ${stCls(r.status)}">${esc(r.status)}</span></td>
           <td>¥${r.payable.toFixed(2)}</td><td>${r.paid ? '¥' + r.paid.toFixed(2) : '—'}</td><td>${esc(r.createdAt)}</td></tr>`),
      };
    }
    async function loadUsers() {
      const d = await aapi('/users?limit=100');
      tables.value = {
        ...tables.value,
        users: table(['ID', '昵称', '手机号', '状态', '会员等级', '积分', '注册日期'], d.items, r =>
          `<tr><td>${r.id}</td><td>${esc(r.nickname)}</td><td>${esc(r.phone)}</td>
           <td><span class="status ${stCls(r.status)}">${esc(r.status)}</span></td>
           <td><span class="status ${r.member === '金卡会员' ? 'st-gold' : ''}">${esc(r.member)}</span></td>
           <td>${r.points}</td><td>${esc(r.createdAt)}</td></tr>`),
      };
    }
    async function loadRefunds() {
      const d = await aapi('/refunds?limit=100');
      tables.value = {
        ...tables.value,
        refunds: table(['退款单号', '状态', '申请金额', '核准金额', '申请时间'], d.items, r =>
          `<tr><td>${esc(r.no)}</td><td><span class="status ${stCls(r.status)}">${esc(r.status)}</span></td>
           <td>¥${r.requested.toFixed(2)}</td><td>${r.approved ? '¥' + r.approved.toFixed(2) : '—'}</td><td>${esc(r.requestedAt)}</td></tr>`),
      };
    }
    async function loadGuides() {
      const d = await aapi('/guides?limit=100');
      tables.value = {
        ...tables.value,
        guides: table(['导游', '城市', '等级', '评分', '带团数', '单价/天', '状态'], d.guides, r =>
          `<tr><td>${esc(r.name)}</td><td>${esc(r.city)}</td><td>${esc(r.level)}</td>
           <td style="color:var(--amber)">★ ${r.rating.toFixed(2)}</td><td>${r.services}</td>
           <td>¥${r.price.toFixed(2)}</td><td><span class="status ${stCls(r.status)}">${r.status === 'active' ? '可预约' : esc(r.status)}</span></td></tr>`),
        bookings: table(['预约单号', '城市', '服务日期', '天数', '金额', '状态'], d.bookings, r =>
          `<tr><td>${esc(r.no)}</td><td>${esc(r.city)}</td><td>${esc(r.date)}</td>
           <td>${r.days} 天</td><td>¥${r.amount.toFixed(2)}</td>
           <td><span class="status ${stCls(r.status)}">${esc(r.status)}</span></td></tr>`),
      };
    }

    /* ---------- AI 运营 ---------- */
    async function loadAI() {
      const d = await aapi('/ai-ops');
      if (!d.kb) return;
      const p = d.products || {};
      aiMini.value = [
        { l: '知识库分片', n: d.kb.chunks },
        { l: '覆盖城市', n: (d.kb.cities || []).length },
        { l: '商品总量', n: (p.hotels || 0) + (p.scenic || 0) + (p.flights || 0) + (p.trains || 0) },
        { l: 'AI联动预约', n: d.aiLinkedBookings },
      ];
      aiModel.value = d.model || '—';
      aiCaps.value = d.capabilities || [];
      aiCities.value = d.kb.cities || [];
      const bt = d.kb.byType || {};
      const c = chart('chart-kb');
      if (c) {
        c.setOption({
          backgroundColor: 'transparent', tooltip: { trigger: 'item' },
          legend: { bottom: 0, textStyle: { color: '#94a3b8', fontSize: 10 } },
          series: [{ type: 'pie', radius: '62%', center: ['50%', '43%'],
            itemStyle: { borderColor: '#1a2740', borderWidth: 2 },
            label: { color: '#e2e8f0', fontSize: 10 },
            data: Object.entries(bt).map(([k, v]) => ({ name: k, value: v })) }],
        });
        setTimeout(() => c.resize(), 50);
      }
    }

    /* ---------- 启动 ---------- */
    onMounted(() => {
      if (sessionStorage.getItem(TOKEN_KEY)) enter();
    });

    return {
      logged, lgUser, lgPass, lgErr, lgBusy, who,
      navs, current, pageTitle, todayLine,
      cards, tables, aiMini, aiCaps, aiCities, aiModel,
      fmtNum, doLogin, doLogout, go,
    };
  },
}).mount('#app');
