"""
routes/student.py — Student blueprint.
"""

import re
from typing import Optional
from flask import (Blueprint, render_template, request,
                   redirect, url_for, abort)
from auth_utils import student_required, write_audit_log, current_user
from db import get_service_client
from utils import now_eat

student_bp = Blueprint("student", __name__)

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')


def _validate_password(pwd: str) -> Optional[str]:
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
    try:
        rows = (db.table("students")
                  .select("*, classes(name, department_id, departments(name))")
                  .eq("user_id", user["id"])
                  .limit(1)
                  .execute().data or [])
        if not rows:
            abort(403)
        return rows[0]
    except Exception:
        abort(403)


# ── Self-Registration ─────────────────────────────────────────────────────────

@student_bp.route("/register", methods=["GET", "POST"])
def register():
    error  = None
    db     = get_service_client()
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
                try:
                    rows = (db.table("students")
                              .select("id, email, user_id")
                              .eq("admission_number", adm)
                              .limit(1)
                              .execute().data or [])
                    if not rows:
                        error = "Admission number not found. Only students added by the Admin can register."
                    elif rows[0].get("user_id"):
                        error = "Account already registered. Please log in."
                    else:
                        student_row = rows[0]
                        resp = db.auth.admin.create_user({
                            "email":         email,
                            "password":      password,
                            "email_confirm": True,
                            "user_metadata": {"full_name": fullname, "role": "student"},
                        })
                        user_id = resp.user.id
                        db.table("user_profiles").upsert({
                            "id":            user_id,
                            "full_name":     fullname,
                            "role":          "student",
                            "department_id": None,
                            "is_active":     True,
                        }).execute()
                        db.table("students").update({
                            "user_id":   user_id,
                            "full_name": fullname,
                            "email":     email,
                            "class_id":  class_id,
                        }).eq("id", student_row["id"]).execute()
                        write_audit_log("student_register", target=adm)
                        return redirect(url_for("auth.student_login") + "?registered=1")
                except Exception as exc:
                    error = f"Registration failed: {exc}"

    try:
        depts = db.table("departments").select("*").order("name").execute().data or []
        if dept_id:
            classes = (db.table("classes").select("*")
                         .eq("department_id", dept_id).order("name")
                         .execute().data or [])
        else:
            classes = db.table("classes").select("*").order("name").execute().data or []
    except Exception:
        depts = []; classes = []

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

    # Build attendance summary manually (avoids dependency on the view)
    try:
        att_rows = (db.table("attendance")
                      .select("unit_id, status, attendance_date, units(id, code, name)")
                      .eq("student_id", student["id"])
                      .execute().data or [])
    except Exception:
        att_rows = []

    # Group by unit
    unit_map = {}
    for r in att_rows:
        uid = r["unit_id"]
        if uid not in unit_map:
            unit_map[uid] = {
                "id":           uid,
                "unit_code":    (r.get("units") or {}).get("code", "—"),
                "unit_name":    (r.get("units") or {}).get("name", "—"),
                "attended":     0,
                "total_records": 0,
                "last_update":  None,
            }
        unit_map[uid]["total_records"] += 1
        if r["status"] == "present":
            unit_map[uid]["attended"] += 1
        # Keep the most recent date as a plain string
        d = r.get("attendance_date") or ""
        if d and (not unit_map[uid]["last_update"] or d > unit_map[uid]["last_update"]):
            unit_map[uid]["last_update"] = d

    summary = list(unit_map.values())

    total_attended = sum(u["attended"] for u in summary)
    total_records  = sum(u["total_records"] for u in summary)
    overall_pct    = round((total_attended / total_records) * 100, 1) if total_records else 0
    current_month  = now_eat().strftime("%B %Y")

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

    try:
        unit_rows = (db.table("units").select("*").eq("id", unit_id)
                       .limit(1).execute().data or [])
        unit = unit_rows[0] if unit_rows else {}
    except Exception:
        unit = {}

    try:
        records = (db.table("attendance")
                     .select("*")
                     .eq("student_id", student["id"])
                     .eq("unit_id", unit_id)
                     .order("year").order("term").order("week").order("lesson")
                     .execute().data or [])
    except Exception:
        records = []

    attended = sum(1 for r in records if r["status"] == "present")
    absent   = sum(1 for r in records if r["status"] == "absent")
    total    = len(records)
    pct      = round((attended / total) * 100, 1) if total else 0

    # Class and dept info from student row
    cls  = student.get("classes") or {}
    dept = cls.get("departments") or {}
    info = {
        "class_name": cls.get("name", "—"),
        "dept_name":  dept.get("name", "—"),
    }

    return render_template("student/unit_detail.html",
                           student=student,
                           unit=unit,
                           records=records,
                           attended=attended,
                           absent=absent,
                           total=total,
                           pct=pct,
                           info=info)


# ── Unit Report PDF ───────────────────────────────────────────────────────────

@student_bp.route("/unit-report-pdf")
@student_required
def unit_report_pdf():
    db      = get_service_client()
    student = _student_row()
    unit_id = request.args.get("unit_id", type=int)

    if not unit_id:
        return redirect(url_for("student.dashboard"))

    try:
        unit_rows = (db.table("units").select("*").eq("id", unit_id)
                       .limit(1).execute().data or [])
        unit = unit_rows[0] if unit_rows else {}
    except Exception:
        unit = {}

    try:
        records = (db.table("attendance")
                     .select("*")
                     .eq("student_id", student["id"])
                     .eq("unit_id", unit_id)
                     .order("year").order("term").order("week").order("lesson")
                     .execute().data or [])
    except Exception:
        records = []

    attended = sum(1 for r in records if r["status"] == "present")
    absent   = sum(1 for r in records if r["status"] == "absent")
    total    = len(records)
    pct      = round((attended / total) * 100, 1) if total else 0

    cls  = student.get("classes") or {}
    dept = cls.get("departments") or {}
    info = {
        "class_name": cls.get("name", "—"),
        "dept_name":  dept.get("name", "—"),
    }

    term_label = {1: "Term 1 (Jan–Apr)", 2: "Term 2 (May–Aug)", 3: "Term 3 (Sep–Dec)"}

    return render_template("student/unit_report_pdf.html",
                           student=student,
                           unit=unit,
                           records=records,
                           attended=attended,
                           absent=absent,
                           total=total,
                           pct=pct,
                           info=info,
                           term_label=term_label,
                           date_gen=now_eat().strftime("%d %b %Y, %H:%M"))
