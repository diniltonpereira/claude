"""Operação: criação do administrador, backup e restauração verificados.

Backup:
- banco copiado pela API de backup do SQLite (consistente mesmo com o sistema no ar, modo WAL);
- anexos copiados por conteúdo (nome = SHA-256), então cópias seguintes só levam o que é novo;
- manifesto com o SHA-256 do banco e a lista de anexos, para conferência na restauração.
Restauração: confere o manifesto, roda `PRAGMA integrity_check`, confere a cadeia do livro-razão e
só então substitui o banco (o atual é preservado com sufixo .antes-da-restauracao).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import acesso
from .banco import conectar


def _sha_arquivo(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def criar_admin(banco: str, *, nome: str, email: str, senha: str) -> dict:
    """Cria (ou recupera) o registrador com senha e TOTP. Devolve o segredo TOTP para o aplicativo autenticador."""
    con = conectar(banco)
    email = email.strip().lower()
    if con.execute("SELECT 1 FROM usuario WHERE email=?", (email,)).fetchone():
        raise SystemExit("Já existe usuário com este e-mail.")
    segredo = acesso.novo_segredo_totp()
    uid = con.execute(
        """INSERT INTO usuario (nome, papel, email, senha_hash, totp_segredo, totp_ativo)
           VALUES (?, 'registrador', ?, ?, ?, 1)""",
        (nome, email, acesso.gerar_hash_senha(senha), segredo)).lastrowid
    con.close()
    return {"id": uid, "totp_segredo": segredo, "totp_uri": acesso.uri_totp(segredo, email)}


def backup(banco: str, anexos: str, destino: str) -> dict:
    dest = Path(destino)
    (dest / "anexos").mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    arquivo = dest / f"carbonmg-{carimbo}.db"
    origem = sqlite3.connect(banco)
    copia = sqlite3.connect(arquivo)
    with copia:
        origem.backup(copia)
    origem.close()
    copia.close()
    novos, total = 0, 0
    pasta_anexos = Path(anexos)
    if pasta_anexos.exists():
        for a in pasta_anexos.iterdir():
            if a.is_file():
                total += 1
                alvo = dest / "anexos" / a.name
                if not alvo.exists():
                    shutil.copy2(a, alvo)
                    novos += 1
    manifesto = {"banco": arquivo.name, "sha256": _sha_arquivo(arquivo), "criado_em": carimbo,
                 "anexos_total": total, "anexos_novos": novos}
    (dest / f"carbonmg-{carimbo}.json").write_text(json.dumps(manifesto, indent=2))
    return manifesto


def restaurar(arquivo_backup: str, banco: str, anexos: str) -> dict:
    origem = Path(arquivo_backup)
    manifesto_path = origem.with_suffix(".json")
    if not manifesto_path.exists():
        raise SystemExit("Manifesto do backup não encontrado ao lado do arquivo .db.")
    manifesto = json.loads(manifesto_path.read_text())
    if _sha_arquivo(origem) != manifesto["sha256"]:
        raise SystemExit("O arquivo de backup não confere com o manifesto (corrompido ou alterado).")
    teste = sqlite3.connect(origem)
    ok = teste.execute("PRAGMA integrity_check").fetchone()[0]
    teste.close()
    if ok != "ok":
        raise SystemExit(f"Falha na verificação de integridade do SQLite: {ok}")
    # confere a cadeia do livro-razão numa cópia temporária migrada
    tmp = Path(banco).with_suffix(".verificacao.db")
    shutil.copy2(origem, tmp)
    from .registro import verificar_cadeia
    con = conectar(str(tmp))
    cadeia = verificar_cadeia(con)
    con.close()
    if not cadeia["integra"]:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"Livro-razão do backup com quebra no movimento {cadeia['quebra_em']}.")
    atual = Path(banco)
    if atual.exists():
        shutil.copy2(atual, atual.with_name(atual.name + ".antes-da-restauracao"))
    for sufixo in ("-wal", "-shm"):
        Path(str(atual) + sufixo).unlink(missing_ok=True)
    shutil.move(tmp, atual)
    pasta_backup = origem.parent / "anexos"
    restaurados = 0
    if pasta_backup.exists():
        Path(anexos).mkdir(parents=True, exist_ok=True)
        for a in pasta_backup.iterdir():
            alvo = Path(anexos) / a.name
            if not alvo.exists():
                shutil.copy2(a, alvo)
                restaurados += 1
    return {"restaurado": str(atual), "movimentos_conferidos": cadeia["movimentos"], "anexos_restaurados": restaurados}
