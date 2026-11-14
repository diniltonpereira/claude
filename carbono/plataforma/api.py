"""API REST da plataforma CarbonMG (FastAPI). Documentação interativa em /docs.

Autenticação do MVP: cabeçalho `X-Usuario-Id`. Em produção, troque por login gov.br
(OpenID Connect) para servidores e e-mail + senha + 2FA para os demais, mantendo a mesma
checagem de papéis (ver docs/ARQUITETURA.md).
"""
from __future__ import annotations

import io
import os
import sqlite3
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import anexos, auditoria, compensacao, fatores, marketplace, mrv, painel, registro, relatorio, terceirizadas
from .banco import ErroNegocio, NaoAutorizado, NaoEncontrado, buscar, conectar
from .enquadramento import (LIMIAR_CONCILIACAO, LIMIAR_MONITORAMENTO, calendario,
                            checar_projeto_publico, checar_reparticao_comunidade, enquadrar_operador,
                            fase_em)

NOME = os.environ.get("PLATAFORMA_NOME", "CarbonMG")


def _base_publica(request: Request) -> str:
    """Endereço público usado nos QR Codes. Atrás de proxy, defina CARBONO_URL_PUBLICA."""
    return os.environ.get("CARBONO_URL_PUBLICA", str(request.base_url)).rstrip("/")
PAPEIS_CONTROLE = ("registrador", "verificador", "moderador", "auditor")


# ---------------------------------------------------------------- modelos
class OrgIn(BaseModel):
    nome: str = Field(min_length=2)
    cnpj: str = Field(pattern=r"^\d{14}$")
    tipo: Literal["orgao_publico", "ppp", "concessao", "concessao_florestal", "empresa"]
    esfera: Literal["federal", "estadual", "municipal"] | None = None
    municipio_ibge: str | None = Field(default=None, pattern=r"^\d{7}$")
    contrato_ref: str | None = None
    poder_concedente_id: int | None = None


class UsuarioIn(BaseModel):
    nome: str
    papel: Literal["elaborador", "verificador", "moderador", "registrador", "fiscal", "fornecedor", "auditor"]
    organizacao_id: int | None = None
    acreditado: bool = False  # organismo de inspeção acreditado (art. 32, parágrafo único)


class FatorIn(BaseModel):
    codigo: str = Field(pattern=r"^[a-z0-9_]{2,40}$")
    valor: float = Field(ge=0)
    fonte: str = Field(min_length=3)
    ano_referencia: int = Field(ge=1990, le=2100)
    descricao: str | None = None
    unidade: str | None = None
    escopo: int | None = Field(default=None, ge=1, le=3)
    fracao_biogenica_padrao: float | None = Field(default=None, ge=0, le=1)
    biogenico_por_unidade: float | None = Field(default=None, ge=0)


class InventarioIn(BaseModel):
    organizacao_id: int
    ano: int = Field(ge=2000, le=2100)
    gwp_conjunto: Literal["AR5", "AR6"] = "AR5"


class AtividadeIn(BaseModel):
    descricao: str = Field(min_length=2)
    quantidade: float = Field(ge=0)
    fator: str | None = None
    gas: str | None = None
    escopo: int = Field(default=1, ge=1, le=3)
    fracao_biogenica: float | None = Field(default=None, ge=0, le=1)
    evidencia: str | None = None
    remocao: bool = False
    evitada: bool = False
    instalacao_id: int | None = None
    mes: int | None = Field(default=None, ge=1, le=12)
    anexo_id: int | None = None


class AtividadeEdicao(BaseModel):
    descricao: str | None = None
    quantidade: float | None = Field(default=None, ge=0)
    mes: int | None = Field(default=None, ge=1, le=12)
    instalacao_id: int | None = None
    anexo_id: int | None = None
    evidencia: str | None = None


class InstalacaoIn(BaseModel):
    nome: str = Field(min_length=2)
    natureza: Literal["unidade", "instalacao", "fonte_movel"] = "instalacao"
    pai_id: int | None = None
    identificador: str | None = None
    municipio_uf: str | None = None
    agropecuaria_primaria: bool = False      # art. 1º, § 2º
    residuos_neutralizacao: bool = False     # art. 30, § 3º


class TransicaoIn(BaseModel):
    acao: Literal["submeter", "assumir", "verificar", "devolver", "aprovar"]
    parecer: str | None = None


class EnquadramentoIn(BaseModel):
    data_referencia: date | None = None
    gera_creditos: bool = False
    terras_publicas_ou_tradicionais: bool = False
    limiar_monitoramento: float = Field(default=LIMIAR_MONITORAMENTO, ge=LIMIAR_MONITORAMENTO)
    limiar_conciliacao: float = Field(default=LIMIAR_CONCILIACAO, ge=LIMIAR_CONCILIACAO)
    fase1_prorrogada: bool = True


