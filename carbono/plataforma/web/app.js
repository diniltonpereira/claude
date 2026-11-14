"use strict";
/* Painel do CarbonMG. Consome a API REST da própria plataforma (mesma origem). */

const estado = { uid: null, eu: null, fatores: null };
const $ = (sel) => document.querySelector(sel);
const conteudo = $("#conteudo");

// ---------------------------------------------------------------- utilidades
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
function esc(v) { return v == null ? "" : String(v).replace(/[&<>"']/g, (c) => ESC[c]); }
const nf = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });
const t = (v) => nf.format(Number(v || 0));
const data = (iso) => iso ? new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "";
const dataCurta = (iso) => new Date(String(iso).slice(0, 10) + "T12:00:00").toLocaleDateString("pt-BR");
const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];

function lerSessao() { try { return localStorage.getItem("carbono.uid"); } catch { return null; } }
function gravarSessao(v) { try { v ? localStorage.setItem("carbono.uid", v) : localStorage.removeItem("carbono.uid"); } catch { /* sem armazenamento */ } }

/** Resposta de uma tela que o usuário já deixou: é descartada para não sobrescrever a tela atual. */
class Obsoleta extends Error {}

function cabecalhos(json = true) {
  const cab = json ? { "Content-Type": "application/json" } : {};
  if (estado.uid) cab["X-Usuario-Id"] = estado.uid;
  return cab;
}

async function lerErro(r) {
  const json = await r.json().catch(() => ({}));
  let msg = json.erro || "";
  if (!msg && Array.isArray(json.detail)) msg = json.detail.map((d) => `${d.loc.slice(-1)[0]}: ${d.msg}`).join("; ");
  if (!msg) msg = typeof json.detail === "string" ? json.detail : `Erro ${r.status}`;
  const e = new Error(msg); e.status = r.status; return e;
}

async function api(metodo, caminho, corpo) {
  const tela = location.hash;
  const r = await fetch(caminho, { method: metodo, headers: cabecalhos(), body: corpo ? JSON.stringify(corpo) : undefined });
  if (!r.ok) throw await lerErro(r);
  const json = await r.json();
  if (metodo === "GET" && location.hash !== tela) throw new Obsoleta();
  return json;
}

/** Envia um arquivo de comprovação e devolve o id do anexo. */
async function enviarArquivo(arquivo) {
  const fd = new FormData();
  fd.append("arquivo", arquivo);
  const r = await fetch("/anexos", { method: "POST", headers: cabecalhos(false), body: fd });
  if (!r.ok) throw await lerErro(r);
  return (await r.json()).id;
}

/** Baixa um recurso autenticado (anexo, planilha). */
async function baixar(caminho, nome) {
  const r = await fetch(caminho, { headers: cabecalhos(false) });
  if (!r.ok) throw await lerErro(r);
  const url = URL.createObjectURL(await r.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: nome });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

/** Abre o relatório HTML autenticado numa nova aba. */
async function abrirRelatorio(caminho) {
  const aba = window.open("", "_blank");
  const r = await fetch(caminho, { headers: cabecalhos(false) });
  if (!r.ok) { aba?.close(); throw await lerErro(r); }
  const url = URL.createObjectURL(new Blob([await r.text()], { type: "text/html" }));
  if (aba) aba.location = url; else location.href = url;
}

let avisoTimer;
function avisar(msg, erro = false) {
  const el = $("#aviso");
  el.textContent = msg;
  el.className = "aviso visivel" + (erro ? " erro" : "");
  clearTimeout(avisoTimer);
  avisoTimer = setTimeout(() => (el.className = "aviso"), erro ? 7000 : 3500);
}

function confirmar(titulo, texto, rotuloSim) {
  const d = $("#confirmacao");
  $("#confirmacao-titulo").textContent = titulo;
  $("#confirmacao-texto").textContent = texto;
  $("#confirmacao-sim").textContent = rotuloSim;
  d.returnValue = "";
  d.showModal();
  return new Promise((ok) => d.addEventListener("close", () => ok(d.returnValue === "sim"), { once: true }));
}

function dadosDe(form) {
  const o = {};
  for (const el of form.elements) {
    if (!el.name || el.type === "file" || el.tagName === "BUTTON") continue;
    if (el.type === "checkbox") o[el.name] = el.checked;
    else if (el.value === "") continue;
    else if (el.type === "number") o[el.name] = Number(el.value);
    else o[el.name] = el.value;
  }
  return o;
}

/** Liga um formulário a uma ação assíncrona com estado de envio e mensagem de erro no lugar. */
function aoEnviar(form, acao) {
  if (!form) return;
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const botoes = form.querySelectorAll("button[type=submit]");
    const erro = form.querySelector(".erro-campo");
    if (erro) erro.textContent = "";
    botoes.forEach((b) => (b.disabled = true));
    try { await acao(dadosDe(form), form); }
    catch (e) {
      if (e instanceof Obsoleta) return;
      if (erro) erro.textContent = e.message; else avisar(e.message, true);
    } finally { botoes.forEach((b) => (b.disabled = false)); }
  });
}

/** Lê o arquivo escolhido num campo do formulário e envia, se houver. */
async function anexoDoCampo(form, nome = "arquivo") {
  const campo = form.elements[nome];
  return campo && campo.files && campo.files[0] ? enviarArquivo(campo.files[0]) : null;
}

const ROTULO_STATUS = {
  rascunho: "Em elaboração", submetido: "Aguardando verificação", em_verificacao: "Em verificação",
  verificado: "Aguardando aprovação", aprovado: "Aprovado", devolvido: "Devolvido",
};
const ROTULO_PAPEL = { elaborador: "Gestão do órgão", verificador: "Verificação (organismo acreditado)",
  moderador: "Moderação e aprovação", registrador: "Administração", fiscal: "Fiscal de contrato",
  fornecedor: "Terceirizada", auditor: "Auditoria (somente leitura)" };
const ROTULO_TIPO = { orgao_publico: "Órgão público", ppp: "PPP", concessao: "Concessão",
  concessao_florestal: "Concessão florestal", empresa: "Empresa" };
const ROTULO_FAIXA = { nao_regulado: "Não regulado", regulado_monitoramento: "Monitoramento e relato",
  regulado_conciliacao: "Monitoramento, relato e conciliação" };
const ROTULO_ATIVO = { CBE: "CBE", CRVE: "CRVE", VOLUNTARIO: "Crédito voluntário" };
const ROTULO_FINALIDADE = { compensacao_voluntaria: "Compensação voluntária", conciliacao_sbce: "Conciliação SBCE",
  invalidacao: "Invalidação" };
const ROTULO_NATUREZA = { unidade: "Unidade", instalacao: "Instalação", fonte_movel: "Fonte móvel" };
const ROTULO_MES = { lancado: "Lançado", lancado_pelo_fiscal: "Lançado pelo fiscal", pendente_comprovacao: "Sem anexo",
  em_prazo: "No prazo", inadimplente: "Inadimplente", sem_lancamento: "Sem lançamento" };
const situacao = (s) => `<span class="situacao ${esc(s)}">${esc(ROTULO_STATUS[s] || s)}</span>`;
const papel = () => estado.eu?.usuario.papel;

// ---------------------------------------------------------------- moldura
function menu() {
  const p = papel();
  const itens = [["inicio", "Início"]];
  if (p === "elaborador" || p === "fiscal") itens.push(["painel", "Painel"]);
  if (["elaborador", "verificador", "moderador", "registrador", "auditor"].includes(p))
    itens.push(["inventarios", p === "elaborador" ? "Inventários" : "Inventários e fila"]);
  if (p === "elaborador") itens.push(["unidades", "Unidades e instalações"]);
  if (["elaborador", "fiscal", "fornecedor", "registrador", "auditor"].includes(p))
    itens.push(["terceirizadas", p === "fornecedor" ? "Meus contratos" : "Terceirizadas"]);
  if (["elaborador", "registrador", "fornecedor"].includes(p)) itens.push(["registro", "Créditos"]);
  if (p === "registrador") itens.push(["cadastros", "Cadastros e fatores"]);
  if (p === "registrador" || p === "auditor") itens.push(["auditoria", "Trilha de auditoria"]);
  itens.push(["transparencia", "Transparência"]);
  const atual = location.hash.split("?")[0].split("/")[1] || "inicio";
  const ativo = { contratos: "terceirizadas", verificar: "transparencia" }[atual] || atual;
  $("#menu").innerHTML = itens.map(([r, n]) =>
    `<a href="#/${r}"${ativo === r ? ' aria-current="page"' : ""}>${esc(n)}</a>`).join("")
    + `<p class="rodape">Lei 15.042/2024 · GHG Protocol · ISO 14064-1</p>`;
  const s = $("#sessao");
  if (estado.eu) {
    const u = estado.eu.usuario;
    s.innerHTML = `<span><strong>${esc(u.nome)}</strong> <span class="papel">· ${esc(ROTULO_PAPEL[u.papel])}${estado.eu.organizacao ? " · " + esc(estado.eu.organizacao.nome) : ""}</span></span>
      <button class="botao secundario" id="sair">Sair</button>`;
    $("#sair").onclick = () => { gravarSessao(null); estado.uid = null; estado.eu = null; location.hash = "#/entrar"; };
  } else {
    s.innerHTML = `<a href="#/entrar">Entrar</a>`;
  }
}

// ---------------------------------------------------------------- entrada e início
async function telaEntrar() {
  conteudo.innerHTML = `<section class="entrada bloco">
    <h1>Entrar</h1>
    <p class="introducao">No piloto, o acesso é pelo número de usuário fornecido pela administração. Na versão de produção, servidores entram com gov.br e terceirizadas com e-mail, senha e verificação em duas etapas.</p>
    <form id="f-entrar" class="grade">
      <label class="largo">Número de usuário<input name="uid" type="number" min="1" required autofocus></label>
      <div class="acoes largo"><button type="submit" class="botao">Entrar</button></div>
      <p class="erro-campo largo"></p>
    </form></section>`;
  aoEnviar($("#f-entrar"), async ({ uid }) => {
    estado.uid = String(uid);
    try { estado.eu = await api("GET", "/eu"); }
    catch (e) { estado.uid = null; throw new Error(e.status === 401 ? "Usuário não encontrado. Confira o número com a administração." : e.message); }
    gravarSessao(estado.uid);
    location.hash = "#/inicio";
  });
}

