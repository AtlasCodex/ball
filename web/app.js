// ============================ BALL Web 前端 ============================
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// ============================ 粒子背景 ============================
function initParticles() {
  const cv = $("#bg");
  const ctx = cv.getContext("2d");
  let w, h, parts;
  const COLORS = ["#8b5cf6", "#38bdf8", "#22d3ee", "#f472b6"];

  function resize() {
    w = cv.width = innerWidth;
    h = cv.height = innerHeight;
    const n = Math.min(90, Math.floor((w * h) / 22000));
    parts = Array.from({ length: n }, () => ({
      x: Math.random() * w, y: Math.random() * h,
      vx: (Math.random() - 0.5) * 0.35, vy: (Math.random() - 0.5) * 0.35,
      r: Math.random() * 1.8 + 0.6,
      c: COLORS[(Math.random() * COLORS.length) | 0],
    }));
  }
  resize();
  addEventListener("resize", resize);

  let mx = -999, my = -999;
  addEventListener("mousemove", (e) => { mx = e.clientX; my = e.clientY; });

  function tick() {
    ctx.clearRect(0, 0, w, h);
    for (const p of parts) {
      p.x += p.vx; p.y += p.vy;
      if (p.x < 0 || p.x > w) p.vx *= -1;
      if (p.y < 0 || p.y > h) p.vy *= -1;
      const dx = p.x - mx, dy = p.y - my;
      const d = Math.hypot(dx, dy);
      if (d < 130) { p.x += dx / d * 1.1; p.y += dy / d * 1.1; }
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = p.c; ctx.globalAlpha = 0.65; ctx.fill();
    }
    ctx.globalAlpha = 1;
    for (let i = 0; i < parts.length; i++) {
      for (let j = i + 1; j < parts.length; j++) {
        const a = parts[i], b = parts[j];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        if (d < 120) {
          ctx.beginPath();
          ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
          ctx.strokeStyle = a.c; ctx.globalAlpha = (1 - d / 120) * 0.14;
          ctx.lineWidth = 0.6; ctx.stroke();
        }
      }
    }
    ctx.globalAlpha = 1;
    requestAnimationFrame(tick);
  }
  tick();
}

// ============================ 动效 ============================
function animateView(view) {
  gsap.from(
    $(`#view-${view}`).querySelectorAll(".stat, .card, .model-card, .op-btn, .league-sec, .tabs, .toolbar"),
    { opacity: 0, y: 20, duration: 0.5, stagger: 0.05, ease: "power2.out", clearProps: "all" }
  );
}
function countUp(el, target) {
  const o = { v: 0 };
  gsap.to(o, { v: target, duration: 1.2, ease: "power2.out",
    onUpdate: () => (el.textContent = Math.floor(o.v).toLocaleString()) });
}

// ============================ 总览 ============================
async function loadOverview() {
  const d = await getJSON("/api/overview");
  const c = d.counts;
  const stats = [
    ["联赛", c.leagues, "fb"], ["球队", c.teams, "bk"],
    ["比赛", c.matches, "cyan"], ["预测", c.predictions, "green"],
    ["模型", c.models, "pink"],
  ];
  $("#stat-grid").innerHTML = stats.map(([label, val, ac]) => `
    <div class="stat">
      <div class="accent"></div>
      <div class="label">${label}</div>
      <div class="value" data-v="${val}">0</div>
    </div>`).join("");
  $$("#stat-grid .value").forEach((el) => countUp(el, +el.dataset.v));

  renderLeagueChart(d.league_breakdown);
  renderRecent(d.recent);
}