class ProjetoPublicoIn(BaseModel):
    esfera: Literal["federal", "estadual", "municipal"]
    proprietario_e_usufrutuario: bool
    sobreposicao_terceiros: bool
    parceria_privada: bool
    licitacao: bool
    anuencia_gestor_area: bool
    programa_jurisdicional: bool = False
    venda_antecipada: bool = False


class ReparticaoIn(BaseModel):
    tipo_projeto: Literal["remocao", "redd_mercado"]
    percentual_comunidade: float = Field(ge=0, le=100)


class MetaIn(BaseModel):
    ano_base: int = Field(ge=2000, le=2100)
    ano_alvo: int = Field(ge=2000, le=2100)
    reducao_percentual: float = Field(gt=0, le=100)


class EmissaoIn(BaseModel):
    conta_id: int
    tipo_ativo: Literal["CBE", "CRVE", "VOLUNTARIO"]
    projeto: str
    metodologia: str
    safra: int = Field(ge=2000, le=2100)
    quantidade: int = Field(gt=0)
    serial: str = Field(min_length=3)
    certificadora: str | None = None
    certificado_anexo_id: int | None = None
    chave_idempotencia: str | None = None


class TransferenciaIn(BaseModel):
    lote_id: int
    quantidade: int = Field(gt=0)
    para_conta: int
    chave_idempotencia: str | None = None


class CancelamentoIn(BaseModel):
    lote_id: int
    quantidade: int = Field(gt=0)
    finalidade: Literal["compensacao_voluntaria", "conciliacao_sbce", "invalidacao"]
    beneficiario: str = Field(min_length=2)
    registro_externo_ref: str | None = None
    chave_idempotencia: str | None = None


class CompensacaoIn(BaseModel):
    finalidade: Literal["compensacao_voluntaria", "conciliacao_sbce"] = "compensacao_voluntaria"
    percentual: float = Field(default=100, gt=0, le=100)
    escopos: list[int] = [1, 2]
    idade_maxima_safra: int = Field(default=5, ge=0, le=30)
    limite_crve: float = Field(default=0.0, ge=0, le=1)
    tipos_aceitos: list[Literal["CBE", "CRVE", "VOLUNTARIO"]] | None = None
    periodo_compromisso: tuple[int, int] | None = None
    cbe_outros_periodos: bool = False
    registros_externos: dict[int, str] | None = None
    simular: bool = False
    chave_idempotencia: str | None = None


class ContratoIn(BaseModel):
    orgao_id: int
    fornecedor_cnpj: str = Field(pattern=r"^\d{14}$")
    fornecedor_nome: str = Field(min_length=2)
    numero: str = Field(min_length=1)
    objeto: str = Field(min_length=3)
    vigencia_inicio: date
    vigencia_fim: date
    clausula_relato: bool = False
    dia_limite: int = Field(default=10, ge=1, le=28)


class LancamentoIn(BaseModel):
    ano: int = Field(ge=2000, le=2100)
    mes: int = Field(ge=1, le=12)
    descricao: str = Field(min_length=2)
    fator: str
    quantidade: float = Field(ge=0)
    anexo_id: int | None = None


class AcaoIn(BaseModel):
    data: date
    descricao: str = Field(min_length=3)
    anexo_id: int


class NotificacaoIn(BaseModel):
    assunto: str = Field(min_length=3)
    texto: str = Field(min_length=3)
    prazo: date | None = None
    anexo_id: int | None = None


class CredenciamentoIn(BaseModel):
    documentos: dict[str, int]
    declaracao_dupla_contagem: bool


class AnaliseIn(BaseModel):
    criterios: dict[str, bool]
    parecer: str | None = None


class OfertaIn(BaseModel):
    lote_id: int
    categoria: Literal["redd", "restauracao", "energia", "residuos_biogas", "eficiencia", "outros"]
    municipio: str = Field(min_length=2)
    uf: str = Field(pattern=r"^[A-Za-z]{2}$")
    preco_unitario: float = Field(gt=0)
    quantidade: int = Field(gt=0)


class SimulacaoIn(BaseModel):
    quantidade: int = Field(gt=0)


class PedidoIn(BaseModel):
    oferta_id: int
    quantidade: int = Field(gt=0)
    inventario_id: int | None = None


class InstrucaoIn(BaseModel):
    processo: str = Field(min_length=3)


class EmpenhoIn(BaseModel):
    numero: str = Field(min_length=3)
    data: date
    valor: float = Field(gt=0)


class LiquidacaoIn(BaseModel):
    nota_fiscal: str = Field(min_length=1)
    conferido: bool


class PagamentoIn(BaseModel):
    referencia: str = Field(min_length=3)
    data: date


class CancelamentoPedidoIn(BaseModel):
    motivo: str = Field(min_length=3)


