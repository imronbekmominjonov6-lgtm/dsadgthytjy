const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const enc = encodeURIComponent;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => Math.round(Number(n)).toLocaleString("ru-RU");
const fmtQty = (q) => Number(q).toFixed(3).replace(/\.?0+$/, "");
const round3 = (n) => Math.round(n * 1000) / 1000;

// ---------- Til ----------
let lang = localStorage.getItem("lang") || "uz";
const t = (k, ...a) => {
  let s = (I18N[lang] && I18N[lang][k]) || I18N.uz[k] || k;
  a.forEach((v, i) => (s = s.replace(`{${i}}`, v)));
  return s;
};
const unitName = (u) => (u === "kg" ? t("unit_kg") : t("unit_dona"));

function applyI18n() {
  $$("[data-i18n]").forEach((e) => (e.textContent = t(e.dataset.i18n)));
  $$("[data-i18n-ph]").forEach((e) => (e.placeholder = t(e.dataset.i18nPh)));
  $$(".lang-sel").forEach((s) => (s.value = lang));
  document.documentElement.lang = lang;
  document.title = t("app_title");
  $("#p-unit").innerHTML = `<option value="dona">${t("unit_dona")}</option><option value="kg">${t("unit_kg")}</option>`;
  $("#u-role").innerHTML = `<option value="cashier">${t("role_cashier")}</option><option value="admin">${t("role_admin")}</option>`;
}

$$(".lang-sel").forEach((s) =>
  s.addEventListener("change", () => {
    lang = s.value;
    localStorage.setItem("lang", lang);
    applyI18n();
    if (me) {
      fillBranchSelects();
      fillCatSelect();
      renderCart();
      openTab(currentTab);
    }
  })
);

// ---------- Umumiy ----------
let me = null, branches = [], cats = [], branchId = null, currentTab = "kassa";

async function api(url, opt = {}) {
  const r = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opt,
    body: opt.body ? JSON.stringify(opt.body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (r.status === 401 && !url.startsWith("/api/login")) {
    me = null;
    show("login");
    throw new Error(t("login_needed"));
  }
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : t("error"));
  return data;
}

function toast(msg, kind = "") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "show " + kind;
  clearTimeout(toast.h);
  toast.h = setTimeout(() => (el.className = ""), 2600);
}
const fail = (e) => toast(e.message, "err");

function show(view) {
  ["setup", "login", "app"].forEach((v) => $("#view-" + v).classList.toggle("hidden", v !== view));
}

function askNumber(title, def = "") {
  return new Promise((resolve) => {
    const m = $("#modal"), inp = $("#modal-input");
    $("#modal-title").textContent = title;
    inp.value = def;
    m.classList.remove("hidden");
    inp.focus();
    inp.select();
    const done = (v) => {
      m.classList.add("hidden");
      $("#modal-ok").onclick = $("#modal-cancel").onclick = inp.onkeydown = null;
      resolve(v);
    };
    $("#modal-ok").onclick = () => {
      const v = parseFloat(String(inp.value).replace(",", "."));
      done(v > 0 ? v : null);
    };
    $("#modal-cancel").onclick = () => done(null);
    inp.onkeydown = (e) => {
      if (e.key === "Enter") $("#modal-ok").click();
      if (e.key === "Escape") done(null);
    };
  });
}

function beep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator();
    o.frequency.value = 880;
    o.connect(ctx.destination);
    o.start();
    setTimeout(() => { o.stop(); ctx.close(); }, 90);
  } catch {}
}

// ---------- Kirish ----------
$("#setup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/setup", {
      method: "POST",
      body: { branch_name: $("#s-branch").value, full_name: $("#s-fullname").value, username: $("#s-user").value, password: $("#s-pass").value },
    });
    me = await api("/api/me");
    await enterApp();
  } catch (err) { fail(err); }
});

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    me = await api("/api/login", { method: "POST", body: { username: $("#l-user").value, password: $("#l-pass").value } });
    $("#l-pass").value = "";
    await enterApp();
  } catch (err) { fail(err); }
});

