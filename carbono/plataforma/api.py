"""API REST da plataforma (FastAPI). Documentação interativa em /docs.

Autenticação do MVP: cabeçalho `X-Usuario-Id`. Em produção, troque por login gov.br
(OpenID Connect) ou certificado ICP-Brasil e mantenha a mesma checagem de papéis.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from datetime import date
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import compensacao, mrv, registro
from .banco import ErroNegocio, NaoAutorizado, NaoEncontrado, buscar, conectar
from .enquadramento import (LIMIAR_CONCILIACAO, LIMIAR_MONITORAMENTO, calendario,
                            checar_projeto_publico, checar_reparticao_comunidade, enquadrar_operador,
                            fase_em)
from .fatores import FATORES, GWP_AR5


# ---------------- Modelos ----------------
class OrgIn(BaseModel):
    nome: str = Field(min_length=2)
    cnpj: str = Field(pattern=r"^\d{14}$")
    tipo: Literal["orgao_publico", "ppp", "concessao", "concessao_florestal", "empresa"]
    esfera: Literal["federal", "estadual", "municipal"] | None = None
    contrato_ref: str | None = None
    poder_concedente_id: int | None = None

class UsuarioIn(BaseModel):
    nome: str
    papel: Literal["elaborador", "verificador", "moderador", "registrador"]
    organizacao_id: int | None = None
    acreditado: bool = False  # organismo de inspeção acreditado (art. 32, parágrafo único)

class InventarioIn(BaseModel):
    organizacao_id: int
    ano: int = Field(ge=2000, le=2100)

class AtividadeIn(BaseModel):
    descricao: str
    quantidade: float = Field(ge=0)
    fator: str | None = None
    gas: str | None = None
    escopo: int = Field(default=1, ge=1, le=3)
    fracao_biogenica: float | None = Field(default=None, ge=0, le=1)
    evidencia: str | None = None
    remocao: bool = False
    instalacao_id: int | None = None


class InstalacaoIn(BaseModel):
    nome: str = Field(min_length=2)
    natureza: Literal["instalacao", "fonte_movel"] = "instalacao"
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

class EmissaoIn(BaseModel):
    conta_id: int
    tipo_ativo: Literal["CBE", "CRVE", "VOLUNTARIO"]
    projeto: str
    metodologia: str
    safra: int = Field(ge=2000, le=2100)
    quantidade: int = Field(gt=0)
    serial: str = Field(min_length=3)
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
    simular: bool = False
    chave_idempotencia: str | None = None



def criar_app(caminho_banco: str | None = None) -> FastAPI:
    app = FastAPI(title="Plataforma Carbono Público",
                  description="Gestão, aferição, moderação e compensação de carbono "
                              "para órgãos públicos, PPPs e concessões.", version="0.1.0")
    caminho = caminho_banco or os.environ.get("CARBONO_DB", "carbono.db")
    conectar(caminho).close()  # cria o esquema uma vez
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

    # ---------------- Referências ----------------
    @app.get("/saude")
    def saude(c=Depends(db)):
        c.execute("SELECT 1").fetchone()
        return {"status": "ok"}

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
    def listar_fatores():
        return {"gwp_ar5": GWP_AR5, "fatores": [vars(f) for f in FATORES.values()]}

    # ---------------- Cadastros ----------------
    @app.post("/organizacoes", status_code=201)
    def criar_org(d: OrgIn, _=Depends(registrador), c=Depends(db)):
        try:
            oid = c.execute(
                """INSERT INTO organizacao (nome, cnpj, tipo, esfera, contrato_ref, poder_concedente_id)
                   VALUES (?,?,?,?,?,?)""",
                (d.nome, d.cnpj, d.tipo, d.esfera, d.contrato_ref, d.poder_concedente_id)).lastrowid
        except sqlite3.IntegrityError as exc:
            raise ErroNegocio(f"Organização inválida ou CNPJ já cadastrado: {exc}") from exc
        return {"id": oid, "conta_id": registro.abrir_conta(c, oid)}

    @app.get("/eu")
    def eu(u=Depends(usuario), c=Depends(db)):
        """Usuário atual, sua organização e sua conta no registro."""
        org, conta = None, None
        if u["organizacao_id"]:
            org = dict(buscar(c, "organizacao", u["organizacao_id"]))
            r = c.execute("SELECT id FROM conta WHERE organizacao_id=?", (org["id"],)).fetchone()
            conta = r["id"] if r else None
        return {"usuario": dict(u), "organizacao": org, "conta_id": conta}

    @app.get("/organizacoes")
    def listar_orgs(u=Depends(usuario), c=Depends(db)):
        sql = """SELECT o.*, ct.id AS conta_id FROM organizacao o
                 LEFT JOIN conta ct ON ct.organizacao_id = o.id"""
        if u["papel"] == "registrador":
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
    def criar_usuario(d: UsuarioIn, _=Depends(registrador), c=Depends(db)):
        if d.organizacao_id is not None:
            buscar(c, "organizacao", d.organizacao_id)
        uid = c.execute("INSERT INTO usuario (nome, papel, organizacao_id, acreditado) VALUES (?,?,?,?)",
                        (d.nome, d.papel, d.organizacao_id, int(d.acreditado))).lastrowid
        return {"id": uid}

    # ---------------- Instalações e fontes (art. 2º, XII e XV) ----------------
    @app.post("/organizacoes/{oid}/instalacoes", status_code=201)
    def criar_inst(oid: int, d: InstalacaoIn, u=Depends(usuario), c=Depends(db)):
        return {"id": mrv.criar_instalacao(c, u["id"], oid, **d.model_dump())}

    @app.get("/organizacoes/{oid}/instalacoes")
    def listar_inst(oid: int, u=Depends(usuario), c=Depends(db)):
        if u["papel"] not in ("registrador", "verificador", "moderador") and u["organizacao_id"] != oid:
            raise NaoAutorizado("Instalações de outra organização.")
        return mrv.listar_instalacoes(c, oid)

    # ---------------- MRV ----------------
    @app.post("/inventarios", status_code=201)
    def criar_inv(d: InventarioIn, u=Depends(usuario), c=Depends(db)):
        return {"id": mrv.criar_inventario(c, u["id"], d.organizacao_id, d.ano)}

    def _pode_ver(c, u, inv) -> None:
        if u["papel"] in ("registrador", "verificador", "moderador"):
            return
        if u["organizacao_id"] != inv["organizacao_id"]:
            raise NaoAutorizado("Inventário de outra organização.")

    @app.get("/inventarios")
    def listar_inv(u=Depends(usuario), c=Depends(db)):
        """Fila de trabalho conforme o papel do usuário."""
        base = """SELECT i.*, o.nome AS organizacao, o.tipo AS tipo_organizacao,
                    COALESCE((SELECT SUM(tco2e) FROM atividade a WHERE a.inventario_id=i.id
                              AND a.natureza='emissao'),0) AS total_tco2e
                 FROM inventario i JOIN organizacao o ON o.id = i.organizacao_id"""
        filtros = {
            "elaborador": (" WHERE i.organizacao_id=?", (u["organizacao_id"],)),
            "verificador": (" WHERE i.status='submetido' OR i.verificado_por=?", (u["id"],)),
            "moderador": (" WHERE i.status='verificado' OR i.moderado_por=?", (u["id"],)),
            "registrador": ("", ()),
        }
        onde, args = filtros[u["papel"]]
        return [dict(r) for r in c.execute(base + onde + " ORDER BY i.ano DESC, i.id DESC", args)]

    @app.get("/inventarios/{iid}")
    def ver_inv(iid: int, u=Depends(usuario), c=Depends(db)):
        inv = buscar(c, "inventario", iid)
        _pode_ver(c, u, inv)
        ativ = [dict(r) for r in c.execute("SELECT * FROM atividade WHERE inventario_id=? ORDER BY id", (iid,))]
        return {**dict(inv), "totais": mrv.totais(c, iid), "atividades": ativ,
                "por_instalacao": mrv.totais_por_instalacao(c, iid),
                "historico": mrv.historico(c, iid),
                "compensado": compensacao.ja_compensado(c, iid, "compensacao_voluntaria"),
                "conciliado": compensacao.ja_compensado(c, iid, "conciliacao_sbce")}

    @app.post("/inventarios/{iid}/atividades", status_code=201)
    def add_ativ(iid: int, d: AtividadeIn, u=Depends(usuario), c=Depends(db)):
        return {"id": mrv.adicionar_atividade(c, u["id"], iid, **d.model_dump())}

    @app.post("/inventarios/{iid}/transicoes")
    def transicao(iid: int, d: TransicaoIn, u=Depends(usuario), c=Depends(db)):
        return {"status": mrv.transitar(c, u["id"], iid, d.acao, d.parecer)}

    @app.post("/inventarios/{iid}/enquadramento")
    def enq(iid: int, d: EnquadramentoIn, u=Depends(usuario), c=Depends(db)):
        inv = buscar(c, "inventario", iid)
        _pode_ver(c, u, inv)
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

    # ---------------- Registro ----------------
    @app.get("/contas/{cid}/saldo")
    def ver_saldo(cid: int, u=Depends(usuario), c=Depends(db)):
        conta = buscar(c, "conta", cid)
        if u["papel"] != "registrador" and u["organizacao_id"] != conta["organizacao_id"]:
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

    @app.get("/registro/auditoria")
    def auditoria(c=Depends(db)):
        """Público (transparência ativa, Lei 12.527/2011): integridade do livro-razão."""
        return registro.verificar_cadeia(c)

    @app.get("/registro/cancelamentos")
    def cancelamentos_publicos(c=Depends(db)):
        """Público: quem compensou o quê — evita alegações de neutralidade sem lastro."""
        return [dict(r) for r in c.execute(
            """SELECT serial, tipo_ativo, projeto, safra, quantidade, finalidade, beneficiario
               FROM lote WHERE status = 'cancelado' ORDER BY id""")]

    painel = Path(__file__).parent / "web"
    app.mount("/painel", StaticFiles(directory=painel, html=True), name="painel")

    @app.get("/", include_in_schema=False)
    def inicio():
        return RedirectResponse("/painel/")

    return app