function renderLeagueChart(rows) {
  const el = $("#chart-leagues");
  if (!rows.length) { el.innerHTML = '<div class="muted" style="padding:30px">暂无数据</div>'; return; }
  const top = rows.slice(0, 12).reverse();
  if (el._chart) el._chart.dispose();
  const chart = echarts.init(el);
  el._chart = chart;
  chart.setOption({
    backgroundColor: "transparent",
    grid: { left: 6, right: 24, top: 10, bottom: 6, containLabel: true },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, backgroundColor: "#0b0f1f", borderColor: "#8b5cf6", textStyle: { color: "#e8ebff" } },
    xAxis: { type: "value", splitLine: { lineStyle: { color: "rgba(255,255,255,.05)" } }, axisLabel: { color: "#6b7299" } },
    yAxis: { type: "category", data: top.map((r) => r.name), axisLine: { lineStyle: { color: "rgba(255,255,255,.1)" } }, axisLabel: { color: "#8b93b8", fontSize: 11 } },
    series: [{
      type: "bar", data: top.map((r) => r.matches), barWidth: "58%",
      itemStyle: { borderRadius: [0, 6, 6, 0],
        color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
          { offset: 0, color: "#8b5cf6" }, { offset: 1, color: "#22d3ee" }]) },
    }],
  });
  setTimeout(() => chart.resize(), 60);
}

function renderRecent(rows) {
  const el = $("#recent-list");
  if (!rows.length) { el.innerHTML = '<div class="muted" style="padding:20px">暂无预测记录</div>'; return; }
  const labelCN = { home: "主胜", draw: "平局", away: "客胜" };
  el.innerHTML = rows.map((r) => `
    <div class="recent-item">
      <div>
        <div class="teams">${r.home} <span class="muted">vs</span> ${r.away}</div>
        <div class="meta">${r.league_code} · ${(r.start_time || "").slice(0, 16).replace("T", " ")}</div>
      </div>
      <span class="tag ${r.label}">${labelCN[r.label] || r.label} · ${(r.confidence * 100).toFixed(0)}%</span>
    </div>`).join("");
}

// ============================ 数据浏览 ============================
let dataTab = "leagues";
let leagueOptions = [];

async function initDataControls() {
  const leagues = await getJSON("/api/leagues");
  leagueOptions = leagues;
  $("#data-league").innerHTML =
    '<option value="">全部联赛</option>' +
    leagues.map((l) => `<option value="${l.code}">${l.name} (${l.code})</option>`).join("");
}

async function loadData() {
  const league = $("#data-league").value;
  const search = $("#data-search").value.trim();
  let rows, html;
  if (dataTab === "leagues") {
    rows = leagueOptions.length ? leagueOptions : await getJSON("/api/leagues");
    html = `<table><thead><tr><th>联赛</th><th>Code</th><th>类型</th><th>赛季</th><th>球队</th><th>比赛</th></tr></thead><tbody>` +
      rows.map((l) => `<tr>
        <td class="cell-strong">${l.name}</td><td class="cell-num">${l.code}</td>
        <td><span class="pill ${l.sport}">${l.sport === "football" ? "足球" : "篮球"}</span></td>
        <td>${l.season || "—"}</td><td class="cell-num">${l.teams}</td><td class="cell-num">${l.matches}</td>
      </tr>`).join("") + "</tbody></table>";
  } else if (dataTab === "teams") {
    rows = await getJSON(`/api/teams?limit=300${league ? `&league=${league}` : ""}${search ? `&search=${encodeURIComponent(search)}` : ""}`);
    html = `<table><thead><tr><th>球队</th><th>简称</th><th>联赛</th><th>胜</th><th>平</th><th>负</th><th>积分</th><th>进/失</th></tr></thead><tbody>` +
      rows.map((t) => `<tr>
        <td class="cell-strong">${t.name}</td><td>${t.short_name || t.abbreviation || "—"}</td>
        <td class="cell-num">${t.league_code}</td>
        <td class="cell-num" style="color:#7dd3fc">${t.wins}</td><td class="cell-num">${t.draws}</td><td class="cell-num" style="color:#f9a8d4">${t.losses}</td>
        <td class="cell-num" style="color:#fcd34d">${t.points}</td>
        <td class="cell-num">${t.goals_for}/${t.goals_against}</td>
      </tr>`).join("") + "</tbody></table>";
  } else {
    rows = await getJSON(`/api/matches?limit=150${league ? `&league=${league}` : ""}${search ? `&search=${encodeURIComponent(search)}` : ""}`);
    html = `<table><thead><tr><th>时间</th><th>联赛</th><th>主队</th><th>比分</th><th>客队</th><th>状态</th></tr></thead><tbody>` +
      rows.map((m) => `<tr>
        <td class="cell-num">${(m.start_time || "").slice(0, 16).replace("T", " ")}</td>
        <td class="cell-num">${m.league_code}</td>
        <td class="cell-strong">${m.home.name || "?"}</td>
        <td class="cell-num" style="color:#fcd34d">${m.home_score ?? "–"} : ${m.away_score ?? "–"}</td>
        <td class="cell-strong">${m.away.name || "?"}</td>
        <td><span class="pill ${m.status}">${m.status}</span></td>
      </tr>`).join("") + "</tbody></table>";
  }
  $("#data-table").innerHTML = html;
}

