"""API REST da plataforma (FastAPI). Documentação interativa em /docs.

Autenticação do MVP: cabeçalho `X-Usuario-Id`. Em produção, troque por login gov.br
(OpenID Connect) ou certificado ICP-Brasil e mantenha a mesma checagem de papéis.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import compensacao, mrv, registro
from .banco import ErroNegocio, NaoAutorizado, NaoEncontrado, buscar, conectar
from .enquadramento import (LIMIAR_CONCILIACAO, LIMIAR_MONITORAMENTO, calendario,
                            checar_projeto_publico, checar_reparticao_comunidade, enquadrar,
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

class TransicaoIn(BaseModel):
    acao: Literal["submeter", "assumir", "verificar", "devolver", "aprovar"]
    parecer: str | None = None

class EnquadramentoIn(BaseModel):
    data_referencia: date | None = None
    atividade_agropecuaria_primaria: bool = False
    residuos_com_neutralizacao: bool = False
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

    @app.post("/usuarios", status_code=201)
    def criar_usuario(d: UsuarioIn, _=Depends(registrador), c=Depends(db)):
        if d.organizacao_id is not None:
            buscar(c, "organizacao", d.organizacao_id)
        uid = c.execute("INSERT INTO usuario (nome, papel, organizacao_id, acreditado) VALUES (?,?,?,?)",
                        (d.nome, d.papel, d.organizacao_id, int(d.acreditado))).lastrowid
        return {"id": uid}

    # ---------------- MRV ----------------
    @app.post("/inventarios", status_code=201)
    def criar_inv(d: InventarioIn, u=Depends(usuario), c=Depends(db)):
        return {"id": mrv.criar_inventario(c, u["id"], d.organizacao_id, d.ano)}

    def _pode_ver(c, u, inv) -> None:
        if u["papel"] in ("registrador", "verificador", "moderador"):
            return
        if u["organizacao_id"] != inv["organizacao_id"]:
            raise NaoAutorizado("Inventário de outra organização.")

    @app.get("/inventarios/{iid}")
    def ver_inv(iid: int, u=Depends(usuario), c=Depends(db)):
        inv = buscar(c, "inventario", iid)
        _pode_ver(c, u, inv)
        ativ = [dict(r) for r in c.execute("SELECT * FROM atividade WHERE inventario_id=? ORDER BY id", (iid,))]
        return {**dict(inv), "totais": mrv.totais(c, iid), "atividades": ativ,
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
        t = mrv.totais(c, iid)
        r = enquadrar(org["tipo"], t["escopo1"], esfera=org["esfera"], **d.model_dump()).como_dict()
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

    return app
