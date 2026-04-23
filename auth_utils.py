"""
auth_utils.py — Authentication helpers and RBAC decorators.

All role checks are enforced here in Python (backend), in addition to
Supabase RLS.  Never rely on frontend-only checks.
"""

from functools import wraps
from flask import session, redirect, url_for, abort, request, g
from db import get_service_client, get_user_client


# ── Session keys ─────────────────────────────────────────────────────────────
SESSION_USER     = "sb_user"       # dict: id, email, role, dept_id, name, active
SESSION_ACCESS   = "sb_access_token"
SESSION_REFRESH  = "sb_refresh_token"


# ── Helpers ───────────────────────────────────────────────────────────────────

def current_user() -> dict | None:
    return session.get(SESSION_USER)


def is_authenticated() -> bool:
    return SESSION_USER in session and session[SESSION_USER].get("active", False)


def get_authed_client():
    """Return a Supabase client scoped to the current user's JWT."""
    token = session.get(SESSION_ACCESS)
    if not token:
        abort(401)
    return get_user_client(token)


def load_user_profile(user_id: str) -> dict | None:
    """Fetch user_profiles row using the service client (bypasses RLS)."""
    svc = get_service_client()
    res = svc.table("user_profiles").select("*").eq("id", user_id).single().execute()
    return res.data if res.data else None


def refresh_session_if_needed():
    """
    Called before each request.  If the access token is close to expiry,
    use the refresh token to get a new one and update the session.
    """
    if SESSION_REFRESH not in session:
        return
    try:
        anon = get_user_client(session.get(SESSION_ACCESS, ""))
        resp = anon.auth.refresh_session(session[SESSION_REFRESH])
        if resp and resp.session:
            session[SESSION_ACCESS]  = resp.session.access_token
            session[SESSION_REFRESH] = resp.session.refresh_token
    except Exception:
        pass  # let the next request fail naturally if token is truly expired


def write_audit_log(action: str, target: str = None, detail: dict = None):
    """Write to system_logs using the service client (bypasses RLS)."""
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
    Decorator that enforces one or more allowed roles.
    Usage:  @role_required('super_admin')
            @role_required('super_admin', 'dept_admin')
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not is_authenticated():
                return redirect(url_for("main.index"))
            user = current_user()
            if user.get("role") not in roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator


def super_admin_required(f):
    return role_required("super_admin")(f)


def dept_admin_required(f):
    return role_required("super_admin", "dept_admin")(f)


def trainer_required(f):
    return role_required("trainer")(f)


def student_required(f):
    return role_required("student")(f)


def dept_isolation_check(department_id: int) -> bool:
    """
    Backend enforcement of department isolation.
    Returns True if the current user is allowed to access the given dept.
    super_admin can access any dept; others only their own.
    """
    user = current_user()
    if not user:
        return False
    if user["role"] == "super_admin":
        return True
    return user.get("dept_id") == department_id