$("#logout").onclick = async () => {
  try { await api("/api/logout", { method: "POST" }); } catch {}
  me = null;
  cart = [];
  show("login");
};

async function boot() {
  applyI18n();
  const st = await fetch("/api/state").then((r) => r.json());
  if (st.setup_needed) return show("setup");
  try { me = await api("/api/me"); } catch { return show("login"); }
  await enterApp();
}

async function enterApp() {
  show("app");
  $("#who").textContent = me.full_name + (me.branch ? ` · ${me.branch}` : "");
  $$(".admin-only").forEach((e) => e.classList.toggle("hidden", me.role !== "admin"));
  branches = await api("/api/branches");
  branchId = me.role === "admin"
    ? (branches.find((b) => b.id === Number(localStorage.getItem("branch")) && b.active) || branches.find((b) => b.active) || {}).id || null
    : me.branch_id;
  cats = await api("/api/categories");
  fillBranchSelects();
  fillCatSelect();
  openTab("kassa");
  if (me.role === "admin") {
    try {
      const low = await api(`/api/stock/low?branch_id=${branchId}`);
      if (low.length) toast(t("low_n", low.length), "warn");
    } catch {}
  }
}

function fillBranchSelects() {
  const active = branches.filter((b) => b.active);
  $("#branch").innerHTML = active.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join("");
  $("#branch").value = branchId;
  $("#r-branch").innerHTML = `<option value="0">${t("all_branches")}</option>` + active.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join("");
  $("#u-branch").innerHTML = active.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join("");
}

$("#branch").addEventListener("change", () => {
  branchId = +$("#branch").value;
  localStorage.setItem("branch", branchId);
  cart = [];
  renderCart();
  openTab(currentTab);
});

// ---------- Tablar ----------
$$(".tab").forEach((b) => b.addEventListener("click", () => openTab(b.dataset.tab)));

function openTab(name) {
  currentTab = name;
  $$(".tab").forEach((x) => x.classList.toggle("active", x.dataset.tab === name));
  $$(".panel").forEach((p) => p.classList.add("hidden"));
  $("#tab-" + name).classList.remove("hidden");
  const loaders = { kassa: loadKassa, products: loadProducts, intake: loadIntake, report: loadReport, settings: loadSettings };
  loaders[name]().catch(fail);
}

// ---------- Kassa ----------
let cart = [], method = "cash", activeCat = 0, searchTimer;

function fillCatSelect() {
  $("#p-cat").innerHTML = `<option value="">—</option>` + cats.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
}

function renderCats() {
  $("#cats").innerHTML =
    `<button class="chip ${activeCat ? "" : "on"}" data-c="0">${t("all")}</button>` +
    cats.map((c) => `<button class="chip ${activeCat === c.id ? "on" : ""}" data-c="${c.id}">${esc(c.name)}</button>`).join("");
}
$("#cats").addEventListener("click", (e) => {
  const b = e.target.closest(".chip");
  if (!b) return;
  activeCat = +b.dataset.c;
  renderCats();
  runSearch().catch(fail);
});

const prodBtn = (p) => `<button class="item" data-id="${p.id}"><b>${esc(p.name)}</b>
  <span class="${p.stock <= 0 ? "low" : ""}">${t("stock")}: ${fmtQty(p.stock)} ${unitName(p.unit)}</span>
  <span class="pr">${fmt(p.price)} ${t("som")}${p.unit === "kg" ? " / " + t("unit_kg") : ""}</span></button>`;

async function loadKassa() {
  renderCats();
  renderCart();
  setMethod(method);
  await Promise.all([loadQuick(), runSearch()]);
  $("#scan").focus();
}

async function loadQuick() {
  const q = await api(`/api/products/quick?branch_id=${branchId}`);
  const all = [...q.manual, ...q.auto];
  const box = $("#quick");
  box._list = all;
  box.innerHTML = all.map(prodBtn).join("") || `<small class="mute">${t("no_quick")}</small>`;
}

