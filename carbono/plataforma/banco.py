"""Persistência em SQLite. As invariantes ficam no esquema, não só no código."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

VERSAO_ESQUEMA = 2

ESQUEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS organizacao (
    id              INTEGER PRIMARY KEY,
    nome            TEXT NOT NULL,
    cnpj            TEXT NOT NULL UNIQUE CHECK (length(cnpj) = 14),
    tipo            TEXT NOT NULL CHECK (tipo IN
                    ('orgao_publico','ppp','concessao','concessao_florestal','empresa')),
    esfera          TEXT CHECK (esfera IN ('federal','estadual','municipal')),
    municipio_ibge  TEXT CHECK (municipio_ibge IS NULL OR length(municipio_ibge) = 7),
    contrato_ref    TEXT,
    poder_concedente_id INTEGER REFERENCES organizacao(id)
);

CREATE TABLE IF NOT EXISTS usuario (
    id              INTEGER PRIMARY KEY,
    nome            TEXT NOT NULL,
    -- fiscal: fiscal de contrato do órgão; fornecedor: terceirizada; auditor: só leitura (TCE/TCU/CGU)
    papel           TEXT NOT NULL CHECK (papel IN ('elaborador','verificador','moderador','registrador',
                                                   'fiscal','fornecedor','auditor')),
    organizacao_id  INTEGER REFERENCES organizacao(id),
    -- Organismo de inspeção acreditado (Lei 15.042/2024, art. 32, parágrafo único)
    acreditado      INTEGER NOT NULL DEFAULT 0 CHECK (acreditado IN (0,1))
);

-- Fatores de emissão VERSIONADOS: nova versão nunca apaga a anterior (auditoria).
CREATE TABLE IF NOT EXISTS fator_emissao (
    id              INTEGER PRIMARY KEY,
    codigo          TEXT NOT NULL,
    versao          INTEGER NOT NULL CHECK (versao >= 1),
    descricao       TEXT NOT NULL,
    unidade         TEXT NOT NULL,
    escopo          INTEGER NOT NULL CHECK (escopo IN (1,2,3)),
    valor           REAL NOT NULL CHECK (valor >= 0),      -- tCO2e por unidade do combustível fóssil puro
    fracao_biogenica_padrao REAL NOT NULL DEFAULT 0 CHECK (fracao_biogenica_padrao BETWEEN 0 AND 1),
    biogenico_por_unidade   REAL NOT NULL DEFAULT 0 CHECK (biogenico_por_unidade >= 0),
    fonte           TEXT NOT NULL,
    ano_referencia  INTEGER NOT NULL,
    vigente         INTEGER NOT NULL DEFAULT 1 CHECK (vigente IN (0,1)),
    criado_por      INTEGER REFERENCES usuario(id),
    criado_em       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (codigo, versao)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_fator_vigente ON fator_emissao(codigo) WHERE vigente = 1;
CREATE TRIGGER IF NOT EXISTS fator_sem_delete BEFORE DELETE ON fator_emissao
BEGIN SELECT RAISE(ABORT, 'fatores não são apagados: crie nova versão'); END;
CREATE TRIGGER IF NOT EXISTS fator_valor_imutavel BEFORE UPDATE OF valor, fracao_biogenica_padrao,
    biogenico_por_unidade, codigo, versao ON fator_emissao
BEGIN SELECT RAISE(ABORT, 'valor de fator publicado é imutável: crie nova versão'); END;

-- Potencial de aquecimento global por conjunto (AR5, AR6).
CREATE TABLE IF NOT EXISTS gwp (
    conjunto        TEXT NOT NULL,
    gas             TEXT NOT NULL,
    valor           REAL NOT NULL CHECK (valor > 0),
    fonte           TEXT NOT NULL,
    PRIMARY KEY (conjunto, gas)
);

CREATE TABLE IF NOT EXISTS inventario (
    id              INTEGER PRIMARY KEY,
    organizacao_id  INTEGER NOT NULL REFERENCES organizacao(id),
    ano             INTEGER NOT NULL CHECK (ano BETWEEN 2000 AND 2100),
    gwp_conjunto    TEXT NOT NULL DEFAULT 'AR5',
    status          TEXT NOT NULL DEFAULT 'rascunho' CHECK (status IN
                    ('rascunho','submetido','em_verificacao','verificado','aprovado','devolvido')),
    elaborado_por   INTEGER NOT NULL REFERENCES usuario(id),
    verificado_por  INTEGER REFERENCES usuario(id),
    moderado_por    INTEGER REFERENCES usuario(id),
    UNIQUE (organizacao_id, ano)
);

-- Unidade organizacional, instalação (art. 2º, XV) ou fonte móvel (art. 2º, XII).
-- Hierarquia: órgão > secretaria/unidade > prédio/instalação (pai_id).
CREATE TABLE IF NOT EXISTS instalacao (
    id              INTEGER PRIMARY KEY,
    organizacao_id  INTEGER NOT NULL REFERENCES organizacao(id),
    pai_id          INTEGER REFERENCES instalacao(id),
    nome            TEXT NOT NULL,
    natureza        TEXT NOT NULL CHECK (natureza IN ('unidade','instalacao','fonte_movel')),
    identificador   TEXT,                   -- licença ambiental, CNPJ da filial, CNES, placa…
    municipio_uf    TEXT,
    agropecuaria_primaria   INTEGER NOT NULL DEFAULT 0 CHECK (agropecuaria_primaria IN (0,1)),
    residuos_neutralizacao  INTEGER NOT NULL DEFAULT 0 CHECK (residuos_neutralizacao IN (0,1)),
    UNIQUE (organizacao_id, nome)
);
CREATE INDEX IF NOT EXISTS ix_instalacao_org ON instalacao(organizacao_id);

-- Anexos (NF, fatura, laudo, foto). Conteúdo guardado fora do banco, identificado pelo SHA-256.
CREATE TABLE IF NOT EXISTS anexo (
    id              INTEGER PRIMARY KEY,
    organizacao_id  INTEGER REFERENCES organizacao(id),
    sha256          TEXT NOT NULL CHECK (length(sha256) = 64),
    nome_original   TEXT NOT NULL,
    tipo_mime       TEXT NOT NULL,
    tamanho         INTEGER NOT NULL CHECK (tamanho > 0),
    enviado_por     INTEGER NOT NULL REFERENCES usuario(id),
    enviado_em      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TRIGGER IF NOT EXISTS anexo_sem_update BEFORE UPDATE ON anexo
BEGIN SELECT RAISE(ABORT, 'anexo é imutável'); END;

CREATE TABLE IF NOT EXISTS atividade (
    id              INTEGER PRIMARY KEY,
    inventario_id   INTEGER NOT NULL REFERENCES inventario(id),
    instalacao_id   INTEGER REFERENCES instalacao(id),
    mes             INTEGER CHECK (mes IS NULL OR mes BETWEEN 1 AND 12),
    descricao       TEXT NOT NULL,
    escopo          INTEGER NOT NULL CHECK (escopo IN (1,2,3)),
    -- remocao: reduz emissões líquidas (art. 2º, XI). evitada: relatada à parte, NÃO abate o inventário.
    natureza        TEXT NOT NULL DEFAULT 'emissao' CHECK (natureza IN ('emissao','remocao','evitada')),
    origem          TEXT NOT NULL,          -- 'fator:<código>', 'gas:<nome>' ou 'contrato:<id>'
    fator_id        INTEGER REFERENCES fator_emissao(id),   -- versão exata usada no cálculo
    contrato_id     INTEGER REFERENCES contrato(id),        -- emissões de terceirizada (escopo 3)
    quantidade      REAL NOT NULL CHECK (quantidade >= 0),
    tco2e           REAL NOT NULL CHECK (tco2e >= 0),
    tco2_biogenico  REAL NOT NULL DEFAULT 0 CHECK (tco2_biogenico >= 0),
    memoria         TEXT NOT NULL,
    evidencia       TEXT,                   -- referência textual (processo SEI etc.)
    anexo_id        INTEGER REFERENCES anexo(id)            -- sem anexo = pendente de comprovação
);
CREATE INDEX IF NOT EXISTS ix_atividade_inventario ON atividade(inventario_id);
CREATE INDEX IF NOT EXISTS ix_atividade_instalacao ON atividade(instalacao_id);

CREATE TABLE IF NOT EXISTS evento_mrv (
    id              INTEGER PRIMARY KEY,
    inventario_id   INTEGER NOT NULL REFERENCES inventario(id),
    de_status       TEXT NOT NULL,
    para_status     TEXT NOT NULL,
    usuario_id      INTEGER NOT NULL REFERENCES usuario(id),
    parecer         TEXT,
    em              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS ix_evento_inventario ON evento_mrv(inventario_id);

-- Meta de redução da organização (ex.: -10% em relação ao ano-base).
CREATE TABLE IF NOT EXISTS meta (
    id              INTEGER PRIMARY KEY,
    organizacao_id  INTEGER NOT NULL REFERENCES organizacao(id),
    ano_base        INTEGER NOT NULL,
    ano_alvo        INTEGER NOT NULL,
    reducao_percentual REAL NOT NULL CHECK (reducao_percentual > 0 AND reducao_percentual <= 100),
    CHECK (ano_alvo > ano_base),
    UNIQUE (organizacao_id, ano_alvo)
);

-- ---------------------------------------------------------------- terceirizadas
CREATE TABLE IF NOT EXISTS contrato (
    id              INTEGER PRIMARY KEY,
    orgao_id        INTEGER NOT NULL REFERENCES organizacao(id),   -- contratante
    fornecedor_id   INTEGER NOT NULL REFERENCES organizacao(id),   -- terceirizada
    numero          TEXT NOT NULL,
    objeto          TEXT NOT NULL,
    vigencia_inicio TEXT NOT NULL,          -- AAAA-MM-DD
    vigencia_fim    TEXT NOT NULL,
    -- Só com cláusula contratual (ou aditivo) o atraso no relato vira inadimplência.
    clausula_relato INTEGER NOT NULL DEFAULT 0 CHECK (clausula_relato IN (0,1)),
    dia_limite      INTEGER NOT NULL DEFAULT 10 CHECK (dia_limite BETWEEN 1 AND 28),
    aceito_em       TEXT,
    CHECK (vigencia_fim > vigencia_inicio),
    CHECK (orgao_id <> fornecedor_id),
    UNIQUE (orgao_id, numero)
);

CREATE TABLE IF NOT EXISTS lancamento_fornecedor (
    id              INTEGER PRIMARY KEY,
    contrato_id     INTEGER NOT NULL REFERENCES contrato(id),
    ano             INTEGER NOT NULL,
    mes             INTEGER NOT NULL CHECK (mes BETWEEN 1 AND 12),
    descricao       TEXT NOT NULL,
    fator_id        INTEGER NOT NULL REFERENCES fator_emissao(id),
    quantidade      REAL NOT NULL CHECK (quantidade >= 0),
    tco2e           REAL NOT NULL CHECK (tco2e >= 0),
    memoria         TEXT NOT NULL,
    anexo_id        INTEGER REFERENCES anexo(id),
    lancado_por     INTEGER NOT NULL REFERENCES usuario(id),
    lancado_pelo_fiscal INTEGER NOT NULL DEFAULT 0 CHECK (lancado_pelo_fiscal IN (0,1)),
    lancado_em      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS ix_lanc_contrato ON lancamento_fornecedor(contrato_id, ano, mes);

-- Ações de descarbonização da terceirizada (treinamento, reciclagem…), com evidência.
CREATE TABLE IF NOT EXISTS acao_fornecedor (
    id              INTEGER PRIMARY KEY,
    contrato_id     INTEGER NOT NULL REFERENCES contrato(id),
    data            TEXT NOT NULL,
    descricao       TEXT NOT NULL,
    anexo_id        INTEGER NOT NULL REFERENCES anexo(id),
    registrado_por  INTEGER NOT NULL REFERENCES usuario(id)
);

CREATE TABLE IF NOT EXISTS score_fornecedor (
    id              INTEGER PRIMARY KEY,
    contrato_id     INTEGER NOT NULL REFERENCES contrato(id),
    ano             INTEGER NOT NULL,
    mes             INTEGER NOT NULL,
    score           REAL NOT NULL CHECK (score BETWEEN 0 AND 100),
    classe          TEXT NOT NULL CHECK (classe IN ('A','B','C','D')),
    componentes     TEXT NOT NULL,          -- JSON com a memória do cálculo
    calculado_em    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (contrato_id, ano, mes)
);

CREATE TABLE IF NOT EXISTS selo (
    id              INTEGER PRIMARY KEY,
    codigo          TEXT NOT NULL UNIQUE,   -- usado no QR Code
    contrato_id     INTEGER NOT NULL REFERENCES contrato(id),
    classe          TEXT NOT NULL CHECK (classe IN ('A','B','C','D')),
    score           REAL NOT NULL,
    periodo_inicio  TEXT NOT NULL,
    periodo_fim     TEXT NOT NULL,
    emissoes_tco2e  REAL NOT NULL,
    emitido_em      TEXT NOT NULL,
    valido_ate      TEXT NOT NULL,
    hash            TEXT NOT NULL UNIQUE
);
CREATE TRIGGER IF NOT EXISTS selo_sem_update BEFORE UPDATE ON selo
BEGIN SELECT RAISE(ABORT, 'selo emitido é imutável'); END;

CREATE TABLE IF NOT EXISTS notificacao (
    id              INTEGER PRIMARY KEY,
    contrato_id     INTEGER NOT NULL REFERENCES contrato(id),
    tipo            TEXT NOT NULL CHECK (tipo IN ('lembrete','alerta','notificacao_formal')),
    destinatario    TEXT NOT NULL CHECK (destinatario IN ('fornecedor','fiscal')),
    assunto         TEXT NOT NULL,
    texto           TEXT NOT NULL,
    prazo           TEXT,
    anexo_id        INTEGER REFERENCES anexo(id),
    emitida_por     INTEGER REFERENCES usuario(id),  -- NULL = rotina automática
    emitida_em      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    referencia      TEXT,                   -- evita lembrete duplicado (ex.: '2026-10')
    UNIQUE (contrato_id, tipo, referencia)
);

-- ---------------------------------------------------------------- registro de créditos
CREATE TABLE IF NOT EXISTS conta (
    id              INTEGER PRIMARY KEY,
    organizacao_id  INTEGER NOT NULL UNIQUE REFERENCES organizacao(id)
);

-- Lote = bloco de unidades fungíveis (1 unidade = 1 tCO2e) com mesmo serial-base.
CREATE TABLE IF NOT EXISTS lote (
    id              INTEGER PRIMARY KEY,
    serial          TEXT NOT NULL UNIQUE,
    tipo_ativo      TEXT NOT NULL CHECK (tipo_ativo IN ('CBE','CRVE','VOLUNTARIO')),
    projeto         TEXT NOT NULL,
    metodologia     TEXT NOT NULL,
    certificadora   TEXT,                  -- obrigatória para crédito voluntário (Verra, Gold Standard…)
    certificado_anexo_id INTEGER REFERENCES anexo(id),
    safra           INTEGER NOT NULL,      -- ano da redução/remoção (vintage)
    quantidade      INTEGER NOT NULL CHECK (quantidade > 0),
    conta_id        INTEGER REFERENCES conta(id),
    -- cancelamento = anulação para comprovar compromisso (art. 2º, II; art. 45)
    status          TEXT NOT NULL CHECK (status IN ('ativo','cancelado')),
    lote_origem_id  INTEGER REFERENCES lote(id),
    beneficiario    TEXT,
    finalidade      TEXT CHECK (finalidade IN ('compensacao_voluntaria','conciliacao_sbce','invalidacao')),
    inventario_id   INTEGER REFERENCES inventario(id),
    -- nº da aposentadoria no registro da certificadora: sem ele não há prova de cancelamento real
    registro_externo_ref TEXT,
    CHECK ((status = 'ativo') = (finalidade IS NULL)),
    CHECK (tipo_ativo <> 'VOLUNTARIO' OR (certificadora IS NOT NULL AND certificado_anexo_id IS NOT NULL)),
    CHECK (tipo_ativo <> 'VOLUNTARIO' OR status = 'ativo' OR registro_externo_ref IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_lote_conta ON lote(conta_id, status);
CREATE INDEX IF NOT EXISTS ix_lote_inventario ON lote(inventario_id);

-- Livro-razão append-only com encadeamento de hashes (auditoria / TCU / CGU).
CREATE TABLE IF NOT EXISTS movimento (
    id              INTEGER PRIMARY KEY,
    tipo            TEXT NOT NULL CHECK (tipo IN ('emissao','transferencia','cancelamento')),
    lote_id         INTEGER NOT NULL REFERENCES lote(id),
    quantidade      INTEGER NOT NULL CHECK (quantidade > 0),
    de_conta        INTEGER REFERENCES conta(id),
    para_conta      INTEGER REFERENCES conta(id),
    usuario_id      INTEGER NOT NULL REFERENCES usuario(id),
    motivo          TEXT,
    chave_idempotencia TEXT UNIQUE,
    em              TEXT NOT NULL,
    hash_anterior   TEXT NOT NULL,
    hash            TEXT NOT NULL UNIQUE
);
CREATE TRIGGER IF NOT EXISTS movimento_sem_update BEFORE UPDATE ON movimento
BEGIN SELECT RAISE(ABORT, 'livro-razão é somente inclusão'); END;
CREATE TRIGGER IF NOT EXISTS movimento_sem_delete BEFORE DELETE ON movimento
BEGIN SELECT RAISE(ABORT, 'livro-razão é somente inclusão'); END;

-- Trilha de auditoria geral: quem, quando, de onde, o que havia antes e depois.
CREATE TABLE IF NOT EXISTS auditoria (
    id              INTEGER PRIMARY KEY,
    em              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    usuario_id      INTEGER,
    ip              TEXT,
    acao            TEXT NOT NULL,
    entidade        TEXT NOT NULL,
    entidade_id     INTEGER,
    antes           TEXT,
    depois          TEXT
);
CREATE INDEX IF NOT EXISTS ix_auditoria_entidade ON auditoria(entidade, entidade_id);
CREATE TRIGGER IF NOT EXISTS auditoria_sem_update BEFORE UPDATE ON auditoria
BEGIN SELECT RAISE(ABORT, 'trilha de auditoria é somente inclusão'); END;
CREATE TRIGGER IF NOT EXISTS auditoria_sem_delete BEFORE DELETE ON auditoria
BEGIN SELECT RAISE(ABORT, 'trilha de auditoria é somente inclusão'); END;
"""