// ============================ 模型 ============================
async function loadModels() {
  const rows = await getJSON("/api/models");
  $("#model-grid").innerHTML = rows.map((m) => {
    const pct = Math.min(100, Math.round((m.samples / 50) * 100)) || (m.exists ? 100 : 0);
    const ringColor = m.ready ? "#34d399" : m.exists ? "#fbbf24" : "#fb7185";
    const status = m.exists ? (m.ready ? `<span class="ready-yes">已就绪</span>` : `<span class="ready-no">样本不足</span>`) : `<span class="ready-no">未训练</span>`;
    return `<div class="model-card">
      <div class="mc-head">
        <div><div class="mc-name">${m.name}</div><div class="mc-code">${m.code} · ${m.sport}</div></div>
        <div class="ring" style="background:conic-gradient(${ringColor} ${pct}%, rgba(255,255,255,.08) 0)"><span>${pct}%</span></div>
      </div>
      <div class="mc-rows">
        <div class="mc-row"><span>状态</span>${status}</div>
        <div class="mc-row"><span>历史样本</span><span>${m.samples} 场</span></div>
        <div class="mc-row"><span>类别数</span><span>${m.meta.num_classes ?? "—"}</span></div>
        <div class="mc-row"><span>训练时间</span><span>${(m.meta.trained_at || "—").slice(0, 16).replace("T", " ")}</span></div>
      </div>
      <button class="mini-btn" data-train="${m.code}" data-sport="${m.sport}" ${m.samples < 1 ? "disabled" : ""}>训练 / 重训</button>
    </div>`;
  }).join("");
  lucide.createIcons();
}

