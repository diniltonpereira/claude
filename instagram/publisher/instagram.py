"""Cliente mínimo da Instagram API com login do Instagram (graph.instagram.com).

Fluxo oficial de publicação:
  1. POST /{ig_id}/media            -> cria o contêiner (imagem, item de carrossel, carrossel ou Reel)
  2. GET  /{container}?fields=status_code  -> aguarda FINISHED (obrigatório para vídeo)
  3. POST /{ig_id}/media_publish    -> publica o contêiner
"""
from __future__ import annotations

import time
from typing import Callable

import requests

API_HOST = "https://graph.instagram.com"
DEFAULT_VERSION = "v25.0"


class InstagramError(RuntimeError):
    pass


class InstagramClient:
    def __init__(self, ig_user_id: str, access_token: str, version: str = DEFAULT_VERSION,
                 session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        if not ig_user_id or not access_token:
            raise InstagramError("IG_USER_ID e IG_ACCESS_TOKEN são obrigatórios.")
        self.ig = ig_user_id
        self.token = access_token
        self.base = f"{API_HOST}/{version}"
        self.http = session or requests.Session()
        self.sleep = sleep

    # ---------- HTTP ----------
    def _call(self, method: str, path: str, **params) -> dict:
        params["access_token"] = self.token
        url = f"{self.base}/{path.lstrip('/')}"
        if method == "GET":
            r = self.http.get(url, params=params, timeout=60)
        else:
            r = self.http.post(url, data=params, timeout=60)
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text}
        if r.status_code >= 400 or "error" in body:
            err = body.get("error", body)
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise InstagramError(f"{method} {path} falhou ({r.status_code}): {msg}")
        return body

    # ---------- Contêineres ----------
    def image_container(self, image_url: str, caption: str | None = None,
                        carousel_item: bool = False) -> str:
        p = {"image_url": image_url}
        if carousel_item:
            p["is_carousel_item"] = "true"
        elif caption:
            p["caption"] = caption
        return self._call("POST", f"{self.ig}/media", **p)["id"]

    def carousel_container(self, children: list[str], caption: str) -> str:
        return self._call("POST", f"{self.ig}/media", media_type="CAROUSEL",
                          children=",".join(children), caption=caption)["id"]

    def reel_container(self, video_url: str, caption: str, cover_url: str | None = None) -> str:
        p = {"media_type": "REELS", "video_url": video_url, "caption": caption}
        if cover_url:
            p["cover_url"] = cover_url
        return self._call("POST", f"{self.ig}/media", **p)["id"]

    def wait_ready(self, container_id: str, timeout_s: int = 600, every_s: int = 15) -> None:
        waited = 0
        while True:
            status = self._call("GET", container_id, fields="status_code").get("status_code")
            if status in ("FINISHED", "PUBLISHED"):
                return
            if status in ("ERROR", "EXPIRED"):
                raise InstagramError(f"Contêiner {container_id} terminou com status {status}.")
            if waited >= timeout_s:
                raise InstagramError(f"Contêiner {container_id} não ficou pronto em {timeout_s}s.")
            self.sleep(every_s)
            waited += every_s

    def publish(self, container_id: str) -> str:
        return self._call("POST", f"{self.ig}/media_publish", creation_id=container_id)["id"]

    def permalink(self, media_id: str) -> str | None:
        try:
            return self._call("GET", media_id, fields="permalink").get("permalink")
        except InstagramError:
            return None

    def publishing_quota(self) -> dict:
        data = self._call("GET", f"{self.ig}/content_publishing_limit",
                          fields="quota_usage,config").get("data", [{}])
        return data[0] if data else {}

    def me(self) -> dict:
        return self._call("GET", "me", fields="user_id,username,account_type")


def refresh_token(token: str, session: requests.Session | None = None) -> dict:
    """Renova o token de longa duração (precisa ter mais de 24 h e ainda estar válido)."""
    http = session or requests.Session()
    r = http.get(f"{API_HOST}/refresh_access_token",
                 params={"grant_type": "ig_refresh_token", "access_token": token}, timeout=60)
    body = r.json()
    if r.status_code >= 400 or "access_token" not in body:
        raise InstagramError(f"Falha ao renovar o token: {body}")
    return body