async function runSearch() {
  const q = $("#scan").value.trim();
  const box = $("#results");
  if (!q && !activeCat) {
    box.classList.add("hidden");
    $("#quick-wrap").classList.remove("hidden");
    return;
  }
  const list = await api(`/api/products?q=${enc(q)}&category_id=${activeCat || ""}&branch_id=${branchId}`);
  box._list = list;
  box.innerHTML = list.map(prodBtn).join("") || `<small class="mute">${t("not_found")}</small>`;
  box.classList.remove("hidden");
  $("#quick-wrap").classList.add("hidden");
}

$("#scan").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => runSearch().catch(fail), 150);
});

$("#scan").addEventListener("keydown", async (e) => {
  if (e.key !== "Enter") return;
  const v = e.target.value.trim();
  if (!v) return;
  try {
    const p = await api(`/api/products/barcode/${enc(v)}?branch_id=${branchId}`);
    e.target.value = "";
    await addToCart(p);
    runSearch().catch(fail);
  } catch {
    const list = $("#results")._list || [];
    if (list.length === 1) {
      e.target.value = "";
      await addToCart(list[0]);
      runSearch().catch(fail);
    } else toast(t("not_found"), "err");
  }
  $("#scan").focus();
});

[$("#quick"), $("#results")].forEach((box) =>
  box.addEventListener("click", async (e) => {
    const b = e.target.closest(".item");
    if (!b) return;
    await addToCart(box._list.find((p) => p.id === +b.dataset.id));
    $("#scan").focus();
  })
);

async function addToCart(p, qty) {
  if (qty === undefined) {
    if (p.unit === "kg") {
      qty = await askNumber(`${p.name} — ${t("weight_kg")}`);
      if (!qty) return;
    } else qty = 1;
  }
  let line = cart.find((i) => i.id === p.id);
  if (line) line.qty = round3(line.qty + qty);
  else cart.push((line = { id: p.id, name: p.name, price: p.price, unit: p.unit, stock: p.stock, qty: round3(qty) }));
  if (line.qty > line.stock) toast(t("low_warn", fmtQty(line.stock)), "warn");
  renderCart();
}

const lineSum = (i) => Math.round(i.price * i.qty);
const cartTotal = () => cart.reduce((s, i) => s + lineSum(i), 0);

function renderCart() {
  $("#cart-items").innerHTML = cart.length
    ? cart
        .map((i, k) => `<div class="line">
      <div class="nm">${esc(i.name)}</div><div class="sm">${fmt(lineSum(i))}</div>
      <div class="qty"><button data-k="${k}" data-a="dec">−</button>
        <span class="qv" data-k="${k}" data-a="edit">${fmtQty(i.qty)} ${unitName(i.unit)}</span>
        <button data-k="${k}" data-a="inc">+</button><small>× ${fmt(i.price)}</small></div>
      <button class="ghost danger x" data-k="${k}" data-a="del">✕</button></div>`)
        .join("")
    : `<small class="mute">${t("cart_empty")}</small>`;
  updateTotals();
}

$("#cart-items").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-a]");
  if (!b) return;
  const k = +b.dataset.k, i = cart[k], a = b.dataset.a;
  if (a === "del") cart.splice(k, 1);
  else if (a === "edit") {
    const v = await askNumber(i.name, fmtQty(i.qty));
    if (!v) return;
    if (i.unit === "dona" && v !== Math.floor(v)) return toast(t("int_only"), "err");
    i.qty = round3(v);
  } else {
    const step = i.unit === "kg" ? 0.1 : 1;
    const q = round3(i.qty + (a === "inc" ? step : -step));
    if (q <= 0) cart.splice(k, 1);
    else i.qty = q;
  }
  renderCart();
});