// ============================ 预测中心 ============================
async function loadPredictions() {
  const data = await getJSON("/api/predictions");
  const wrap = $("#predictions-wrap");
  const codes = Object.keys(data);
  if (!codes.length) { wrap.innerHTML = '<div class="card muted" style="padding:30px">暂无预测记录，请到「操作台」运行预测。</div>'; return; }
  wrap.innerHTML = codes.map((code) => {
    const list = data[code];
    const head = `<div class="league-sec-head"><span class="ln">${list[0]?.league_name || code}</span><span class="lc">${code}</span><span class="count">${list.length} 场</span></div>`;
    if (!list.length) return `<div class="league-sec">${head}<div class="card muted" style="padding:18px">该联赛暂无已存储的预测。</div></div>`;
    const cards = list.map((p) => {
      const labelCN = { home: "主胜", draw: "平局", away: "客胜" };
      const cls = { home: "home", draw: "draw", away: "away" }[p.label] || "draw";
      return `<div class="pred-card">
        <div class="pc-top">
          <div><div class="pc-teams">${p.home} <span class="muted">vs</span> ${p.away}</div>
            <div class="pc-time">${(p.start_time || "").slice(0, 16).replace("T", " ")}</div></div>
          <span class="pc-label ${cls}">${labelCN[p.label] || p.label}</span>
        </div>
        <div class="bars">
          <div class="bar-row"><span class="bl">主胜</span><div class="bar-track"><div class="bar-fill home" style="width:${(p.prob_home * 100).toFixed(0)}%"></div></div><span class="bv">${(p.prob_home * 100).toFixed(0)}%</span></div>
          <div class="bar-row"><span class="bl">平局</span><div class="bar-track"><div class="bar-fill draw" style="width:${(p.prob_draw * 100).toFixed(0)}%"></div></div><span class="bv">${(p.prob_draw * 100).toFixed(0)}%</span></div>
          <div class="bar-row"><span class="bl">客胜</span><div class="bar-track"><div class="bar-fill away" style="width:${(p.prob_away * 100).toFixed(0)}%"></div></div><span class="bv">${(p.prob_away * 100).toFixed(0)}%</span></div>
        </div>
        <div class="conf">置信度 ${(p.confidence * 100).toFixed(0)}%</div>
      </div>`;
    }).join("");
    return `<div class="league-sec">${head}${cards}</div>`;
  }).join("");
  requestAnimationFrame(() => $$("#predictions-wrap .bar-fill").forEach((b) => { const w = b.style.width; b.style.width = "0"; requestAnimationFrame(() => (b.style.width = w)); }));
}

// ============================ 操作台 ============================
let terminalBusy = false;

function buildOps() {
  const ops = [
    { id: "init", icon: "zap", t: "初始化数据库", d: "建表", league: false },
    { id: "crawl", icon: "download", t: "抓取赛程", d: "crawl", league: true },
    { id: "train", icon: "brain", t: "训练模型", d: "train", league: true },
    { id: "predict", icon: "sparkles", t: "生成预测", d: "predict", league: true },
    { id: "run", icon: "play", t: "完整流程", d: "run", league: true },
    { id: "run-all", icon: "layers", t: "全部联赛", d: "run-all", league: false },
    { id: "sporttery", icon: "ticket", t: "体彩竞猜", d: "sporttery", league: false },
    { id: "notify", icon: "mail", t: "推送邮件", d: "notify", league: false },
  ];
  const grid = $("#ops-grid");
  grid.innerHTML = ops.map((o) => `
    <button class="op-btn" data-op="${o.id}" data-league="${o.league}">
      <i data-lucide="${o.icon}"></i>
      <span class="ot">${o.t}</span>
      <span class="od">${o.d}</span>
    </button>`).join("");
  lucide.createIcons();
  $$("#ops-grid .op-btn").forEach((btn) =>
    btn.addEventListener("click", () => onOp(btn)));
}

async function onOp(btn) {
  if (terminalBusy) return;
  const action = btn.dataset.op;
  const params = {};
  const sel = $("#op-league");
  if (btn.dataset.league === "true" && sel && sel.value) {
    params.league = sel.value;
  }
  if (action === "run-all" || action === "notify") {
    params.sport = ($("#op-sport")?.value) || "football";
  }
  if (action === "run-all") { params.train = true; params.notify = true; }
  if (action === "sporttery") { params.sync = true; params.train_missing = true; params.notify = true; }

  appendLog(`▶ 触发操作：${action}  ${JSON.stringify(params)}`, "info");
  terminalBusy = true;
  $$("#ops-grid .op-btn").forEach((b) => b.classList.add("busy"));
  try {
    const { task_id } = await fetch("/api/op", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, params }),
    }).then((r) => r.json());
    streamTask(task_id);
  } catch (e) {
    appendLog("✗ 请求失败：" + e.message, "err");
    terminalBusy = false;
    $$("#ops-grid .op-btn").forEach((b) => b.classList.remove("busy"));
  }
}

function appendLog(line, kind) {
  const term = $("#terminal");
  const div = document.createElement("div");
  div.className = "t-new " + (kind === "err" ? "t-err" : kind === "warn" ? "t-warn" : "t-info");
  div.textContent = line;
  term.appendChild(div);
  term.scrollTop = term.scrollHeight;
}

