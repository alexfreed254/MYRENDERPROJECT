"""
routes/auth.py — Unified login / logout using Supabase Auth.

Self-healing: if a user_profiles row is missing at login time,
it is created automatically so the user is never locked out.
"""

import traceback
from flask import (Blueprint, render_template, request,
                   session, redirect, url_for, flash, jsonify)
from db import get_anon_client, get_service_client
from auth_utils import (
    SESSION_USER, SESSION_ACCESS, SESSION_REFRESH,
    load_user_profile, write_audit_log,
)

auth_bp = Blueprint("auth", __name__)


def _ensure_profile(user_id: str, email: str) -> dict:
    """
    svc = get_service_client()

    # Try to fetch existing profile
    try:
        res = svc.table("user_profiles").select("*").eq("id", user_id).single().execute()
        if res.data:
            return res.data
    except Exception:
        pass  # .single() raises if no row found — fall through to create

    # Profile missing — create a default one
    try:
        svc.table("user_profiles").insert({
            "id":            user_id,
            "full_name":     email,
            "role":          "student",
            "department_id": None,
            "is_active":     True,
        }).execute()
        # Fetch the newly created row
        res = svc.table("user_profiles").select("*").eq("id", user_id).single().execute()
        return res.data if res.data else None
    except Exception as exc:
        print(f"[auth] _ensure_profile failed for {user_id}: {exc}")
        traceback.print_exc()
        return None


# ── Student Login (admission number + password) ───────────────────────────────

@auth_bp.route("/student-login", methods=["GET", "POST"])
def student_login():
    """Dedicated login for students using admission number instead of email."""
    if request.method == "POST":
        adm_number = request.form.get("admission_number", "").strip()
        password   = request.form.get("password", "")

        if not adm_number or not password:
            return render_template("student/login.html",
                                   error="Admission number and password are required.",
                                   admission_number=adm_number)

        svc = get_service_client()

        # Look up the student's email by admission number
        try:
            rows = (svc.table("students")
                       .select("email, user_id")
                       .eq("admission_number", adm_number)
                       .limit(1)
                       .execute().data or [])
        except Exception as exc:
            return render_template("student/login.html",
                                   error=f"Database error: {exc}",
                                   admission_number=adm_number)

        if not rows:
            return render_template("student/login.html",
                                   error="Admission number not found.",
                                   admission_number=adm_number)

        email = rows[0].get("email")
        if not email:
            return render_template("student/login.html",
                                   error="Account not registered yet. Please register first.",
                                   admission_number=adm_number)

        # Authenticate via Supabase Auth
        try:
            client = get_anon_client()
            resp   = client.auth.sign_in_with_password({"email": email, "password": password})
        except Exception as exc:
            msg = str(exc)
            if any(k in msg.lower() for k in ["invalid login", "invalid credentials", "invalid"]):
                return render_template("student/login.html",
                                       error="Invalid admission number or password.",
                                       admission_number=adm_number)
            return render_template("student/login.html",
                                   error=f"Login error: {msg}",
                                   admission_number=adm_number)

        if not resp or not resp.user:
            return render_template("student/login.html",
                                   error="Login failed. Please try again.",
                                   admission_number=adm_number)

        profile = _ensure_profile(resp.user.id, email)
        if not profile:
            return render_template("student/login.html",
                                   error="Profile could not be loaded. Contact administrator.",
                                   admission_number=adm_number)

        if not profile.get("is_active", False):
            return render_template("student/login.html",
                                   error="Your account has been disabled.",
                                   admission_number=adm_number)

        session.permanent = bool(request.form.get("remember"))
        session[SESSION_ACCESS]  = resp.session.access_token
        session[SESSION_REFRESH] = resp.session.refresh_token
        session[SESSION_USER] = {
            "id":      resp.user.id,
            "email":   resp.user.email,
            "name":    profile.get("full_name") or adm_number,
            "role":    "student",
            "dept_id": profile.get("department_id"),
            "active":  profile["is_active"],
        }

        write_audit_log("student_login", target=adm_number)
        return redirect(url_for("student.dashboard"))

    return render_template("student/login.html",
                           registered=request.args.get("registered"))


# ── Login ─────────────────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            return render_template("auth/login.html",
                                   error="Email and password are required.")

        # ── Step 1: Authenticate with Supabase Auth ───────────────────────────
        try:
            client = get_anon_client()
            resp   = client.auth.sign_in_with_password({
                "email":    email,
                "password": password,
            })
        except Exception as exc:
            msg = str(exc)
            print(f"[auth] sign_in failed for {email}: {msg}")
            if any(k in msg.lower() for k in ["invalid login", "invalid credentials",
                                               "email not confirmed", "invalid"]):
                return render_template("auth/login.html",
                                       error="Invalid email or password.")
            return render_template("auth/login.html",
                                   error=f"Login error: {msg}")

        if not resp or not resp.user:
            return render_template("auth/login.html",
                                   error="Login failed — no user returned.")

        user_id = resp.user.id

        # ── Step 2: Load (or auto-create) user_profiles row ──────────────────
        profile = _ensure_profile(user_id, email)

        if not profile:
            return render_template("auth/login.html",
                                   error=(
                                       "Profile could not be loaded. "
                                       "Please run the fix SQL in Supabase and try again."
                                   ))

        if not profile.get("is_active", False):
            return render_template("auth/login.html",
                                   error="Your account has been disabled. "
                                         "Contact your administrator.")

        # ── Step 3: Store session ─────────────────────────────────────────────
        session.permanent = bool(request.form.get("remember"))
        session[SESSION_ACCESS]  = resp.session.access_token
        session[SESSION_REFRESH] = resp.session.refresh_token
        session[SESSION_USER] = {
            "id":      user_id,
            "email":   resp.user.email,
            "name":    profile.get("full_name") or email,
            "role":    profile["role"],
            "dept_id": profile.get("department_id"),
            "active":  profile["is_active"],
        }

        write_audit_log("login", target=email)

        # ── Step 4: Redirect by role ──────────────────────────────────────────
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
                pass
        msg = ("If an account exists for that email address, "
               "a password reset link has been sent.")
    return render_template("auth/forgot_password.html", msg=msg)


# ── Debug: check profile (remove after confirming login works) ────────────────

@auth_bp.route("/debug-profile")
def debug_profile():
    """
    Temporary diagnostic endpoint.
    Visit /auth/debug-profile?email=your@email.com to check if a
    user_profiles row exists for that email.
    Remove this route once login is confirmed working.
    """
    email = request.args.get("email", "").strip().lower()
    if not email:
        return jsonify({"error": "Pass ?email=your@email.com"}), 400

    svc = get_service_client()
    result = {"email": email}

    try:
        # Check auth.users
        # Note: we can't query auth.users directly via the client,
        # but we can check user_profiles by looking for any row
        profiles = (svc.table("user_profiles")
                       .select("id, full_name, role, is_active, department_id")
                       .execute().data or [])
        result["total_profiles"] = len(profiles)
        result["profiles_sample"] = profiles[:5]
    except Exception as exc:
        result["profiles_error"] = str(exc)

    return jsonify(result)
