"""
routes/auth.py — Unified login / logout using Supabase Auth.

All roles (super_admin, dept_admin, trainer, student) log in through
the same endpoint.  After authentication the user's profile is loaded
from user_profiles and stored in the Flask session.
"""

from flask import (Blueprint, render_template, request,
                   session, redirect, url_for, flash)
from db import get_anon_client
from auth_utils import (
    SESSION_USER, SESSION_ACCESS, SESSION_REFRESH,
    load_user_profile, write_audit_log,
)

auth_bp = Blueprint("auth", __name__)


# ── Login ─────────────────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            return render_template("auth/login.html", error="Email and password are required.")

        try:
            client = get_anon_client()
            resp   = client.auth.sign_in_with_password({"email": email, "password": password})
        except Exception as exc:
            error_msg = str(exc)
            # Supabase returns "Invalid login credentials" for wrong password
            if "Invalid login credentials" in error_msg or "invalid" in error_msg.lower():
                return render_template("auth/login.html", error="Invalid email or password.")
            return render_template("auth/login.html", error="Login failed. Please try again.")

        if not resp or not resp.user:
            return render_template("auth/login.html", error="Login failed. Please try again.")

        # Load role + department from user_profiles (service client, bypasses RLS)
        profile = load_user_profile(resp.user.id)
        if not profile:
            return render_template("auth/login.html",
                                   error="Account not fully set up. Contact your administrator.")

        if not profile.get("is_active", False):
            return render_template("auth/login.html",
                                   error="Your account has been disabled. Contact your administrator.")

        # Store minimal info in session (never store the full JWT payload)
        session.permanent = bool(request.form.get("remember"))
        session[SESSION_ACCESS]  = resp.session.access_token
        session[SESSION_REFRESH] = resp.session.refresh_token
        session[SESSION_USER] = {
            "id":      resp.user.id,
            "email":   resp.user.email,
            "name":    profile.get("full_name") or resp.user.email,
            "role":    profile["role"],
            "dept_id": profile.get("department_id"),
            "active":  profile["is_active"],
        }

        write_audit_log("login", target=email)

        # Redirect based on role
        role = profile["role"]
        if role == "super_admin":
            return redirect(url_for("super_admin.dashboard"))
        elif role == "dept_admin":
            return redirect(url_for("dept_admin.dashboard"))
        elif role == "trainer":
            return redirect(url_for("lecturer.dashboard"))
        else:
            return redirect(url_for("student.dashboard"))

    return render_template("auth/login.html")


# ── Logout ────────────────────────────────────────────────────────────────────

@auth_bp.route("/logout")
def logout():
    write_audit_log("logout")
    try:
        token = session.get(SESSION_ACCESS)
        if token:
            client = get_anon_client()
            client.auth.sign_out()
    except Exception:
        pass
    session.clear()
    return redirect(url_for("main.index"))


# ── Forgot password ───────────────────────────────────────────────────────────

@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    msg = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if email:
            try:
                client = get_anon_client()
                client.auth.reset_password_email(email)
            except Exception:
                pass  # always show the same message to prevent email enumeration
        msg = ("If an account exists for that email address, "
               "a password reset link has been sent.")
    return render_template("auth/forgot_password.html", msg=msg)
