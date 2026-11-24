import os
import time
import secrets
import hashlib
import hmac
import threading
from collections import deque
from fastapi import Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse

COOKIE = "teacher_session"
TTL = 12 * 3600
def install_auth(app, templates):
    username = os.getenv("AUTH_USER", "teacher")
    encoded = os.getenv("AUTH_PASSWORD_HASH", "")
    origin = (os.getenv("AUTH_ORIGIN") or os.getenv("RENDER_EXTERNAL_URL") or "http://127.0.0.1:8000").rstrip("/")
    if os.getenv("RENDER") == "true" and not origin.startswith("https://"):
        raise RuntimeError("Render requires an HTTPS AUTH_ORIGIN")
    secure = origin.startswith("https://")
    try:
        salt_hex, digest_hex = encoded.split(":")
        salt, digest = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
        if len(salt) != 16 or len(digest) != 32:
            raise ValueError()
    except ValueError:
        raise RuntimeError("Run python setup_auth.py before starting the journal")
    sessions = {}
    attempts = deque()
    lock = threading.Lock()

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        now = time.monotonic()
        token = request.cookies.get(COOKIE, "")
        with lock:
            for key, expiry in list(sessions.items()):
                if expiry <= now:
                    del sessions[key]
            valid = token in sessions
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("origin") != origin:
                return PlainTextResponse("Forbidden: invalid origin", status_code=403,
                                         headers={"Cache-Control": "no-store"})
        if path == "/healthz" and request.method == "GET":
            return PlainTextResponse("ok", headers={"Cache-Control": "no-store"})
        # Logout is idempotent, even after a server restart or session expiry.
        # The origin check above still applies to every POST.
        logout_post = path == "/logout" and request.method == "POST"
        if not valid and path != "/login" and not path.startswith("/static/") and not logout_post:
            if request.method in ("GET", "HEAD"):
                return RedirectResponse("/login", status_code=303, headers={"Cache-Control": "no-store"})
            return PlainTextResponse("Session expired. Sign in again.", status_code=401,
                                     headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {"error": ""})

    @app.post("/login")
    def login(request: Request, login: str = Form(...), password: str = Form(...)):
        now = time.monotonic()
        with lock:
            while attempts and attempts[0] <= now - 300:
                attempts.popleft()
            if len(attempts) >= 8:
                return templates.TemplateResponse(request, "login.html",
                    {"error": "Слишком много попыток. Подождите 5 минут."}, status_code=429)
            attempts.append(now)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600000)
        if not (hmac.compare_digest(login.encode(), username.encode()) and hmac.compare_digest(candidate, digest)):
            return templates.TemplateResponse(request, "login.html",
                {"error": "Неверный логин или пароль."}, status_code=401)
        token = secrets.token_urlsafe(32)
        with lock:
            sessions.pop(request.cookies.get(COOKIE, ""), None)
            sessions[token] = time.monotonic() + TTL
            attempts.clear()
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, token, max_age=TTL, httponly=True, secure=secure, samesite="strict", path="/")
        return response

    @app.post("/logout")
    def logout(request: Request):
        with lock:
            sessions.pop(request.cookies.get(COOKIE, ""), None)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE, path="/", secure=secure, httponly=True, samesite="strict")
        return response