function updateTotals() {
  const tot = cartTotal();
  $("#cart-total").textContent = fmt(tot);
  const paid = +$("#paid").value || 0;
  $("#change").textContent = method === "cash" && paid ? (paid >= tot ? t("change_n", fmt(paid - tot)) : t("short_n", fmt(tot - paid))) : "";
}
$("#paid").addEventListener("input", updateTotals);

function setMethod(m) {
  method = m;
  $("#m-cash").classList.toggle("on", m === "cash");
  $("#m-card").classList.toggle("on", m === "card");
  $("#paid").classList.toggle("hidden", m !== "cash");
  updateTotals();
}
$("#m-cash").onclick = () => setMethod("cash");
$("#m-card").onclick = () => setMethod("card");

async function sell() {
  if (!cart.length) return toast(t("cart_empty"), "err");
  const tot = cartTotal();
  const paid = method === "cash" ? +$("#paid").value || tot : tot;
  try {
    const sale = await api("/api/sales", {
      method: "POST",
      body: { items: cart.map((i) => ({ product_id: i.id, qty: i.qty })), method, paid, branch_id: branchId },
    });
    toast(t("sold_n", sale.id));
    if (sale.negative && sale.negative.length) setTimeout(() => toast(t("negative_n", sale.negative.join(", ")), "warn"), 1800);
    cart = [];
    $("#paid").value = "";
    renderCart();
    printReceipt(sale);
    loadQuick().catch(() => {});
    runSearch().catch(() => {});
  } catch (err) { fail(err); }
}
$("#sell").onclick = sell;
$("#clear").onclick = () => { cart = []; $("#paid").value = ""; renderCart(); };
document.addEventListener("keydown", (e) => {
  if (e.key === "F2" && me && currentTab === "kassa") { e.preventDefault(); sell(); }
});

async function printReceipt(s) {
  try {
    await api(`/api/sales/${s.id}/print`, { method: "POST" });
  } catch (err) {
    toast(err.message + " — " + t("browser_print"), "err");
    browserPrint(s);
  }
}

function browserPrint(s) {
  $("#receipt").innerHTML = `<div class="c"><b>${esc(s.branch || "")}</b><br>${t("receipt_n", s.id)}<br>${esc(s.created)}<br>${esc(s.cashier || "")}</div><hr>
    ${s.items.map((i) => `<div>${esc(i.name)}</div><div class="ln"><span>${fmtQty(i.qty)} ${unitName(i.unit)} × ${fmt(i.price)}</span><span>${fmt(i.qty * i.price)}</span></div>`).join("")}
    <hr><div class="ln"><b>${t("total")}</b><b>${fmt(s.total)}</b></div>
    <div class="ln"><span>${s.method === "cash" ? t("cash") : t("card")}</span><span>${fmt(s.paid)}</span></div>
    ${s.change ? `<div class="ln"><span>${t("change")}</span><span>${fmt(s.change)}</span></div>` : ""}`;
  window.print();
}

// ---------- Kamera orqali skanerlash ----------
let camStream = null, camTimer = null, lastCode = "", lastAt = 0;

async function openCamera() {
  if (!window.isSecureContext) return toast(t("need_https"), "err");
  if (!("BarcodeDetector" in window)) return toast(t("no_detector"), "err");
  try {
    camStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
  } catch { return toast(t("cam_denied"), "err"); }
  const v = $("#cam-video");
  v.srcObject = camStream;
  await v.play();
  $("#cam-modal").classList.remove("hidden");
  const det = new BarcodeDetector({ formats: ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "itf"] });
  camTimer = setInterval(async () => {
    try {
      const codes = await det.detect(v);
      if (!codes.length) return;
      const code = codes[0].rawValue, now = Date.now();
      if (code === lastCode && now - lastAt < 2000) return;
      lastCode = code;
      lastAt = now;
      const p = await api(`/api/products/barcode/${enc(code)}?branch_id=${branchId}`).catch(() => null);
      if (!p) return toast(`${code}: ${t("not_found")}`, "err");
      beep();
      await addToCart(p);
    } catch {}
  }, 250);
}

