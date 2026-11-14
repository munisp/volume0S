"""YouTube for Creators Home: connecting a channel (Google OAuth 2.0) and publishing a finished video.

Only the long-lived refresh token is kept, sealed with AES-GCM (key derived from the service token). The OAuth
state is signed and expires, and names the business, the Creators Home channel and the person, so a callback can't
be replayed or pointed at another business. Uploads are resumable and read the video from storage in chunks.
Until Google audits the app, YouTube keeps uploads from it private, whatever privacy is asked for.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, AsyncIterator
from urllib.parse import urlencode

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly",
          "https://www.googleapis.com/auth/youtube.force-ssl"]  # force-ssl: captions and thumbnails
STATE_SECONDS = 900


class YouTubeError(RuntimeError):
    """Something a person should read (shown on the stage or the connect page)."""


def _key(purpose: bytes) -> bytes:
    secret = os.environ.get("VOLUMEOS_SERVICE_TOKEN", "")
    if len(secret) < 32:
        raise YouTubeError("VOLUMEOS_SERVICE_TOKEN is required")
    return hmac.new(secret.encode(), purpose, hashlib.sha256).digest()


def seal(text: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return nonce + AESGCM(_key(b"volumeos.creators.youtube-token")).encrypt(nonce, text.encode(), b"youtube-refresh-token")


def unseal(sealed: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(_key(b"volumeos.creators.youtube-token")).decrypt(bytes(sealed[:12]), bytes(sealed[12:]), b"youtube-refresh-token").decode()


def sign_state(tenant: str, channel_id: str, actor: str, now: float | None = None) -> str:
    body = json.dumps({"t": tenant, "c": channel_id, "a": actor, "e": int((now or time.time()) + STATE_SECONDS),
                       "n": base64.urlsafe_b64encode(os.urandom(9)).decode()}, separators=(",", ":")).encode()
    mac = hmac.new(_key(b"volumeos.creators.youtube-state"), body, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(body + mac).decode().rstrip("=")


def read_state(state: str, now: float | None = None) -> dict[str, str] | None:
    try:
        raw = base64.urlsafe_b64decode(state + "=" * (-len(state) % 4))
        body, mac = raw[:-16], raw[-16:]
        if not hmac.compare_digest(mac, hmac.new(_key(b"volumeos.creators.youtube-state"), body, hashlib.sha256).digest()[:16]):
            return None
        value = json.loads(body)
    except (ValueError, TypeError):
        return None
    if int(value.get("e", 0)) < (now or time.time()):
        return None
    return {"tenant": value["t"], "channel_id": value["c"], "actor": value["a"]}


class YouTube:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, transport: httpx.AsyncBaseTransport | None = None):
        if not client_id or not client_secret or not redirect_uri:
            raise RuntimeError("YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET and YOUTUBE_REDIRECT_URI are required")
        self.client_id, self.client_secret, self.redirect_uri, self.transport = client_id, client_secret, redirect_uri, transport

    @classmethod
    def from_env(cls) -> "YouTube | None":
        cid, secret = os.environ.get("YOUTUBE_CLIENT_ID", ""), os.environ.get("YOUTUBE_CLIENT_SECRET", "")
        uri = os.environ.get("YOUTUBE_REDIRECT_URI", "https://volumeos.newfire.app/v1/creators/youtube/callback")
        return cls(cid, secret, uri) if cid and secret else None

    def client(self, timeout: float = 60.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self.transport)

    def authorize_url(self, state: str) -> str:
        return AUTH_URL + "?" + urlencode({"client_id": self.client_id, "redirect_uri": self.redirect_uri, "response_type": "code",
                                           "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
                                           "include_granted_scopes": "true", "state": state})

    async def _token(self, data: dict[str, str]) -> dict[str, Any]:
        async with self.client() as c:
            r = await c.post(TOKEN_URL, data=data | {"client_id": self.client_id, "client_secret": self.client_secret})
        if r.status_code != 200:
            raise YouTubeError(f"Google refused the sign-in ({r.json().get('error', r.status_code) if r.headers.get('content-type','').startswith('application/json') else r.status_code})")
        return r.json()

    async def exchange(self, code: str) -> dict[str, Any]:
        tokens = await self._token({"code": code, "grant_type": "authorization_code", "redirect_uri": self.redirect_uri})
        if not tokens.get("refresh_token"):
            raise YouTubeError("Google didn't give lasting access; remove the app from your Google account's permissions and connect again")
        return tokens

    async def access_token(self, refresh_token: str) -> str:
        return (await self._token({"refresh_token": refresh_token, "grant_type": "refresh_token"}))["access_token"]

    async def revoke(self, refresh_token: str) -> None:
        async with self.client() as c:
            await c.post(REVOKE_URL, data={"token": refresh_token})

    async def my_channel(self, access_token: str) -> dict[str, Any]:
        async with self.client() as c:
            r = await c.get(f"{API}/channels", params={"part": "snippet,statistics", "mine": "true"}, headers={"Authorization": f"Bearer {access_token}"})
        items = r.json().get("items", []) if r.status_code == 200 else []
        if not items:
            raise YouTubeError("this Google account has no YouTube channel")
        item = items[0]
        return {"youtube_channel_id": item["id"], "title": item["snippet"].get("title", ""),
                "handle": item["snippet"].get("customUrl", ""), "subscribers": int(item.get("statistics", {}).get("subscriberCount", 0) or 0)}

    async def upload(self, access_token: str, metadata: dict[str, Any], size: int, chunks: AsyncIterator[bytes]) -> str:
        """Resumable upload: open a session with the metadata, then send the file in 8 MB pieces."""
        headers = {"Authorization": f"Bearer {access_token}", "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)}
        async with self.client(timeout=600.0) as c:
            r = await c.post(f"{UPLOAD}/videos", params={"uploadType": "resumable", "part": "snippet,status"}, json=metadata, headers=headers)
            if r.status_code != 200 or "location" not in r.headers:
                raise YouTubeError(f"YouTube refused the upload ({r.status_code}): {r.text[:300]}")
            session, sent, buffer = r.headers["location"], 0, b""

            async def send(piece: bytes, last: bool) -> httpx.Response:
                end = sent + len(piece) - 1
                return await c.put(session, content=piece, headers={"Content-Length": str(len(piece)),
                                   "Content-Range": f"bytes {sent}-{end}/{size}"})

            async for data in chunks:
                buffer += data
                while len(buffer) >= 8 << 20:
                    piece, buffer = buffer[:8 << 20], buffer[8 << 20:]
                    r = await send(piece, False)
                    if r.status_code not in (200, 201, 308):
                        raise YouTubeError(f"the upload stopped ({r.status_code}): {r.text[:200]}")
                    sent += len(piece)
            r = await send(buffer, True)
            if r.status_code not in (200, 201):
                raise YouTubeError(f"the upload didn't finish ({r.status_code}): {r.text[:200]}")
            return r.json()["id"]

    async def set_thumbnail(self, access_token: str, video_id: str, jpeg: bytes) -> None:
        async with self.client(timeout=120.0) as c:
            r = await c.post(f"{UPLOAD}/thumbnails/set", params={"videoId": video_id}, content=jpeg,
                             headers={"Authorization": f"Bearer {access_token}", "Content-Type": "image/jpeg"})
        if r.status_code != 200:
            raise YouTubeError(f"the thumbnail wasn't accepted ({r.status_code}); new channels need phone verification for custom thumbnails")

    async def add_captions(self, access_token: str, video_id: str, srt: bytes, language: str = "en") -> None:
        boundary = "creatorshome" + base64.urlsafe_b64encode(os.urandom(6)).decode()
        meta = json.dumps({"snippet": {"videoId": video_id, "language": language, "name": "Captions", "isDraft": False}})
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
                f"--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n").encode() + srt + f"\r\n--{boundary}--".encode()
        async with self.client(timeout=120.0) as c:
            r = await c.post(f"{UPLOAD}/captions", params={"uploadType": "multipart", "part": "snippet"}, content=body,
                             headers={"Authorization": f"Bearer {access_token}", "Content-Type": f"multipart/related; boundary={boundary}"})
        if r.status_code != 200:
            raise YouTubeError(f"the captions weren't accepted ({r.status_code})")