# ---------------------------------------------------------------- aplicação
def _qr_svg(texto: str) -> Response:
    import segno
    buf = io.BytesIO()
    segno.make_qr(texto, error="m").save(buf, kind="svg", scale=5, border=2, dark="#172A3A")
    return Response(buf.getvalue(), media_type="image/svg+xml")


def criar_app(caminho_banco: str | None = None) -> FastAPI:
    app = FastAPI(title=f"{NOME} — API",
                  description="Inventário de emissões, gestão de terceirizadas, registro e compensação "
                              "de créditos para órgãos públicos, PPPs e concessões. A plataforma não "
                              "certifica, não verifica e não emite créditos.", version="0.2.0")
    caminho = caminho_banco or os.environ.get("CARBONO_DB", "carbono.db")
    conectar(caminho).close()  # cria o esquema e carrega os fatores iniciais
    app.state.caminho = caminho

    @app.exception_handler(ErroNegocio)
    async def _neg(_: Request, exc: ErroNegocio):
        return JSONResponse(status_code=409, content={"erro": str(exc)})

    @app.exception_handler(NaoAutorizado)
    async def _aut(_: Request, exc: NaoAutorizado):
        return JSONResponse(status_code=403, content={"erro": str(exc)})

    @app.exception_handler(NaoEncontrado)
    async def _nf(_: Request, exc: NaoEncontrado):
        return JSONResponse(status_code=404, content={"erro": str(exc)})

    @app.middleware("http")
    async def _ip(request: Request, call_next):
        token = auditoria.ip_atual.set(request.client.host if request.client else None)
        try:
            return await call_next(request)
        finally:
            auditoria.ip_atual.reset(token)

    def db():
        c = conectar(app.state.caminho, criar_esquema=False)
        try:
            yield c
        finally:
            c.close()

    def usuario(x_usuario_id: int = Header(...), c: sqlite3.Connection = Depends(db)) -> sqlite3.Row:
        try:
            return buscar(c, "usuario", x_usuario_id)
        except NaoEncontrado:
            raise HTTPException(401, "Usuário não identificado.") from None

    def registrador(u=Depends(usuario)):
        if u["papel"] != "registrador":
            raise NaoAutorizado("Operação restrita ao registrador.")
        return u

    def _pode_ver_org(u, organizacao_id: int) -> None:
        if u["papel"] not in PAPEIS_CONTROLE and u["organizacao_id"] != organizacao_id:
            raise NaoAutorizado("Dados de outra organização.")

    def _contrato_visivel(c, u, cid: int):
        ct = buscar(c, "contrato", cid)
        if not terceirizadas.pode_ver(c, u["id"], ct):
            raise NaoAutorizado("Contrato de outra organização.")
        return ct

    # ---------------- referências e público ----------------
    @app.get("/saude")
    def saude(c=Depends(db)):
        c.execute("SELECT 1").fetchone()
        return {"status": "ok", "plataforma": NOME}

    @app.get("/sbce/calendario")
    def ver_calendario(fase1_prorrogada: bool = True):
        """Fases de implementação do SBCE (Lei 15.042/2024, art. 50)."""
        return {"fase_atual": fase_em(date.today(), fase1_prorrogada).numero,
                "fases": [vars(f) for f in calendario(fase1_prorrogada)]}

    @app.post("/checagens/projeto-publico")
    def chk_projeto(d: ProjetoPublicoIn):
        """Pendências de projeto público ou programa jurisdicional (arts. 43 e 49)."""
        p = checar_projeto_publico(**d.model_dump())
        return {"apto": not p, "pendencias": p}

    @app.post("/checagens/reparticao-comunidade")
    def chk_reparticao(d: ReparticaoIn):
        """Mínimo de créditos para povos indígenas e comunidades tradicionais (art. 47, I, b)."""
        p = checar_reparticao_comunidade(d.tipo_projeto, d.percentual_comunidade)
        return {"apto": not p, "pendencias": p}

    @app.get("/fatores")
    def listar_fatores(historico: bool = False, c=Depends(db)):
        return {"gwp": fatores.conjuntos_gwp(c), "fatores": fatores.listar(c, historico)}

    @app.post("/fatores", status_code=201)
    def publicar_fator(d: FatorIn, u=Depends(usuario), c=Depends(db)):
        """Publica nova versão de um fator (a anterior fica preservada para auditoria)."""
        dados = d.model_dump()
        return {"id": fatores.nova_versao(c, u["id"], dados.pop("codigo"), **dados)}

    @app.get("/publico/cancelamentos/{hash_}")
    def verificar_cancelamento(hash_: str, c=Depends(db)):
        """Verificação pública de um certificado de cancelamento pelo hash."""
        return registro.certificado_por_hash(c, hash_)

    @app.get("/publico/cancelamentos/{hash_}/qr.svg", include_in_schema=False)
    def qr_cancelamento(hash_: str, request: Request, c=Depends(db)):
        registro.certificado_por_hash(c, hash_)
        return _qr_svg(f"{_base_publica(request)}/painel/#/verificar/cancelamento/{hash_}")

    @app.get("/publico/selos/{codigo}")
    def verificar_selo(codigo: str, c=Depends(db)):
        """Página pública do selo (aberta pelo QR Code)."""
        return terceirizadas.verificar_selo(c, codigo)

    @app.get("/publico/selos/{codigo}/qr.svg", include_in_schema=False)
    def qr_selo(codigo: str, request: Request, c=Depends(db)):
        terceirizadas.verificar_selo(c, codigo)
        return _qr_svg(f"{_base_publica(request)}/painel/#/verificar/selo/{codigo}")

    @app.get("/publico/organizacoes")
    def orgs_publicas(c=Depends(db)):
        """Órgãos com inventário aprovado (painel de transparência, Lei 12.527/2011)."""
        ids = [r["organizacao_id"] for r in c.execute(
            "SELECT DISTINCT organizacao_id FROM inventario WHERE status='aprovado'")]
        return [{"id": i, **painel.transparencia_organizacao(c, i)} for i in ids]

    @app.get("/publico/organizacoes/{oid}")
    def org_publica(oid: int, c=Depends(db)):
        return painel.transparencia_organizacao(c, oid)

    @app.get("/registro/auditoria")
    def integridade(c=Depends(db)):
        """Público: integridade do livro-razão de créditos."""
        return registro.verificar_cadeia(c)

    @app.get("/registro/cancelamentos")
    def cancelamentos_publicos(c=Depends(db)):
        """Público: quem cancelou créditos e para quê, com o hash verificável."""
        return [dict(r) for r in c.execute(
            """SELECT l.serial, l.tipo_ativo, l.projeto, l.certificadora, l.safra, l.quantidade,
                      l.finalidade, l.beneficiario, l.registro_externo_ref, m.hash
               FROM lote l JOIN movimento m ON m.lote_id = l.id AND m.tipo = 'cancelamento'
               WHERE l.status = 'cancelado' ORDER BY l.id""")]

    # ---------------- cadastros ----------------
    @app.post("/organizacoes", status_code=201)
    def criar_org(d: OrgIn, u=Depends(registrador), c=Depends(db)):
        try:
            oid = c.execute(
                """INSERT INTO organizacao (nome, cnpj, tipo, esfera, municipio_ibge, contrato_ref,
                   poder_concedente_id) VALUES (?,?,?,?,?,?,?)""",
                (d.nome, d.cnpj, d.tipo, d.esfera, d.municipio_ibge, d.contrato_ref,
                 d.poder_concedente_id)).lastrowid
        except sqlite3.IntegrityError as exc:
            raise ErroNegocio(f"Organização inválida ou CNPJ já cadastrado: {exc}") from exc
        auditoria.registrar(c, u["id"], "criar", "organizacao", oid, depois=d.model_dump())
        return {"id": oid, "conta_id": registro.abrir_conta(c, oid)}

    @app.get("/eu")
    def eu(u=Depends(usuario), c=Depends(db)):
        org, conta = None, None
        if u["organizacao_id"]:
            org = dict(buscar(c, "organizacao", u["organizacao_id"]))
            r = c.execute("SELECT id FROM conta WHERE organizacao_id=?", (org["id"],)).fetchone()
            conta = r["id"] if r else None
        return {"usuario": dict(u), "organizacao": org, "conta_id": conta, "plataforma": NOME}

    @app.get("/organizacoes")
    def listar_orgs(u=Depends(usuario), c=Depends(db)):
        sql = """SELECT o.*, ct.id AS conta_id FROM organizacao o
                 LEFT JOIN conta ct ON ct.organizacao_id = o.id"""
        if u["papel"] in ("registrador", "auditor"):
            return [dict(r) for r in c.execute(sql + " ORDER BY o.nome")]
        return [dict(r) for r in c.execute(sql + " WHERE o.id=?", (u["organizacao_id"],))]

    @app.get("/contas")
    def listar_contas(_=Depends(usuario), c=Depends(db)):
        """Contas do registro (destino de transferências). Não expõe saldos."""
        return [dict(r) for r in c.execute(
            """SELECT ct.id AS conta_id, o.nome, o.tipo FROM conta ct
               JOIN organizacao o ON o.id = ct.organizacao_id ORDER BY o.nome""")]

    @app.get("/usuarios")
    def listar_usuarios(_=Depends(registrador), c=Depends(db)):
        return [dict(r) for r in c.execute(
            """SELECT u.*, o.nome AS organizacao FROM usuario u
               LEFT JOIN organizacao o ON o.id = u.organizacao_id ORDER BY u.id""")]

    @app.post("/usuarios", status_code=201)
    def criar_usuario(d: UsuarioIn, u=Depends(registrador), c=Depends(db)):
        if d.organizacao_id is not None:
            buscar(c, "organizacao", d.organizacao_id)
        uid = c.execute("INSERT INTO usuario (nome, papel, organizacao_id, acreditado) VALUES (?,?,?,?)",
                        (d.nome, d.papel, d.organizacao_id, int(d.acreditado))).lastrowid
        auditoria.registrar(c, u["id"], "criar", "usuario", uid, depois=d.model_dump())
        return {"id": uid}

    # ---------------- unidades e instalações ----------------
    @app.post("/organizacoes/{oid}/instalacoes", status_code=201)
    def criar_inst(oid: int, d: InstalacaoIn, u=Depends(usuario), c=Depends(db)):
        return {"id": mrv.criar_instalacao(c, u["id"], oid, **d.model_dump())}

    @app.get("/organizacoes/{oid}/instalacoes")
    def listar_inst(oid: int, u=Depends(usuario), c=Depends(db)):
        _pode_ver_org(u, oid)
        return mrv.listar_instalacoes(c, oid)

    @app.get("/organizacoes/{oid}/painel")
    def ver_painel(oid: int, ano: int, u=Depends(usuario), c=Depends(db)):
        _pode_ver_org(u, oid)
        return painel.painel(c, oid, ano)

    @app.post("/organizacoes/{oid}/metas", status_code=201)
    def criar_meta(oid: int, d: MetaIn, u=Depends(usuario), c=Depends(db)):
        return {"id": painel.definir_meta(c, u["id"], oid, **d.model_dump())}

    # ---------------- anexos ----------------
    @app.post("/anexos", status_code=201)
    async def enviar_anexo(arquivo: UploadFile = File(...), u=Depends(usuario), c=Depends(db)):
        conteudo = await arquivo.read(anexos.TAMANHO_MAXIMO + 1)
        return anexos.salvar(c, u["id"], arquivo.filename or "anexo", arquivo.content_type or "", conteudo)

    @app.get("/anexos/{aid}")
    def baixar_anexo(aid: int, u=Depends(usuario), c=Depends(db)):
        a, conteudo = anexos.abrir(c, u["id"], aid)
        return Response(conteudo, media_type=a["tipo_mime"],
                        headers={"Content-Disposition": f'attachment; filename="{a["sha256"][:12]}"',
                                 "X-Content-Type-Options": "nosniff"})

    # ---------------- inventário (MRV) ----------------
    @app.post("/inventarios", status_code=201)
    def criar_inv(d: InventarioIn, u=Depends(usuario), c=Depends(db)):
        return {"id": mrv.criar_inventario(c, u["id"], d.organizacao_id, d.ano, d.gwp_conjunto)}

    def _inv_visivel(c, u, iid: int):
        inv = buscar(c, "inventario", iid)
        _pode_ver_org(u, inv["organizacao_id"])
        return inv

    @app.get("/inventarios")
    def listar_inv(u=Depends(usuario), c=Depends(db)):
        """Fila de trabalho conforme o papel do usuário."""
        base = f"""SELECT i.*, o.nome AS organizacao, o.tipo AS tipo_organizacao,
                    COALESCE((SELECT SUM(tco2e) FROM atividade a WHERE a.inventario_id=i.id
                              AND a.natureza='emissao' AND {mrv.COMPROVADA}),0) AS total_tco2e
                 FROM inventario i JOIN organizacao o ON o.id = i.organizacao_id"""
        filtros = {
            "elaborador": (" WHERE i.organizacao_id=?", (u["organizacao_id"],)),
            "verificador": (" WHERE i.status='submetido' OR i.verificado_por=?", (u["id"],)),
            "moderador": (" WHERE i.status='verificado' OR i.moderado_por=?", (u["id"],)),
            "registrador": ("", ()), "auditor": ("", ()),
        }
        if u["papel"] not in filtros:
            return []
        onde, args = filtros[u["papel"]]
        return [dict(r) for r in c.execute(base + onde + " ORDER BY i.ano DESC, i.id DESC", args)]

    @app.get("/inventarios/{iid}")
    def ver_inv(iid: int, u=Depends(usuario), c=Depends(db)):
        inv = _inv_visivel(c, u, iid)
        ativ = [dict(r) for r in c.execute(
            """SELECT a.*, x.nome_original AS anexo_nome, x.sha256 AS anexo_sha256,
                      f.versao AS fator_versao, i.nome AS unidade
               FROM atividade a LEFT JOIN anexo x ON x.id=a.anexo_id
               LEFT JOIN fator_emissao f ON f.id=a.fator_id LEFT JOIN instalacao i ON i.id=a.instalacao_id
               WHERE a.inventario_id=? ORDER BY a.id""", (iid,))]
        for a in ativ:
            a["comprovada"] = bool(a["anexo_id"] or a["contrato_id"])
        return {**dict(inv), "totais": mrv.totais(c, iid), "atividades": ativ,
                "por_instalacao": mrv.totais_por_instalacao(c, iid),
                "historico": mrv.historico(c, iid),
                "compensado": compensacao.ja_compensado(c, iid, "compensacao_voluntaria"),
                "conciliado": compensacao.ja_compensado(c, iid, "conciliacao_sbce")}

    @app.post("/inventarios/{iid}/atividades", status_code=201)
    def add_ativ(iid: int, d: AtividadeIn, u=Depends(usuario), c=Depends(db)):
        return {"id": mrv.adicionar_atividade(c, u["id"], iid, **d.model_dump())}

    @app.patch("/atividades/{aid}")
    def editar_ativ(aid: int, d: AtividadeEdicao, u=Depends(usuario), c=Depends(db)):
        mrv.editar_atividade(c, u["id"], aid, **d.model_dump())
        return {"ok": True}

    @app.delete("/atividades/{aid}")
    def remover_ativ(aid: int, u=Depends(usuario), c=Depends(db)):
        mrv.remover_atividade(c, u["id"], aid)
        return {"ok": True}

    @app.post("/inventarios/{iid}/importar-terceirizadas")
    def importar(iid: int, u=Depends(usuario), c=Depends(db)):
        """Traz para o escopo 3 as emissões comprovadas das terceirizadas no ano do inventário."""
        return terceirizadas.importar_para_inventario(c, u["id"], iid)

    @app.post("/inventarios/{iid}/transicoes")
    def transicao(iid: int, d: TransicaoIn, u=Depends(usuario), c=Depends(db)):
        return {"status": mrv.transitar(c, u["id"], iid, d.acao, d.parecer)}

    @app.post("/inventarios/{iid}/enquadramento")
    def enq(iid: int, d: EnquadramentoIn, u=Depends(usuario), c=Depends(db)):
        inv = _inv_visivel(c, u, iid)
        org = buscar(c, "organizacao", inv["organizacao_id"])
        r = enquadrar_operador(org["tipo"], mrv.totais_por_instalacao(c, iid), esfera=org["esfera"],
                               **d.model_dump())
        if inv["status"] != "aprovado":
            r["alertas"].append("Inventário ainda não aprovado: enquadramento preliminar.")
        return r

    @app.post("/inventarios/{iid}/compensacoes")
    def compensar(iid: int, d: CompensacaoIn, u=Depends(usuario), c=Depends(db)):
        dados = d.model_dump()
        dados["escopos"] = tuple(dados["escopos"])
        if dados["tipos_aceitos"] is not None:
            dados["tipos_aceitos"] = tuple(dados["tipos_aceitos"])
        return compensacao.compensar(c, u["id"], iid, **dados)

    @app.get("/inventarios/{iid}/relatorio.html", response_class=HTMLResponse)
    def rel_html(iid: int, request: Request, u=Depends(usuario), c=Depends(db)):
        _inv_visivel(c, u, iid)
        return HTMLResponse(relatorio.html_relatorio(c, iid, _base_publica(request)))

    @app.get("/inventarios/{iid}/relatorio.xlsx")
    def rel_xlsx(iid: int, u=Depends(usuario), c=Depends(db)):
        inv = _inv_visivel(c, u, iid)
        return Response(relatorio.xlsx_relatorio(c, iid),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="inventario-{inv["ano"]}.xlsx"'})

    # ---------------- terceirizadas ----------------
    @app.post("/contratos", status_code=201)
    def convidar(d: ContratoIn, u=Depends(usuario), c=Depends(db)):
        dados = d.model_dump()
        dados["vigencia_inicio"] = d.vigencia_inicio.isoformat()
        dados["vigencia_fim"] = d.vigencia_fim.isoformat()
        return {"id": terceirizadas.convidar(c, u["id"], **dados)}

    @app.get("/contratos")
    def listar_contratos(u=Depends(usuario), c=Depends(db)):
        return terceirizadas.listar(c, u["id"])

    @app.get("/contratos/{cid}")
    def ver_contrato(cid: int, u=Depends(usuario), c=Depends(db)):
        ct = _contrato_visivel(c, u, cid)
        score = c.execute("SELECT * FROM score_fornecedor WHERE contrato_id=? ORDER BY ano DESC, mes DESC "
                          "LIMIT 1", (cid,)).fetchone()
        nomes = c.execute("""SELECT o.nome AS orgao, f.nome AS fornecedor, f.cnpj AS fornecedor_cnpj
                             FROM organizacao o, organizacao f WHERE o.id=? AND f.id=?""",
                          (ct["orgao_id"], ct["fornecedor_id"])).fetchone()
        return {**dict(ct), **dict(nomes), "situacao_mensal": terceirizadas.situacao_mensal(c, cid),
                "lancamentos": terceirizadas.lancamentos(c, cid),
                "notificacoes": terceirizadas.notificacoes(c, cid),
                "score": dict(score) if score else None,
                "selos": [dict(r) for r in c.execute(
                    "SELECT codigo, classe, score, emitido_em, valido_ate FROM selo WHERE contrato_id=? "
                    "ORDER BY id DESC", (cid,))]}

    @app.post("/contratos/{cid}/aceite")
    def aceitar(cid: int, u=Depends(usuario), c=Depends(db)):
        terceirizadas.aceitar(c, u["id"], cid)
        return {"ok": True}

    @app.post("/contratos/{cid}/lancamentos", status_code=201)
    def lancar(cid: int, d: LancamentoIn, u=Depends(usuario), c=Depends(db)):
        return {"id": terceirizadas.lancar(c, u["id"], cid, **d.model_dump())}

    @app.post("/contratos/{cid}/acoes", status_code=201)
    def acao(cid: int, d: AcaoIn, u=Depends(usuario), c=Depends(db)):
        return {"id": terceirizadas.registrar_acao(c, u["id"], cid, data=d.data.isoformat(),
                                                   descricao=d.descricao, anexo_id=d.anexo_id)}

    @app.post("/contratos/{cid}/score")
    def score(cid: int, u=Depends(usuario), c=Depends(db)):
        _contrato_visivel(c, u, cid)
        return terceirizadas.calcular_score(c, cid)

    @app.post("/contratos/{cid}/selos", status_code=201)
    def selo(cid: int, u=Depends(usuario), c=Depends(db)):
        return terceirizadas.emitir_selo(c, u["id"], cid)

    @app.post("/contratos/{cid}/notificacoes", status_code=201)
    def notificar(cid: int, d: NotificacaoIn, u=Depends(usuario), c=Depends(db)):
        return {"id": terceirizadas.notificar(c, u["id"], cid, assunto=d.assunto, texto=d.texto,
                                              prazo=d.prazo.isoformat() if d.prazo else None,
                                              anexo_id=d.anexo_id)}

    # ---------------- registro de créditos ----------------
    @app.get("/contas/{cid}/saldo")
    def ver_saldo(cid: int, u=Depends(usuario), c=Depends(db)):
        conta = buscar(c, "conta", cid)
        if u["papel"] not in ("registrador", "auditor") and u["organizacao_id"] != conta["organizacao_id"]:
            raise NaoAutorizado("Conta de outra organização.")
        lotes = registro.saldo(c, cid)
        return {"conta_id": cid, "total": sum(l["quantidade"] for l in lotes), "lotes": lotes}

    @app.post("/registro/emissoes", status_code=201)
    def emitir(d: EmissaoIn, u=Depends(usuario), c=Depends(db)):
        return registro.emitir(c, u["id"], **d.model_dump())

    @app.post("/registro/transferencias")
    def transferir(d: TransferenciaIn, u=Depends(usuario), c=Depends(db)):
        return registro.transferir(c, u["id"], **d.model_dump())

    @app.post("/registro/cancelamentos")
    def cancelar(d: CancelamentoIn, u=Depends(usuario), c=Depends(db)):
        """Cancelamento de ativos (Lei 15.042/2024, art. 2º, II, e art. 45). Irreversível."""
        return registro.cancelar(c, u["id"], **d.model_dump())

    # ---------------- compra de créditos (credenciamento, catálogo, pedidos) ----------------
    @app.get("/marketplace/regras")
    def regras_marketplace():
        """Critérios de credenciamento, categorias e taxa da plataforma (públicos)."""
        return {"criterios": marketplace.CRITERIOS, "categorias": marketplace.CATEGORIAS,
                "taxa_percentual": marketplace.taxa_percentual()}

    @app.post("/credenciamentos", status_code=201)
    def solicitar_cred(d: CredenciamentoIn, u=Depends(usuario), c=Depends(db)):
        return {"id": marketplace.solicitar_credenciamento(c, u["id"], **d.model_dump())}

    @app.get("/credenciamentos")
    def listar_cred(u=Depends(usuario), c=Depends(db)):
        return marketplace.credenciamentos(c, u["id"])

    @app.post("/credenciamentos/{cid}/analise")
    def analisar_cred(cid: int, d: AnaliseIn, u=Depends(usuario), c=Depends(db)):
        return {"status": marketplace.analisar_credenciamento(c, u["id"], cid, **d.model_dump())}

    @app.get("/ofertas")
    def ver_catalogo(uf: str | None = None, categoria: str | None = None, _=Depends(usuario), c=Depends(db)):
        return marketplace.catalogo(c, uf=uf, categoria=categoria)

    @app.get("/ofertas/minhas")
    def minhas_ofertas(u=Depends(usuario), c=Depends(db)):
        return [dict(r) for r in c.execute(
            """SELECT o.*, l.serial, l.tipo_ativo, l.certificadora FROM oferta o JOIN lote l ON l.id=o.lote_id
               WHERE o.fornecedor_id=? ORDER BY o.id DESC""", (u["organizacao_id"],))]

    @app.post("/ofertas", status_code=201)
    def publicar(d: OfertaIn, u=Depends(usuario), c=Depends(db)):
        return {"id": marketplace.publicar_oferta(c, u["id"], **d.model_dump())}

    @app.delete("/ofertas/{oid}")
    def retirar(oid: int, u=Depends(usuario), c=Depends(db)):
        marketplace.retirar_oferta(c, u["id"], oid)
        return {"ok": True}

    @app.post("/ofertas/{oid}/simulacao")
    def simular(oid: int, d: SimulacaoIn, _=Depends(usuario), c=Depends(db)):
        return marketplace.simular(c, oid, d.quantidade)

    @app.post("/pedidos", status_code=201)
    def criar_pedido(d: PedidoIn, u=Depends(usuario), c=Depends(db)):
        return {"id": marketplace.criar_pedido(c, u["id"], **d.model_dump())}

    @app.get("/pedidos")
    def listar_pedidos(u=Depends(usuario), c=Depends(db)):
        return marketplace.listar_pedidos(c, u["id"])

    @app.get("/pedidos/{pid}")
    def ver_pedido(pid: int, u=Depends(usuario), c=Depends(db)):
        return marketplace.ver_pedido(c, u["id"], pid)

    @app.get("/pedidos/{pid}/minuta-tr.html", response_class=HTMLResponse)
    def minuta(pid: int, u=Depends(usuario), c=Depends(db)):
        return HTMLResponse(marketplace.minuta_tr_html(c, u["id"], pid))

    @app.post("/pedidos/{pid}/instrucao")
    def instruir(pid: int, d: InstrucaoIn, u=Depends(usuario), c=Depends(db)):
        marketplace.instruir(c, u["id"], pid, processo=d.processo)
        return {"ok": True}

    @app.post("/pedidos/{pid}/empenho")
    def empenho(pid: int, d: EmpenhoIn, u=Depends(usuario), c=Depends(db)):
        marketplace.registrar_empenho(c, u["id"], pid, numero=d.numero, data=d.data.isoformat(), valor=d.valor)
        return {"ok": True}

    @app.post("/pedidos/{pid}/entrega")
    def entrega(pid: int, u=Depends(usuario), c=Depends(db)):
        return marketplace.entregar(c, u["id"], pid)

    @app.post("/pedidos/{pid}/liquidacao")
    def liquidacao(pid: int, d: LiquidacaoIn, u=Depends(usuario), c=Depends(db)):
        marketplace.liquidar(c, u["id"], pid, nota_fiscal=d.nota_fiscal, conferido=d.conferido)
        return {"ok": True}

    @app.post("/pedidos/{pid}/pagamento")
    def pagamento(pid: int, d: PagamentoIn, u=Depends(usuario), c=Depends(db)):
        marketplace.registrar_pagamento(c, u["id"], pid, referencia=d.referencia, data=d.data.isoformat())
        return {"ok": True}

    @app.post("/pedidos/{pid}/cancelamento")
    def cancelar_ped(pid: int, d: CancelamentoPedidoIn, u=Depends(usuario), c=Depends(db)):
        marketplace.cancelar_pedido(c, u["id"], pid, motivo=d.motivo)
        return {"ok": True}

    @app.get("/organizacoes/{oid}/carteira")
    def ver_carteira(oid: int, u=Depends(usuario), c=Depends(db)):
        _pode_ver_org(u, oid)
        return marketplace.carteira(c, oid)

    # ---------------- auditoria ----------------
    @app.get("/auditoria")
    def trilha(entidade: str | None = None, entidade_id: int | None = None, limite: int = 200,
               u=Depends(usuario), c=Depends(db)):
        """Trilha de auditoria (quem, quando, IP, antes e depois). Registrador e auditor."""
        if u["papel"] not in ("registrador", "auditor"):
            raise NaoAutorizado("Trilha de auditoria restrita ao registrador e ao auditor.")
        return auditoria.consultar(c, entidade=entidade, entidade_id=entidade_id, limite=limite)

    web = Path(__file__).parent / "web"
    app.mount("/painel", StaticFiles(directory=web, html=True), name="painel")

    @app.get("/", include_in_schema=False)
    def inicio():
        return RedirectResponse("/painel/")

    return app