function closeCamera() {
  clearInterval(camTimer);
  if (camStream) camStream.getTracks().forEach((tr) => tr.stop());
  camStream = null;
  $("#cam-modal").classList.add("hidden");
}
$("#cam-btn").onclick = openCamera;
$("#cam-close").onclick = closeCamera;

// ---------- Mahsulotlar ----------
let products = [];

async function loadProducts() {
  const q = $("#p-search").value.trim();
  products = await api(`/api/products?q=${enc(q)}&branch_id=${branchId}`);
  const admin = me.role === "admin";
  $("#p-body").innerHTML =
    products
      .map((p) => {
        const low = p.stock < 0 || (p.min_stock > 0 && p.stock <= p.min_stock);
        return `<tr><td>${esc(p.name)}${p.quick ? " ⭐" : ""}</td><td>${esc(p.category || "—")}</td><td>${unitName(p.unit)}</td>
        <td class="r">${fmt(p.price)}</td>${admin ? `<td class="r">${fmt(p.cost)}</td>` : ""}
        <td class="r ${low ? "low" : ""}">${fmtQty(p.stock)}</td><td>${esc(p.barcode || "—")}</td>
        <td class="r">${admin ? `<button data-edit="${p.id}">${t("edit")}</button><button class="danger" data-del="${p.id}">${t("delete")}</button>` : ""}</td></tr>`;
      })
      .join("") || `<tr><td colspan="8">${t("empty")}</td></tr>`;
}
$("#p-search").addEventListener("input", () => loadProducts().catch(fail));

$("#p-body").addEventListener("click", async (e) => {
  const ed = e.target.closest("[data-edit]"), del = e.target.closest("[data-del]");
  if (ed) {
    const p = products.find((x) => x.id === +ed.dataset.edit);
    $("#p-id").value = p.id;
    $("#p-name").value = p.name;
    $("#p-barcode").value = p.barcode || "";
    $("#p-cat").value = p.category_id || "";
    $("#p-unit").value = p.unit;
    $("#p-price").value = p.price;
    $("#p-cost").value = p.cost;
    $("#p-min").value = p.min_stock;
    $("#p-quick").checked = !!p.quick;
    $("#p-stock-wrap").classList.add("hidden");
    $("#p-name").focus();
  }
  if (del && confirm(t("confirm_delete"))) {
    try { await api("/api/products/" + del.dataset.del, { method: "DELETE" }); loadProducts(); } catch (err) { fail(err); }
  }
});

function resetProductForm() {
  $("#pform").reset();
  $("#p-id").value = "";
  $("#p-stock-wrap").classList.remove("hidden");
}
$("#p-reset").onclick = resetProductForm;

$("#pform").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = $("#p-id").value;
  const body = {
    name: $("#p-name").value, barcode: $("#p-barcode").value || null, category_id: +$("#p-cat").value || null,
    unit: $("#p-unit").value, price: +$("#p-price").value || 0, cost: +$("#p-cost").value || 0,
    min_stock: +$("#p-min").value || 0, quick: $("#p-quick").checked, stock: +$("#p-stock").value || 0,
  };
  try {
    await api(id ? `/api/products/${id}` : `/api/products?branch_id=${branchId}`, { method: id ? "PUT" : "POST", body });
    toast(t("saved"));
    resetProductForm();
    loadProducts();
  } catch (err) { fail(err); }
});

// ---------- Kirim ----------
let lines = [], iTimer;

async function loadIntake() {
  renderLines();
  await Promise.all([loadLow(), loadRecentIntakes()]);
  $("#i-scan").focus();
}

async function loadLow() {
  const low = await api(`/api/stock/low?branch_id=${branchId}`);
  $("#low-box").classList.toggle("hidden", !low.length);
  $("#low-list").innerHTML = low
    .map((p) => `<div class="lrow"><span>${esc(p.name)}</span><b class="low">${fmtQty(p.stock)} ${unitName(p.unit)}</b><small class="mute">min ${fmtQty(p.min_stock)}</small></div>`)
    .join("");
}

