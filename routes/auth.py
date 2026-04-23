"""
routes/auth.py — Unified login / logout using Supabase Auth.

Self-healing: if a user_profiles row is missing at login time,
it is created automatically so the user is never locked out.
"""

import traceback
from flask import (Blueprint, render_template, request,
                   session, redirect, url_for, jsonify)
from db import get_anon_client, get_service_client
from auth_utils import (
    SESSION_USER, SESSION_ACCESS, SESSION_REFRESH,
    write_audit_log,
)

auth_bp = Blueprint("auth", __name__)


def _ensure_profile(user_id: str, email: str) -> dict:
    # Returns the user_profiles row, creating it if missing.
    svc = get_service_client()
    try:
        res = (svc.table("user_profiles")
                  .select("*")
                  .eq("id", user_id)
                  .limit(1)
                  .execute().data or [])
        if res:
            return res[0]
    except Exception:
        pass

    # Profile missing — create a default student row
    try:
        svc.table("user_profiles").insert({
            "id":            user_id,
            "full_name":     email,
            "role":          "student",
            "department_id": None,
            "is_active":     True,
        }).execute()
        res = (svc.table("user_profiles")
                  .select("*")
                  .eq("id", user_id)
                  .limit(1)
                  .execute().data or [])
        return res[0] if res else None
    except Exception as exc:
        print(f"[auth] _ensure_profile failed for {user_id}: {exc}")
        traceback.print_exc()
        return None


# ── Student Login (admission number + password) ───────────────────────────────

@auth_bp.route("/student-login", methods=["GET", "POST"])
def student_login():
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


# ── Trainer Login (username + password) ──────────────────────────────────────

@auth_bp.route("/trainer-login", methods=["GET", "POST"])
def trainer_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            return render_template("lecturer/login.html",
                                   error="Username and password are required.",
                                   username=username)

        svc = get_service_client()

        # Look up the trainer's auth email by username
        try:
            rows = (svc.table("trainers")
                       .select("user_id, name")
                       .eq("username", username)
                       .limit(1)
                       .execute().data or [])
        except Exception as exc:
            return render_template("lecturer/login.html",
                                   error=f"Database error: {exc}",
                                   username=username)

        if not rows:
            return render_template("lecturer/login.html",
                                   error="Invalid username or password.",
                                   username=username)

        user_id = rows[0].get("user_id")
        if not user_id:
            return render_template("lecturer/login.html",
                                   error="Account not set up yet. Contact your administrator.",
                                   username=username)

        # Get the email from auth.users via user_profiles
        try:
            profile_rows = (svc.table("user_profiles")
                               .select("id")
                               .eq("id", user_id)
                               .limit(1)
                               .execute().data or [])
        except Exception:
            profile_rows = []

        # Fetch email from Supabase admin API
        try:
            user_data = svc.auth.admin.get_user_by_id(user_id)
            email = user_data.user.email if user_data and user_data.user else None
        except Exception as exc:
            return render_template("lecturer/login.html",
                                   error=f"Could not retrieve account: {exc}",
                                   username=username)

        if not email:
            return render_template("lecturer/login.html",
                                   error="Account email not found. Contact administrator.",
                                   username=username)

        # Authenticate via Supabase Auth
        try:
            client = get_anon_client()
            resp   = client.auth.sign_in_with_password({"email": email, "password": password})
        except Exception as exc:
            msg = str(exc)
            if any(k in msg.lower() for k in ["invalid login", "invalid credentials", "invalid"]):
                return render_template("lecturer/login.html",
                                       error="Invalid username or password.",
                                       username=username)
            return render_template("lecturer/login.html",
                                   error=f"Login error: {msg}",
                                   username=username)

        if not resp or not resp.user:
            return render_template("lecturer/login.html",
                                   error="Login failed. Please try again.",
                                   username=username)

        profile = _ensure_profile(resp.user.id, email)
        if not profile:
            return render_template("lecturer/login.html",
                                   error="Profile could not be loaded. Contact administrator.",
                                   username=username)

        if not profile.get("is_active", False):
            return render_template("lecturer/login.html",
                                   error="Your account has been disabled.",
                                   username=username)

        session.permanent = bool(request.form.get("remember"))
        session[SESSION_ACCESS]  = resp.session.access_token
        session[SESSION_REFRESH] = resp.session.refresh_token
        session[SESSION_USER] = {
            "id":      resp.user.id,
            "email":   resp.user.email,
            "name":    profile.get("full_name") or rows[0].get("name") or username,
            "role":    "trainer",
            "dept_id": profile.get("department_id"),
            "active":  profile["is_active"],
        }

        write_audit_log("trainer_login", target=username)
        return redirect(url_for("lecturer.dashboard"))

    return render_template("lecturer/login.html", username=request.args.get("username"))


# ── Unified Login (admin / dept_admin) ───────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            return render_template("auth/login.html",
                                   error="Email and password are required.")

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
        profile = _ensure_profile(user_id, email)

        if not profile:
            return render_template("auth/login.html",
                                   error="Profile could not be loaded. "
                                         "Please run the fix SQL in Supabase and try again.")

        if not profile.get("is_active", False):
            return render_template("auth/login.html",
                                   error="Your account has been disabled. "
                                         "Contact your administrator.")

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
        get_anon_client().auth.sign_out()
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
                get_anon_client().auth.reset_password_email(email)
            except Exception:
                pass
        msg = ("If an account exists for that email address, "
               "a password reset link has been sent.")
    return render_template("auth/forgot_password.html", msg=msg)