async function telaInicio() {
  const cal = await api("GET", "/sbce/calendario");
  const nomes = { I: "Regulamentação", II: "Preparação do relato", III: "Plano e relato", IV: "1º Plano de Alocação", V: "Implementação plena" };
  let resumo = "";
  const p = papel();
  if (p && ["elaborador", "verificador", "moderador"].includes(p)) {
    const invs = await api("GET", "/inventarios");
    const pend = invs.filter((i) => ({ elaborador: ["rascunho", "devolvido"], verificador: ["submetido", "em_verificacao"],
      moderador: ["verificado"] }[p] || []).includes(i.status));
    resumo = `<section class="bloco"><h2>O que espera por você</h2>
      ${pend.length ? `<div class="rolagem"><table><thead><tr><th>Organização</th><th>Ano</th><th>Situação</th></tr></thead><tbody>
        ${pend.map((i) => `<tr class="clicavel" data-ir="#/inventarios/${i.id}"><td><a href="#/inventarios/${i.id}">${esc(i.organizacao)}</a></td><td>${i.ano}</td><td>${situacao(i.status)}</td></tr>`).join("")}
      </tbody></table></div>` : `<p class="vazio">Nenhum inventário aguarda sua ação.</p>`}</section>`;
  }
  if (p === "fiscal" || p === "fornecedor") {
    const cts = await api("GET", "/contratos");
    resumo = `<section class="bloco"><h2>${p === "fiscal" ? "Contratos que você fiscaliza" : "Seus contratos"}</h2>${tabelaContratos(cts)}</section>`;
  }
  conteudo.innerHTML = `<h1>${estado.eu ? "Olá, " + esc(estado.eu.usuario.nome.split(" ")[0]) : "CarbonMG"}</h1>
    <p class="introducao">Inventário de emissões, gestão de terceirizadas e compensação com créditos certificados por terceiros. A plataforma organiza e comprova; não certifica, não verifica e não emite créditos.</p>
    ${resumo}
    <section class="bloco"><h2>Onde estamos na implantação do SBCE</h2>
      <ol class="fases">${cal.fases.map((f) => `<li class="${f.numero === cal.fase_atual ? "agora" : ""}">
        <strong>Fase ${esc(f.numero)}: ${esc(nomes[f.numero])}</strong><span>${
          f.numero === "V" ? "Ao fim do 1º Plano de Alocação"
          : f.fim ? `${dataCurta(f.inicio)} a ${dataCurta(f.fim)}`
          : `A partir de ${dataCurta(f.inicio)}, pela vigência do plano`}</span></li>`).join("")}</ol>
      <p class="memoria" style="margin-top:1rem">Art. 50 da Lei 15.042/2024, considerando a prorrogação da fase I. Plano de monitoramento e relato passam a ser exigidos na fase III; a conciliação, na fase IV.</p>
    </section>
    ${estado.eu ? "" : `<p><a class="botao" href="#/entrar">Entrar no painel</a></p>`}`;
}