async function loadRecentIntakes() {
  const rows = await api(`/api/intakes?branch_id=${branchId}`);
  $("#i-recent").innerHTML =
    rows.map((r) => `<tr><td>${r.id}</td><td>${esc(r.created)}</td><td>${esc(r.user)}</td><td>${r.lines}</td><td class="r">${fmt(r.total)}</td><td>${esc(r.note)}</td></tr>`).join("") ||
    `<tr><td colspan="6">${t("empty")}</td></tr>`;
}

function addLine(p) {
  const ex = lines.find((l) => l.id === p.id);
  if (ex) ex.qty = round3(ex.qty + 1);
  else lines.push({ id: p.id, name: p.name, unit: p.unit, qty: 1, cost: p.cost || 0 });
  renderLines();
}

function renderLines() {
  $("#i-body").innerHTML =
    lines
      .map((l, k) => `<tr><td>${esc(l.name)}</td><td>${unitName(l.unit)}</td>
      <td><input type="number" min="0" step="${l.unit === "kg" ? "any" : "1"}" value="${l.qty}" data-k="${k}" data-f="qty"></td>
      <td><input type="number" min="0" step="1" value="${l.cost}" data-k="${k}" data-f="cost"></td>
      <td class="r" id="ls-${k}">${fmt(l.qty * l.cost)}</td><td><button class="danger" data-del="${k}">✕</button></td></tr>`)
      .join("") || `<tr><td colspan="6">${t("empty")}</td></tr>`;
  updateIntakeTotal();
}

function updateIntakeTotal() {
  $("#i-total").textContent = t("total") + ": " + fmt(lines.reduce((s, l) => s + l.qty * l.cost, 0));
}

$("#i-body").addEventListener("input", (e) => {
  const inp = e.target.closest("input[data-k]");
  if (!inp) return;
  const l = lines[+inp.dataset.k];
  l[inp.dataset.f] = +inp.value || 0;
  const cell = $("#ls-" + inp.dataset.k);
  if (cell) cell.textContent = fmt(l.qty * l.cost);
  updateIntakeTotal();
});
$("#i-body").addEventListener("click", (e) => {
  const d = e.target.closest("[data-del]");
  if (d) { lines.splice(+d.dataset.del, 1); renderLines(); }
});

$("#i-scan").addEventListener("input", () => {
  clearTimeout(iTimer);
  iTimer = setTimeout(async () => {
    const q = $("#i-scan").value.trim();
    const box = $("#i-results");
    if (!q) { box.innerHTML = ""; return; }
    try {
      const list = await api(`/api/products?q=${enc(q)}&branch_id=${branchId}`);
      box._list = list;
      box.innerHTML = list.slice(0, 12).map(prodBtn).join("");
    } catch (err) { fail(err); }
  }, 150);
});
$("#i-scan").addEventListener("keydown", async (e) => {
  if (e.key !== "Enter") return;
  const v = e.target.value.trim();
  if (!v) return;
  try {
    addLine(await api(`/api/products/barcode/${enc(v)}?branch_id=${branchId}`));
    e.target.value = "";
    $("#i-results").innerHTML = "";
  } catch {
    const list = $("#i-results")._list || [];
    if (list.length === 1) { addLine(list[0]); e.target.value = ""; $("#i-results").innerHTML = ""; }
    else toast(t("not_found"), "err");
  }
});
$("#i-results").addEventListener("click", (e) => {
  const b = e.target.closest(".item");
  if (!b) return;
  addLine($("#i-results")._list.find((p) => p.id === +b.dataset.id));
  $("#i-scan").value = "";
  $("#i-results").innerHTML = "";
  $("#i-scan").focus();
});

