"""Persistência em SQLite. As invariantes ficam no esquema, não só no código."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

ESQUEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS organizacao (
    id              INTEGER PRIMARY KEY,
    nome            TEXT NOT NULL,
    cnpj            TEXT NOT NULL UNIQUE CHECK (length(cnpj) = 14),
    tipo            TEXT NOT NULL CHECK (tipo IN
                    ('orgao_publico','ppp','concessao','concessao_florestal','empresa')),
    esfera          TEXT CHECK (esfera IN ('federal','estadual','municipal')),
    contrato_ref    TEXT,
    poder_concedente_id INTEGER REFERENCES organizacao(id)
);

CREATE TABLE IF NOT EXISTS usuario (
    id              INTEGER PRIMARY KEY,
    nome            TEXT NOT NULL,
    papel           TEXT NOT NULL CHECK (papel IN ('elaborador','verificador','moderador','registrador')),
    organizacao_id  INTEGER REFERENCES organizacao(id),
    -- Organismo de inspeção acreditado (Lei 15.042/2024, art. 32, parágrafo único)
    acreditado      INTEGER NOT NULL DEFAULT 0 CHECK (acreditado IN (0,1))
);

CREATE TABLE IF NOT EXISTS inventario (
    id              INTEGER PRIMARY KEY,
    organizacao_id  INTEGER NOT NULL REFERENCES organizacao(id),
    ano             INTEGER NOT NULL CHECK (ano BETWEEN 2000 AND 2100),
    status          TEXT NOT NULL DEFAULT 'rascunho' CHECK (status IN
                    ('rascunho','submetido','em_verificacao','verificado','aprovado','devolvido')),
    elaborado_por   INTEGER NOT NULL REFERENCES usuario(id),
    verificado_por  INTEGER REFERENCES usuario(id),
    moderado_por    INTEGER REFERENCES usuario(id),
    UNIQUE (organizacao_id, ano)
);

-- Instalação (art. 2º, XV) ou fonte móvel (art. 2º, XII): unidade de aplicação dos patamares (art. 30).
CREATE TABLE IF NOT EXISTS instalacao (
    id              INTEGER PRIMARY KEY,
    organizacao_id  INTEGER NOT NULL REFERENCES organizacao(id),
    nome            TEXT NOT NULL,
    natureza        TEXT NOT NULL CHECK (natureza IN ('instalacao','fonte_movel')),
    identificador   TEXT,                   -- licença ambiental, CNPJ da filial, placa da frota…
    municipio_uf    TEXT,
    agropecuaria_primaria   INTEGER NOT NULL DEFAULT 0 CHECK (agropecuaria_primaria IN (0,1)),
    residuos_neutralizacao  INTEGER NOT NULL DEFAULT 0 CHECK (residuos_neutralizacao IN (0,1)),
    UNIQUE (organizacao_id, nome)
);

CREATE TABLE IF NOT EXISTS atividade (
    id              INTEGER PRIMARY KEY,
    inventario_id   INTEGER NOT NULL REFERENCES inventario(id),
    instalacao_id   INTEGER REFERENCES instalacao(id),
    descricao       TEXT NOT NULL,
    escopo          INTEGER NOT NULL CHECK (escopo IN (1,2,3)),
    -- remoção por sumidouro: entra nas emissões líquidas (art. 2º, XI; art. 1º, § 4º)
    natureza        TEXT NOT NULL DEFAULT 'emissao' CHECK (natureza IN ('emissao','remocao')),
    origem          TEXT NOT NULL,          -- código do fator ou 'gas:<nome>'
    quantidade      REAL NOT NULL CHECK (quantidade >= 0),
    tco2e           REAL NOT NULL CHECK (tco2e >= 0),
    tco2_biogenico  REAL NOT NULL DEFAULT 0 CHECK (tco2_biogenico >= 0),
    memoria         TEXT NOT NULL,
    evidencia       TEXT                    -- nota fiscal, fatura, laudo…
);
CREATE INDEX IF NOT EXISTS ix_atividade_inventario ON atividade(inventario_id);
CREATE INDEX IF NOT EXISTS ix_atividade_instalacao ON atividade(instalacao_id);
CREATE INDEX IF NOT EXISTS ix_instalacao_org ON instalacao(organizacao_id);

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
    safra           INTEGER NOT NULL,      -- ano da redução/remoção (vintage)
    quantidade      INTEGER NOT NULL CHECK (quantidade > 0),
    conta_id        INTEGER REFERENCES conta(id),
    -- cancelamento = anulação para comprovar compromisso (art. 2º, II; art. 45)
    status          TEXT NOT NULL CHECK (status IN ('ativo','cancelado')),
    lote_origem_id  INTEGER REFERENCES lote(id),
    beneficiario    TEXT,
    finalidade      TEXT CHECK (finalidade IN ('compensacao_voluntaria','conciliacao_sbce','invalidacao')),
    inventario_id   INTEGER REFERENCES inventario(id),
    CHECK ((status = 'ativo') = (finalidade IS NULL))
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
"""


def conectar(caminho: str, criar_esquema: bool = True) -> sqlite3.Connection:
    con = sqlite3.connect(caminho, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 5000")
    if criar_esquema:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(ESQUEMA)
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
