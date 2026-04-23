"""
auth_utils.py — Authentication helpers and RBAC decorators.

All role checks are enforced here in Python (backend), in addition to
Supabase RLS. Never rely on frontend-only checks.
"""

import traceback
from functools import wraps
from typing import Optional
from flask import session, redirect, url_for, abort, request
from db import get_service_client, get_anon_client


# ── Session keys ──────────────────────────────────────────────────────────────
SESSION_USER    = "sb_user"
SESSION_ACCESS  = "sb_access_token"
SESSION_REFRESH = "sb_refresh_token"


# ── Helpers ───────────────────────────────────────────────────────────────────

def current_user() -> Optional[dict]:
    return session.get(SESSION_USER)


def is_authenticated() -> bool:
    user = session.get(SESSION_USER)
    return bool(user and user.get("active", False))


def load_user_profile(user_id: str) -> Optional[dict]:
    """Fetch user_profiles row using the service client (bypasses RLS)."""
    try:
        svc = get_service_client()
        res = svc.table("user_profiles").select("*").eq("id", user_id).single().execute()
        return res.data if res.data else None
    except Exception:
        return None


def refresh_session_if_needed():
    """
    Called before each request. Attempts to refresh the JWT using the
    stored refresh token. Silently skips on any error.
    """
    if SESSION_REFRESH not in session or SESSION_ACCESS not in session:
        return
    try:
        client = get_anon_client()
        resp = client.auth.refresh_session(session[SESSION_REFRESH])
        if resp and resp.session:
            session[SESSION_ACCESS]  = resp.session.access_token
            session[SESSION_REFRESH] = resp.session.refresh_token
    except Exception:
        # Token may be expired — user will be redirected to login on next
        # protected route access. Do not crash here.
        pass


def write_audit_log(action: str, target: str = None, detail: dict = None):
    """Write to system_logs using the service client. Never raises."""
    user = current_user()
    try:
        svc = get_service_client()
        svc.table("system_logs").insert({
            "actor_id":   user["id"]   if user else None,
            "actor_role": user["role"] if user else None,
            "action":     action,
            "target":     target,
            "detail":     detail,
            "ip_address": request.remote_addr,
        }).execute()
    except Exception:
        pass  # logging must never break the main flow


# ── RBAC Decorators ───────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_authenticated():
            return redirect(url_for("main.index"))
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    """
    Enforce one or more allowed roles.
    Usage:  @role_required('super_admin')
            @role_required('super_admin', 'dept_admin')
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not is_authenticated():
                return redirect(url_for("main.index"))
            user = current_user()
            if not user or user.get("role") not in roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator


def super_admin_required(f):
    return role_required("super_admin")(f)


def dept_admin_required(f):
    # super_admin can also access dept_admin pages
    return role_required("super_admin", "dept_admin")(f)


def trainer_required(f):
    return role_required("trainer")(f)


def student_required(f):
    return role_required("student")(f)


def dept_isolation_check(department_id: int) -> bool:
    """
    Returns True if the current user may access the given department.
    super_admin can access any dept; others only their own.
    """
    user = current_user()
    if not user:
        return False
    if user["role"] == "super_admin":
        return True
    return user.get("dept_id") == department_id