$("#i-save").onclick = async () => {
  const items = lines.filter((l) => l.qty > 0).map((l) => ({ product_id: l.id, qty: l.qty, cost: Math.round(l.cost) }));
  if (!items.length) return toast(t("empty"), "err");
  try {
    await api("/api/intakes", { method: "POST", body: { items, note: $("#i-note").value, branch_id: branchId } });
    toast(t("saved"));
    lines = [];
    $("#i-note").value = "";
    loadIntake();
  } catch (err) { fail(err); }
};

// ---------- Hisobot ----------
const today = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 10); };

async function loadReport() {
  if (!$("#r-date").value) $("#r-date").value = today();
  const date = $("#r-date").value;
  const bid = me.role === "admin" ? +$("#r-branch").value || 0 : 0;
  const [r, sales] = await Promise.all([api(`/api/report?date=${date}&branch_id=${bid}`), api(`/api/sales?date=${date}&branch_id=${bid}`)]);
  const cards = [[t("r_count"), r.n], [t("r_total"), fmt(r.total) + " " + t("som")], [t("cash"), fmt(r.cash) + " " + t("som")], [t("card"), fmt(r.card) + " " + t("som")]];
  if (r.profit !== undefined) cards.push([t("r_profit"), fmt(r.profit) + " " + t("som")]);
  $("#r-cards").innerHTML = cards.map(([a, b]) => `<div class="card"><span>${a}</span><b>${b}</b></div>`).join("");
  $("#r-top").innerHTML =
    r.top.map((x) => `<tr><td>${esc(x.name)}</td><td class="r">${fmtQty(x.qty)} ${unitName(x.unit)}</td><td class="r">${fmt(x.sum)}</td></tr>`).join("") ||
    `<tr><td colspan="3">${t("empty")}</td></tr>`;
  $("#r-cashiers").innerHTML = (r.by_cashier || []).map((x) => `<tr><td>${esc(x.name)}</td><td class="r">${x.n}</td><td class="r">${fmt(x.total)}</td></tr>`).join("");
  $("#r-sales").innerHTML =
    sales.map((s) => `<tr><td>${s.id}</td><td>${esc(s.created.slice(11))}</td><td>${esc(s.branch)}</td><td>${esc(s.cashier)}</td>
      <td>${s.method === "cash" ? t("cash") : t("card")}</td><td class="r">${fmt(s.total)}</td><td class="r"><button data-rc="${s.id}">${t("receipt")}</button></td></tr>`).join("") ||
    `<tr><td colspan="7">${t("empty")}</td></tr>`;
}
$("#r-load").onclick = () => loadReport().catch(fail);
$("#r-sales").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-rc]");
  if (!b) return;
  try { printReceipt(await api("/api/sales/" + b.dataset.rc)); } catch (err) { fail(err); }
});

// ---------- Sozlamalar (admin) ----------
let users = [];

async function loadSettings() {
  branches = await api("/api/branches");
  users = await api("/api/users");
  cats = await api("/api/categories");
  fillBranchSelects();
  fillCatSelect();
  renderBranches();
  renderUsers();
  renderCatList();
  toggleUserBranch();
}

function renderBranches() {
  $("#b-body").innerHTML = branches
    .map((b) => `<tr><td>${esc(b.name)}</td><td>${esc(b.address)}</td><td>${esc(b.phone)}</td><td>${esc(b.printer_name || "—")}</td>
      <td>${b.active ? "✓" : "✕"}</td><td class="r"><button data-be="${b.id}">${t("edit")}</button></td></tr>`)
    .join("");
}
$("#b-body").addEventListener("click", (e) => {
  const b = e.target.closest("[data-be]");
  if (!b) return;
  const x = branches.find((y) => y.id === +b.dataset.be);
  $("#b-id").value = x.id;
  $("#b-name").value = x.name;
  $("#b-addr").value = x.address;
  $("#b-phone").value = x.phone;
  $("#b-printer").value = x.printer_name;
  $("#b-active").checked = !!x.active;
});
$("#b-reset").onclick = () => { $("#bform").reset(); $("#b-id").value = ""; $("#b-active").checked = true; };
$("#bform").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = $("#b-id").value;
  const body = { name: $("#b-name").value, address: $("#b-addr").value, phone: $("#b-phone").value, printer_name: $("#b-printer").value, active: $("#b-active").checked };
  try {
    await api(id ? `/api/branches/${id}` : "/api/branches", { method: id ? "PUT" : "POST", body });
    toast(t("saved"));
    $("#b-reset").click();
    loadSettings();
  } catch (err) { fail(err); }
});

