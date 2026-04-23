"""
routes/student.py — Student blueprint.

Students can only view their own attendance data.
Self-registration links a Supabase Auth account to an existing
students row (pre-created by admin).
"""

import re
from flask import (Blueprint, render_template, request,
                   session, redirect, url_for, abort)
from auth_utils import (student_required, write_audit_log, current_user,
                        SESSION_USER, SESSION_ACCESS, SESSION_REFRESH)
from db import get_anon_client, get_service_client
from utils import now_eat

student_bp = Blueprint("student", __name__)

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')


def _validate_password(pwd: str) -> str | None:
    if len(pwd) < 8:
        return "Password must be at least 8 characters."
    if not re.search(r'\d', pwd):
        return "Password must contain at least one number."
    if not re.search(r'[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>/?]', pwd):
        return "Password must contain at least one symbol (e.g. @, #, !)."
    return None


def _student_row() -> dict:
    """Return the students table row for the current user, or abort 403."""
    user = current_user()
    db   = get_service_client()
    row  = (db.table("students")
              .select("*")
              .eq("user_id", user["id"])
              .single()
              .execute().data)
    if not row:
        abort(403)
    return row


# ── Self-Registration ─────────────────────────────────────────────────────────

@student_bp.route("/register", methods=["GET", "POST"])
def register():
    """
    Students register by providing their admission number (pre-loaded by admin)
    plus an email and password.  This creates a Supabase Auth account and links
    it to the existing students row.
    """
    error = None
    db    = get_service_client()

    dept_id = request.args.get("dept_id", 0, type=int)

    if request.method == "POST":
        adm      = request.form.get("admission_number", "").strip()
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        fullname = request.form.get("fullname", "").strip().upper()
        class_id = request.form.get("class_id", 0, type=int)
        dept_id  = request.form.get("dept_id", 0, type=int)

        if not all([adm, email, password, fullname, class_id]):
            error = "All fields are required."
        elif not EMAIL_RE.match(email):
            error = "Please enter a valid email address."
        else:
            pwd_err = _validate_password(password)
            if pwd_err:
                error = pwd_err
            else:
                # Check admission number exists and is not yet registered
                student_row = (db.table("students")
                                 .select("id, email, user_id")
                                 .eq("admission_number", adm)
                                 .single()
                                 .execute().data)
                if not student_row:
                    error = "Admission number not found. Only students added by the Admin can register."
                elif student_row.get("user_id"):
                    error = "Account already registered. Please log in."
                else:
                    try:
                        # Create Supabase Auth user
                        resp = db.auth.admin.create_user({
                            "email":    email,
                            "password": password,
                            "email_confirm": True,
                            "user_metadata": {
                                "full_name": fullname,
                                "role":      "student",
                            },
                        })
                        user_id = resp.user.id

                        # Upsert profile
                        db.table("user_profiles").upsert({
                            "id":            user_id,
                            "full_name":     fullname,
                            "role":          "student",
                            "department_id": None,
                            "is_active":     True,
                        }).execute()

                        # Link auth user to students row
                        db.table("students").update({
                            "user_id":   user_id,
                            "full_name": fullname,
                            "email":     email,
                            "class_id":  class_id,
                        }).eq("id", student_row["id"]).execute()

                        write_audit_log("student_register", target=adm)
                        return redirect(url_for("auth.login") + "?registered=1")
                    except Exception as exc:
                        error = f"Registration failed: {exc}"

    depts   = db.table("departments").select("*").order("name").execute().data or []
    if dept_id:
        classes = (db.table("classes")
                     .select("*")
                     .eq("department_id", dept_id)
                     .order("name")
                     .execute().data or [])
    else:
        classes = db.table("classes").select("*").order("name").execute().data or []

    return render_template("student/register.html",
                           error=error, classes=classes,
                           departments=depts, dept_id=dept_id)


# ── Dashboard ─────────────────────────────────────────────────────────────────

@student_bp.route("/")
@student_bp.route("/dashboard")
@student_required
def dashboard():
    db      = get_service_client()
    student = _student_row()

    # Attendance summary per unit
    summary = (db.table("v_student_attendance_summary")
                 .select("*")
                 .eq("student_id", student["id"])
                 .execute().data or [])

    total_attended = sum(r.get("attended", 0) or 0 for r in summary)
    total_records  = sum(r.get("total_records", 0) or 0 for r in summary)
    overall_pct    = round((total_attended / total_records) * 100, 1) if total_records else 0

    current_month = now_eat().strftime("%B %Y")

    return render_template("student/dashboard.html",
                           student=student,
                           attendance_data=summary,
                           total_attended=total_attended,
                           overall_pct=overall_pct,
                           current_month=current_month)


# ── Unit Detail ───────────────────────────────────────────────────────────────

@student_bp.route("/unit-detail")
@student_required
def unit_detail():
    db      = get_service_client()
    student = _student_row()
    unit_id = request.args.get("unit_id", type=int)

    if not unit_id:
        return redirect(url_for("student.dashboard"))

    unit = db.table("units").select("*").eq("id", unit_id).single().execute().data or {}

    records = (db.table("attendance")
                 .select("*")
                 .eq("student_id", student["id"])
                 .eq("unit_id", unit_id)
                 .order("year").order("term").order("week").order("lesson")
                 .execute().data or [])

    attended = sum(1 for r in records if r["status"] == "present")
    total    = len(records)
    pct      = round((attended / total) * 100, 1) if total else 0

    return render_template("student/unit_detail.html",
                           student=student,
                           unit=unit,
                           records=records,
                           attended=attended,
                           total=total,
                           pct=pct)