// ---------------------------------------------------------------- painel do gestor
async function telaPainel() {
  const org = estado.eu.organizacao;
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const ano = Number(params.get("ano")) || new Date().getFullYear() - 1;
  const p = await api("GET", `/organizacoes/${org.id}/painel?ano=${ano}`);
  const anos = [ano - 2, ano - 1, ano, ano + 1].filter((a) => a <= new Date().getFullYear());
  const seletor = `<div class="acoes" role="group" aria-label="Ano">${anos.map((a) =>
    `<a class="botao ${a === ano ? "" : "secundario"}" href="#/painel?ano=${a}">${a}</a>`).join("")}</div>`;
  if (!p.inventario) {
    conteudo.innerHTML = `<h1>Painel ${ano}</h1>${seletor}<section class="bloco" style="margin-top:1rem">
      <p class="vazio">Não há inventário de ${ano}. ${papel() === "elaborador" ? `<a href="#/inventarios">Crie o inventário</a> para ver o painel.` : ""}</p></section>`;
    return;
  }
  const tot = p.totais;
  const maxMes = Math.max(...Object.values(p.por_mes), 1);
  const meta = p.meta;
  conteudo.innerHTML = `<h1>Painel ${ano}</h1>
    <p class="introducao">${esc(p.organizacao)} · inventário ${situacao(p.inventario.status)} · só entram lançamentos com comprovante.</p>
    ${seletor}
    <section class="bloco" style="margin-top:1rem"><dl class="cifra">
      <div><dt>Emissões totais</dt><dd>${t(tot.total)} <small>tCO2e</small></dd></div>
      <div><dt>Em relação a ${ano - 1}</dt><dd>${p.variacao_anual_percentual == null ? "—" : (p.variacao_anual_percentual > 0 ? "+" : "") + t(p.variacao_anual_percentual) + "%"}</dd></div>
      <div><dt>Compensado</dt><dd>${t(p.percentual_compensado)}%</dd></div>
      <div><dt>Emissões evitadas, à parte</dt><dd>${t(tot.emissoes_evitadas)} <small>tCO2e</small></dd></div>
    </dl>
    <div class="acoes" style="margin-top:1rem"><a class="botao secundario" href="#/inventarios/${p.inventario.id}">Abrir inventário</a></div></section>
    ${p.alertas.length ? `<section class="bloco"><h2>Alertas</h2><ul class="alertas">${p.alertas.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></section>` : ""}
    <div class="colunas">
      <section class="bloco"><h2>Emissões por mês</h2>
        <div class="barras" role="img" aria-label="Emissões por mês em tCO2e">${Object.entries(p.por_mes).map(([m, v]) =>
          `<div class="barra"><span style="height:${(v / maxMes) * 100}%" title="${MESES[m - 1]}: ${t(v)} tCO2e"></span><small>${MESES[m - 1]}</small></div>`).join("")}</div>
        ${p.sem_mes ? `<p class="memoria">${t(p.sem_mes)} tCO2e lançadas sem mês (anuais).</p>` : ""}
        <details><summary>Ver em tabela</summary><table><tbody>${Object.entries(p.por_mes).map(([m, v]) => `<tr><td>${MESES[m - 1]}</td><td class="num">${t(v)}</td></tr>`).join("")}</tbody></table></details>
      </section>
      <section class="bloco"><h2>Meta de redução</h2>
        ${meta && meta.progresso_percentual != null ? `<p>Reduzir ${t(meta.reducao_meta)}% em relação a ${meta.ano_base} até ${meta.ano_alvo}.</p>
          <div class="progresso" role="progressbar" aria-label="Progresso da meta" aria-valuenow="${meta.progresso_percentual}" aria-valuemin="0" aria-valuemax="100"><span style="width:${meta.progresso_percentual}%"></span></div>
          <p class="memoria">Redução atual: ${t(meta.reducao_atual_percentual)}% (${t(meta.base_tco2e)} → ${t(tot.total)} tCO2e).</p>`
          : meta ? `<p class="vazio">${esc(meta.aviso)}</p>` : `<p class="vazio">Nenhuma meta definida.</p>`}
        ${papel() === "elaborador" ? `<form id="f-meta" class="grade" style="margin-top:1rem">
          <label>Ano-base<input name="ano_base" type="number" value="${ano - 1}" required></label>
          <label>Ano-alvo<input name="ano_alvo" type="number" value="${ano + 1}" required></label>
          <label>Redução (%)<input name="reducao_percentual" type="number" min="1" max="100" value="10" required></label>
          <div class="acoes largo"><button type="submit" class="botao secundario">Definir meta</button></div><p class="erro-campo largo"></p></form>` : ""}
      </section>
    </div>
    <section class="bloco"><h2>Unidades que mais emitem</h2>${p.ranking_unidades.length ? `<div class="rolagem"><table>
      <thead><tr><th>Unidade</th><th class="num">${ano} (tCO2e)</th><th class="num">${ano - 1}</th></tr></thead><tbody>
      ${p.ranking_unidades.map((r) => `<tr><td>${esc(r.unidade)}</td><td class="num">${t(r.tco2e)}</td><td class="num">${t(r.ano_anterior)}</td></tr>`).join("")}
      </tbody></table></div>` : `<p class="vazio">Sem lançamentos comprovados.</p>`}</section>`;
  aoEnviar($("#f-meta"), async (d) => {
    await api("POST", `/organizacoes/${org.id}/metas`, d); avisar("Meta definida."); telaPainel();
  });
}

// ---------------------------------------------------------------- inventários
async function telaInventarios() {
  const invs = await api("GET", "/inventarios");
  const elab = papel() === "elaborador";
  conteudo.innerHTML = `<h1>${elab ? "Inventários" : "Inventários e fila"}</h1>
    <p class="introducao">${elab ? "Um inventário por ano. Lance as atividades com o comprovante, submeta à verificação e acompanhe até a aprovação."
      : "Inventários que aguardam sua análise ou que você pode consultar."}</p>
    ${elab ? `<section class="bloco"><h2>Novo inventário</h2><form id="f-inv" class="grade">
      <label>Ano de referência<input name="ano" type="number" min="2000" max="2100" value="${new Date().getFullYear() - 1}" required></label>
      <label>Potenciais de aquecimento (GWP)<select name="gwp_conjunto"><option value="AR5">IPCC AR5</option><option value="AR6">IPCC AR6</option></select></label>
      <div class="acoes"><button type="submit" class="botao">Criar inventário</button></div>
      <p class="erro-campo largo"></p></form></section>` : ""}
    <section class="bloco">${invs.length ? `<div class="rolagem"><table>
      <thead><tr><th>Organização</th><th>Ano</th><th>Situação</th><th class="num">Emissões comprovadas (tCO2e)</th></tr></thead><tbody>
      ${invs.map((i) => `<tr class="clicavel" data-ir="#/inventarios/${i.id}"><td><a href="#/inventarios/${i.id}">${esc(i.organizacao)}</a><br><span class="memoria">${esc(ROTULO_TIPO[i.tipo_organizacao])}</span></td>
        <td>${i.ano}</td><td>${situacao(i.status)}</td><td class="num">${t(i.total_tco2e)}</td></tr>`).join("")}
      </tbody></table></div>` : `<p class="vazio">${elab ? "Nenhum inventário ainda. Crie o primeiro acima." : "Nada na fila por enquanto."}</p>`}</section>`;
  aoEnviar($("#f-inv"), async (d) => {
    const r = await api("POST", "/inventarios", { organizacao_id: estado.eu.organizacao.id, ...d });
    avisar("Inventário criado."); location.hash = `#/inventarios/${r.id}`;
  });
}

function trilha(inv) {
  const hist = inv.historico;
  const ultimo = (para) => [...hist].reverse().find((e) => e.para_status === para);
  const ordem = ["rascunho", "submetido", "em_verificacao", "verificado", "aprovado"];
  const pos = inv.status === "devolvido" ? -1 : ordem.indexOf(inv.status);
  const etapas = [["Elaboração", "submetido", 1], ["Verificação", "em_verificacao", 2],
    ["Conclusão da verificação", "verificado", 3], ["Aprovação", "aprovado", 4]];
  const itens = etapas.map(([nome, marco, i]) => {
    const ev = ultimo(marco);
    let classe = "";
    if (inv.status === "devolvido") classe = ev && hist.indexOf(ev) < hist.indexOf(ultimo("devolvido")) ? "feita" : "";
    else if (pos >= i) classe = "feita";
    else if (pos === i - 1) classe = "atual";
    const carimbo = classe === "feita" && ev ? `<span class="carimbo">${esc(ev.usuario_nome)}<br>${data(ev.em)}</span>` : "";
    return `<li class="${classe}"><strong>${nome}</strong>${carimbo}</li>`;
  });
  if (inv.status === "devolvido") {
    const ev = ultimo("devolvido");
    itens[ev.de_status === "verificado" ? 3 : 1] = `<li class="devolvida"><strong>Devolvido</strong><span class="carimbo vermelho">${esc(ev.usuario_nome)}<br>${data(ev.em)}</span></li>`;
  }
  return `<ol class="trilha" aria-label="Etapas do inventário">${itens.join("")}</ol>`;
}

function acoesDisponiveis(inv) {
  const u = estado.eu.usuario;
  const a = [];
  if (u.papel === "elaborador" && ["rascunho", "devolvido"].includes(inv.status)) a.push(["submeter", "Submeter à verificação"]);
  if (u.papel === "verificador" && inv.status === "submetido") a.push(["assumir", "Assumir a verificação"]);
  if (u.papel === "verificador" && inv.status === "em_verificacao" && inv.verificado_por === u.id)
    a.push(["verificar", "Concluir verificação"], ["devolver", "Devolver com parecer"]);
  if (u.papel === "moderador" && inv.status === "verificado") a.push(["aprovar", "Aprovar inventário"], ["devolver", "Devolver com parecer"]);
  return a;
}

async function telaInventario(id) {
  const inv = await api("GET", `/inventarios/${id}`);
  const u = estado.eu.usuario;
  const editavel = u.papel === "elaborador" && ["rascunho", "devolvido"].includes(inv.status);
  const acoes = acoesDisponiveis(inv);
  const devolucao = inv.status === "devolvido" ? [...inv.historico].reverse().find((e) => e.para_status === "devolvido") : null;
  const tot = inv.totais;
  if (!estado.fatores) estado.fatores = await api("GET", "/fatores");
  const instalacoes = editavel ? await api("GET", `/organizacoes/${inv.organizacao_id}/instalacoes`) : [];
  const pend = tot.pendentes_comprovacao;

  conteudo.innerHTML = `<p><a href="#/inventarios">Voltar aos inventários</a></p>
    <h1>Inventário ${inv.ano}</h1>
    <p class="introducao">${situacao(inv.status)} · GWP IPCC ${esc(inv.gwp_conjunto)}</p>
    ${trilha(inv)}
    ${devolucao ? `<blockquote class="parecer"><strong>Parecer da devolução:</strong> ${esc(devolucao.parecer)}</blockquote>` : ""}
    ${acoes.length ? `<section class="bloco"><h2>Sua ação</h2><form id="f-acao" class="grade">
      <label class="largo">Parecer <span class="ajuda">Obrigatório para devolver. Registrado no histórico.</span><textarea name="parecer"></textarea></label>
      <div class="acoes largo">${acoes.map(([v, n], i) => `<button type="submit" value="${v}" class="botao${v === "devolver" ? " perigo" : i ? " secundario" : ""}">${esc(n)}</button>`).join("")}</div>
      <p class="erro-campo largo"></p></form></section>` : ""}
    <section class="bloco"><h2>Emissões comprovadas</h2><dl class="cifra">
      <div><dt>Escopo 1</dt><dd>${t(tot.escopo1)} <small>tCO2e</small></dd></div>
      <div><dt>Escopo 2</dt><dd>${t(tot.escopo2)} <small>tCO2e</small></dd></div>
      <div><dt>Escopo 3</dt><dd>${t(tot.escopo3)} <small>tCO2e</small></dd></div>
      <div><dt>Remoções</dt><dd>${t(tot.remocoes)} <small>tCO2e</small></dd></div>
      <div><dt>Evitadas, à parte</dt><dd>${t(tot.emissoes_evitadas)} <small>tCO2e</small></dd></div></dl>
      ${pend.quantidade ? `<p class="erro-campo">${pend.quantidade} lançamento(s) sem comprovante (${t(pend.tco2e)} tCO2e) estão fora do total e do relatório final.</p>` : ""}
      <div class="acoes" style="margin-top:1rem">
        <button class="botao secundario" id="b-rel">Relatório para impressão (PDF)</button>
        <button class="botao secundario" id="b-xlsx">Planilha (Excel)</button>
        ${editavel ? `<button class="botao secundario" id="b-import">Importar emissões das terceirizadas</button>` : ""}
      </div></section>
    <div class="colunas">
      <div>
        <section class="bloco"><h2>Atividades</h2>${inv.atividades.length ? `<div class="rolagem"><table>
          <thead><tr><th>Atividade</th><th>Comprovante</th><th class="num">tCO2e</th></tr></thead><tbody>
          ${inv.atividades.map((a) => linhaAtividade(a, editavel)).join("")}</tbody></table></div>`
          : `<p class="vazio">Nenhuma atividade lançada.</p>`}</section>
        ${editavel ? formAtividade(instalacoes) : ""}
        <section class="bloco"><h2>Escopo 1 por instalação</h2>${inv.por_instalacao.length ? `<div class="rolagem"><table>
          <thead><tr><th>Instalação ou fonte</th><th class="num">Emissões</th><th class="num">Remoções</th></tr></thead><tbody>
          ${inv.por_instalacao.map((i) => `<tr><td>${esc(i.nome)}</td><td class="num">${t(i.escopo1)}</td><td class="num">${t(i.remocoes)}</td></tr>`).join("")}
          </tbody></table></div>` : `<p class="vazio">Nenhuma instalação cadastrada. Os limiares do SBCE valem por instalação ou fonte (art. 30).</p>`}</section>
      </div>
      <div>
        <section class="bloco"><h2>Enquadramento no SBCE</h2>
          <form id="f-enq" class="grade">
            <label class="opcao largo"><input type="checkbox" name="gera_creditos"> A organização gera créditos</label>
            <label class="opcao largo"><input type="checkbox" name="terras_publicas_ou_tradicionais"> Envolve terras públicas, indígenas ou tradicionais</label>
            <div class="acoes largo"><button type="submit" class="botao">Calcular enquadramento</button></div>
            <p class="erro-campo largo"></p></form>
          <div id="enq-resultado"></div></section>
        ${u.papel === "elaborador" && inv.status === "aprovado" ? formCompensacao(inv) : ""}
      </div>
    </div>`;

  ligarAcoesInventario(inv);
  if (editavel) ligarFormAtividade(id);
  aoEnviar($("#f-enq"), async (d) => mostrarEnquadramento(await api("POST", `/inventarios/${id}/enquadramento`, d)));
  if ($("#f-comp")) ligarCompensacao(inv);
}

function linhaAtividade(a, editavel) {
  const comp = a.contrato_id ? `<span class="memoria">Lançamentos da terceirizada</span>`
    : a.anexo_id ? `<button type="button" class="link" data-baixar="${a.anexo_id}" data-nome="${esc(a.anexo_nome)}">${esc(a.anexo_nome)}</button>`
    : editavel ? `<label class="anexar">Anexar comprovante<input type="file" data-anexar="${a.id}" accept=".pdf,.jpg,.jpeg,.png,.csv,.xlsx"></label>`
    : `<span class="faixa-regulado_conciliacao">Pendente</span>`;
  const rotulo = { remocao: " <strong>(remoção)</strong>", evitada: " <strong>(evitada, à parte)</strong>" }[a.natureza] || "";
  return `<tr><td>${esc(a.descricao)}${rotulo}${a.mes ? ` <span class="memoria">${MESES[a.mes - 1]}</span>` : ""}
      <div class="memoria">${esc(a.memoria)}</div>${a.evidencia ? `<div class="memoria">Referência: ${esc(a.evidencia)}</div>` : ""}
      ${editavel && !a.contrato_id ? `<button type="button" class="link perigo-texto" data-remover="${a.id}">Excluir</button>` : ""}</td>
    <td>${comp}</td><td class="num">${t(a.tco2e)}</td></tr>`;
}

function ligarAcoesInventario(inv) {
  const id = inv.id;
  const f = $("#f-acao");
  if (f) {
    f.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => (f.dataset.acao = b.value)));
    aoEnviar(f, async ({ parecer }) => {
      const r = await api("POST", `/inventarios/${id}/transicoes`, { acao: f.dataset.acao, parecer });
      avisar(`Inventário agora: ${ROTULO_STATUS[r.status]}.`); telaInventario(id);
    });
  }
  $("#b-rel").onclick = () => abrirRelatorio(`/inventarios/${id}/relatorio.html`).catch((e) => avisar(e.message, true));
  $("#b-xlsx").onclick = () => baixar(`/inventarios/${id}/relatorio.xlsx`, `inventario-${inv.ano}.xlsx`).catch((e) => avisar(e.message, true));
  const imp = $("#b-import");
  if (imp) imp.onclick = async () => {
    try {
      const r = await api("POST", `/inventarios/${id}/importar-terceirizadas`);
      avisar(`${r.atividades_criadas} contrato(s) importado(s). ${r.lancamentos_pendentes_ignorados} lançamento(s) sem anexo ficaram de fora.`);
      telaInventario(id);
    } catch (e) { avisar(e.message, true); }
  };
  conteudo.querySelectorAll("[data-baixar]").forEach((b) => (b.onclick = () =>
    baixar(`/anexos/${b.dataset.baixar}`, b.dataset.nome).catch((e) => avisar(e.message, true))));
  conteudo.querySelectorAll("[data-anexar]").forEach((campo) => campo.addEventListener("change", async () => {
    try {
      const anexo = await enviarArquivo(campo.files[0]);
      await api("PATCH", `/atividades/${campo.dataset.anexar}`, { anexo_id: anexo });
      avisar("Comprovante anexado."); telaInventario(id);
    } catch (e) { avisar(e.message, true); }
  }));
  conteudo.querySelectorAll("[data-remover]").forEach((b) => (b.onclick = async () => {
    if (!(await confirmar("Excluir atividade?", "A exclusão fica registrada na trilha de auditoria.", "Excluir atividade"))) return;
    try { await api("DELETE", `/atividades/${b.dataset.remover}`); avisar("Atividade excluída."); telaInventario(id); }
    catch (e) { avisar(e.message, true); }
  }));
}