function renderUsers() {
  $("#u-body").innerHTML = users
    .map((u) => `<tr><td>${esc(u.username)}</td><td>${esc(u.full_name)}</td><td>${u.role === "admin" ? t("role_admin") : t("role_cashier")}</td>
      <td>${esc(u.branch || "—")}</td><td class="r">${u.role === "cashier" ? u.discount_limit + "%" : "—"}</td><td>${u.active ? "✓" : "✕"}</td>
      <td class="r"><button data-ue="${u.id}">${t("edit")}</button></td></tr>`)
    .join("");
}
function toggleUserBranch() {
  const cashier = $("#u-role").value === "cashier";
  $("#u-branch-wrap").classList.toggle("hidden", !cashier);
  $("#u-limit-wrap").classList.toggle("hidden", !cashier);
}
$("#u-role").addEventListener("change", toggleUserBranch);
$("#u-body").addEventListener("click", (e) => {
  const b = e.target.closest("[data-ue]");
  if (!b) return;
  const u = users.find((y) => y.id === +b.dataset.ue);
  $("#u-id").value = u.id;
  $("#u-login").value = u.username;
  $("#u-full").value = u.full_name;
  $("#u-role").value = u.role;
  $("#u-branch").value = u.branch_id || "";
  $("#u-pass").value = "";
  $("#u-limit").value = u.discount_limit;
  $("#u-active").checked = !!u.active;
  toggleUserBranch();
});
$("#u-reset").onclick = () => { $("#uform").reset(); $("#u-id").value = ""; $("#u-active").checked = true; toggleUserBranch(); };
$("#uform").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = $("#u-id").value;
  const body = {
    username: $("#u-login").value, full_name: $("#u-full").value, role: $("#u-role").value,
    branch_id: +$("#u-branch").value || null, password: $("#u-pass").value || null,
    discount_limit: +$("#u-limit").value || 0, active: $("#u-active").checked,
  };
  try {
    await api(id ? `/api/users/${id}` : "/api/users", { method: id ? "PUT" : "POST", body });
    toast(t("saved"));
    $("#u-reset").click();
    loadSettings();
  } catch (err) { fail(err); }
});

function renderCatList() {
  $("#c-list").innerHTML = cats.map((c) => `<span class="tag">${esc(c.name)} <button data-cd="${c.id}" title="${t("delete")}">✕</button></span>`).join("") || `<small class="mute">${t("empty")}</small>`;
}
$("#c-list").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-cd]");
  if (!b || !confirm(t("confirm_delete"))) return;
  try { await api("/api/categories/" + b.dataset.cd, { method: "DELETE" }); loadSettings(); } catch (err) { fail(err); }
});
$("#cform").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/categories", { method: "POST", body: { name: $("#c-name").value } });
    $("#c-name").value = "";
    loadSettings();
  } catch (err) { fail(err); }
});

$("#load-printers").onclick = async () => {
  try {
    const r = await api("/api/printers");
    $("#printers-dl").innerHTML = r.printers.map((p) => `<option value="${esc(p)}">`).join("");
    $("#printer-list").textContent = r.printers.join("  •  ") || t("empty");
  } catch (err) { fail(err); }
};
$("#test-print").onclick = async () => {
  try { await api(`/api/print/test?branch_id=${branchId}`, { method: "POST" }); toast(t("test_sent")); } catch (err) { fail(err); }
};

boot();
