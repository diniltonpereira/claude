"""Linha de comando.

  python -m publisher validar                 # confere todos os posts
  python -m publisher listar                  # mostra a fila e o que já saiu
  python -m publisher publicar                # publica os posts agendados que já venceram
  python -m publisher publicar --slug X       # publica um post agora (ignora a data)
  python -m publisher publicar --simular      # mostra o que faria, sem chamar a API
  python -m publisher preparar posts/X        # converte PNG em JPEG
  python -m publisher conta                   # confere token, conta e cota de publicação
  python -m publisher renovar-token           # imprime um token renovado (uso no workflow)
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from . import posts as P
from .instagram import InstagramClient, InstagramError, refresh_token

TZ = ZoneInfo("America/Sao_Paulo")


def _resumo(linhas: list[str]) -> None:
    texto = "\n".join(linhas)
    print(texto)
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write(texto + "\n")


def _cliente() -> InstagramClient:
    return InstagramClient(os.environ.get("IG_USER_ID", ""), os.environ.get("IG_ACCESS_TOKEN", ""),
                           version=os.environ.get("IG_API_VERSION", "v25.0"))


def publicar_post(cli: InstagramClient, post: P.Post) -> str:
    if post.tipo == "imagem":
        cid = cli.image_container(P.url_publica(post, post.midia[0]), caption=post.texto)
    elif post.tipo == "carrossel":
        filhos = [cli.image_container(P.url_publica(post, m), carousel_item=True) for m in post.midia]
        for f in filhos:
            cli.wait_ready(f, timeout_s=120, every_s=5)
        cid = cli.carousel_container(filhos, post.texto)
    elif post.tipo == "reel":
        capa = P.url_publica(post, post.capa) if post.capa else None
        cid = cli.reel_container(P.url_publica(post, post.midia[0]), post.texto, cover_url=capa)
    else:
        raise InstagramError(f"tipo desconhecido: {post.tipo}")
    cli.wait_ready(cid, timeout_s=600 if post.tipo == "reel" else 120,
                   every_s=20 if post.tipo == "reel" else 5)
    return cli.publish(cid)


def cmd_validar(_a) -> int:
    lista = P.todos()
    falhas = 0
    for post in lista:
        erros = P.validar(post)
        marca = "OK " if not erros else "ERRO"
        print(f"[{marca}] {post.slug} ({post.tipo}, {post.status})")
        for e in erros:
            print(f"       - {e}")
        falhas += bool(erros)
    print(f"\n{len(lista)} post(s), {falhas} com erro.")
    return 1 if falhas else 0


def cmd_listar(_a) -> int:
    estado = P.ler_estado()
    agora = dt.datetime.now(TZ)
    for post in P.todos():
        if post.slug in estado:
            situacao = f"publicado {estado[post.slug].get('permalink') or ''}".strip()
        elif post.vencido(agora):
            situacao = "na fila (vencido)"
        else:
            situacao = post.status
        quando = post.publicar_em.astimezone(TZ).strftime("%d/%m %H:%M")
        print(f"{quando}  {post.tipo:<9} {post.slug:<40} {situacao}")
    return 0


def cmd_publicar(a) -> int:
    estado = P.ler_estado()
    agora = dt.datetime.now(TZ)
    lista = P.todos()
    if a.slug:
        alvo = [p for p in lista if p.slug == a.slug]
        if not alvo:
            print(f"Post '{a.slug}' não encontrado.")
            return 1
        if alvo[0].status == "aguardando_video":
            print(f"Post '{a.slug}' ainda está aguardando o vídeo.")
            return 1
    else:
        alvo = [p for p in lista if p.vencido(agora) and p.slug not in estado]
    alvo = alvo[: a.max]

    if not alvo:
        _resumo(["Nenhum post para publicar agora."])
        return 0

    linhas = [f"## Publicação — {agora:%d/%m/%Y %H:%M}", ""]
    erros = [f"{p.slug}: {e}" for p in alvo for e in P.validar(p)]
    if erros:
        _resumo(linhas + ["Posts com erro, nada publicado:"] + [f"- {e}" for e in erros])
        return 1

    if a.simular:
        for p in alvo:
            linhas.append(f"- (simulação) {p.slug}: {p.tipo}, {len(p.midia)} arquivo(s)")
            for m in p.midia:
                linhas.append(f"    {P.url_publica(p, m)}")
        _resumo(linhas)
        return 0

    cli = _cliente()
    falhou = False
    for p in alvo:
        if p.slug in estado and not a.forcar:
            linhas.append(f"- {p.slug}: já publicado, ignorado")
            continue
        try:
            media_id = publicar_post(cli, p)
            link = cli.permalink(media_id)
            estado[p.slug] = {"media_id": media_id, "permalink": link,
                              "publicado_em": dt.datetime.now(TZ).isoformat(timespec="seconds")}
            P.gravar_estado(estado)  # grava a cada post, para nunca repetir
            linhas.append(f"- ✅ {p.slug} → {link or media_id}")
        except InstagramError as e:
            falhou = True
            linhas.append(f"- ❌ {p.slug}: {e}")
    _resumo(linhas)
    return 1 if falhou else 0


def cmd_preparar(a) -> int:
    pasta = Path(a.pasta)
    feitos = P.preparar(pasta)
    print(f"{len(feitos)} arquivo(s) convertido(s) para JPEG em {pasta}.")
    return 0


def cmd_conta(_a) -> int:
    cli = _cliente()
    me = cli.me()
    cota = cli.publishing_quota()
    print(f"Conta: @{me.get('username')} ({me.get('account_type')}), id {me.get('user_id')}")
    print(f"Publicações nas últimas 24 h: {cota.get('quota_usage', '?')} "
          f"de {cota.get('config', {}).get('quota_total', '?')}")
    return 0


def cmd_renovar(_a) -> int:
    novo = refresh_token(os.environ["IG_ACCESS_TOKEN"])
    dias = int(novo.get("expires_in", 0)) // 86400
    print(f"::add-mask::{novo['access_token']}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"token={novo['access_token']}\n")
    print(f"Token renovado, válido por {dias} dias.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="publisher")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validar").set_defaults(fn=cmd_validar)
    sub.add_parser("listar").set_defaults(fn=cmd_listar)
    pp = sub.add_parser("publicar")
    pp.add_argument("--slug")
    pp.add_argument("--simular", action="store_true")
    pp.add_argument("--forcar", action="store_true", help="publica mesmo se já constar como publicado")
    pp.add_argument("--max", type=int, default=int(os.environ.get("MAX_POR_EXECUCAO", "3")))
    pp.set_defaults(fn=cmd_publicar)
    pr = sub.add_parser("preparar")
    pr.add_argument("pasta")
    pr.set_defaults(fn=cmd_preparar)
    sub.add_parser("conta").set_defaults(fn=cmd_conta)
    sub.add_parser("renovar-token").set_defaults(fn=cmd_renovar)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