function formAtividade(instalacoes) {
  const f = estado.fatores.fatores;
  const gwp = estado.fatores.gwp.AR5 || {};
  return `<section class="bloco"><h2>Lançar atividade</h2><form id="f-atv" class="grade">
    <label class="largo">Descrição<input name="descricao" required placeholder="Ex.: Diesel da frota de coleta — outubro"></label>
    <label>Tipo<select name="modo">
      <option value="fator">Consumo (combustível, energia…)</option>
      <option value="gas">Massa de gás emitida</option>
      <option value="remocao">Remoção de CO2 (sumidouro)</option>
      <option value="evitada">Emissão evitada (solar, biogás…)</option></select></label>
    <label data-modo="fator evitada">Item<select name="fator">${f.map((x) => `<option value="${esc(x.codigo)}">${esc(x.descricao)} (${esc(x.unidade)})</option>`).join("")}</select></label>
    <label data-modo="gas" hidden>Gás<select name="gas">${Object.keys(gwp).map((g) => `<option>${esc(g)}</option>`).join("")}</select></label>
    <label data-modo="gas" hidden>Escopo<select name="escopo"><option value="1">1: emissão direta</option><option value="3">3: indireta</option></select></label>
    <label>Quantidade <span class="ajuda">Na unidade do item; gases em toneladas</span><input name="quantidade" type="number" step="any" min="0" required></label>
    <label>Mês<select name="mes"><option value="">Anual</option>${MESES.map((m, i) => `<option value="${i + 1}">${m}</option>`).join("")}</select></label>
    <label>Unidade ou instalação<select name="instalacao_id"><option value="">Sem vínculo</option>
      ${instalacoes.map((i) => `<option value="${i.id}">${esc(i.nome)}</option>`).join("")}</select></label>
    <label class="largo">Comprovante <span class="ajuda">NF, fatura, laudo ou foto (PDF, JPG, PNG, CSV ou XLSX, até 10 MB). Sem ele, o lançamento fica pendente e fora do total.</span><input type="file" name="arquivo" accept=".pdf,.jpg,.jpeg,.png,.csv,.xlsx"></label>
    <label class="largo">Referência <span class="ajuda">Número do processo, opcional</span><input name="evidencia"></label>
    <div class="acoes largo"><button type="submit" class="botao">Lançar atividade</button></div>
    <p class="erro-campo largo"></p></form></section>`;
}

function ligarFormAtividade(id) {
  const form = $("#f-atv");
  const modo = form.elements.modo;
  const sync = () => form.querySelectorAll("[data-modo]").forEach((el) => (el.hidden = !el.dataset.modo.split(" ").includes(modo.value)));
  modo.addEventListener("change", sync); sync();
  aoEnviar(form, async (d) => {
    const anexo = await anexoDoCampo(form);
    const corpo = { descricao: d.descricao, quantidade: d.quantidade, evidencia: d.evidencia || null,
      instalacao_id: d.instalacao_id ? Number(d.instalacao_id) : null, mes: d.mes ? Number(d.mes) : null, anexo_id: anexo };
    if (d.modo === "fator" || d.modo === "evitada") { corpo.fator = d.fator; corpo.evitada = d.modo === "evitada"; }
    else if (d.modo === "gas") { corpo.gas = d.gas; corpo.escopo = Number(d.escopo); }
    else { corpo.gas = "CO2"; corpo.remocao = true; }
    await api("POST", `/inventarios/${id}/atividades`, corpo);
    avisar(anexo ? "Atividade lançada." : "Atividade lançada sem comprovante: fica pendente."); telaInventario(id);
  });
}

