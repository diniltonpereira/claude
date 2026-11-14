"use strict";
/* Painel do Carbono Público. Consome a API REST da própria plataforma. */

const estado = { uid: null, eu: null, fatores: null };
const $ = (sel) => document.querySelector(sel);
const conteudo = $("#conteudo");

// ---------------------------------------------------------------- utilidades
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
function esc(v) { return v == null ? "" : String(v).replace(/[&<>"']/g, (c) => ESC[c]); }
const nf = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });
const t = (v) => nf.format(Number(v || 0));
const data = (iso) => iso ? new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "";
const dataCurta = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("pt-BR");

function lerSessao() { try { return localStorage.getItem("carbono.uid"); } catch { return null; } }
function gravarSessao(v) { try { v ? localStorage.setItem("carbono.uid", v) : localStorage.removeItem("carbono.uid"); } catch { /* sem armazenamento */ } }

/** Resposta de uma tela que o usuário já deixou: é descartada para não sobrescrever a tela atual. */
class Obsoleta extends Error {}

async function api(metodo, caminho, corpo) {
  const tela = location.hash;
  const cab = { "Content-Type": "application/json" };
  if (estado.uid) cab["X-Usuario-Id"] = estado.uid;
  const r = await fetch(caminho, { method: metodo, headers: cab, body: corpo ? JSON.stringify(corpo) : undefined });
  const json = await r.json().catch(() => ({}));
  if (!r.ok) {
    let msg = json.erro || "";
    if (!msg && Array.isArray(json.detail)) msg = json.detail.map((d) => `${d.loc.slice(-1)[0]}: ${d.msg}`).join("; ");
    if (!msg) msg = typeof json.detail === "string" ? json.detail : `Erro ${r.status}`;
    const e = new Error(msg); e.status = r.status; throw e;
  }
  if (metodo === "GET" && location.hash !== tela) throw new Obsoleta();
  return json;
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
    if (!el.name) continue;
    if (el.type === "checkbox") o[el.name] = el.checked;
    else if (el.value === "") continue;
    else if (el.type === "number") o[el.name] = Number(el.value);
    else o[el.name] = el.value;
  }
  return o;
}

/** Liga um formulário a uma ação assíncrona com estado de envio e mensagem de erro no lugar. */
function aoEnviar(form, acao) {
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const botao = form.querySelector("button[type=submit]");
    const erro = form.querySelector(".erro-campo");
    if (erro) erro.textContent = "";
    if (botao) botao.disabled = true;
    try { await acao(dadosDe(form)); }
    catch (e) {
      if (e instanceof Obsoleta) return;
      if (erro) erro.textContent = e.message; else avisar(e.message, true);
    }
    finally { if (botao) botao.disabled = false; }
  });
}

const ROTULO_STATUS = {
  rascunho: "Em elaboração", submetido: "Aguardando verificação", em_verificacao: "Em verificação",
  verificado: "Aguardando aprovação", aprovado: "Aprovado", devolvido: "Devolvido",
};
const ROTULO_PAPEL = { elaborador: "Elaboração", verificador: "Verificação (organismo acreditado)",
  moderador: "Moderação e aprovação", registrador: "Registro" };
const ROTULO_TIPO = { orgao_publico: "Órgão público", ppp: "PPP", concessao: "Concessão",
  concessao_florestal: "Concessão florestal", empresa: "Empresa" };
const ROTULO_FAIXA = { nao_regulado: "Não regulado", regulado_monitoramento: "Monitoramento e relato",
  regulado_conciliacao: "Monitoramento, relato e conciliação" };
const ROTULO_ATIVO = { CBE: "CBE", CRVE: "CRVE", VOLUNTARIO: "Crédito voluntário" };
const ROTULO_FINALIDADE = { compensacao_voluntaria: "Compensação voluntária", conciliacao_sbce: "Conciliação SBCE",
  invalidacao: "Invalidação" };
const situacao = (s) => `<span class="situacao ${esc(s)}">${esc(ROTULO_STATUS[s] || s)}</span>`;

