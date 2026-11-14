"""Linha de comando.

    python -m plataforma criar-admin --email X --nome Y [--banco carbono.db]   administrador com senha + TOTP
    python -m plataforma servir   [--banco carbono.db] [--porta 8000] [--demo]  sobe a API e o painel
    python -m plataforma rotina-diaria [--banco carbono.db]                     lembretes, alertas e score (1x/dia)
    python -m plataforma backup   --destino DIR [--banco carbono.db] [--anexos anexos]
    python -m plataforma restaurar ARQUIVO.db [--banco carbono.db] [--anexos anexos]
    python -m plataforma migrar   [--banco carbono.db]                          atualiza o esquema do banco
    python -m plataforma demo     [--banco demo.db]                             dados fictícios (modo demonstração)

Variáveis de ambiente: ver .env.example e docs/IMPLANTACAO.md.
"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import os

from .banco import VERSAO_ESQUEMA, conectar


def main() -> None:
    p = argparse.ArgumentParser(prog="plataforma")
    sub = p.add_subparsers(dest="cmd", required=True)
    banco_padrao = os.environ.get("CARBONO_DB", "carbono.db")
    anexos_padrao = os.environ.get("CARBONO_ANEXOS", "anexos")

    a = sub.add_parser("criar-admin"); a.add_argument("--banco", default=banco_padrao)
    a.add_argument("--email", required=True); a.add_argument("--nome", required=True)
    d = sub.add_parser("demo"); d.add_argument("--banco", default="demo.db")
    s = sub.add_parser("servir"); s.add_argument("--banco", default=banco_padrao)
    s.add_argument("--porta", type=int, default=8000); s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--demo", action="store_true", help="modo demonstração (acesso por número de usuário)")
    r = sub.add_parser("rotina-diaria"); r.add_argument("--banco", default=banco_padrao)
    b = sub.add_parser("backup"); b.add_argument("--banco", default=banco_padrao)
    b.add_argument("--anexos", default=anexos_padrao); b.add_argument("--destino", required=True)
    rs = sub.add_parser("restaurar"); rs.add_argument("arquivo")
    rs.add_argument("--banco", default=banco_padrao); rs.add_argument("--anexos", default=anexos_padrao)
    m = sub.add_parser("migrar"); m.add_argument("--banco", default=banco_padrao)
    args = p.parse_args()

    if args.cmd == "criar-admin":
        from .operacao import criar_admin
        senha = os.environ.get("CARBONO_ADMIN_SENHA") or getpass.getpass("Senha (mínimo 12 caracteres): ")
        r = criar_admin(args.banco, nome=args.nome, email=args.email, senha=senha)
        print(f"Administrador criado (id {r['id']}).\n"
              f"Cadastre no aplicativo autenticador (Google Authenticator, Microsoft Authenticator…):\n"
              f"  segredo: {r['totp_segredo']}\n  ou o endereço: {r['totp_uri']}\n"
              "Guarde o segredo em local seguro; ele não será mostrado de novo.")
    elif args.cmd == "rotina-diaria":
        from .terceirizadas import rotina_diaria
        print(json.dumps(rotina_diaria(conectar(args.banco)), ensure_ascii=False))
    elif args.cmd == "demo":
        from .demo import executar
        print(json.dumps(executar(args.banco), ensure_ascii=False, indent=2))
    elif args.cmd == "backup":
        from .operacao import backup
        print(json.dumps(backup(args.banco, args.anexos, args.destino), ensure_ascii=False))
    elif args.cmd == "restaurar":
        from .operacao import restaurar
        print(json.dumps(restaurar(args.arquivo, args.banco, args.anexos), ensure_ascii=False))
    elif args.cmd == "migrar":
        conectar(args.banco).close()
        print(f"Banco na versão {VERSAO_ESQUEMA} do esquema.")
    else:
        import uvicorn
        from .api import criar_app
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        app = criar_app(args.banco, modo="demo" if args.demo else None)
        uvicorn.run(app, host=args.host, port=args.porta, proxy_headers=True,
                    forwarded_allow_ips=os.environ.get("CARBONO_PROXY_IPS", "127.0.0.1"))


if __name__ == "__main__":
    main()