function mostrarEnquadramento(r) {
  $("#enq-resultado").innerHTML = `
    <p style="margin-top:1rem"><strong class="faixa-${esc(r.faixa_sbce)}">${esc(ROTULO_FAIXA[r.faixa_sbce])}</strong><br>
      <span class="memoria">${esc(r.fase_sbce)}</span></p>
    ${r.instalacoes?.length ? `<div class="rolagem"><table><thead><tr><th>Unidade</th><th class="num">tCO2e</th><th>Faixa</th></tr></thead><tbody>
      ${r.instalacoes.map((i) => `<tr><td>${esc(i.nome)}</td><td class="num">${t(i.escopo1)}</td><td class="faixa-${esc(i.faixa_sbce)}">${esc(ROTULO_FAIXA[i.faixa_sbce])}</td></tr>`).join("")}
    </tbody></table></div>` : ""}
    ${r.alertas.length ? `<ul class="alertas" style="margin-top:1rem">${r.alertas.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>` : ""}
    <h3 style="margin-top:1rem">Obrigações e cuidados</h3>
    <ul class="obrigacoes">${r.obrigacoes.map((o) => `<li><strong>${esc(o.titulo)}</strong>
      ${o.exigivel ? "" : `<span class="marca-futura">exigível em fase futura</span>`}
      ${o.conferir ? `<span class="marca-conferir">conferir</span>` : ""}
      <div>${esc(o.detalhe)}</div><span class="fundamento">${esc(o.fundamento)}</span></li>`).join("")}</ul>`;
}

function formCompensacao(inv) {
  return `<section class="bloco"><h2>Compensar emissões</h2>
    <p class="memoria">Os créditos usados são cancelados no registro em nome da organização (art. 45). Crédito Verra ou Gold Standard precisa ser aposentado antes no registro da certificadora. O cancelamento não pode ser desfeito.</p>
    ${inv.compensado || inv.conciliado ? `<p>Já cancelados para este inventário: ${t(inv.compensado)} em compensação voluntária e ${t(inv.conciliado)} em conciliação.</p>` : ""}
    <form id="f-comp" class="grade">
      <label class="largo">Finalidade<select name="finalidade">
        <option value="compensacao_voluntaria">Compensação voluntária</option>
        <option value="conciliacao_sbce">Conciliação no SBCE (art. 34)</option></select></label>
      <label data-fin="compensacao_voluntaria">Percentual a compensar<input name="percentual" type="number" min="1" max="100" value="100"></label>
      <label data-fin="compensacao_voluntaria">Escopos<select name="escopos"><option value="1,2">1 e 2</option><option value="1">Só 1</option><option value="1,2,3">1, 2 e 3</option></select></label>
      <label data-fin="conciliacao_sbce" hidden>Limite de CRVE na conciliação <span class="ajuda">Fração definida no Plano Nacional de Alocação</span><input name="limite_crve" type="number" min="0" max="1" step="0.01" value="0"></label>
      <div class="acoes largo"><button type="submit" class="botao secundario">Simular</button></div>
      <p class="erro-campo largo"></p></form>
    <div id="comp-resultado"></div></section>`;
}

function ligarCompensacao(inv) {
  const form = $("#f-comp");
  const sync = () => form.querySelectorAll("[data-fin]").forEach((el) => (el.hidden = el.dataset.fin !== form.elements.finalidade.value));
  form.elements.finalidade.addEventListener("change", () => { sync(); $("#comp-resultado").innerHTML = ""; });
  sync();
  const corpoDe = (d) => ({ finalidade: d.finalidade, percentual: d.percentual, limite_crve: d.limite_crve || 0,
    escopos: d.escopos.split(",").map(Number) });
  aoEnviar(form, async (d) => {
    const corpo = corpoDe(d);
    const r = await api("POST", `/inventarios/${inv.id}/compensacoes`, { ...corpo, simular: true });
    const qtd = r.plano.reduce((s, p) => s + p.quantidade, 0);
    const externos = r.plano.filter((p) => p.exige_registro_externo);
    $("#comp-resultado").innerHTML = `<h3 style="margin-top:1rem">Plano</h3>
      <p>Base: ${t(r.emissoes_base_tco2e)} tCO2e. A compensar: <strong>${t(r.a_compensar)}</strong> créditos.</p>
      ${r.plano.length ? `<div class="rolagem"><table><thead><tr><th>Lote</th><th>Tipo</th><th>Safra</th><th class="num">Qtd.</th></tr></thead><tbody>
        ${r.plano.map((p) => `<tr><td class="mono">${esc(p.serial)}</td><td>${esc(ROTULO_ATIVO[p.tipo_ativo])}${p.certificadora ? " · " + esc(p.certificadora) : ""}</td><td>${p.safra}</td><td class="num">${t(p.quantidade)}</td></tr>`).join("")}
      </tbody></table></div>` : ""}
      ${r.lotes_ignorados.length ? `<ul class="alertas">${r.lotes_ignorados.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : ""}
      ${r.deficit ? `<p class="erro-campo">Faltam ${t(r.deficit)} créditos elegíveis na conta. Adquira créditos antes de compensar.</p>`
        : `<form id="f-cancelar" class="grade">
          ${externos.map((p) => `<label class="largo">Nº da aposentadoria na ${esc(p.certificadora)} para ${esc(p.serial)}
            <span class="ajuda">Aposente primeiro no registro da certificadora e informe o número aqui.</span>
            <input name="ref_${p.lote_id}" required></label>`).join("")}
          <div class="acoes largo"><button type="submit" class="botao perigo">Cancelar ${t(qtd)} créditos</button></div>
          <p class="erro-campo largo"></p></form>`}`;
    aoEnviar($("#f-cancelar"), async (dd) => {
      const ok = await confirmar(`Cancelar ${t(qtd)} créditos?`,
        `Os créditos serão cancelados no registro em nome de ${estado.eu.organizacao.nome}, para o inventário ${inv.ano}. Não é possível desfazer.`,
        `Cancelar ${t(qtd)} créditos`);
      if (!ok) return;
      const registros_externos = Object.fromEntries(externos.map((p) => [p.lote_id, dd[`ref_${p.lote_id}`]]));
      await api("POST", `/inventarios/${inv.id}/compensacoes`, { ...corpo, registros_externos,
        chave_idempotencia: `painel-${inv.id}-${Date.now()}` });
      avisar(`${t(qtd)} créditos cancelados.`); telaInventario(inv.id);
    });
  });
}

// ---------------------------------------------------------------- unidades
async function telaUnidades() {
  const org = estado.eu.organizacao;
  const lista = await api("GET", `/organizacoes/${org.id}/instalacoes`);
  const filhos = (pai) => lista.filter((i) => i.pai_id === pai);
  const arvore = (pai, nivel) => filhos(pai).map((i) => `<tr><td style="padding-left:${0.6 + nivel * 1.4}rem">${nivel ? "└ " : ""}${esc(i.nome)}</td>
      <td>${esc(ROTULO_NATUREZA[i.natureza])}</td><td>${esc(i.identificador || "")} ${esc(i.municipio_uf || "")}</td>
      <td>${i.residuos_neutralizacao ? "Resíduos com neutralização (art. 30, § 3º)" : i.agropecuaria_primaria ? "Agropecuária primária (art. 1º, § 2º)" : ""}</td></tr>`
    + arvore(i.id, nivel + 1)).join("");
  conteudo.innerHTML = `<h1>Unidades e instalações</h1>
    <p class="introducao">Monte a estrutura do órgão: secretarias e unidades, e abaixo delas prédios, instalações e frotas. O painel soma as emissões pela hierarquia. Para o SBCE, os limiares de 10 mil e 25 mil tCO2e por ano valem por instalação ou fonte (art. 30).</p>
    <section class="bloco">${lista.length ? `<div class="rolagem"><table><thead><tr><th>Nome</th><th>Tipo</th><th>Identificação</th><th>Situação especial</th></tr></thead>
      <tbody>${arvore(null, 0)}</tbody></table></div>` : `<p class="vazio">Nada cadastrado. Comece pelas secretarias.</p>`}</section>
    <section class="bloco"><h2>Cadastrar</h2><form id="f-inst" class="grade">
      <label>Nome<input name="nome" required placeholder="Ex.: Secretaria de Saúde"></label>
      <label>Tipo<select name="natureza"><option value="unidade">Unidade (secretaria, autarquia…)</option><option value="instalacao">Instalação (prédio, aterro, ETE…)</option><option value="fonte_movel">Fonte móvel (frota)</option></select></label>
      <label>Subordinada a<select name="pai_id"><option value="">Órgão (topo)</option>${lista.filter((i) => i.natureza !== "fonte_movel").map((i) => `<option value="${i.id}">${esc(i.nome)}</option>`).join("")}</select></label>
      <label>Identificação <span class="ajuda">Licença, CNES, CNPJ da filial…</span><input name="identificador"></label>
      <label>Município/UF<input name="municipio_uf"></label>
      <label class="opcao largo"><input type="checkbox" name="residuos_neutralizacao"> Trata resíduos ou efluentes com neutralização comprovada (art. 30, § 3º)</label>
      <label class="opcao largo"><input type="checkbox" name="agropecuaria_primaria"> Produção primária agropecuária (art. 1º, § 2º)</label>
      <div class="acoes largo"><button type="submit" class="botao">Cadastrar</button></div>
      <p class="erro-campo largo"></p></form></section>`;
  aoEnviar($("#f-inst"), async (d) => {
    await api("POST", `/organizacoes/${org.id}/instalacoes`, { ...d, pai_id: d.pai_id ? Number(d.pai_id) : null });
    avisar("Cadastrado."); telaUnidades();
  });
}

// ---------------------------------------------------------------- terceirizadas
function tabelaContratos(cts) {
  if (!cts.length) return `<p class="vazio">Nenhum contrato.</p>`;
  return `<div class="rolagem"><table><thead><tr><th>Contrato</th><th>Terceirizada</th><th>Órgão</th><th>Vigência</th><th>Score</th></tr></thead><tbody>
    ${cts.map((c) => `<tr class="clicavel" data-ir="#/contratos/${c.id}"><td><a href="#/contratos/${c.id}">${esc(c.numero)}</a><div class="memoria">${esc(c.objeto)}</div></td>
      <td>${esc(c.fornecedor)}</td><td>${esc(c.orgao)}</td><td>${dataCurta(c.vigencia_inicio)} a ${dataCurta(c.vigencia_fim)}</td>
      <td>${c.aceito_em ? (c.classe_atual ? `<span class="classe classe-${esc(c.classe_atual)}">${esc(c.classe_atual)}</span>` : "—") : `<span class="situacao rascunho">Convite pendente</span>`}</td></tr>`).join("")}
  </tbody></table></div>`;
}

async function telaTerceirizadas() {
  const cts = await api("GET", "/contratos");
  const orgao = ["elaborador", "fiscal"].includes(papel());
  conteudo.innerHTML = `<h1>${papel() === "fornecedor" ? "Meus contratos" : "Terceirizadas"}</h1>
    <p class="introducao">${papel() === "fornecedor"
      ? "Lance todo mês as emissões do serviço prestado, com a nota fiscal. Seu score e o selo dependem da regularidade, da redução e das ações registradas."
      : "Convide as empresas contratadas para lançar mensalmente as emissões do serviço. Elas entram no escopo 3 do inventário do órgão."}</p>
    <section class="bloco">${tabelaContratos(cts)}</section>
    ${orgao ? `<section class="bloco"><h2>Convidar terceirizada</h2><form id="f-contrato" class="grade">
      <label>CNPJ <span class="ajuda">14 dígitos</span><input name="fornecedor_cnpj" pattern="\\d{14}" inputmode="numeric" required></label>
      <label>Razão social<input name="fornecedor_nome" required></label>
      <label>Nº do contrato<input name="numero" required></label>
      <label>Objeto<input name="objeto" required placeholder="Ex.: Limpeza e conservação"></label>
      <label>Início da vigência<input name="vigencia_inicio" type="date" required></label>
      <label>Fim da vigência<input name="vigencia_fim" type="date" required></label>
      <label>Dia limite para lançar<input name="dia_limite" type="number" min="1" max="28" value="10"></label>
      <label class="opcao largo"><input type="checkbox" name="clausula_relato"> O contrato (ou aditivo) obriga a terceirizada a relatar emissões. Sem essa cláusula, atraso é só informativo.</label>
      <div class="acoes largo"><button type="submit" class="botao">Enviar convite</button></div>
      <p class="erro-campo largo"></p></form></section>` : ""}`;
  aoEnviar($("#f-contrato"), async (d) => {
    const r = await api("POST", "/contratos", { ...d, orgao_id: estado.eu.organizacao.id });
    avisar("Convite registrado. Cadastre o usuário da terceirizada para ela aceitar."); location.hash = `#/contratos/${r.id}`;
  });
}

function tabelaLancamentos(lista) {
  return `<div class="rolagem"><table>
    <thead><tr><th>Mês</th><th>Descrição</th><th>Comprovante</th><th class="num">tCO2e</th></tr></thead><tbody>
    ${lista.map((l) => `<tr><td>${MESES[l.mes - 1]}/${l.ano}</td><td>${esc(l.descricao)}<div class="memoria">${esc(l.memoria)} · por ${esc(l.lancado_por_nome)}</div></td>
      <td>${l.anexo_id ? `<button type="button" class="link" data-baixar="${l.anexo_id}" data-nome="comprovante">Baixar</button>` : `<span class="faixa-regulado_conciliacao">Pendente</span>`}</td>
      <td class="num">${t(l.tco2e)}</td></tr>`).join("")}</tbody></table></div>`;
}

async function telaContrato(id) {
  const ct = await api("GET", `/contratos/${id}`);
  const p = papel();
  const ehForn = p === "fornecedor", ehFiscal = p === "fiscal", ehOrgao = ["elaborador", "fiscal"].includes(p);
  const podeLancar = (ehForn && ct.aceito_em) || ehFiscal;
  if (podeLancar && !estado.fatores) estado.fatores = await api("GET", "/fatores");
  const sc = ct.score;
  const pontos = sc ? JSON.parse(sc.componentes).pontos : null;
  const hoje = new Date();
  conteudo.innerHTML = `<p><a href="#/terceirizadas">Voltar</a></p>
    <h1>Contrato ${esc(ct.numero)}</h1>
    <p class="introducao">${esc(ct.fornecedor)} · CNPJ <span class="mono">${esc(ct.fornecedor_cnpj)}</span> · contratante ${esc(ct.orgao)}<br>
      ${esc(ct.objeto)} · vigência ${dataCurta(ct.vigencia_inicio)} a ${dataCurta(ct.vigencia_fim)} · lançar até o dia ${ct.dia_limite}
      ${ct.clausula_relato ? "· com cláusula de relato" : "· sem cláusula de relato (informativo)"}</p>
    ${!ct.aceito_em ? `<section class="bloco"><p>${ehForn ? "Aceite o convite para começar a lançar." : "Aguardando a terceirizada aceitar o convite."}</p>
      ${ehForn ? `<button class="botao" id="b-aceite">Aceitar convite</button>` : ""}</section>` : ""}
    <div class="colunas">
      <section class="bloco"><h2>Situação mês a mês</h2>
        <ul class="meses">${ct.situacao_mensal.slice(-18).reverse().map((m) => `<li class="mes-${esc(m.situacao)}"><strong>${MESES[m.mes - 1]}/${m.ano}</strong><span>${esc(ROTULO_MES[m.situacao])}</span>${m.tco2e_comprovado ? `<small>${t(m.tco2e_comprovado)} t</small>` : ""}</li>`).join("") || `<li>Sem meses vencidos.</li>`}</ul>
      </section>
      <section class="bloco"><h2>Score de descarbonização</h2>
        ${sc ? `<p><span class="classe classe-${esc(sc.classe)} grande">${esc(sc.classe)}</span> <strong>${t(sc.score)}</strong> de 100 <span class="memoria">em ${String(sc.mes).padStart(2, "0")}/${sc.ano}</span></p>
          <dl class="componentes">
            <div><dt>Regularidade</dt><dd>${t(pontos.regularidade)} / 40</dd></div><div><dt>Redução</dt><dd>${t(pontos.reducao)} / 30</dd></div>
            <div><dt>Compensação</dt><dd>${t(pontos.compensacao)} / 20</dd></div><div><dt>Ações</dt><dd>${t(pontos.acoes)} / 10</dd></div></dl>`
          : `<p class="vazio">Score ainda não calculado.</p>`}
        <div class="acoes">${ct.aceito_em ? `<button class="botao secundario" id="b-score">Recalcular score</button>` : ""}
          ${ehOrgao && ct.aceito_em ? `<button class="botao" id="b-selo">Emitir selo</button>` : ""}</div>
        <p class="memoria">A: 80 a 100 · B: 60 a 79 · C: 40 a 59 · D: até 39 (sem selo). O score é informativo; só vira critério de licitação se o edital previr.</p>
        ${ct.selos.length ? `<h3>Selos</h3><ul class="selos">${ct.selos.map((s) => `<li>
          <img src="/publico/selos/${encodeURIComponent(s.codigo)}/qr.svg" alt="QR Code do selo ${esc(s.codigo)}" width="96" height="96">
          <div><strong>Selo de Gestão de Emissões — Nível ${esc(s.classe)}</strong><br>
          <span class="memoria">Emitido em ${dataCurta(s.emitido_em)}, válido até ${dataCurta(s.valido_ate)}</span><br>
          <a href="#/verificar/selo/${encodeURIComponent(s.codigo)}">Página pública de verificação</a></div></li>`).join("")}</ul>` : ""}
      </section>
    </div>
    ${podeLancar ? `<section class="bloco"><h2>Lançar emissões do mês</h2><form id="f-lanc" class="grade">
      <label>Mês<select name="mes">${MESES.map((m, i) => `<option value="${i + 1}"${i === (hoje.getMonth() + 11) % 12 ? " selected" : ""}>${m}</option>`).join("")}</select></label>
      <label>Ano<input name="ano" type="number" value="${hoje.getMonth() === 0 ? hoje.getFullYear() - 1 : hoje.getFullYear()}" required></label>
      <label>Item<select name="fator">${estado.fatores.fatores.map((x) => `<option value="${esc(x.codigo)}">${esc(x.descricao)} (${esc(x.unidade)})</option>`).join("")}</select></label>
      <label>Quantidade<input name="quantidade" type="number" step="any" min="0" required></label>
      <label class="largo">Descrição<input name="descricao" required placeholder="Ex.: Diesel dos veículos de apoio"></label>
      <label class="largo">Nota fiscal ou fatura <span class="ajuda">Sem anexo, o mês fica pendente e não conta para o score.</span><input type="file" name="arquivo" accept=".pdf,.jpg,.jpeg,.png"></label>
      ${ehFiscal ? `<p class="memoria largo">O lançamento ficará marcado como "lançado pelo fiscal".</p>` : ""}
      <div class="acoes largo"><button type="submit" class="botao">Lançar</button></div><p class="erro-campo largo"></p></form></section>
    <section class="bloco"><h2>Registrar ação de descarbonização</h2><form id="f-acao-forn" class="grade">
      <label>Data<input name="data" type="date" required></label>
      <label>Ação<input name="descricao" required placeholder="Ex.: Treinamento de condução econômica"></label>
      <label class="largo">Evidência <span class="ajuda">Obrigatória: lista de presença, foto, relatório</span><input type="file" name="arquivo" accept=".pdf,.jpg,.jpeg,.png" required></label>
      <div class="acoes largo"><button type="submit" class="botao secundario">Registrar ação</button></div><p class="erro-campo largo"></p></form></section>` : ""}
    <section class="bloco"><h2>Lançamentos</h2>${ct.lancamentos.length ? tabelaLancamentos(ct.lancamentos.slice(0, 12))
      + (ct.lancamentos.length > 12 ? `<details><summary>Ver os ${ct.lancamentos.length - 12} lançamentos anteriores</summary>${tabelaLancamentos(ct.lancamentos.slice(12))}</details>` : "")
      : `<p class="vazio">Nenhum lançamento.</p>`}</section>
    <section class="bloco"><h2>Notificações</h2>${ct.notificacoes.length ? `<ul class="obrigacoes">${ct.notificacoes.map((n) => `<li>
      <strong>${esc(n.assunto)}</strong> <span class="memoria">${esc({ lembrete: "Lembrete automático", alerta: "Alerta ao fiscal", notificacao_formal: "Notificação formal" }[n.tipo])} · ${data(n.emitida_em)}${n.prazo ? " · prazo " + dataCurta(n.prazo) : ""}</span>
      <div>${esc(n.texto)}</div></li>`).join("")}</ul>` : `<p class="vazio">Nenhuma notificação.</p>`}
      ${ehFiscal ? `<h3 style="margin-top:1rem">Notificar a terceirizada</h3><form id="f-notif" class="grade">
        <label class="largo">Assunto<input name="assunto" required></label>
        <label class="largo">Texto<textarea name="texto" required></textarea></label>
        <label>Prazo<input name="prazo" type="date"></label>
        <label>Anexo<input type="file" name="arquivo" accept=".pdf"></label>
        <div class="acoes largo"><button type="submit" class="botao">Enviar notificação formal</button></div><p class="erro-campo largo"></p></form>` : ""}
    </section>`;

  const recarregar = () => telaContrato(id);
  const aceite = $("#b-aceite");
  if (aceite) aceite.onclick = async () => { try { await api("POST", `/contratos/${id}/aceite`); avisar("Convite aceito."); recarregar(); } catch (e) { avisar(e.message, true); } };
  const bs = $("#b-score");
  if (bs) bs.onclick = async () => { try { const r = await api("POST", `/contratos/${id}/score`); avisar(`Score ${t(r.score)} (classe ${r.classe}).`); recarregar(); } catch (e) { avisar(e.message, true); } };
  const bselo = $("#b-selo");
  if (bselo) bselo.onclick = async () => { try { const r = await api("POST", `/contratos/${id}/selos`); avisar(`Selo nível ${r.classe} emitido.`); recarregar(); } catch (e) { avisar(e.message, true); } };
  conteudo.querySelectorAll("[data-baixar]").forEach((b) => (b.onclick = () => baixar(`/anexos/${b.dataset.baixar}`, b.dataset.nome).catch((e) => avisar(e.message, true))));
  aoEnviar($("#f-lanc"), async (d, form) => {
    const anexo = await anexoDoCampo(form);
    await api("POST", `/contratos/${id}/lancamentos`, { ...d, ano: Number(d.ano), mes: Number(d.mes), anexo_id: anexo });
    avisar(anexo ? "Lançamento registrado." : "Lançamento registrado sem anexo: fica pendente."); recarregar();
  });
  aoEnviar($("#f-acao-forn"), async (d, form) => {
    const anexo = await anexoDoCampo(form);
    await api("POST", `/contratos/${id}/acoes`, { ...d, anexo_id: anexo }); avisar("Ação registrada."); recarregar();
  });
  aoEnviar($("#f-notif"), async (d, form) => {
    const anexo = await anexoDoCampo(form);
    await api("POST", `/contratos/${id}/notificacoes`, { ...d, anexo_id: anexo }); avisar("Notificação registrada."); recarregar();
  });
}

// ---------------------------------------------------------------- créditos
async function telaRegistro() {
  const reg = papel() === "registrador";
  const contas = await api("GET", "/contas");
  let saldoHtml = "", lotes = [];
  if (estado.eu.conta_id) {
    const s = await api("GET", `/contas/${estado.eu.conta_id}/saldo`);
    lotes = s.lotes;
    saldoHtml = `<section class="bloco"><h2>Saldo da conta</h2><dl class="cifra"><div><dt>Créditos disponíveis</dt><dd>${t(s.total)} <small>tCO2e</small></dd></div></dl>
      ${lotes.length ? `<div class="rolagem" style="margin-top:1rem"><table><thead><tr><th>Serial</th><th>Tipo</th><th>Projeto</th><th>Safra</th><th class="num">Qtd.</th></tr></thead><tbody>
        ${lotes.map((l) => `<tr><td class="mono">${esc(l.serial)}</td><td>${esc(ROTULO_ATIVO[l.tipo_ativo])}</td><td>${esc(l.projeto)}</td><td>${l.safra}</td><td class="num">${t(l.quantidade)}</td></tr>`).join("")}
      </tbody></table></div>` : `<p class="vazio">A conta não tem créditos.</p>`}</section>`;
  }
  const opLotes = lotes.map((l) => `<option value="${l.lote_id}">${esc(l.serial)} (${t(l.quantidade)})</option>`).join("");
  conteudo.innerHTML = `<h1>Créditos</h1>
    <p class="introducao">Espelho de gestão de créditos emitidos por terceiros: CBE e CRVE do Registro Central do SBCE (art. 23) e créditos voluntários certificados (Verra, Gold Standard). A plataforma não emite créditos. Cada movimento entra num livro-razão encadeado que não aceita alteração.</p>
    ${saldoHtml}
    ${lotes.length ? `<section class="bloco"><h2>Transferir créditos</h2><form id="f-transf" class="grade">
      <label>Lote<select name="lote_id">${opLotes}</select></label>
      <label>Quantidade<input name="quantidade" type="number" min="1" required></label>
      <label>Conta de destino<select name="para_conta">${contas.filter((c) => c.conta_id !== estado.eu.conta_id).map((c) => `<option value="${c.conta_id}">${esc(c.nome)}</option>`).join("")}</select></label>
      <div class="acoes largo"><button type="submit" class="botao">Transferir</button></div><p class="erro-campo largo"></p></form></section>` : ""}
    ${reg ? `<section class="bloco"><h2>Escriturar lote emitido</h2>
      <p class="memoria">Lance aqui o lote já emitido no registro oficial ou pela certificadora. Crédito voluntário exige certificadora e o certificado em PDF.</p><form id="f-emit" class="grade">
      <label>Conta<select name="conta_id">${contas.map((c) => `<option value="${c.conta_id}">${esc(c.nome)}</option>`).join("")}</select></label>
      <label>Tipo<select name="tipo_ativo"><option value="CRVE">CRVE</option><option value="CBE">CBE</option><option value="VOLUNTARIO">Crédito voluntário</option></select></label>
      <label data-vol hidden>Certificadora<select name="certificadora"><option>Verra</option><option>Gold Standard</option></select></label>
      <label data-vol hidden>Certificado do lote (PDF)<input type="file" name="arquivo" accept=".pdf"></label>
      <label>Serial<input name="serial" required></label>
      <label>Projeto<input name="projeto" required></label>
      <label>Metodologia<input name="metodologia" required></label>
      <label>Safra<input name="safra" type="number" min="2000" max="2100" required></label>
      <label>Quantidade (tCO2e)<input name="quantidade" type="number" min="1" required></label>
      <div class="acoes largo"><button type="submit" class="botao">Escriturar lote</button></div><p class="erro-campo largo"></p></form></section>` : ""}`;
  aoEnviar($("#f-transf"), async (d) => {
    await api("POST", "/registro/transferencias", { lote_id: Number(d.lote_id), quantidade: d.quantidade,
      para_conta: Number(d.para_conta), chave_idempotencia: `painel-t-${Date.now()}` });
    avisar("Transferência registrada."); telaRegistro();
  });
  const fe = $("#f-emit");
  if (fe) {
    const sync = () => fe.querySelectorAll("[data-vol]").forEach((el) => (el.hidden = fe.elements.tipo_ativo.value !== "VOLUNTARIO"));
    fe.elements.tipo_ativo.addEventListener("change", sync); sync();
    aoEnviar(fe, async (d, form) => {
      const vol = d.tipo_ativo === "VOLUNTARIO";
      const anexo = vol ? await anexoDoCampo(form) : null;
      await api("POST", "/registro/emissoes", { ...d, conta_id: Number(d.conta_id), certificadora: vol ? d.certificadora : null,
        certificado_anexo_id: anexo, chave_idempotencia: `painel-e-${d.serial}` });
      avisar("Lote escriturado."); telaRegistro();
    });
  }
}

// ---------------------------------------------------------------- cadastros e fatores
async function telaCadastros() {
  const [orgs, users, fat] = await Promise.all([api("GET", "/organizacoes"), api("GET", "/usuarios"), api("GET", "/fatores?historico=true")]);
  conteudo.innerHTML = `<h1>Cadastros e fatores</h1>
    <p class="introducao">Organizações abrem conta no registro automaticamente. O número do usuário é o que ele usa para entrar no piloto.</p>
    <div class="colunas"><section class="bloco"><h2>Organizações</h2><div class="rolagem"><table><thead><tr><th>Nome</th><th>Tipo</th><th>CNPJ</th></tr></thead><tbody>
      ${orgs.map((o) => `<tr><td>${esc(o.nome)}</td><td>${esc(ROTULO_TIPO[o.tipo])}${o.esfera ? " " + esc(o.esfera) : ""}</td><td class="mono">${esc(o.cnpj)}</td></tr>`).join("") || `<tr><td colspan="3" class="vazio">Nenhuma organização.</td></tr>`}
      </tbody></table></div>
      <h3 style="margin-top:1.25rem">Nova organização</h3><form id="f-org" class="grade">
        <label class="largo">Nome<input name="nome" required></label>
        <label>CNPJ <span class="ajuda">14 dígitos</span><input name="cnpj" pattern="\\d{14}" inputmode="numeric" required></label>
        <label>Tipo<select name="tipo">${Object.entries(ROTULO_TIPO).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
        <label>Esfera<select name="esfera"><option value="">Não se aplica</option><option value="federal">Federal</option><option value="estadual">Estadual</option><option value="municipal">Municipal</option></select></label>
        <label>Código IBGE do município <span class="ajuda">7 dígitos</span><input name="municipio_ibge" pattern="\\d{7}" inputmode="numeric"></label>
        <div class="acoes largo"><button type="submit" class="botao">Cadastrar organização</button></div><p class="erro-campo largo"></p></form></section>
    <section class="bloco"><h2>Usuários</h2><div class="rolagem"><table><thead><tr><th class="num">Nº</th><th>Nome</th><th>Papel</th></tr></thead><tbody>
      ${users.map((x) => `<tr><td class="num">${x.id}</td><td>${esc(x.nome)}<div class="memoria">${esc(x.organizacao || "")}</div></td><td>${esc(ROTULO_PAPEL[x.papel])}${x.acreditado ? " ✓" : ""}</td></tr>`).join("")}
      </tbody></table></div>
      <h3 style="margin-top:1.25rem">Novo usuário</h3><form id="f-user" class="grade">
        <label class="largo">Nome<input name="nome" required></label>
        <label>Papel<select name="papel">${Object.entries(ROTULO_PAPEL).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
        <label>Organização<select name="organizacao_id"><option value="">Nenhuma</option>${orgs.map((o) => `<option value="${o.id}">${esc(o.nome)}</option>`).join("")}</select></label>
        <label class="opcao largo"><input type="checkbox" name="acreditado"> Organismo de inspeção acreditado (art. 32, parágrafo único)</label>
        <div class="acoes largo"><button type="submit" class="botao">Cadastrar usuário</button></div><p class="erro-campo largo"></p></form></section></div>
    <section class="bloco"><h2>Fatores de emissão</h2>
      <p class="memoria">Publicar um valor novo cria outra versão. A anterior é preservada e continua ligada aos inventários que a usaram.</p>
      <div class="rolagem"><table><thead><tr><th>Código</th><th>Versão</th><th>Descrição</th><th class="num">Valor</th><th>Fonte</th><th>Situação</th></tr></thead><tbody>
      ${fat.fatores.map((f) => `<tr><td class="mono">${esc(f.codigo)}</td><td>${f.versao}</td><td>${esc(f.descricao)}</td><td class="num">${f.valor} /${esc(f.unidade)}</td><td>${esc(f.fonte)} (${f.ano_referencia})</td><td>${f.vigente ? "Vigente" : "Histórico"}</td></tr>`).join("")}
      </tbody></table></div>
      <h3 style="margin-top:1.25rem">Publicar nova versão</h3><form id="f-fator" class="grade">
        <label>Fator<select name="codigo">${[...new Set(fat.fatores.map((f) => f.codigo))].map((c) => `<option>${esc(c)}</option>`).join("")}</select></label>
        <label>Novo valor (tCO2e por unidade)<input name="valor" type="number" step="any" min="0" required></label>
        <label>Fonte<input name="fonte" required placeholder="Ex.: MCTI — fator médio do SIN 2025"></label>
        <label>Ano de referência<input name="ano_referencia" type="number" required></label>
        <div class="acoes largo"><button type="submit" class="botao">Publicar versão</button></div><p class="erro-campo largo"></p></form></section>`;
  aoEnviar($("#f-org"), async (d) => { await api("POST", "/organizacoes", d); avisar("Organização cadastrada."); telaCadastros(); });
  aoEnviar($("#f-user"), async (d) => {
    const r = await api("POST", "/usuarios", { ...d, organizacao_id: d.organizacao_id ? Number(d.organizacao_id) : null });
    avisar(`Usuário cadastrado com o número ${r.id}.`); telaCadastros();
  });
  aoEnviar($("#f-fator"), async (d) => {
    const ok = await confirmar("Publicar nova versão?", `O fator ${d.codigo} passa a valer ${d.valor} nos novos lançamentos. Os lançamentos já feitos mantêm a versão anterior.`, "Publicar versão");
    if (!ok) return;
    await api("POST", "/fatores", d); estado.fatores = null; avisar("Nova versão publicada."); telaCadastros();
  });
}

// ---------------------------------------------------------------- auditoria
async function telaAuditoria() {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const entidade = params.get("entidade") || "";
  const linhas = await api("GET", `/auditoria${entidade ? "?entidade=" + encodeURIComponent(entidade) : ""}`);
  const ents = ["", "atividade", "inventario", "fator_emissao", "contrato", "lancamento_fornecedor", "anexo", "organizacao", "usuario"];
  conteudo.innerHTML = `<h1>Trilha de auditoria</h1>
    <p class="introducao">Quem fez o quê, quando e de qual endereço IP, com o conteúdo anterior e o novo. A trilha não aceita alteração nem exclusão.</p>
    <div class="acoes">${ents.map((e) => `<a class="botao ${e === entidade ? "" : "secundario"}" href="#/auditoria${e ? "?entidade=" + e : ""}">${e || "Tudo"}</a>`).join("")}</div>
    <section class="bloco" style="margin-top:1rem">${linhas.length ? `<div class="rolagem"><table>
      <thead><tr><th>Quando</th><th>Quem</th><th>IP</th><th>Ação</th><th>Registro</th><th>Detalhe</th></tr></thead><tbody>
      ${linhas.map((l) => `<tr><td>${data(l.em)}</td><td>${esc(l.usuario_nome || "Sistema")}</td><td class="mono">${esc(l.ip || "")}</td>
        <td>${esc(l.acao)}</td><td>${esc(l.entidade)} ${l.entidade_id ?? ""}</td>
        <td><details><summary>Ver</summary>${l.antes ? `<p class="memoria"><strong>Antes:</strong> ${esc(l.antes)}</p>` : ""}${l.depois ? `<p class="memoria"><strong>Depois:</strong> ${esc(l.depois)}</p>` : ""}</details></td></tr>`).join("")}
      </tbody></table></div>` : `<p class="vazio">Nenhum registro.</p>`}</section>`;
}

// ---------------------------------------------------------------- transparência e verificação pública
async function telaTransparencia() {
  const [aud, canc, orgs] = await Promise.all([api("GET", "/registro/auditoria"), api("GET", "/registro/cancelamentos"), api("GET", "/publico/organizacoes")]);
  conteudo.innerHTML = `<h1>Transparência</h1>
    <p class="introducao">Consulta pública, sem login (Lei 12.527/2011). Mostra as emissões dos órgãos com inventário aprovado, quem cancelou créditos e para quê, e se o livro-razão está íntegro.</p>
    <section class="bloco"><h2>Verificar certificado ou selo</h2><form id="f-verif" class="grade">
      <label class="largo">Hash do cancelamento ou código do selo<input name="codigo" required class="mono"></label>
      <div class="acoes largo"><button type="submit" class="botao">Verificar</button></div></form></section>
    <section class="bloco"><h2>Órgãos</h2>${orgs.length ? `<div class="rolagem"><table>
      <thead><tr><th>Órgão</th><th>Ano</th><th class="num">Emissões (tCO2e)</th><th class="num">Compensado</th></tr></thead><tbody>
      ${orgs.map((o) => `<tr><td>${esc(o.organizacao)}</td><td>${o.ano}</td><td class="num">${t(o.emissoes_tco2e)}</td><td class="num">${t(o.percentual_compensado)}%</td></tr>`).join("")}
      </tbody></table></div><p class="memoria">Percentual compensado não equivale a "carbono neutro".</p>` : `<p class="vazio">Nenhum inventário aprovado ainda.</p>`}</section>
    <section class="bloco"><h2>Integridade do livro-razão</h2>
      <p class="integridade ${aud.integra ? "ok" : "falha"}">${aud.integra
        ? `Íntegro: ${t(aud.movimentos)} movimentos conferidos pela cadeia de hashes.`
        : `Quebra detectada no movimento ${aud.quebra_em}. Avise a controladoria.`}</p></section>
    <section class="bloco"><h2>Créditos cancelados</h2>${canc.length ? `<div class="rolagem"><table>
      <thead><tr><th>Serial</th><th>Tipo</th><th>Projeto</th><th>Finalidade</th><th>Beneficiário</th><th class="num">Qtd.</th><th>Certificado</th></tr></thead><tbody>
      ${canc.map((c) => `<tr><td class="mono">${esc(c.serial)}</td><td>${esc(ROTULO_ATIVO[c.tipo_ativo])}${c.certificadora ? " · " + esc(c.certificadora) : ""}</td><td>${esc(c.projeto)}</td>
        <td>${esc(ROTULO_FINALIDADE[c.finalidade])}</td><td>${esc(c.beneficiario)}</td><td class="num">${t(c.quantidade)}</td>
        <td><a href="#/verificar/cancelamento/${esc(c.hash)}">Ver</a></td></tr>`).join("")}
      </tbody></table></div>` : `<p class="vazio">Nenhum crédito cancelado até agora.</p>`}</section>`;
  aoEnviar($("#f-verif"), async ({ codigo }) => {
    const c = codigo.trim();
    location.hash = c.length === 64 ? `#/verificar/cancelamento/${c}` : `#/verificar/selo/${c}`;
  });
}

async function telaVerificar(tipo, codigo) {
  if (tipo === "selo") {
    const s = await api("GET", `/publico/selos/${encodeURIComponent(codigo)}`);
    conteudo.innerHTML = `<p><a href="#/transparencia">Transparência</a></p>
      <section class="bloco verificacao">
        <img src="/publico/selos/${encodeURIComponent(s.codigo)}/qr.svg" alt="" width="120" height="120">
        <div><h1>${esc(s.titulo)}</h1>
        <p class="integridade ${s.situacao === "valido" && s.integro ? "ok" : "falha"}">${s.situacao === "valido" ? "Válido" : "Expirado"} até ${dataCurta(s.valido_ate)}${s.integro ? "" : " · dados não conferem com o hash"}</p></div></section>
      <section class="bloco"><dl class="ficha">
        <div><dt>Empresa</dt><dd>${esc(s.fornecedor)} · CNPJ <span class="mono">${esc(s.fornecedor_cnpj)}</span></dd></div>
        <div><dt>Contratante</dt><dd>${esc(s.orgao)}, contrato ${esc(s.contrato_numero)} (${esc(s.objeto)})</dd></div>
        <div><dt>Período avaliado</dt><dd>${dataCurta(s.periodo_inicio)} a ${dataCurta(s.periodo_fim)}</dd></div>
        <div><dt>Emissões comprovadas no período</dt><dd>${t(s.emissoes_tco2e)} tCO2e</dd></div>
        <div><dt>Score</dt><dd>${t(s.score)} de 100 (nível ${esc(s.classe)})</dd></div>
        <div><dt>Hash</dt><dd class="hash">${esc(s.hash)}</dd></div></dl>
        <p class="memoria">O selo atesta a gestão e o relato das emissões do serviço contratado. Não afirma neutralidade de carbono nem descarbonização total.</p></section>`;
    return;
  }
  const c = await api("GET", `/publico/cancelamentos/${encodeURIComponent(codigo)}`);
  conteudo.innerHTML = `<p><a href="#/transparencia">Transparência</a></p>
    <section class="bloco verificacao">
      <img src="/publico/cancelamentos/${encodeURIComponent(c.hash)}/qr.svg" alt="" width="120" height="120">
      <div><h1>Certificado de cancelamento</h1>
      <p class="integridade ${c.livro_razao_integro ? "ok" : "falha"}">${c.livro_razao_integro ? "Registro conferido: livro-razão íntegro" : "Atenção: quebra no livro-razão"}</p></div></section>
    <section class="bloco"><dl class="ficha">
      <div><dt>Titular</dt><dd>${esc(c.titular || "")}</dd></div>
      <div><dt>Beneficiário</dt><dd>${esc(c.beneficiario)}</dd></div>
      <div><dt>Finalidade</dt><dd>${esc(ROTULO_FINALIDADE[c.finalidade])}</dd></div>
      <div><dt>Quantidade</dt><dd>${t(c.quantidade)} tCO2e</dd></div>
      <div><dt>Crédito</dt><dd>${esc(ROTULO_ATIVO[c.tipo_ativo])} · serial <span class="mono">${esc(c.serial)}</span> · safra ${c.safra}</dd></div>
      <div><dt>Projeto</dt><dd>${esc(c.projeto)} (${esc(c.metodologia)})</dd></div>
      ${c.certificadora ? `<div><dt>Aposentadoria na ${esc(c.certificadora)}</dt><dd class="mono">${esc(c.registro_externo_ref)}</dd></div>` : ""}
      <div><dt>Data</dt><dd>${data(c.em)}</dd></div>
      <div><dt>Hash</dt><dd class="hash">${esc(c.hash)}</dd></div></dl>
      ${c.certificadora ? `<p class="memoria">Confira também o número da aposentadoria no registro público da ${esc(c.certificadora)}.</p>` : ""}</section>`;
}

// ---------------------------------------------------------------- rotas
const PUBLICAS = new Set(["entrar", "inicio", "transparencia", "verificar"]);
async function navegar() {
  const [caminho] = location.hash.split("?");
  const [, rota = "inicio", a, b] = caminho.split("/");
  if (!PUBLICAS.has(rota) && !estado.eu) { location.hash = "#/entrar"; return; }
  menu();
  conteudo.innerHTML = `<p class="vazio">Carregando…</p>`;
  try {
    const telas = {
      entrar: telaEntrar, inicio: telaInicio, transparencia: telaTransparencia, painel: telaPainel,
      inventarios: () => (a ? telaInventario(Number(a)) : telaInventarios()),
      unidades: telaUnidades, terceirizadas: telaTerceirizadas, contratos: () => telaContrato(Number(a)),
      registro: telaRegistro, cadastros: telaCadastros, auditoria: telaAuditoria,
      verificar: () => telaVerificar(a, decodeURIComponent(b || "")),
    };
    await (telas[rota] || telaInicio)();
  } catch (e) {
    if (e instanceof Obsoleta) return;
    if (e.status === 401) { gravarSessao(null); estado.eu = null; location.hash = "#/entrar"; return; }
    conteudo.innerHTML = `<section class="bloco"><h1>Não foi possível abrir esta tela</h1><p>${esc(e.message)}</p><p><a href="#/inicio">Voltar ao início</a></p></section>`;
  }
  conteudo.focus({ preventScroll: true });
}

conteudo.addEventListener("click", (ev) => {
  const tr = ev.target.closest("tr[data-ir]");
  if (tr && !ev.target.closest("a, button, input, label")) location.hash = tr.dataset.ir;
});
window.addEventListener("hashchange", navegar);
// Recargas de tela disparadas após uma ação não são aguardadas: se o usuário já mudou de tela,
// a resposta é descartada (Obsoleta) e não deve virar erro; outros erros viram aviso.
window.addEventListener("unhandledrejection", (ev) => {
  ev.preventDefault();
  if (!(ev.reason instanceof Obsoleta)) avisar(ev.reason?.message || "Erro inesperado.", true);
});

(async function iniciar() {
  estado.uid = lerSessao();
  if (estado.uid) {
    try { estado.eu = await api("GET", "/eu"); } catch { estado.uid = null; gravarSessao(null); }
  }
  if (!location.hash) location.hash = "#/inicio"; else navegar();
})();