// ---------------------------------------------------------------- moldura
function menu() {
  const p = estado.eu?.usuario.papel;
  const itens = [["inicio", "Início"]];
  if (p) itens.push(["inventarios", p === "elaborador" ? "Inventários" : "Fila de trabalho"]);
  if (p === "elaborador") itens.push(["instalacoes", "Instalações e fontes"]);
  if (p === "elaborador" || p === "registrador") itens.push(["registro", "Registro de créditos"]);
  if (p === "registrador") itens.push(["cadastros", "Cadastros"]);
  itens.push(["transparencia", "Transparência"]);
  const atual = location.hash.split("/")[1] || "inicio";
  $("#menu").innerHTML = itens.map(([r, n]) =>
    `<a href="#/${r}"${atual === r ? ' aria-current="page"' : ""}>${esc(n)}</a>`).join("")
    + `<p class="rodape">Lei 15.042/2024 (SBCE)</p>`;
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

// ---------------------------------------------------------------- telas
async function telaEntrar() {
  conteudo.innerHTML = `<section class="entrada bloco">
    <h1>Entrar</h1>
    <p class="introducao">No piloto, o acesso é pelo número de usuário fornecido pelo registrador. A versão de produção usará o login gov.br.</p>
    <form id="f-entrar" class="grade">
      <label class="largo">Número de usuário<input name="uid" type="number" min="1" required autofocus></label>
      <div class="acoes largo"><button type="submit" class="botao">Entrar</button></div>
      <p class="erro-campo largo"></p>
    </form></section>`;
  aoEnviar($("#f-entrar"), async ({ uid }) => {
    estado.uid = String(uid);
    try { estado.eu = await api("GET", "/eu"); }
    catch (e) { estado.uid = null; throw new Error(e.status === 401 ? "Usuário não encontrado. Confira o número com o registrador." : e.message); }
    gravarSessao(estado.uid);
    location.hash = "#/inicio";
  });
}

async function telaInicio() {
  const cal = await api("GET", "/sbce/calendario");
  const nomes = { I: "Regulamentação", II: "Preparação do relato", III: "Plano e relato", IV: "1º Plano de Alocação", V: "Implementação plena" };
  let resumo = "";
  if (estado.eu) {
    const invs = await api("GET", "/inventarios");
    const pend = invs.filter((i) => ({
      elaborador: ["rascunho", "devolvido"], verificador: ["submetido", "em_verificacao"],
      moderador: ["verificado"], registrador: [] }[estado.eu.usuario.papel] || []).includes(i.status));
    resumo = `<section class="bloco"><h2>O que espera por você</h2>
      ${pend.length ? `<div class="rolagem"><table><thead><tr><th>Organização</th><th>Ano</th><th>Situação</th></tr></thead><tbody>
        ${pend.map((i) => `<tr class="clicavel" data-ir="#/inventarios/${i.id}"><td><a href="#/inventarios/${i.id}">${esc(i.organizacao)}</a></td><td>${i.ano}</td><td>${situacao(i.status)}</td></tr>`).join("")}
      </tbody></table></div>` : `<p class="vazio">Nenhum inventário aguarda sua ação.</p>`}
    </section>`;
    if (estado.eu.conta_id) {
      const s = await api("GET", `/contas/${estado.eu.conta_id}/saldo`);
      resumo += `<section class="bloco"><h2>Saldo no registro</h2><dl class="cifra"><div><dt>Créditos disponíveis</dt><dd>${t(s.total)} <small>tCO2e</small></dd></div><div><dt>Lotes</dt><dd>${s.lotes.length}</dd></div></dl></section>`;
    }
  }
  conteudo.innerHTML = `<h1>${estado.eu ? "Olá, " + esc(estado.eu.usuario.nome.split(" ")[0]) : "Carbono Público"}</h1>
    <p class="introducao">Inventário, verificação, enquadramento no SBCE e compensação de emissões para órgãos públicos, PPPs e concessões.</p>
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

async function telaInventarios() {
  const invs = await api("GET", "/inventarios");
  const elab = estado.eu.usuario.papel === "elaborador";
  conteudo.innerHTML = `<h1>${elab ? "Inventários" : "Fila de trabalho"}</h1>
    <p class="introducao">${elab ? "Um inventário por ano. Lance as atividades, submeta à verificação e acompanhe até a aprovação."
      : "Inventários que aguardam sua análise ou que você já analisou."}</p>
    ${elab ? `<section class="bloco"><h2>Novo inventário</h2><form id="f-inv" class="grade">
      <label>Ano de referência<input name="ano" type="number" min="2000" max="2100" value="${new Date().getFullYear() - 1}" required></label>
      <div class="acoes"><button type="submit" class="botao">Criar inventário</button></div>
      <p class="erro-campo largo"></p></form></section>` : ""}
    <section class="bloco">${invs.length ? `<div class="rolagem"><table>
      <thead><tr><th>Organização</th><th>Ano</th><th>Situação</th><th class="num">Emissões (tCO2e)</th></tr></thead><tbody>
      ${invs.map((i) => `<tr class="clicavel" data-ir="#/inventarios/${i.id}"><td><a href="#/inventarios/${i.id}">${esc(i.organizacao)}</a><br><span class="memoria">${esc(ROTULO_TIPO[i.tipo_organizacao])}</span></td>
        <td>${i.ano}</td><td>${situacao(i.status)}</td><td class="num">${t(i.total_tco2e)}</td></tr>`).join("")}
      </tbody></table></div>` : `<p class="vazio">${elab ? "Nenhum inventário ainda. Crie o primeiro acima." : "Nada na fila por enquanto."}</p>`}</section>`;
  if (elab) aoEnviar($("#f-inv"), async ({ ano }) => {
    const r = await api("POST", "/inventarios", { organizacao_id: estado.eu.organizacao.id, ano });
    avisar("Inventário criado."); location.hash = `#/inventarios/${r.id}`;
  });
}

