"""Linha de comando.

    python -m plataforma iniciar  [--banco carbono.db]   cria o banco e o 1º registrador
    python -m plataforma demo     [--banco demo.db]      roda um ciclo completo de exemplo
    python -m plataforma servir   [--banco carbono.db] [--porta 8000]
"""
from __future__ import annotations

import argparse
import json

from .banco import conectar


def iniciar(banco: str, nome: str) -> int:
    con = conectar(banco)
    existe = con.execute("SELECT id FROM usuario WHERE papel='registrador' LIMIT 1").fetchone()
    if existe:
        print(f"Registrador já existe (id {existe['id']}).")
        return existe["id"]
    uid = con.execute("INSERT INTO usuario (nome, papel) VALUES (?, 'registrador')", (nome,)).lastrowid
    print(f"Registrador criado: id {uid}. Use o cabeçalho X-Usuario-Id: {uid} na API.")
    return uid


def main() -> None:
    p = argparse.ArgumentParser(prog="plataforma")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("iniciar"); a.add_argument("--banco", default="carbono.db")
    a.add_argument("--nome", default="Registrador")
    d = sub.add_parser("demo"); d.add_argument("--banco", default="demo.db")
    s = sub.add_parser("servir"); s.add_argument("--banco", default="carbono.db")
    s.add_argument("--porta", type=int, default=8000); s.add_argument("--host", default="127.0.0.1")
    args = p.parse_args()

    if args.cmd == "iniciar":
        iniciar(args.banco, args.nome)
    elif args.cmd == "demo":
        from .demo import executar
        print(json.dumps(executar(args.banco), ensure_ascii=False, indent=2))
    else:
        import uvicorn
        from .api import criar_app
        uvicorn.run(criar_app(args.banco), host=args.host, port=args.porta)


if __name__ == "__main__":
    main()
