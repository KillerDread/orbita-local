"""Local-only X account audit. Standard library only; binds to 127.0.0.1."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = int(os.getenv("ORBITA_PORT", "8765"))
REDIRECT_URI = f"http://{HOST}:{PORT}/oauth/callback"
API = "https://api.x.com/2"
AUTH = "https://x.com/i/oauth2/authorize"
TOKEN = "https://api.x.com/2/oauth2/token"
SCOPES = "users.read follows.read tweet.read"
MAX_SCAN = 250
oauth_states: dict[str, tuple[str, float, str]] = {}
access_token: str | None = None
connected_user: dict | None = None
jobs: dict[str, dict] = {}
lock = threading.Lock()


def load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def config() -> tuple[str, str]:
    load_dotenv()
    client_id = os.getenv("X_CLIENT_ID", "").strip()
    client_secret = os.getenv("X_CLIENT_SECRET", "").strip()
    if not client_id:
        raise RuntimeError("Falta X_CLIENT_ID. Copia .env.example a .env y añade tu Client ID.")
    return client_id, client_secret


def request_json(url: str, *, method: str = "GET", form: dict | None = None,
                 token: str | None = None, client_id: str | None = None,
                 client_secret: str | None = None) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "OrbitaLocal/1.0"}
    body = None
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        body = urllib.parse.urlencode(form).encode()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif client_id and client_secret:
        pair = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers["Authorization"] = f"Basic {pair}"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1500]
        try:
            parsed = json.loads(detail)
            detail = "; ".join(e.get("message", e.get("title", "")) for e in parsed.get("errors", [])) or detail
        except Exception:
            pass
        raise RuntimeError(f"X devolvió HTTP {exc.code}: {detail or exc.reason}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"No se pudo conectar con X: {exc}") from None


def x_get(path: str, params: dict | None = None) -> dict:
    if not access_token:
        raise RuntimeError("Conecta tu cuenta de X antes de iniciar el análisis.")
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return request_json(url, token=access_token)


def get_following(user_id: str, limit: int) -> tuple[list[dict], bool]:
    result: list[dict] = []
    page_token = None
    truncated = False
    while len(result) < limit:
        params = {
            "max_results": min(1000, max(1, limit - len(result))),
            "user.fields": "created_at,description,public_metrics,profile_image_url,verified",
        }
        if page_token:
            params["pagination_token"] = page_token
        page = x_get(f"/users/{urllib.parse.quote(user_id)}/following", params)
        result.extend(page.get("data", []))
        page_token = page.get("meta", {}).get("next_token")
        if not page_token:
            break
    else:
        truncated = bool(page_token)
    return result[:limit], truncated


def inspect_user(user: dict) -> dict:
    tweets = x_get(f"/users/{urllib.parse.quote(str(user['id']))}/tweets", {
        "max_results": 5,
        "tweet.fields": "created_at",
    }).get("data", [])
    recent = max((t.get("created_at", "") for t in tweets), default="")
    return {"user": user, "last_post": recent or None}


def bot_signals(user: dict) -> list[str]:
    metrics = user.get("public_metrics") or {}
    followers = metrics.get("followers_count")
    following = metrics.get("following_count")
    posts = metrics.get("post_count")
    signals: list[str] = []
    if isinstance(followers, (int, float)) and isinstance(following, (int, float)):
        if following >= 1000 and following / max(followers, 1) >= 20:
            signals.append("Sigue muchas más cuentas de las que le siguen")
    created = user.get("created_at")
    if created and isinstance(posts, (int, float)):
        try:
            age_days = max(1, (datetime.now(timezone.utc) - datetime.fromisoformat(created.replace("Z", "+00:00"))).days)
            per_day = posts / age_days
            if posts >= 3000 and per_day >= 25:
                signals.append("Ritmo medio de publicaciones muy alto")
        except (ValueError, TypeError):
            pass
    if isinstance(posts, (int, float)) and isinstance(followers, (int, float)):
        if posts >= 10000 and followers < 100:
            signals.append("Muchísimas publicaciones con muy pocos seguidores")
    return signals


def analyze(result: dict, records: list[dict], truncated: bool) -> None:
    result.update({"total": len(records), "done": 0, "truncated": truncated})
    analyzed = []
    errors = 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(inspect_user, item): item for item in records}
        for future in as_completed(futures):
            item = futures[future]
            try:
                examined = future.result()
                user = examined["user"]
                last = examined["last_post"]
                signals = bot_signals(user)
                status = "bot" if len(signals) >= 2 else "unknown"
                if last:
                    dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                    inactive = (datetime.now(timezone.utc) - dt).days >= 365
                    if status != "bot":
                        status = "inactive" if inactive else "active"
                    inactivity_label = f"Inactiva desde {dt.year}" if inactive else ""
                else:
                    inactivity_label = ""
                    if status != "bot":
                        status = "unknown"
                if not signals:
                    signals = ["Sin señales suficientes para estimar automatización"]
                analyzed.append({
                    "id": user.get("id"), "username": user.get("username", "cuenta"),
                    "name": user.get("name", user.get("username", "Cuenta")),
                    "image": user.get("profile_image_url"),
                    "followers": (user.get("public_metrics") or {}).get("followers_count"),
                    "following": (user.get("public_metrics") or {}).get("following_count"),
                    "posts": (user.get("public_metrics") or {}).get("post_count"),
                    "last_post": last, "inactivity_label": inactivity_label,
                    "status": status, "signals": signals,
                    "profile": "https://x.com/" + urllib.parse.quote(user.get("username", "")),
                })
            except Exception as exc:
                errors += 1
                user = item
                analyzed.append({"id": user.get("id"), "username": user.get("username", "cuenta"),
                    "name": user.get("name", user.get("username", "Cuenta")), "image": user.get("profile_image_url"),
                    "last_post": None, "inactivity_label": "", "status": "unknown",
                    "signals": ["X no permitió consultar publicaciones: " + str(exc)[:180]],
                    "profile": "https://x.com/" + urllib.parse.quote(user.get("username", ""))})
            with lock:
                result["done"] += 1
                result["partial"] = analyzed
    analyzed.sort(key=lambda row: (row["status"] != "bot", row["status"] != "inactive", row["username"].lower()))
    with lock:
        result["partial"] = None
        result["data"] = analyzed
        result["errors"] = errors
        result["state"] = "done"


class Handler(BaseHTTPRequestHandler):
    server_version = "OrbitaLocal/1.0"

    def log_message(self, fmt, *args):
        # Avoid writing tokens or account data to routine console logs.
        print("Órbita local · " + (fmt % args).split(" ")[0])

    def send_bytes(self, status: int, payload: bytes, content_type: str = "text/html; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, status: int, value: dict):
        self.send_bytes(status, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        global access_token, connected_user
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            return self.send_bytes(200, (ROOT / "index.html").read_bytes())
        if parsed.path == "/api/status":
            return self.send_json(200, {"connected": bool(access_token), "user": connected_user})
        if parsed.path == "/connect":
            try:
                client_id, _ = config()
            except RuntimeError as exc:
                return self.send_json(400, {"error": str(exc)})
            requested_handle = urllib.parse.parse_qs(parsed.query).get("handle", [""])[0].strip().lstrip("@").lower()
            if not requested_handle or len(requested_handle) > 15 or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in requested_handle):
                return self.send_json(400, {"error": "Escribe un @ de X válido antes de conectar."})
            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            state = secrets.token_urlsafe(32)
            oauth_states[state] = (verifier, time.time(), requested_handle)
            params = urllib.parse.urlencode({"response_type": "code", "client_id": client_id,
                "redirect_uri": REDIRECT_URI, "scope": SCOPES, "state": state,
                "code_challenge": challenge, "code_challenge_method": "S256"})
            self.send_response(302)
            self.send_header("Location", AUTH + "?" + params)
            self.end_headers()
            return
        if parsed.path == "/oauth/callback":
            params = urllib.parse.parse_qs(parsed.query)
            state = params.get("state", [""])[0]
            code = params.get("code", [""])[0]
            saved = oauth_states.pop(state, None)
            if not saved or time.time() - saved[1] > 600 or not code:
                return self.send_bytes(400, b"<h1>Inicio de sesion cancelado o caducado</h1><p>Vuelve a la app y prueba otra vez.</p>")
            try:
                client_id, client_secret = config()
                token_data = request_json(TOKEN, method="POST", form={"grant_type": "authorization_code",
                    "code": code, "redirect_uri": REDIRECT_URI, "code_verifier": saved[0],
                    **({} if client_secret else {"client_id": client_id})},
                    client_id=client_id if client_secret else None,
                    client_secret=client_secret if client_secret else None)
                access_token = token_data["access_token"]
                me = x_get("/users/me", {"user.fields": "profile_image_url,public_metrics"}).get("data", {})
                if me.get("username", "").lower() != saved[2]:
                    access_token = None
                    raise RuntimeError(f"La cuenta autorizada es @{me.get('username', 'desconocida')}, pero escribiste @{saved[2]}. Vuelve atrás y escribe el @ correcto.")
                connected_user = {"id": me.get("id"), "username": me.get("username"), "name": me.get("name"), "image": me.get("profile_image_url")}
                self.send_response(302)
                self.send_header("Location", "/?connected=1")
                self.end_headers()
            except Exception as exc:
                access_token = None
                connected_user = None
                self.send_bytes(400, ("<h1>No se pudo conectar con X</h1><pre>" + str(exc).replace("<", "&lt;") + "</pre><p>Revisa las credenciales y la URL de retorno en .env / Developer Console.</p><a href='/'>Volver</a>").encode())
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            with lock:
                result = jobs.get(job_id)
                if result is not None:
                    snapshot = {k: v for k, v in result.items() if k != "partial"}
                    if result.get("state") == "running":
                        snapshot["partial"] = result.get("partial")
            return self.send_json(200 if result else 404, snapshot if result else {"error": "No se encontró el análisis"})
        self.send_json(404, {"error": "No encontrado"})

    def do_POST(self):
        if self.path == "/api/disconnect":
            global access_token, connected_user
            access_token = None
            connected_user = None
            return self.send_json(200, {"ok": True})
        if self.path != "/api/scan":
            return self.send_json(404, {"error": "No encontrado"})
        try:
            if not access_token or not connected_user:
                raise RuntimeError("Primero conecta tu cuenta de X.")
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(min(size, 4096)) or b"{}")
            limit = int(payload.get("limit", 100))
            if limit < 1 or limit > MAX_SCAN:
                raise RuntimeError(f"El límite debe estar entre 1 y {MAX_SCAN} cuentas por análisis.")
            job_id = secrets.token_urlsafe(18)
            job = {"state": "running", "done": 0, "total": 0, "data": None, "partial": []}
            with lock:
                jobs[job_id] = job
            thread = threading.Thread(target=self._run_scan, args=(job, limit), daemon=True)
            thread.start()
            self.send_json(202, {"job_id": job_id})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def _run_scan(self, job: dict, limit: int):
        try:
            followed, truncated = get_following(connected_user["id"], limit)
            analyze(job, followed, truncated)
        except Exception as exc:
            with lock:
                job["state"] = "error"
                job["error"] = str(exc)


def main():
    try:
        config()
    except RuntimeError as exc:
        print("Configuración pendiente: " + str(exc))
    print(f"Órbita se ejecuta solo en este equipo: http://{HOST}:{PORT}")
    print("Detén el servidor con Ctrl+C. No compartas el archivo .env.")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