function trilha(inv) {
  const hist = inv.historico;
  const ultimo = (para) => [...hist].reverse().find((e) => e.para_status === para);
  const ordem = ["rascunho", "submetido", "em_verificacao", "verificado", "aprovado"];
  const pos = inv.status === "devolvido" ? -1 : ordem.indexOf(inv.status);
  const etapas = [
    ["Elaboração", "submetido", 1], ["Verificação", "em_verificacao", 2],
    ["Conclusão da verificação", "verificado", 3], ["Aprovação", "aprovado", 4],
  ];
  const itens = etapas.map(([nome, marco, i]) => {
    const ev = ultimo(marco);
    let classe = "";
    if (inv.status === "devolvido") classe = ev && hist.indexOf(ev) < hist.indexOf(ultimo("devolvido")) ? "feita" : "";
    else if (pos >= i) classe = "feita";
    else if (pos === i - 1) classe = "atual";
    const carimbo = classe === "feita" && ev
      ? `<span class="carimbo">${esc(ev.usuario_nome)}<br>${data(ev.em)}</span>` : "";
    return `<li class="${classe}"><strong>${nome}</strong>${carimbo}</li>`;
  });
  if (inv.status === "devolvido") {
    const ev = ultimo("devolvido");
    const etapa = ev.de_status === "verificado" ? 3 : 1;
    itens[etapa] = `<li class="devolvida"><strong>Devolvido</strong><span class="carimbo vermelho">${esc(ev.usuario_nome)}<br>${data(ev.em)}</span></li>`;
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
  let instalacoes = [];
  if (editavel) instalacoes = await api("GET", `/organizacoes/${inv.organizacao_id}/instalacoes`);

  conteudo.innerHTML = `<p><a href="#/inventarios">Voltar aos inventários</a></p>
    <h1>Inventário ${inv.ano}</h1>
    <p class="introducao">${situacao(inv.status)}</p>
    ${trilha(inv)}
    ${devolucao ? `<blockquote class="parecer"><strong>Parecer da devolução:</strong> ${esc(devolucao.parecer)}</blockquote>` : ""}
    ${acoes.length ? `<section class="bloco"><h2>Sua ação</h2><form id="f-acao" class="grade">
      <label class="largo">Parecer <span class="ajuda">Obrigatório para devolver. Registrado no histórico.</span><textarea name="parecer"></textarea></label>
      <div class="acoes largo">${acoes.map(([v, n], i) => `<button type="submit" name="acao" value="${v}" class="botao${v === "devolver" ? " perigo" : i ? " secundario" : ""}">${esc(n)}</button>`).join("")}</div>
      <p class="erro-campo largo"></p></form></section>` : ""}
    <section class="bloco"><h2>Emissões</h2><dl class="cifra">
      <div><dt>Escopo 1</dt><dd>${t(tot.escopo1)} <small>tCO2e</small></dd></div>
      <div><dt>Escopo 2</dt><dd>${t(tot.escopo2)} <small>tCO2e</small></dd></div>
      <div><dt>Escopo 3</dt><dd>${t(tot.escopo3)} <small>tCO2e</small></dd></div>
      <div><dt>Remoções</dt><dd>${t(tot.remocoes)} <small>tCO2e</small></dd></div>
      <div><dt>CO2 biogênico, à parte</dt><dd>${t(tot.co2_biogenico)} <small>tCO2</small></dd></div></dl></section>
    <div class="colunas">
      <div>
        <section class="bloco"><h2>Atividades</h2>${inv.atividades.length ? `<div class="rolagem"><table>
          <thead><tr><th>Atividade</th><th>Escopo</th><th class="num">tCO2e</th></tr></thead><tbody>
          ${inv.atividades.map((a) => `<tr><td>${esc(a.descricao)}${a.natureza === "remocao" ? " <strong>(remoção)</strong>" : ""}
            <div class="memoria">${esc(a.memoria)}</div>${a.evidencia ? `<div class="memoria">Evidência: ${esc(a.evidencia)}</div>` : ""}</td>
            <td>${a.escopo}</td><td class="num">${t(a.tco2e)}</td></tr>`).join("")}</tbody></table></div>`
          : `<p class="vazio">Nenhuma atividade lançada.</p>`}</section>
        ${editavel ? formAtividade(instalacoes) : ""}
        <section class="bloco"><h2>Escopo 1 por instalação</h2>${inv.por_instalacao.length ? `<div class="rolagem"><table>
          <thead><tr><th>Instalação ou fonte</th><th class="num">Emissões</th><th class="num">Remoções</th></tr></thead><tbody>
          ${inv.por_instalacao.map((i) => `<tr><td>${esc(i.nome)}</td><td class="num">${t(i.escopo1)}</td><td class="num">${t(i.remocoes)}</td></tr>`).join("")}
          </tbody></table></div>` : `<p class="vazio">Nenhuma instalação cadastrada. Os limiares do SBCE valem por instalação ou fonte (art. 30).</p>`}</section>
      </div>
      <div>
        <section class="bloco" id="enquadramento"><h2>Enquadramento no SBCE</h2>
          <form id="f-enq" class="grade">
            <label class="opcao largo"><input type="checkbox" name="gera_creditos"> A organização gera créditos</label>
            <label class="opcao largo"><input type="checkbox" name="terras_publicas_ou_tradicionais"> Envolve terras públicas, indígenas ou tradicionais</label>
            <div class="acoes largo"><button type="submit" class="botao">Calcular enquadramento</button></div>
            <p class="erro-campo largo"></p></form>
          <div id="enq-resultado"></div></section>
        ${u.papel === "elaborador" && inv.status === "aprovado" ? formCompensacao(inv) : ""}
      </div>
    </div>`;

  conteudo.querySelectorAll("#f-acao button").forEach((b) => b.addEventListener("click", () => (b.form.dataset.acao = b.value)));
  if (acoes.length) aoEnviar($("#f-acao"), async ({ parecer }) => {
    const acao = $("#f-acao").dataset.acao;
    const r = await api("POST", `/inventarios/${id}/transicoes`, { acao, parecer });
    avisar(`Inventário agora: ${ROTULO_STATUS[r.status]}.`); telaInventario(id);
  });
  if (editavel) ligarFormAtividade(id);
  aoEnviar($("#f-enq"), async (d) => mostrarEnquadramento(await api("POST", `/inventarios/${id}/enquadramento`, d)));
  if ($("#f-comp")) ligarCompensacao(inv);
}

function formAtividade(instalacoes) {
  const f = estado.fatores.fatores;
  return `<section class="bloco"><h2>Lançar atividade</h2><form id="f-atv" class="grade">
    <label class="largo">Descrição<input name="descricao" required placeholder="Ex.: Frota de coleta de lixo"></label>
    <label>Forma de cálculo<select name="modo">
      <option value="fator">Por consumo (combustível, energia…)</option>
      <option value="gas">Por massa de gás emitida</option>
      <option value="remocao">Remoção de CO2 (sumidouro)</option></select></label>
    <label data-modo="fator">Item<select name="fator">${f.map((x) => `<option value="${esc(x.codigo)}">${esc(x.descricao)} (${esc(x.unidade)})</option>`).join("")}</select></label>
    <label data-modo="gas" hidden>Gás<select name="gas">${Object.keys(estado.fatores.gwp_ar5).map((g) => `<option>${esc(g)}</option>`).join("")}</select></label>
    <label data-modo="gas" hidden>Escopo<select name="escopo"><option value="1">1: emissão direta</option><option value="3">3: indireta</option></select></label>
    <label>Quantidade <span class="ajuda" id="unidade">na unidade do item; gás em toneladas</span><input name="quantidade" type="number" step="any" min="0" required></label>
    <label>Instalação ou fonte<select name="instalacao_id"><option value="">Sem vínculo</option>
      ${instalacoes.map((i) => `<option value="${i.id}">${esc(i.nome)}</option>`).join("")}</select></label>
    <label class="largo">Evidência <span class="ajuda">Nota fiscal, fatura, laudo ou número do processo</span><input name="evidencia"></label>
    <div class="acoes largo"><button type="submit" class="botao">Lançar atividade</button></div>
    <p class="erro-campo largo"></p></form></section>`;
}

function ligarFormAtividade(id) {
  const form = $("#f-atv");
  const modo = form.elements.modo;
  const sync = () => form.querySelectorAll("[data-modo]").forEach((el) => {
    el.hidden = el.dataset.modo !== (modo.value === "remocao" ? "nenhum" : modo.value);
  });
  modo.addEventListener("change", sync); sync();
  aoEnviar(form, async (d) => {
    const corpo = { descricao: d.descricao, quantidade: d.quantidade, evidencia: d.evidencia || null,
      instalacao_id: d.instalacao_id ? Number(d.instalacao_id) : null };
    if (d.modo === "fator") corpo.fator = d.fator;
    else if (d.modo === "gas") { corpo.gas = d.gas; corpo.escopo = Number(d.escopo); }
    else { corpo.gas = "CO2"; corpo.remocao = true; }
    await api("POST", `/inventarios/${id}/atividades`, corpo);
    avisar("Atividade lançada."); telaInventario(id);
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
    <p class="memoria">Os créditos usados são cancelados no registro, em nome da organização (art. 45). O cancelamento não pode ser desfeito.</p>
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
  aoEnviar($("#f-comp"), async (d) => {
    const corpo = corpoDe(d);
    const r = await api("POST", `/inventarios/${inv.id}/compensacoes`, { ...corpo, simular: true });
    const qtd = r.plano.reduce((s, p) => s + p.quantidade, 0);
    $("#comp-resultado").innerHTML = `<h3 style="margin-top:1rem">Plano</h3>
      <p>Base: ${t(r.emissoes_base_tco2e)} tCO2e. A compensar: <strong>${t(r.a_compensar)}</strong> créditos.</p>
      ${r.plano.length ? `<div class="rolagem"><table><thead><tr><th>Lote</th><th>Tipo</th><th>Safra</th><th class="num">Qtd.</th></tr></thead><tbody>
        ${r.plano.map((p) => `<tr><td class="mono">${esc(p.serial)}</td><td>${esc(ROTULO_ATIVO[p.tipo_ativo])}</td><td>${p.safra}</td><td class="num">${t(p.quantidade)}</td></tr>`).join("")}
      </tbody></table></div>` : ""}
      ${r.lotes_ignorados.length ? `<ul class="alertas">${r.lotes_ignorados.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : ""}
      ${r.deficit ? `<p class="erro-campo">Faltam ${t(r.deficit)} créditos elegíveis na conta. Adquira créditos antes de compensar.</p>`
        : `<div class="acoes"><button class="botao perigo" id="b-cancelar">Cancelar ${t(qtd)} créditos</button></div>`}`;
    const b = $("#b-cancelar");
    if (b) b.onclick = async () => {
      const ok = await confirmar(`Cancelar ${t(qtd)} créditos?`,
        `Os créditos serão cancelados no registro em nome de ${estado.eu.organizacao.nome}, para o inventário ${inv.ano}. Não é possível desfazer.`,
        `Cancelar ${t(qtd)} créditos`);
      if (!ok) return;
      b.disabled = true;
      try {
        await api("POST", `/inventarios/${inv.id}/compensacoes`, { ...corpo, chave_idempotencia: `painel-${inv.id}-${Date.now()}` });
        avisar(`${t(qtd)} créditos cancelados.`); telaInventario(inv.id);
      } catch (e) { if (!(e instanceof Obsoleta)) { avisar(e.message, true); b.disabled = false; } }
    };
  });
}

async function telaInstalacoes() {
  const org = estado.eu.organizacao;
  const lista = await api("GET", `/organizacoes/${org.id}/instalacoes`);
  conteudo.innerHTML = `<h1>Instalações e fontes</h1>
    <p class="introducao">Os limiares do SBCE (10 mil e 25 mil tCO2e por ano) valem para cada instalação ou fonte, não para o total da organização (art. 30).</p>
    <section class="bloco">${lista.length ? `<div class="rolagem"><table><thead><tr><th>Nome</th><th>Tipo</th><th>Identificação</th><th>Situação especial</th></tr></thead><tbody>
      ${lista.map((i) => `<tr><td>${esc(i.nome)}</td><td>${i.natureza === "fonte_movel" ? "Fonte móvel" : "Instalação"}</td>
        <td>${esc(i.identificador || "")} ${esc(i.municipio_uf || "")}</td>
        <td>${i.residuos_neutralizacao ? "Resíduos com neutralização (art. 30, § 3º)" : i.agropecuaria_primaria ? "Agropecuária primária (art. 1º, § 2º)" : ""}</td></tr>`).join("")}
      </tbody></table></div>` : `<p class="vazio">Nenhuma instalação cadastrada. Cadastre aterros, estações, garagens e frotas abaixo.</p>`}</section>
    <section class="bloco"><h2>Cadastrar instalação ou fonte</h2><form id="f-inst" class="grade">
      <label>Nome<input name="nome" required placeholder="Ex.: Aterro sanitário Leste"></label>
      <label>Tipo<select name="natureza"><option value="instalacao">Instalação</option><option value="fonte_movel">Fonte móvel (frota)</option></select></label>
      <label>Identificação <span class="ajuda">Licença, CNPJ da filial…</span><input name="identificador"></label>
      <label>Município/UF<input name="municipio_uf"></label>
      <label class="opcao largo"><input type="checkbox" name="residuos_neutralizacao"> Trata resíduos ou efluentes com neutralização comprovada (art. 30, § 3º)</label>
      <label class="opcao largo"><input type="checkbox" name="agropecuaria_primaria"> Produção primária agropecuária (art. 1º, § 2º)</label>
      <div class="acoes largo"><button type="submit" class="botao">Cadastrar</button></div>
      <p class="erro-campo largo"></p></form></section>`;
  aoEnviar($("#f-inst"), async (d) => {
    await api("POST", `/organizacoes/${org.id}/instalacoes`, d);
    avisar("Instalação cadastrada."); telaInstalacoes();
  });
}

async function telaRegistro() {
  const reg = estado.eu.usuario.papel === "registrador";
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
  conteudo.innerHTML = `<h1>Registro de créditos</h1>
    <p class="introducao">Espelho de gestão do Registro Central do SBCE (art. 23). Cada movimento entra num livro-razão encadeado que não aceita alteração.</p>
    ${saldoHtml}
    ${lotes.length ? `<section class="bloco"><h2>Transferir créditos</h2><form id="f-transf" class="grade">
      <label>Lote<select name="lote_id">${opLotes}</select></label>
      <label>Quantidade<input name="quantidade" type="number" min="1" required></label>
      <label>Conta de destino<select name="para_conta">${contas.filter((c) => c.conta_id !== estado.eu.conta_id).map((c) => `<option value="${c.conta_id}">${esc(c.nome)}</option>`).join("")}</select></label>
      <div class="acoes largo"><button type="submit" class="botao">Transferir</button></div><p class="erro-campo largo"></p></form></section>` : ""}
    ${reg ? `<section class="bloco"><h2>Escriturar lote emitido</h2>
      <p class="memoria">Lance aqui o lote já emitido no registro oficial ou o crédito voluntário verificado.</p><form id="f-emit" class="grade">
      <label>Conta<select name="conta_id">${contas.map((c) => `<option value="${c.conta_id}">${esc(c.nome)}</option>`).join("")}</select></label>
      <label>Tipo<select name="tipo_ativo"><option value="CRVE">CRVE</option><option value="CBE">CBE</option><option value="VOLUNTARIO">Crédito voluntário</option></select></label>
      <label>Serial<input name="serial" required placeholder="BR-CRVE-2025-000001"></label>
      <label>Projeto<input name="projeto" required></label>
      <label>Metodologia<input name="metodologia" required></label>
      <label>Safra<input name="safra" type="number" min="2000" max="2100" required></label>
      <label>Quantidade (tCO2e)<input name="quantidade" type="number" min="1" required></label>
      <div class="acoes largo"><button type="submit" class="botao">Escriturar lote</button></div><p class="erro-campo largo"></p></form></section>` : ""}`;
  if ($("#f-transf")) aoEnviar($("#f-transf"), async (d) => {
    await api("POST", "/registro/transferencias", { lote_id: Number(d.lote_id), quantidade: d.quantidade,
      para_conta: Number(d.para_conta), chave_idempotencia: `painel-t-${Date.now()}` });
    avisar("Transferência registrada."); telaRegistro();
  });
  if ($("#f-emit")) aoEnviar($("#f-emit"), async (d) => {
    await api("POST", "/registro/emissoes", { ...d, conta_id: Number(d.conta_id), chave_idempotencia: `painel-e-${d.serial}` });
    avisar("Lote escriturado."); telaRegistro();
  });
}

async function telaCadastros() {
  const [orgs, users] = await Promise.all([api("GET", "/organizacoes"), api("GET", "/usuarios")]);
  conteudo.innerHTML = `<h1>Cadastros</h1>
    <p class="introducao">Organizações abrem conta no registro automaticamente. O número do usuário é o que ele usa para entrar.</p>
    <div class="colunas"><section class="bloco"><h2>Organizações</h2><div class="rolagem"><table><thead><tr><th>Nome</th><th>Tipo</th><th>CNPJ</th></tr></thead><tbody>
      ${orgs.map((o) => `<tr><td>${esc(o.nome)}</td><td>${esc(ROTULO_TIPO[o.tipo])}${o.esfera ? " " + esc(o.esfera) : ""}</td><td class="mono">${esc(o.cnpj)}</td></tr>`).join("") || `<tr><td colspan="3" class="vazio">Nenhuma organização.</td></tr>`}
      </tbody></table></div>
      <h3 style="margin-top:1.25rem">Nova organização</h3><form id="f-org" class="grade">
        <label class="largo">Nome<input name="nome" required></label>
        <label>CNPJ <span class="ajuda">14 dígitos</span><input name="cnpj" pattern="\\d{14}" inputmode="numeric" required></label>
        <label>Tipo<select name="tipo">${Object.entries(ROTULO_TIPO).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
        <label>Esfera<select name="esfera"><option value="">Não se aplica</option><option value="federal">Federal</option><option value="estadual">Estadual</option><option value="municipal">Municipal</option></select></label>
        <label>Contrato <span class="ajuda">PPP ou concessão</span><input name="contrato_ref"></label>
        <div class="acoes largo"><button type="submit" class="botao">Cadastrar organização</button></div><p class="erro-campo largo"></p></form></section>
    <section class="bloco"><h2>Usuários</h2><div class="rolagem"><table><thead><tr><th class="num">Nº</th><th>Nome</th><th>Papel</th></tr></thead><tbody>
      ${users.map((x) => `<tr><td class="num">${x.id}</td><td>${esc(x.nome)}<div class="memoria">${esc(x.organizacao || "")}</div></td><td>${esc(ROTULO_PAPEL[x.papel])}${x.acreditado ? " ✓" : ""}</td></tr>`).join("")}
      </tbody></table></div>
      <h3 style="margin-top:1.25rem">Novo usuário</h3><form id="f-user" class="grade">
        <label class="largo">Nome<input name="nome" required></label>
        <label>Papel<select name="papel">${Object.entries(ROTULO_PAPEL).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
        <label>Organização<select name="organizacao_id"><option value="">Nenhuma</option>${orgs.map((o) => `<option value="${o.id}">${esc(o.nome)}</option>`).join("")}</select></label>
        <label class="opcao largo"><input type="checkbox" name="acreditado"> Organismo de inspeção acreditado (art. 32, parágrafo único)</label>
        <div class="acoes largo"><button type="submit" class="botao">Cadastrar usuário</button></div><p class="erro-campo largo"></p></form></section></div>`;
  aoEnviar($("#f-org"), async (d) => { await api("POST", "/organizacoes", d); avisar("Organização cadastrada."); telaCadastros(); });
  aoEnviar($("#f-user"), async (d) => {
    const r = await api("POST", "/usuarios", { ...d, organizacao_id: d.organizacao_id ? Number(d.organizacao_id) : null });
    avisar(`Usuário cadastrado com o número ${r.id}.`); telaCadastros();
  });
}

async function telaTransparencia() {
  const [aud, canc] = await Promise.all([api("GET", "/registro/auditoria"), api("GET", "/registro/cancelamentos")]);
  conteudo.innerHTML = `<h1>Transparência</h1>
    <p class="introducao">Consulta pública, sem login. Mostra quem cancelou créditos e para quê, e se o livro-razão está íntegro (art. 24, VI; Lei 12.527/2011).</p>
    <section class="bloco"><h2>Integridade do livro-razão</h2>
      <p class="integridade ${aud.integra ? "ok" : "falha"}">${aud.integra
        ? `Íntegro: ${t(aud.movimentos)} movimentos conferidos pela cadeia de hashes.`
        : `Quebra detectada no movimento ${aud.quebra_em}. Avise a controladoria.`}</p></section>
    <section class="bloco"><h2>Créditos cancelados</h2>${canc.length ? `<div class="rolagem"><table>
      <thead><tr><th>Serial</th><th>Tipo</th><th>Projeto</th><th>Safra</th><th>Finalidade</th><th>Beneficiário</th><th class="num">Qtd.</th></tr></thead><tbody>
      ${canc.map((c) => `<tr><td class="mono">${esc(c.serial)}</td><td>${esc(ROTULO_ATIVO[c.tipo_ativo])}</td><td>${esc(c.projeto)}</td><td>${c.safra}</td>
        <td>${esc(ROTULO_FINALIDADE[c.finalidade])}</td><td>${esc(c.beneficiario)}</td><td class="num">${t(c.quantidade)}</td></tr>`).join("")}
      </tbody></table></div>` : `<p class="vazio">Nenhum crédito cancelado até agora.</p>`}</section>`;
}

// ---------------------------------------------------------------- rotas
const PUBLICAS = new Set(["entrar", "inicio", "transparencia"]);
async function navegar() {
  const [, rota = "inicio", id] = location.hash.split("/");
  if (!PUBLICAS.has(rota) && !estado.eu) { location.hash = "#/entrar"; return; }
  menu();
  conteudo.innerHTML = `<p class="vazio">Carregando…</p>`;
  try {
    const telas = { entrar: telaEntrar, inicio: telaInicio, transparencia: telaTransparencia,
      inventarios: () => (id ? telaInventario(Number(id)) : telaInventarios()),
      instalacoes: telaInstalacoes, registro: telaRegistro, cadastros: telaCadastros };
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
  if (tr && !ev.target.closest("a")) location.hash = tr.dataset.ir;
});
window.addEventListener("hashchange", navegar);

(async function iniciar() {
  estado.uid = lerSessao();
  if (estado.uid) {
    try { estado.eu = await api("GET", "/eu"); } catch { estado.uid = null; gravarSessao(null); }
  }
  if (!location.hash) location.hash = "#/inicio"; else navegar();
})();
