/* Cerne · painel do provedor. JavaScript puro, sem build. Todo dado da API entra como texto (sem innerHTML). */
"use strict";

// ------------------------------------------------------------------ utilidades
const $ = (sel, root = document) => root.querySelector(sel);

function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(n.dataset, v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return n;
}

const BRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const money = (v) => BRL.format(Number(v || 0));
const int = (v) => Number(v || 0).toLocaleString("pt-BR");
const date = (v) => (v ? new Date(v).toLocaleDateString("pt-BR") : "—");
const dateTime = (v) => (v ? new Date(v).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—");
const toISO = (local) => (local ? new Date(local).toISOString() : null);

function previousPeriod() {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function toast(msg, isError = false) {
  const t = el("div", { class: "toast" + (isError ? " err" : ""), role: isError ? "alert" : "status" }, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), isError ? 7000 : 3500);
}

const KIND_LABEL = { SCM: "SCM", SVA: "SVA" };
const badge = (text, cls = "") => el("span", { class: `badge ${cls}` }, text);
const kindBadge = (k) => badge(KIND_LABEL[k] || k, (k || "").toLowerCase());

function emptyRow(cols, title, hint) {
  return el("tr", {}, el("td", { colspan: cols, class: "empty" }, el("strong", {}, title), hint || ""));
}

function table(headers, rows) {
  return el("div", { class: "table-wrap" },
    el("table", {},
      el("thead", {}, el("tr", {}, headers.map((h) =>
        typeof h === "string" ? el("th", {}, h) : el("th", { class: h.cls }, h.label)))),
      el("tbody", {}, rows)));
}

// ------------------------------------------------------------------ sessão e API
const store = {
  get key() {
    try { return sessionStorage.getItem("cerne.key") || localStorage.getItem("cerne.key"); } catch { return null; }
  },
  setKey(k, remember) {
    try {
      sessionStorage.setItem("cerne.key", k);
      if (remember) localStorage.setItem("cerne.key", k);
    } catch { /* armazenamento indisponível: fica só em memória */ }
    memKey = k;
  },
  clear() {
    memKey = null;
    try { sessionStorage.removeItem("cerne.key"); localStorage.removeItem("cerne.key"); } catch { /* */ }
  },
};
let memKey = null;
let adminToken = null;

// Nos aplicativos (Android/iOS via Capacitor, Windows via Electron) o painel roda empacotado e a API
// fica em outro endereço, informado no login. No navegador, API e painel estão no mesmo domínio.
const IS_NATIVE = !!(window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform());
const IS_APP = IS_NATIVE || !!window.cerneDesktop;
const server = {
  get url() {
    if (!IS_APP) return "";
    try { return localStorage.getItem("cerne.server") || ""; } catch { return ""; }
  },
  set(url) { try { localStorage.setItem("cerne.server", url); } catch { /* */ } },
};

/** Normaliza o endereço digitado; exige https (http só para testes em localhost). */
function normalizeServer(raw) {
  let v = (raw || "").trim().replace(/\/+$/, "");
  if (v && !/^[a-z]+:\/\//i.test(v)) v = "https://" + v;
  let u;
  try { u = new URL(v); } catch { return null; }
  const local = ["localhost", "127.0.0.1"].includes(u.hostname);
  if (u.protocol !== "https:" && !(local && u.protocol === "http:")) return null;
  return u.origin;
}
const state = { me: null, period: previousPeriod() };

class ApiError extends Error {
  constructor(status, detail) {
    super(detailText(detail) || `Erro ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

function detailText(detail) {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => {
      const field = (d.loc || []).filter((x) => x !== "body").join(".");
      return field ? `${field}: ${d.msg}` : d.msg;
    }).join(" · ");
  }
  return JSON.stringify(detail);
}

async function api(path, { method = "GET", body, admin = false, raw = false } = {}) {
  const headers = { Accept: raw ? "application/pdf" : "application/json" };
  if (admin) headers["X-Admin-Token"] = adminToken || "";
  else headers["X-API-Key"] = memKey || store.key || "";
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let r;
  try {
    r = await fetch(server.url + path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
  } catch {
    throw new ApiError(0, "Sem conexão com o servidor. Verifique a internet e tente de novo.");
  }
  if (r.status === 401 && !admin) {
    logout("Sua sessão expirou ou a chave foi revogada. Entre novamente.");
    throw new ApiError(401, "Não autenticado");
  }
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { detail = r.statusText; }
    if (r.status === 429) detail = "Muitas requisições em pouco tempo. Aguarde um minuto.";
    throw new ApiError(r.status, detail);
  }
  if (raw) return r;
  return r.status === 204 ? null : r.json();
}

async function withBusy(btn, fn) {
  if (btn) { btn.disabled = true; btn.setAttribute("aria-busy", "true"); }
  try { return await fn(); } finally {
    if (btn) { btn.disabled = false; btn.removeAttribute("aria-busy"); }
  }
}

// ------------------------------------------------------------------ formulários em diálogo
/**
 * fields: [{name, label, type, required, minlength, options, hint, value, span}]
 * onSubmit(values) -> Promise; erros da API aparecem no próprio diálogo.
 */
function formDialog({ title, intro, fields, submitLabel = "Salvar", onSubmit }) {
  const dlg = $("#dialog");
  dlg.replaceChildren();
  const errBox = el("p", { class: "field-error", role: "alert" });
  const inputs = {};

  const grid = el("div", { class: "form-grid" }, fields.map((f) => {
    const id = `f-${f.name}`;
    let input;
    if (f.type === "textarea") input = el("textarea", { id, name: f.name });
    else if (f.type === "select") {
      input = el("select", { id, name: f.name }, f.options.map(([v, l]) => el("option", { value: v }, l)));
    } else input = el("input", { id, name: f.name, type: f.type || "text", step: f.step, autocomplete: "off" });
    if (f.value != null) input.value = f.value;
    if (f.placeholder) input.placeholder = f.placeholder;
    const fe = el("p", { class: "field-error", id: `${id}-err` });
    input.setAttribute("aria-describedby", `${id}-err`);
    const check = () => {
      const v = input.value.trim();
      let msg = "";
      if (f.required && !v) msg = "Campo obrigatório.";
      else if (f.minlength && v && v.length < f.minlength) msg = `Mínimo de ${f.minlength} caracteres.`;
      else if (f.pattern && v && !new RegExp(f.pattern).test(v)) msg = f.patternMsg || "Formato inválido.";
      fe.textContent = msg;
      input.setAttribute("aria-invalid", msg ? "true" : "false");
      return !msg;
    };
    input.addEventListener("blur", check);
    input.addEventListener("input", () => { if (fe.textContent) check(); });
    inputs[f.name] = { input, check, f };
    return el("div", { class: "field" + (f.span ? " span-2" : "") },
      el("label", { for: id }, f.label + (f.required ? "" : " (opcional)")), input,
      f.hint ? el("p", { class: "field-hint" }, f.hint) : null, fe);
  }));

  const submit = el("button", { class: "btn", type: "submit" }, submitLabel);
  const form = el("form", { novalidate: true },
    grid, errBox,
    el("div", { class: "form-actions" },
      el("button", { class: "btn btn-ghost", type: "button", onclick: () => dlg.close() }, "Cancelar"),
      submit));

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const ok = Object.values(inputs).map((x) => x.check()).every(Boolean);
    if (!ok) { Object.values(inputs).find((x) => x.input.getAttribute("aria-invalid") === "true")?.input.focus(); return; }
    const values = {};
    for (const [k, { input, f }] of Object.entries(inputs)) {
      const v = input.value.trim();
      values[k] = v === "" ? null : f.type === "datetime-local" ? toISO(v) : v;
    }
    errBox.textContent = "";
    await withBusy(submit, async () => {
      try {
        await onSubmit(values);
        dlg.close();
      } catch (err) {
        errBox.textContent = err.message;
      }
    });
  });

  dlg.append(el("div", { class: "dlg-head" }, el("h2", {}, title)),
    el("div", { class: "dlg-body" }, intro ? el("p", { class: "note" }, intro) : null, form));
  dlg.showModal();
  Object.values(inputs)[0]?.input.focus();
}

function infoDialog(title, ...body) {
  const dlg = $("#dialog");
  dlg.replaceChildren(
    el("div", { class: "dlg-head" }, el("h2", {}, title)),
    el("div", { class: "dlg-body" }, ...body,
      el("div", { class: "form-actions" }, el("button", { class: "btn", type: "button", onclick: () => dlg.close() }, "Fechar"))));
  dlg.showModal();
}

function confirmDialog(title, message, confirmLabel, onConfirm) {
  const dlg = $("#dialog");
  const btn = el("button", { class: "btn btn-danger", type: "button" }, confirmLabel);
  btn.addEventListener("click", () => withBusy(btn, async () => {
    try { await onConfirm(); dlg.close(); } catch (e) { toast(e.message, true); }
  }));
  dlg.replaceChildren(
    el("div", { class: "dlg-head" }, el("h2", {}, title)),
    el("div", { class: "dlg-body" }, el("p", {}, message),
      el("div", { class: "form-actions" },
        el("button", { class: "btn btn-ghost", type: "button", onclick: () => dlg.close() }, "Cancelar"), btn)));
  dlg.showModal();
}

function secretDialog(title, key, extra) {
  const copy = el("button", { class: "btn btn-ghost btn-sm", type: "button" }, "Copiar");
  copy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(key); toast("Chave copiada."); } catch { toast("Não foi possível copiar.", true); }
  });
  infoDialog(title,
    el("p", {}, "Guarde esta chave agora em local seguro. Ela não será exibida novamente."),
    el("div", { class: "secret" }, key), copy, extra || null);
}

// ------------------------------------------------------------------ navegação
const ROUTES = [
  ["visao-geral", "Visão geral", viewOverview],
  ["catalogo", "Catálogo de serviços", viewCatalog],
  ["ativacoes", "Ativações e uso", viewActivations],
  ["faturas", "Faturas", viewInvoices],
  ["auditoria", "Trilha de auditoria", viewAudit],
  ["tributos", "Parâmetros tributários", viewTax],
  ["conta", "Conta e integrações", viewAccount],
];

function buildNav() {
  $("#nav").replaceChildren(...ROUTES.map(([id, label]) => el("a", { href: `#/${id}`, dataset: { route: id } }, label)));
}

async function route() {
  if (!$("#app").hidden) {
    const id = (location.hash.match(/^#\/([\w-]+)/) || [])[1] || "visao-geral";
    const r = ROUTES.find(([rid]) => rid === id) || ROUTES[0];
    document.querySelectorAll("#nav a").forEach((a) => a.toggleAttribute("aria-current", a.dataset.route === r[0]));
    if ($("#nav a[aria-current]")) $("#nav a[aria-current]").setAttribute("aria-current", "page");
    $("#page-title").textContent = r[1];
    document.title = `${r[1]} · Cerne`;
    $("#page-actions").replaceChildren();
    $("#sidebar").classList.remove("open");
    const view = $("#view");
    view.replaceChildren(el("p", { class: "note" }, "Carregando…"));
    try {
      await r[2](view);
    } catch (e) {
      if (e.status !== 401) view.replaceChildren(el("div", { class: "card" }, el("p", {}, "Não foi possível carregar: ", e.message)));
    }
    view.focus({ preventScroll: true });
  }
}

function periodPicker(onChange) {
  const input = el("input", { type: "month", value: state.period, "aria-label": "Competência" });
  input.addEventListener("change", () => {
    if (/^\d{4}-\d{2}$/.test(input.value)) { state.period = input.value; onChange(); }
  });
  return input;
}

// ------------------------------------------------------------------ visão geral
async function viewOverview(view) {
  const render = async () => {
    const s = await api(`/v1/reports/${state.period}/summary`);
    const t = s.trilha;
    const pdfBtn = el("button", { class: "btn", type: "button" }, "Baixar relatório de evidências (PDF)");
    pdfBtn.addEventListener("click", () => withBusy(pdfBtn, downloadReport));
    view.replaceChildren(
      el("div", { class: "kpis" },
        kpi(money(s.total_scm), "Total SCM"), kpi(money(s.total_sva), "Total SVA"),
        kpi(money(s.total_pendente), "Pendente de revisão"), kpi(int(s.faturas), "Faturas analisadas")),
      el("div", { class: "kpis" },
        kpi(int(s.faturas_com_pendencia), "Faturas com pendência"), kpi(int(s.avisos), "Avisos emitidos"),
        kpi(int(t.entradas), "Entradas na trilha"),
        el("div", { class: "kpi" }, el("div", { class: "v" }, t.integra ? badge("Íntegra", "ok") : badge("Inconsistente", "bad")),
          el("div", { class: "l" }, "Verificação da trilha"))),
      el("p", { class: "note" }, "Valores em PENDENTE não foram classificados como SCM nem como SVA: aguardam validação ou comprovação e devem ser revistos antes de qualquer enquadramento fiscal."),
      el("div", { class: "card" },
        el("div", { class: "card-head" }, el("div", {}, el("h2", {}, "Pendências e avisos"), el("p", {}, `Competência ${state.period}`)), pdfBtn),
        table(["Tipo de aviso", { label: "Ocorrências", cls: "num" }, "Providência sugerida"],
          s.avisos_por_tipo.length
            ? s.avisos_por_tipo.map((a) => el("tr", {}, el("td", {}, a.descricao), el("td", { class: "num" }, int(a.ocorrencias)), el("td", {}, a.providencia)))
            : [emptyRow(3, "Nenhum aviso nesta competência", s.faturas ? "" : "Ainda não há faturas separadas neste período.")])),
    );
  };
  $("#page-actions").append(periodPicker(() => route()));
  await render();
}

const kpi = (v, l) => el("div", { class: "kpi" }, el("div", { class: "v" }, v), el("div", { class: "l" }, l));

async function downloadReport() {
  try {
    const r = await api(`/v1/reports/${state.period}/evidence.pdf`, { raw: true });
    const id = r.headers.get("X-Report-Id") || `relatorio-${state.period}`;
    const blob = await r.blob();
    if (IS_NATIVE) { await shareNative(blob, `${id}.pdf`); toast(`Relatório ${id} gerado e registrado na trilha.`); return; }
    const url = URL.createObjectURL(blob);
    const a = el("a", { href: url, download: `${id}.pdf` });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    toast(`Relatório ${id} gerado e registrado na trilha.`);
  } catch (e) { toast(e.message, true); }
}

/** Android/iOS: grava o PDF no cache do app e abre a folha de compartilhamento (salvar, e-mail, WhatsApp…). */
async function shareNative(blob, name) {
  const { Filesystem, Share } = window.Capacitor.Plugins;
  const data = await new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result).split(",")[1]);
    fr.onerror = () => reject(fr.error);
    fr.readAsDataURL(blob);
  });
  const { uri } = await Filesystem.writeFile({ path: name, data, directory: "CACHE" });
  await Share.share({ title: name, url: uri, dialogTitle: "Salvar ou enviar o relatório" });
}

// ------------------------------------------------------------------ catálogo
async function viewCatalog(view) {
  const add = el("button", { class: "btn", type: "button", onclick: () => serviceForm() }, "Cadastrar serviço");
  $("#page-actions").append(add);
  const rows = await api("/v1/services");
  view.replaceChildren(
    el("div", { class: "callout" }, "A Cerne não decide a classificação. Ela aplica a que o provedor cadastrou e o contador validou. Sem validação, os valores vão para PENDENTE."),
    el("div", { class: "card" },
      table(["Código", "Serviço", "Tipo", "Validação", "Regra", ""],
        rows.length ? rows.map((s) => el("tr", {},
          el("td", {}, el("code", {}, s.code)),
          el("td", {}, s.name, !s.active ? [" ", badge("Inativo", "bad")] : null,
            el("details", {}, el("summary", {}, "Critério"), el("p", { class: "note" }, s.criteria),
              s.contract_ref ? el("p", { class: "note" }, "Contrato: ", s.contract_ref) : null)),
          el("td", {}, kindBadge(s.kind)),
          el("td", {}, s.validated ? [badge("Validado", "ok"), el("div", { class: "note" }, s.validated_by, " · ", date(s.validated_at))] : badge("Pendente de validação", "warn")),
          el("td", {}, s.rule_version),
          el("td", { class: "actions" },
            !s.validated && s.active ? el("button", { class: "btn btn-sm", type: "button", onclick: () => validateForm(s) }, "Validar") : null,
            el("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => serviceForm(s) }, "Editar"),
            s.active ? el("button", { class: "btn btn-danger btn-sm", type: "button", onclick: () => confirmDialog(
              `Desativar ${s.code}?`, "Os valores deste serviço passarão a ir para PENDENTE. A ação fica registrada na trilha.", "Desativar",
              async () => { await api(`/v1/services/${encodeURIComponent(s.code)}/deactivate`, { method: "POST" }); toast("Serviço desativado."); route(); }) }, "Desativar") : null)))
          : [emptyRow(6, "Nenhum serviço cadastrado", "Cadastre cada oferta (internet, antivírus, cursos…) com o fundamento da classificação.")])));
}

function serviceForm(s) {
  const editing = !!s;
  formDialog({
    title: editing ? `Editar ${s.code}` : "Cadastrar serviço",
    intro: editing ? "Alterar o tipo ou o critério cria nova versão da regra e exige nova validação." : null,
    fields: [
      ...(editing ? [] : [{ name: "code", label: "Código", required: true, pattern: "^[A-Za-z0-9_.\\-]+$", patternMsg: "Use letras, números, ponto, hífen ou sublinhado.", hint: "Mesmo código usado no mapeamento do ERP. Ex.: INTERNET, ANTIVIRUS." }]),
      { name: "name", label: "Nome", required: true, value: s?.name },
      { name: "kind", label: "Tipo", type: "select", options: [["SCM", "SCM · comunicação multimídia"], ["SVA", "SVA · valor adicionado"]], value: s?.kind || "SCM" },
      { name: "contract_ref", label: "Referência do contrato", value: s?.contract_ref },
      { name: "criteria", label: "Fundamento da classificação", type: "textarea", required: true, minlength: 20, span: true, value: s?.criteria, hint: "Por que este serviço é SCM ou SVA (mín. 20 caracteres)." },
    ],
    submitLabel: editing ? "Salvar alterações" : "Cadastrar",
    onSubmit: async (v) => {
      if (editing) {
        const r = await api(`/v1/services/${encodeURIComponent(s.code)}`, { method: "PUT", body: v });
        toast(r.rule_version !== s.rule_version ? `Regra atualizada para ${r.rule_version}. Valide novamente.` : "Serviço atualizado.");
      } else {
        await api("/v1/services", { method: "POST", body: v });
        toast("Serviço cadastrado. Falta validar com o contador.");
      }
      route();
    },
  });
}

function validateForm(s) {
  formDialog({
    title: `Validar ${s.code}`,
    intro: `Registre quem validou a classificação ${s.kind} (regra ${s.rule_version}). O registro entra na trilha de auditoria.`,
    fields: [{ name: "validated_by", label: "Validado por (nome e registro CRC/OAB)", required: true, minlength: 5, span: true, placeholder: "Ex.: Maria Souza · CRC/MG 000000" }],
    submitLabel: "Registrar validação",
    onSubmit: async (v) => {
      await api(`/v1/services/${encodeURIComponent(s.code)}/validate`, { method: "POST", body: v });
      toast("Validação registrada.");
      route();
    },
  });
}

// ------------------------------------------------------------------ ativações
async function viewActivations(view) {
  const add = el("button", { class: "btn", type: "button", onclick: activationForm }, "Registrar ativação");
  $("#page-actions").append(add);
  const q = el("input", { type: "search", placeholder: "Código do cliente no ERP", "aria-label": "Filtrar por cliente" });
  const body = el("div");
  const load = async () => {
    const qs = q.value.trim() ? `?customer_ref=${encodeURIComponent(q.value.trim())}` : "";
    const rows = await api(`/v1/activations${qs}`);
    body.replaceChildren(table(["Cliente", "Serviço", "Consentimento", "Ativação", "Situação", { label: "Usos", cls: "num" }, ""],
      rows.length ? rows.map((a) => el("tr", {},
        el("td", { class: "nw" }, a.customer_ref), el("td", {}, el("code", {}, a.service_code)), el("td", {}, a.consent_ref || "—"),
        el("td", {}, date(a.activated_at)),
        el("td", {}, a.canceled_at ? badge(`Cancelada ${date(a.canceled_at)}`, "bad") : badge("Ativa", "ok")),
        el("td", { class: "num" }, int(a.usage_events)),
        el("td", { class: "actions" },
          !a.canceled_at ? el("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => usageForm(a) }, "Registrar uso") : null,
          el("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => showEvidence(a.customer_ref) }, "Evidências"),
          !a.canceled_at ? el("button", { class: "btn btn-danger btn-sm", type: "button", onclick: () => confirmDialog(
            "Cancelar ativação?", `O SVA ${a.service_code} do cliente ${a.customer_ref} deixa de valer a partir da próxima competência.`, "Cancelar ativação",
            async () => { await api(`/v1/activations/${a.id}/cancel`, { method: "POST" }); toast("Ativação cancelada."); load(); }) }, "Cancelar") : null)))
        : [emptyRow(7, q.value ? "Nenhuma ativação para este cliente" : "Nenhuma ativação registrada", "Registre a contratação do SVA com a referência do aceite do assinante.")]));
  };
  let t;
  q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(load, 300); });
  view.replaceChildren(el("div", { class: "card" },
    el("div", { class: "card-head" }, el("div", {}, el("h2", {}, "Ativações de SVA"),
      el("p", {}, "Assinantes identificados só pelo código interno do ERP. Nunca informe nome, CPF ou contato.")), q),
    body));
  await load();
}

async function activationForm() {
  const svas = (await api("/v1/services")).filter((s) => s.kind === "SVA" && s.active);
  if (!svas.length) { toast("Cadastre ao menos um serviço SVA no catálogo antes.", true); return; }
  formDialog({
    title: "Registrar ativação de SVA",
    fields: [
      { name: "customer_ref", label: "Código do cliente no ERP", required: true },
      { name: "service_code", label: "Serviço", type: "select", options: svas.map((s) => [s.code, `${s.code} · ${s.name}`]) },
      { name: "activated_at", label: "Data da ativação", type: "datetime-local", required: true },
      { name: "consent_ref", label: "Referência do consentimento", hint: "Aceite, contrato ou registro de log." },
    ],
    submitLabel: "Registrar",
    onSubmit: async (v) => { await api("/v1/activations", { method: "POST", body: v }); toast("Ativação registrada."); route(); },
  });
}

function usageForm(a) {
  formDialog({
    title: `Registrar uso · ${a.service_code}`,
    intro: `Cliente ${a.customer_ref}. Uso registrado comprova que o SVA foi efetivamente prestado.`,
    fields: [
      { name: "event_type", label: "Tipo de evento", required: true, placeholder: "login, download, sessão…" },
      { name: "occurred_at", label: "Data e hora", type: "datetime-local", required: true },
    ],
    submitLabel: "Registrar uso",
    onSubmit: async (v) => { await api(`/v1/activations/${a.id}/usage`, { method: "POST", body: v }); toast("Uso registrado."); route(); },
  });
}

async function showEvidence(customer) {
  try {
    const ev = await api(`/v1/evidence/${encodeURIComponent(customer)}`);
    infoDialog(`Evidências · cliente ${customer}`,
      table(["Serviço", "Consentimento", "Ativação", "Cancelamento", { label: "Usos", cls: "num" }, "Último uso"],
        ev.map((e) => el("tr", {}, el("td", {}, e.service_code), el("td", {}, e.consent_ref || "—"), el("td", {}, date(e.activated_at)),
          el("td", {}, date(e.canceled_at)), el("td", { class: "num" }, int(e.usage_events)), el("td", {}, date(e.last_use))))));
  } catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------------ faturas
async function viewInvoices(view) {
  const onlyPending = el("input", { type: "checkbox", id: "only-pending" });
  const manual = el("button", { class: "btn", type: "button", onclick: splitForm }, "Separar fatura manualmente");
  $("#page-actions").append(periodPicker(() => load()), manual);
  const body = el("div");
  const load = async () => {
    const r = await api(`/v1/invoices?period=${state.period}&only_pending=${onlyPending.checked}`);
    body.replaceChildren(
      el("p", { class: "note" }, `${int(r.total)} fatura(s) em ${state.period}${r.total > r.items.length ? `; exibindo ${r.items.length}` : ""}.`),
      table(["Fatura", "Cliente", { label: "SCM", cls: "num" }, { label: "SVA", cls: "num" }, { label: "Pendente", cls: "num" }, "Avisos", ""],
        r.items.length ? r.items.map((s) => el("tr", {},
          el("td", { class: "nw" }, s.invoice_ref), el("td", { class: "nw" }, s.customer_ref),
          el("td", { class: "num" }, money(s.total_scm)), el("td", { class: "num" }, money(s.total_sva)),
          el("td", { class: "num" }, Number(s.total_pendente) > 0 ? badge(money(s.total_pendente), "warn") : money(0)),
          el("td", { class: "note wide" }, s.warnings.length ? s.warnings.join("; ") : "—"),
          el("td", { class: "actions" }, el("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => showInvoice(s.invoice_ref) }, "Detalhes"))))
          : [emptyRow(7, "Nenhuma fatura nesta competência", "As faturas chegam pela sincronização com o ERP ou pela separação manual.")]));
  };
  onlyPending.addEventListener("change", load);
  view.replaceChildren(el("div", { class: "card" },
    el("div", { class: "card-head" }, el("h2", {}, "Separação por fatura"),
      el("label", { class: "check", for: "only-pending" }, onlyPending, "Só com pendência")), body));
  await load();
}

async function showInvoice(ref) {
  try {
    const d = await api(`/v1/invoices/${encodeURIComponent(ref)}?period=${state.period}`);
    const bucket = (b) => (b === "PENDENTE" ? badge("Pendente", "warn") : kindBadge(b));
    infoDialog(`Fatura ${d.invoice_ref}`,
      el("p", { class: "note" }, `Cliente ${d.customer_ref} · competência ${d.period} · separada em ${dateTime(d.created_at)}`),
      el("div", { class: "lines-table" }, table(["Serviço", "Descrição", { label: "Valor", cls: "num" }, "Destino", "Regra"],
        d.lines.map((l) => el("tr", {}, el("td", {}, el("code", {}, l.service_code)), el("td", {}, l.description || "—"),
          el("td", { class: "num" }, money(l.amount)), el("td", {}, bucket(l.bucket)), el("td", {}, l.rule_version || "—"))))),
      d.warnings.length ? el("div", { class: "callout" }, d.warnings.join(" · ")) : null,
      d.historico.length ? el("p", { class: "note" }, `Reprocessada ${d.historico.length} vez(es); vale a separação mais recente.`) : null);
  } catch (e) { toast(e.message, true); }
}

function splitForm() {
  const dlg = $("#dialog");
  const lines = el("div");
  const addLine = () => {
    const row = el("div", { class: "row-lines" },
      el("input", { name: "service_code", placeholder: "Código do serviço", "aria-label": "Código do serviço", required: true }),
      el("input", { name: "amount", type: "number", step: "0.01", min: "0", placeholder: "Valor", "aria-label": "Valor (R$)", required: true }),
      el("input", { name: "description", class: "desc", placeholder: "Descrição", "aria-label": "Descrição" }),
      el("button", { class: "btn btn-ghost btn-sm", type: "button", "aria-label": "Remover linha", onclick: () => { if (lines.children.length > 1) row.remove(); } }, "×"));
    lines.append(row);
  };
  addLine();
  const ref = el("input", { id: "s-ref", required: true });
  const cust = el("input", { id: "s-cust", required: true });
  const period = el("input", { id: "s-period", type: "month", value: state.period, required: true });
  const err = el("p", { class: "field-error", role: "alert" });
  const submit = el("button", { class: "btn", type: "submit" }, "Separar");
  const form = el("form", { novalidate: true },
    el("div", { class: "form-grid" },
      el("div", { class: "field" }, el("label", { for: "s-ref" }, "Fatura"), ref),
      el("div", { class: "field" }, el("label", { for: "s-cust" }, "Código do cliente no ERP"), cust),
      el("div", { class: "field" }, el("label", { for: "s-period" }, "Competência"), period)),
    el("label", {}, "Itens da fatura"), lines,
    el("button", { class: "link", type: "button", onclick: addLine }, "+ Adicionar item"),
    err,
    el("div", { class: "form-actions" }, el("button", { class: "btn btn-ghost", type: "button", onclick: () => dlg.close() }, "Cancelar"), submit));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const items = [...lines.children].map((r) => ({
      service_code: r.querySelector("[name=service_code]").value.trim(),
      amount: r.querySelector("[name=amount]").value,
      description: r.querySelector("[name=description]").value.trim(),
    }));
    if (!ref.value.trim() || !cust.value.trim() || !period.value || items.some((i) => !i.service_code || i.amount === "")) {
      err.textContent = "Preencha fatura, cliente, competência e o código e valor de cada item.";
      return;
    }
    withBusy(submit, async () => {
      try {
        const r = await api("/v1/invoices/split", { method: "POST", body: { invoice_ref: ref.value.trim(), customer_ref: cust.value.trim(), period: period.value, lines: items } });
        dlg.close();
        state.period = period.value;
        toast(`Separada: SCM ${money(r.total_scm)} · SVA ${money(r.total_sva)} · Pendente ${money(r.total_pendente)}`);
        route();
      } catch (e2) { err.textContent = e2.message; }
    });
  });
  dlg.replaceChildren(el("div", { class: "dlg-head" }, el("h2", {}, "Separar fatura")), el("div", { class: "dlg-body" }, form));
  dlg.showModal();
  ref.focus();
}

// ------------------------------------------------------------------ auditoria
async function viewAudit(view) {
  const verifyBtn = el("button", { class: "btn", type: "button" }, "Verificar integridade");
  $("#page-actions").append(verifyBtn);
  const status = el("div");
  const list = el("tbody");
  const more = el("button", { class: "btn btn-ghost", type: "button", hidden: true }, "Carregar mais");
  let lastId = null;

  const renderVerify = (v) => status.replaceChildren(el("div", { class: "card" },
    el("div", { class: "card-head" }, el("div", {}, el("h2", {}, "Integridade da trilha"),
      el("p", {}, `${int(v.entradas)} entradas verificadas`)), v.integra ? badge("Íntegra", "ok") : badge(`Inconsistente a partir da entrada ${v.primeira_inconsistencia}`, "bad")),
    el("p", { class: "note" }, "Hash da última entrada (SHA-256)"), el("p", { class: "hash" }, v.hash_final),
    el("p", { class: "note" }, "A trilha é encadeada por hash: qualquer alteração em um registro anterior invalida os seguintes. O mecanismo evidencia adulteração, mas não a impede.")));

  const loadPage = async () => {
    const rows = await api(`/v1/audit?limit=50${lastId ? `&before_id=${lastId}` : ""}`);
    if (!lastId && !rows.length) list.append(emptyRow(4, "Trilha vazia", ""));
    rows.forEach((e) => list.append(el("tr", {},
      el("td", { class: "num" }, e.id), el("td", {}, dateTime(e.ts)), el("td", {}, el("code", {}, e.action)),
      el("td", {}, el("details", {}, el("summary", {}, "Conteúdo"), el("pre", { class: "json" }, JSON.stringify(e.payload, null, 2)),
        el("p", { class: "hash" }, "hash ", e.hash))))));
    lastId = rows.length ? rows[rows.length - 1].id : lastId;
    more.hidden = rows.length < 50;
  };
  verifyBtn.addEventListener("click", () => withBusy(verifyBtn, async () => { renderVerify(await api("/v1/audit/verify")); toast("Verificação concluída."); }));
  more.addEventListener("click", () => withBusy(more, loadPage));

  view.replaceChildren(status, el("div", { class: "card" },
    el("div", { class: "card-head" }, el("h2", {}, "Registros")),
    el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, el("th", { class: "num" }, "#"), el("th", {}, "Quando"), el("th", {}, "Ação"), el("th", {}, "Detalhes"))), list)),
    el("div", { class: "form-actions" }, more)));
  renderVerify(await api("/v1/audit/verify"));
  await loadPage();
}

// ------------------------------------------------------------------ parâmetros tributários
async function viewTax(view) {
  $("#page-actions").append(el("button", { class: "btn", type: "button", onclick: taxForm }, "Novo parâmetro"));
  const rows = await api("/v1/tax-parameters");
  view.replaceChildren(
    el("div", { class: "callout" }, "Alíquotas informadas pelo provedor e pelo contador, por vigência. A Cerne registra; não recomenda enquadramento."),
    el("div", { class: "card" }, table(["Tributo", "Aplica-se a", { label: "Alíquota", cls: "num" }, "Vigência", "Fonte", ""],
      rows.length ? rows.map((t) => el("tr", {},
        el("td", {}, t.tax), el("td", {}, kindBadge(t.applies_to)), el("td", { class: "num" }, `${Number(t.rate).toLocaleString("pt-BR", { maximumFractionDigits: 4 })}%`),
        el("td", {}, `${date(t.valid_from)} → ${t.valid_to ? date(t.valid_to) : "em vigor"}`), el("td", { class: "note" }, t.source_note || "—"),
        el("td", { class: "actions" }, !t.valid_to ? el("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => closeTaxForm(t) }, "Encerrar vigência") : null)))
        : [emptyRow(6, "Nenhum parâmetro cadastrado", "Cadastre ICMS/ISS hoje e IBS/CBS conforme a transição.")])));
}

function taxForm() {
  formDialog({
    title: "Novo parâmetro tributário",
    fields: [
      { name: "tax", label: "Tributo", type: "select", options: [["ICMS", "ICMS"], ["ISS", "ISS"], ["IBS", "IBS"], ["CBS", "CBS"]] },
      { name: "applies_to", label: "Aplica-se a", type: "select", options: [["SCM", "SCM"], ["SVA", "SVA"]] },
      { name: "rate", label: "Alíquota (%)", type: "number", step: "0.0001", required: true },
      { name: "valid_from", label: "Início da vigência", type: "datetime-local", required: true },
      { name: "source_note", label: "Fonte / observação", span: true, hint: "Ex.: legislação, parecer do contador." },
    ],
    onSubmit: async (v) => { await api("/v1/tax-parameters", { method: "POST", body: v }); toast("Parâmetro cadastrado."); route(); },
  });
}

function closeTaxForm(t) {
  formDialog({
    title: `Encerrar ${t.tax} · ${t.applies_to}`,
    fields: [{ name: "valid_to", label: "Fim da vigência", type: "datetime-local", required: true }],
    submitLabel: "Encerrar",
    onSubmit: async (v) => { await api(`/v1/tax-parameters/${t.id}/close`, { method: "POST", body: v }); toast("Vigência encerrada."); route(); },
  });
}

// ------------------------------------------------------------------ conta
async function viewAccount(view) {
  const [keys, erp] = await Promise.all([api("/v1/keys"), api("/v1/erp/credentials")]);
  const newKey = el("button", { class: "btn", type: "button" }, "Gerar nova chave");
  newKey.addEventListener("click", () => formDialog({
    title: "Gerar nova chave de API",
    fields: [{ name: "label", label: "Identificação", span: true, placeholder: "Ex.: integração ERP, painel financeiro" }],
    submitLabel: "Gerar",
    onSubmit: async (v) => {
      const r = await api("/v1/keys", { method: "POST", body: v });
      setTimeout(() => secretDialog("Nova chave de API", r.api_key), 0);
      route();
    },
  }));
  const erpBtn = el("button", { class: "btn", type: "button", onclick: () => erpForm(erp) }, erp.configured ? "Atualizar credenciais" : "Configurar Hubsoft");

  view.replaceChildren(
    el("div", { class: "card" },
      el("div", { class: "card-head" }, el("div", {}, el("h2", {}, state.me.name), el("p", {}, `CNPJ ${state.me.cnpj} · provedor nº ${state.me.provider_id}`)))),
    el("div", { class: "card" },
      el("div", { class: "card-head" }, el("div", {}, el("h2", {}, "Chaves de API"), el("p", {}, "Para trocar uma chave: gere a nova, atualize as integrações e só então revogue a antiga.")), newKey),
      table(["Prefixo", "Identificação", "Criada", "Último uso", "Situação", ""],
        keys.map((k) => el("tr", {},
          el("td", {}, el("code", {}, `cerne_${k.prefix}_…`)), el("td", {}, k.label || "—"), el("td", {}, date(k.created_at)),
          el("td", {}, dateTime(k.last_used_at)), el("td", {}, k.revoked_at ? badge("Revogada", "bad") : badge("Ativa", "ok")),
          el("td", { class: "actions" }, !k.revoked_at ? el("button", { class: "btn btn-danger btn-sm", type: "button", onclick: () => confirmDialog(
            "Revogar chave?", `A chave cerne_${k.prefix}_… deixará de funcionar imediatamente.`, "Revogar",
            async () => { await api(`/v1/keys/${k.id}`, { method: "DELETE" }); toast("Chave revogada."); route(); }) }, "Revogar") : null))))),
    el("div", { class: "card" },
      el("div", { class: "card-head" }, el("div", {}, el("h2", {}, "Integração com o ERP (somente leitura)"),
        el("p", {}, erp.configured ? `Hubsoft · ${erp.base_url} · atualizado em ${dateTime(erp.updated_at)}` : "Não configurada.")), erpBtn),
      el("p", { class: "note" }, "As credenciais são guardadas cifradas e nunca são exibidas de volta. Use um usuário do Hubsoft com permissão apenas de leitura."),
      erp.configured ? el("button", { class: "btn btn-danger btn-sm", type: "button", onclick: () => confirmDialog(
        "Remover credenciais do ERP?", "A sincronização automática deixará de funcionar para este provedor.", "Remover",
        async () => { await api("/v1/erp/credentials", { method: "DELETE" }); toast("Credenciais removidas."); route(); }) }, "Remover credenciais") : null),
  );
}

function erpForm(erp) {
  formDialog({
    title: "Credenciais do Hubsoft",
    intro: "Somente leitura. Antes de informar credenciais reais, o Termo de Autorização de Acesso a Dados deve estar assinado.",
    fields: [
      { name: "base_url", label: "URL da API", required: true, span: true, value: erp.base_url, pattern: "^https://", patternMsg: "Use https://", placeholder: "https://api.seuprovedor.hubsoft.com.br" },
      { name: "client_id", label: "Client ID", required: true },
      { name: "client_secret", label: "Client secret", type: "password", required: true },
      { name: "username", label: "Usuário", required: true },
      { name: "password", label: "Senha", type: "password", required: true },
    ],
    onSubmit: async (v) => { await api("/v1/erp/credentials", { method: "PUT", body: { erp: "hubsoft", ...v } }); toast("Credenciais salvas (cifradas)."); route(); },
  });
}

// ------------------------------------------------------------------ administração
function renderAdminLogin() {
  const input = el("input", { id: "admin-token", type: "password", autocomplete: "off" });
  const err = el("p", { class: "field-error", role: "alert" });
  const btn = el("button", { class: "btn btn-gold", type: "submit" }, "Entrar");
  const form = el("form", { novalidate: true }, el("label", { for: "admin-token" }, "Token de administração (ADMIN_TOKEN)"), input, err, btn);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    adminToken = input.value.trim();
    withBusy(btn, async () => {
      try { await renderAdmin(); } catch (e2) { adminToken = null; err.textContent = e2.status === 403 ? "Token inválido." : e2.message; }
    });
  });
  $("#admin-body").replaceChildren(form);
  input.focus();
}

async function renderAdmin() {
  const providers = await api("/v1/admin/providers", { admin: true });
  const create = el("button", { class: "btn", type: "button" }, "Cadastrar provedor");
  create.addEventListener("click", () => formDialog({
    title: "Cadastrar provedor",
    fields: [{ name: "name", label: "Razão social", required: true, span: true }, { name: "cnpj", label: "CNPJ", required: true, minlength: 14, placeholder: "00.000.000/0001-00" }],
    submitLabel: "Cadastrar",
    onSubmit: async (v) => {
      const r = await api("/v1/admin/providers", { method: "POST", body: v, admin: true });
      setTimeout(() => secretDialog(`Provedor nº ${r.provider_id} cadastrado`, r.api_key, el("p", { class: "note" }, "Envie a chave ao provedor por canal seguro.")), 0);
      renderAdmin();
    },
  }));
  $("#admin-body").replaceChildren(el("div", { class: "card" },
    el("div", { class: "card-head" }, el("h2", {}, "Provedores"), create),
    table(["#", "Razão social", "CNPJ", "Situação", ""],
      providers.length ? providers.map((p) => el("tr", {},
        el("td", {}, p.id), el("td", {}, p.name), el("td", {}, p.cnpj), el("td", {}, p.active ? badge("Ativo", "ok") : badge("Desativado", "bad")),
        el("td", { class: "actions" },
          p.active ? el("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: async () => {
            try { const r = await api(`/v1/admin/providers/${p.id}/keys`, { method: "POST", body: { label: "emitida pelo administrador" }, admin: true }); secretDialog(`Nova chave · ${p.name}`, r.api_key); } catch (e) { toast(e.message, true); }
          } }, "Emitir chave") : null,
          p.active ? el("button", { class: "btn btn-danger btn-sm", type: "button", onclick: () => confirmDialog(
            `Desativar ${p.name}?`, "Todas as chaves do provedor deixam de funcionar. Os dados e a trilha são preservados.", "Desativar",
            async () => { await api(`/v1/admin/providers/${p.id}/deactivate`, { method: "POST", admin: true }); toast("Provedor desativado."); renderAdmin(); }) }, "Desativar") : null)))
        : [emptyRow(5, "Nenhum provedor cadastrado", "")])));
}

// ------------------------------------------------------------------ inicialização
function show(which) {
  for (const id of ["login", "admin", "app"]) $(`#${id}`).hidden = id !== which;
}

/** Valida e grava o endereço do servidor digitado no login dos aplicativos. */
function useServerField() {
  const url = normalizeServer($("#server-url").value);
  if (!url) {
    $("#login-error").textContent = "Informe o endereço do servidor, por exemplo https://cerne.seuprovedor.com.br";
    $("#server-url").focus();
    return false;
  }
  $("#server-url").value = url;
  server.set(url);
  return true;
}

function logout(msg) {
  store.clear();
  state.me = null;
  show("login");
  $("#login-error").textContent = msg || "";
  $("#api-key").value = "";
}

async function enterApp() {
  state.me = await api("/v1/me");
  $("#provider-name").textContent = state.me.name;
  show("app");
  if (!location.hash) location.hash = "#/visao-geral";
  else route();
}

document.addEventListener("DOMContentLoaded", async () => {
  buildNav();
  window.addEventListener("hashchange", route);
  $("#menu-btn").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  $("#logout").addEventListener("click", () => logout());
  $("#go-admin").addEventListener("click", () => {
    if (IS_APP && !useServerField()) return;
    show("admin"); renderAdminLogin();
  });
  $("#leave-admin").addEventListener("click", () => { adminToken = null; show("login"); });
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const key = $("#api-key").value.trim();
    const err = $("#login-error");
    if (IS_APP && !useServerField()) return;
    if (!key) { err.textContent = "Informe a chave de API."; $("#api-key").focus(); return; }
    store.setKey(key, $("#remember").checked);
    const btn = e.submitter;
    await withBusy(btn, async () => {
      try { err.textContent = ""; await enterApp(); } catch (e2) {
        if (e2.status !== 401) err.textContent = e2.message;
        else err.textContent = "Chave inválida ou revogada.";
      }
    });
  });
  if (IS_APP) {
    $("#server-field").hidden = false;
    $("#server-url").value = server.url;
  } else if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  memKey = store.key;
  if (memKey && (!IS_APP || server.url)) {
    try { await enterApp(); } catch { /* logout() já mostrou o login */ }
  } else show("login");
});