function streamTask(tid) {
  const es = new EventSource("/api/op/" + tid + "/stream");
  es.onmessage = (e) => {
    const d = JSON.parse(e.data);
    if (d.type === "log") {
      const k = /ERROR|Error|失败|异常/.test(d.line) ? "err" : /WARN|警告|跳过/.test(d.line) ? "warn" : "info";
      appendLog(d.line, k);
    } else if (d.type === "done") {
      appendLog(`— 操作结束：状态 ${d.status} —`, d.status === "error" ? "err" : "info");
      es.close();
      terminalBusy = false;
      $$("#ops-grid .op-btn").forEach((b) => b.classList.remove("busy"));
      setTimeout(() => { loadOverview(); loadModels(); loadPredictions(); }, 600);
    }
  };
  es.onerror = () => { es.close(); terminalBusy = false; $$("#ops-grid .op-btn").forEach((b) => b.classList.remove("busy")); };
}

function buildOpControls() {
  const head = $("#view-ops .card .card-head");
  const bar = document.createElement("div");
  bar.style.cssText = "display:flex;gap:10px;margin:0 0 14px;";
  bar.innerHTML = `
    <select id="op-league" class="select" style="min-width:200px"><option value="">默认联赛(eng.1)</option>${leagueOptions.map((l) => `<option value="${l.code}">${l.name}</option>`).join("")}</select>
    <select id="op-sport" class="select" style="min-width:120px"><option value="football">足球</option><option value="nba">篮球</option></select>`;
  head.insertAdjacentElement("afterend", bar);
}

// ============================ 路由 ============================
const TITLES = {
  overview: ["总览", "实时数据可视化与模型控制台"],
  database: ["数据浏览", "联赛 / 球队 / 比赛全景查询"],
  models: ["模型 · 训练", "各联赛独立模型状态与训练触发"],
  predictions: ["预测中心", "多联赛概率可视化与置信度"],
  ops: ["操作台", "抓取 / 训练 / 预测 / 推送 一键触发"],
};

function switchView(view) {
  $$(".nav-item").forEach((n) => n.classList.toggle("active", n.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
  $("#page-title").textContent = TITLES[view][0];
  $("#page-sub").textContent = TITLES[view][1];
  animateView(view);
  if (view === "overview") loadOverview();
  else if (view === "database") loadData();
  else if (view === "models") loadModels();
  else if (view === "predictions") loadPredictions();
  else if (view === "ops") { /* 已构建 */ }
}

// ============================ 启动 ============================
async function boot() {
  initParticles();
  lucide.createIcons();
  $$(".nav-item").forEach((n) => n.addEventListener("click", () => switchView(n.dataset.view)));
  $$("#data-tabs .tab").forEach((t) =>
    t.addEventListener("click", () => {
      dataTab = t.dataset.tab;
      $$("#data-tabs .tab").forEach((x) => x.classList.toggle("active", x === t));
      loadData();
    }));
  $("#data-search").addEventListener("input", debounce(loadData, 300));
  $("#data-league").addEventListener("change", loadData);
  $("#refresh-btn").addEventListener("click", () => {
    const active = $(".view.active").id.replace("view-", "");
    if (active === "database") loadData();
    else if (active === "models") loadModels();
    else switchView(active);
  });
  // 模型训练按钮（事件委托）
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-train]");
    if (b) {
      appendLogToModels(b);
    }
  });

  buildOps();
  await initDataControls();
  buildOpControls();
  switchView("overview");
}

function debounce(fn, ms) {
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

async function appendLogToModels(btn) {
  // 跳到操作台并触发训练
  switchView("ops");
  const sel = $("#op-league"); if (sel) sel.value = btn.dataset.train;
  const b = $(`#ops-grid .op-btn[data-op="train"]`);
  if (b) onOp(b);
}

boot();