class EsquemaIncompativel(RuntimeError):
    """Banco criado por versão anterior da plataforma."""


def conectar(caminho: str, criar_esquema: bool = True) -> sqlite3.Connection:
    con = sqlite3.connect(caminho, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 5000")
    if criar_esquema:
        versao = con.execute("PRAGMA user_version").fetchone()[0]
        tem_tabelas = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        if tem_tabelas and versao != VERSAO_ESQUEMA:
            con.close()
            raise EsquemaIncompativel(
                f"O banco {caminho!r} é da versão {versao} do esquema; esta plataforma usa a versão "
                f"{VERSAO_ESQUEMA}. Recrie o banco (não há migração automática no MVP).")
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(ESQUEMA)
        con.execute(f"PRAGMA user_version = {VERSAO_ESQUEMA}")
        from .fatores import semear  # evita import circular
        semear(con)
    return con


@contextmanager
def transacao(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Transação com trava de escrita (evita saldo gasto duas vezes em concorrência)."""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


class ErroNegocio(Exception):
    """Violação de regra de negócio (vira HTTP 409/422 na API)."""


class NaoAutorizado(Exception):
    """Usuário sem permissão para a operação (vira HTTP 403)."""


class NaoEncontrado(Exception):
    """Registro inexistente (vira HTTP 404)."""


def buscar(con: sqlite3.Connection, tabela: str, id_: int) -> sqlite3.Row:
    assert tabela.isidentifier()
    linha = con.execute(f"SELECT * FROM {tabela} WHERE id = ?", (id_,)).fetchone()
    if linha is None:
        raise NaoEncontrado(f"{tabela} {id_} não encontrado")
    return linha
