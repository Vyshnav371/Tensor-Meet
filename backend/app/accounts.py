"""Accounts: email + password sign-in, companies, invites.

Passwords are hashed with scrypt; sessions are random tokens kept server-side (Firestore stores only their SHA-256)
and travel in an HttpOnly cookie, so no credential or API key ever reaches the browser's JavaScript.

Layout (Firestore):
  users/{uid}            uid, email, name, password (scrypt), company_id, role (admin | member), created_at
  user_emails/{sha256}   uid                      unique email index (create-if-absent)
  sessions/{sha256}      uid, expires
  companies/{cid}        name, industry, jira_key, dataset, invite_code, status, team, examples, ...
  invites/{code}         company_id
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import re
import secrets
import time

from . import templates
from .config import Settings
from .store import now_iso

SESSION_SECONDS = 14 * 24 * 3600
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[a-z]{2,24}$", re.I)
SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)
FAILURES_ALLOWED, FAILURE_WINDOW = 8, 600  # per email and per client, per 10 minutes


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        candidate = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), **SCRYPT)
        return hmac.compare_digest(candidate, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def public_user(user: dict) -> dict:
    return {k: user.get(k) for k in ("uid", "email", "name", "role", "company_id")}


def public_company(company: dict, user: dict) -> dict:
    data = {k: company.get(k) for k in ("id", "name", "industry", "industry_label", "jira_key", "status", "error", "examples", "created_at", "voice")}
    data["sandbox"] = True
    if user.get("role") == "admin":
        data["invite_code"] = company.get("invite_code")
    return data


class Accounts:
    def __init__(self, settings: Settings, backend):
        self.settings = settings
        self.db = backend
        self._sessions: dict[str, tuple[float, dict]] = {}  # token hash -> (cached_at, user)
        self._companies: dict[str, tuple[float, dict]] = {}
        self._failures: dict[str, list[float]] = {}
        self._signups: dict[str, list[float]] = {}

    # ---- throttling ---------------------------------------------------------------------------

    def _limited(self, bucket: dict, key: str, allowed: int, window: float) -> bool:
        now = time.time()
        hits = bucket[key] = [t for t in bucket.get(key, []) if now - t < window]
        return len(hits) >= allowed

    def _fail(self, *keys: str) -> None:
        for key in keys:
            self._failures.setdefault(key, []).append(time.time())

    # ---- registration and sign-in ------------------------------------------------------------------

    async def register(self, *, name: str, email: str, password: str, client: str,
                       company_name: str | None = None, industry: str | None = None, invite: str | None = None) -> tuple[dict, dict, str]:
        """Create an account, plus either a new company (admin) or membership through an invite."""
        name, email = name.strip()[:40], email.strip().lower()
        if not name:
            raise AuthError("Please enter your name.")
        if not EMAIL.match(email):
            raise AuthError("Please enter a valid email address.")
        if not 8 <= len(password) <= 128:
            raise AuthError("Use a password of at least 8 characters.")
        if self._limited(self._signups, client, 10, 3600):
            raise AuthError("Too many sign-ups from this network. Try again later.", 429)
        if invite:
            ref = await self.db.get("invites", invite.strip())
            company = ref and await self.company(ref["company_id"])
            if not company:
                raise AuthError("This invite link is no longer valid. Ask your admin for a new one.", 404)
        else:
            company_name = (company_name or "").strip()[:60]
            if len(company_name) < 2:
                raise AuthError("Please enter your company name.")
            if industry not in templates.INDUSTRIES:
                raise AuthError("Please choose an industry.")
            if len(await self.db.list("companies")) >= self.settings.max_companies:
                raise AuthError("This Tensor instance has reached its company limit.", 403)
            company = None
        uid = "u_" + secrets.token_hex(8)
        if not await self.db.create("user_emails", _sha(email), dict(uid=uid)):
            raise AuthError("An account with this email already exists. Sign in instead.", 409)
        self._signups.setdefault(client, []).append(time.time())
        try:
            password_hash = await asyncio.to_thread(hash_password, password)
            if company is None:
                company = await self._create_company(company_name, industry, uid)
                role = "admin"
            else:
                role = "admin" if not company.get("created_by") else "member"  # a seeded company's first member runs it
                team = [p for p in company.get("team", []) if p.get("email") != email]
                team.append(dict(key=name.split()[0].lower(), name=name, role="Admin" if role == "admin" else "Member",
                                 email=email, source="member"))
                await self.update_company(company["id"], team=team, **({"created_by": uid} if role == "admin" else {}))
                company = await self.company(company["id"])
            user = dict(uid=uid, email=email, name=name, password=password_hash, company_id=company["id"], role=role, created_at=now_iso())
            await self.db.put("users", uid, user)
        except Exception:
            await self.db.delete("user_emails", _sha(email))  # don't leave the email reserved by a failed sign-up
            raise
        return user, company, await self._new_session(uid)

    async def _create_company(self, name: str, industry: str, uid: str) -> dict:
        cid = (templates.slug(name)[:24].strip("-") or "company") + "-" + secrets.token_hex(2)
        company = dict(id=cid, name=name, industry=industry, industry_label=templates.INDUSTRIES[industry]["label"],
                       jira_key=templates.jira_key(name), dataset="tensor_co_" + cid.replace("-", "_"),
                       invite_code=secrets.token_urlsafe(9), status="PROVISIONING", created_by=uid, created_at=now_iso(),
                       team=templates.team(industry, name), examples=templates.examples(industry))
        await self.db.put("companies", cid, company)
        await self.db.put("invites", company["invite_code"], dict(company_id=cid))
        return company

    async def login(self, email: str, password: str, client: str) -> tuple[dict, str]:
        email = email.strip().lower()
        if self._limited(self._failures, email, FAILURES_ALLOWED, FAILURE_WINDOW) or \
                self._limited(self._failures, client, FAILURES_ALLOWED * 3, FAILURE_WINDOW):
            raise AuthError("Too many attempts. Wait a few minutes and try again.", 429)
        ref = await self.db.get("user_emails", _sha(email))
        user = ref and await self.db.get("users", ref["uid"])
        ok = await asyncio.to_thread(verify_password, password, user["password"] if user else hash_password("timing-equaliser"))
        if not user or not ok:
            self._fail(email, client)
            raise AuthError("That email and password don't match.", 401)
        return user, await self._new_session(user["uid"])

    async def _new_session(self, uid: str) -> str:
        token = secrets.token_urlsafe(32)
        await self.db.put("sessions", _sha(token), dict(uid=uid, expires=time.time() + SESSION_SECONDS))
        return token

    async def session(self, token: str | None) -> dict | None:
        if not token or len(token) > 100:
            return None
        key = _sha(token)
        cached = self._sessions.get(key)
        if cached and time.time() - cached[0] < 60:
            return cached[1]
        record = await self.db.get("sessions", key)
        if not record or record["expires"] < time.time():
            return None
        user = await self.db.get("users", record["uid"])
        if user:
            self._sessions[key] = (time.time(), user)
        return user

    async def logout(self, token: str | None) -> None:
        if token:
            self._sessions.pop(_sha(token), None)
            await self.db.delete("sessions", _sha(token))

    # ---- companies ---------------------------------------------------------------------------------

    async def company(self, cid: str, fresh: bool = False) -> dict | None:
        cached = self._companies.get(cid)
        if cached and not fresh and time.time() - cached[0] < 30:
            return cached[1]
        company = await self.db.get("companies", cid)
        if company:
            self._companies[cid] = (time.time(), company)
        return company

    async def update_company(self, cid: str, **changes) -> dict:
        company = await self.db.get("companies", cid) or {}
        company.update(changes)
        await self.db.put("companies", cid, company)
        self._companies[cid] = (time.time(), company)
        return company

    async def members(self, cid: str) -> list[dict]:
        return sorted((public_user(u) for u in await self.db.query("users", "company_id", cid)), key=lambda u: u["name"].lower())

    async def rotate_invite(self, cid: str) -> str:
        company = await self.company(cid, fresh=True)
        if company.get("invite_code"):
            await self.db.delete("invites", company["invite_code"])
        code = secrets.token_urlsafe(9)
        await self.db.put("invites", code, dict(company_id=cid))
        await self.update_company(cid, invite_code=code)
        return code
