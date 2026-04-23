"""
routes/super_admin.py — Super Admin blueprint.

Super Admin has full system control:
  • Create / manage Department Admins
  • Assign departments to admins
  • View all system data
  • Monitor system logs
  • Enable / disable user accounts
  • Full CRUD on departments, classes, units, trainers, students
"""

import csv
import io
from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, abort, jsonify)
from auth_utils import (super_admin_required, write_audit_log,
                        current_user, dept_isolation_check)
from db import get_service_client

super_admin_bp = Blueprint("super_admin", __name__)
svc = get_service_client   # callable — always get fresh reference


# ── Dashboard ─────────────────────────────────────────────────────────────────

@super_admin_bp.route("/")
@super_admin_bp.route("/dashboard")
@super_admin_required
def dashboard():
    return redirect(url_for("super_admin.welcome"))


@super_admin_bp.route("/welcome")
@super_admin_required
def welcome():
    db = svc()
    depts_count    = db.table("departments").select("id", count="exact").execute().count or 0
    trainers_count = db.table("trainers").select("id", count="exact").execute().count or 0
    classes_count  = db.table("classes").select("id", count="exact").execute().count or 0
    students_count = db.table("students").select("id", count="exact").execute().count or 0
    units_count    = db.table("units").select("id", count="exact").execute().count or 0

    dept_stats = db.rpc("v_department_stats_fn", {}).execute().data or []
    # Fallback: manual query if view not available as RPC
    if not dept_stats:
        dept_stats = db.table("v_department_stats").select("*").execute().data or []

    return render_template("super_admin/welcome.html",
                           depts_count=depts_count,
                           trainers_count=trainers_count,
                           classes_count=classes_count,
                           students_count=students_count,
                           units_count=units_count,
                           dept_stats=dept_stats)


# ── Departments ───────────────────────────────────────────────────────────────

@super_admin_bp.route("/departments", methods=["GET", "POST"])
@super_admin_required
def departments():
    db = svc()
    error = None
    if request.method == "POST" and request.form.get("add_dept"):
        name = request.form.get("name", "").strip().upper()
        if not name:
            error = "Department name cannot be empty."
        else:
            existing = db.table("departments").select("id").eq("name", name).execute()
            if existing.data:
                error = "Department already exists."
            else:
                db.table("departments").insert({"name": name}).execute()
                write_audit_log("create_department", target=name)
                flash("Department added successfully.", "success")
                return redirect(url_for("super_admin.departments"))

    if request.args.get("delete"):
        dept_id = int(request.args["delete"])
        db.table("departments").delete().eq("id", dept_id).execute()
        write_audit_log("delete_department", target=str(dept_id))
        flash("Department deleted.", "success")
        return redirect(url_for("super_admin.departments"))

    depts = db.table("departments").select("*").order("name").execute().data or []
    return render_template("super_admin/departments.html", depts=depts, error=error)


# ── User Management (Dept Admins) ─────────────────────────────────────────────

@super_admin_bp.route("/dept-admins", methods=["GET", "POST"])
@super_admin_required
def dept_admins():
    """Create and manage Department Admin accounts."""
    db = svc()
    error = None

    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            email     = request.form.get("email", "").strip().lower()
            full_name = request.form.get("full_name", "").strip()
            dept_id   = request.form.get("department_id", type=int)
            password  = request.form.get("password", "")

            if not all([email, full_name, dept_id, password]):
                error = "All fields are required."
            elif len(password) < 8:
                error = "Password must be at least 8 characters."
            else:
                try:
                    # Create Supabase Auth user
                    resp = db.auth.admin.create_user({
                        "email":    email,
                        "password": password,
                        "email_confirm": True,
                        "user_metadata": {
                            "full_name": full_name,
                            "role":      "dept_admin",
                        },
                    })
                    user_id = resp.user.id
                    # Upsert profile with dept_admin role
                    db.table("user_profiles").upsert({
                        "id":            user_id,
                        "full_name":     full_name,
                        "role":          "dept_admin",
                        "department_id": dept_id,
                        "is_active":     True,
                    }).execute()
                    write_audit_log("create_dept_admin", target=email,
                                    detail={"dept_id": dept_id})
                    flash(f"Department Admin '{full_name}' created.", "success")
                    return redirect(url_for("super_admin.dept_admins"))
                except Exception as exc:
                    error = f"Could not create user: {exc}"

        elif action == "toggle_active":
            user_id   = request.form.get("user_id")
            is_active = request.form.get("is_active") == "true"
            db.table("user_profiles").update({"is_active": is_active}).eq("id", user_id).execute()
            write_audit_log("toggle_user_active", target=user_id,
                            detail={"is_active": is_active})
            flash("Account status updated.", "success")
            return redirect(url_for("super_admin.dept_admins"))

        elif action == "assign_dept":
            user_id = request.form.get("user_id")
            dept_id = request.form.get("department_id", type=int)
            db.table("user_profiles").update({"department_id": dept_id}).eq("id", user_id).execute()
            write_audit_log("assign_dept_to_admin", target=user_id,
                            detail={"dept_id": dept_id})
            flash("Department assigned.", "success")
            return redirect(url_for("super_admin.dept_admins"))

    # List all dept_admin profiles
    admins = (db.table("user_profiles")
                .select("*, departments(name)")
                .eq("role", "dept_admin")
                .order("full_name")
                .execute().data or [])
    depts  = db.table("departments").select("*").order("name").execute().data or []
    return render_template("super_admin/dept_admins.html",
                           admins=admins, depts=depts, error=error)


# ── All Users (enable / disable) ─────────────────────────────────────────────

@super_admin_bp.route("/users")
@super_admin_required
def users():
    db = svc()
    role_filter = request.args.get("role", "")
    query = db.table("user_profiles").select("*, departments(name)").order("full_name")
    if role_filter:
        query = query.eq("role", role_filter)
    users_list = query.execute().data or []
    return render_template("super_admin/users.html", users=users_list,
                           role_filter=role_filter)


@super_admin_bp.route("/users/toggle", methods=["POST"])
@super_admin_required
def toggle_user():
    user_id   = request.form.get("user_id")
    is_active = request.form.get("is_active") == "true"
    if not user_id:
        abort(400)
    svc().table("user_profiles").update({"is_active": is_active}).eq("id", user_id).execute()
    write_audit_log("toggle_user_active", target=user_id, detail={"is_active": is_active})
    flash("Account status updated.", "success")
    return redirect(url_for("super_admin.users"))


# ── System Logs ───────────────────────────────────────────────────────────────

@super_admin_bp.route("/logs")
@super_admin_required
def system_logs():
    db     = svc()
    page   = request.args.get("page", 1, type=int)
    limit  = 50
    offset = (page - 1) * limit
    logs   = (db.table("system_logs")
                .select("*, user_profiles(full_name, role)")
                .order("created_at", desc=True)
                .range(offset, offset + limit - 1)
                .execute().data or [])
    return render_template("super_admin/system_logs.html", logs=logs, page=page)


# ── Trainers ──────────────────────────────────────────────────────────────────

@super_admin_bp.route("/trainers", methods=["GET", "POST"])
@super_admin_required
def trainers():
    db = svc()
    error = None

    if request.method == "POST":
        action = request.form.get("action", "create")

        if action == "create":
            name      = request.form.get("name", "").strip()
            username  = request.form.get("username", "").strip()
            email     = request.form.get("email", "").strip().lower()
            dept_id   = request.form.get("department_id", type=int)
            password  = request.form.get("password", "")

            if not all([name, username, email, dept_id, password]):
                error = "All fields are required."
            elif len(password) < 8:
                error = "Password must be at least 8 characters."
            else:
                try:
                    resp = db.auth.admin.create_user({
                        "email":    email,
                        "password": password,
                        "email_confirm": True,
                        "user_metadata": {"full_name": name, "role": "trainer"},
                    })
                    user_id = resp.user.id
                    db.table("user_profiles").upsert({
                        "id":            user_id,
                        "full_name":     name,
                        "role":          "trainer",
                        "department_id": dept_id,
                        "is_active":     True,
                    }).execute()
                    db.table("trainers").insert({
                        "user_id":       user_id,
                        "name":          name,
                        "username":      username,
                        "department_id": dept_id,
                    }).execute()
                    write_audit_log("create_trainer", target=email)
                    flash(f"Trainer '{name}' created.", "success")
                    return redirect(url_for("super_admin.trainers"))
                except Exception as exc:
                    error = f"Could not create trainer: {exc}"

        elif action == "delete":
            trainer_id = request.form.get("trainer_id", type=int)
            db.table("trainers").delete().eq("id", trainer_id).execute()
            write_audit_log("delete_trainer", target=str(trainer_id))
            flash("Trainer deleted.", "success")
            return redirect(url_for("super_admin.trainers"))

    search = request.args.get("q", "").strip()
    query  = db.table("trainers").select("*, departments(name)").order("name")
    if search:
        query = query.ilike("name", f"%{search}%")
    trainers_list = query.execute().data or []
    depts = db.table("departments").select("*").order("name").execute().data or []
    return render_template("super_admin/trainers.html",
                           trainers=trainers_list, depts=depts,
                           error=error, search=search)


# ── Classes ───────────────────────────────────────────────────────────────────

@super_admin_bp.route("/classes", methods=["GET", "POST"])
@super_admin_required
def classes():
    db = svc()
    error = None

    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "create":
            name    = request.form.get("name", "").strip().upper()
            dept_id = request.form.get("department_id", type=int)
            if not name or not dept_id:
                error = "Class name and department are required."
            else:
                db.table("classes").insert({"name": name, "department_id": dept_id}).execute()
                write_audit_log("create_class", target=name)
                flash("Class added.", "success")
                return redirect(url_for("super_admin.classes"))
        elif action == "delete":
            class_id = request.form.get("class_id", type=int)
            db.table("classes").delete().eq("id", class_id).execute()
            write_audit_log("delete_class", target=str(class_id))
            flash("Class deleted.", "success")
            return redirect(url_for("super_admin.classes"))

    dept_filter = request.args.get("dept_id", type=int)
    query = db.table("classes").select("*, departments(name)").order("name")
    if dept_filter:
        query = query.eq("department_id", dept_filter)
    classes_list = query.execute().data or []
    depts = db.table("departments").select("*").order("name").execute().data or []
    return render_template("super_admin/classes.html",
                           classes=classes_list, depts=depts,
                           error=error, dept_filter=dept_filter)


# ── Units ─────────────────────────────────────────────────────────────────────

@super_admin_bp.route("/units", methods=["GET", "POST"])
@super_admin_required
def units():
    db = svc()
    error = None

    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "create":
            code    = request.form.get("code", "").strip().upper()
            name    = request.form.get("name", "").strip()
            dept_id = request.form.get("department_id", type=int)
            if not code or not name:
                error = "Unit code and name are required."
            else:
                db.table("units").insert({
                    "code": code, "name": name, "department_id": dept_id or None
                }).execute()
                write_audit_log("create_unit", target=code)
                flash("Unit added.", "success")
                return redirect(url_for("super_admin.units"))
        elif action == "delete":
            unit_id = request.form.get("unit_id", type=int)
            db.table("units").delete().eq("id", unit_id).execute()
            write_audit_log("delete_unit", target=str(unit_id))
            flash("Unit deleted.", "success")
            return redirect(url_for("super_admin.units"))

    units_list = (db.table("units")
                    .select("*, departments(name)")
                    .order("code")
                    .execute().data or [])
    depts = db.table("departments").select("*").order("name").execute().data or []
    return render_template("super_admin/units.html",
                           units=units_list, depts=depts, error=error)


# ── Students ──────────────────────────────────────────────────────────────────

@super_admin_bp.route("/students", methods=["GET", "POST"])
@super_admin_required
def students():
    db = svc()
    error = None

    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "create":
            adm      = request.form.get("admission_number", "").strip()
            name     = request.form.get("full_name", "").strip().upper()
            class_id = request.form.get("class_id", type=int)
            if not adm or not name or not class_id:
                error = "Admission number, name and class are required."
            else:
                db.table("students").insert({
                    "admission_number": adm,
                    "full_name":        name,
                    "class_id":         class_id,
                }).execute()
                write_audit_log("create_student", target=adm)
                flash("Student added.", "success")
                return redirect(url_for("super_admin.students"))
        elif action == "delete":
            student_id = request.form.get("student_id", type=int)
            db.table("students").delete().eq("id", student_id).execute()
            write_audit_log("delete_student", target=str(student_id))
            flash("Student deleted.", "success")
            return redirect(url_for("super_admin.students"))

    search      = request.args.get("q", "").strip()
    dept_filter = request.args.get("dept_id", type=int)
    query = (db.table("students")
               .select("*, classes(name, department_id, departments(name))")
               .order("full_name"))
    if search:
        query = query.ilike("full_name", f"%{search}%")
    students_list = query.execute().data or []
    if dept_filter:
        students_list = [
            s for s in students_list
            if s.get("classes", {}).get("department_id") == dept_filter
        ]
    depts   = db.table("departments").select("*").order("name").execute().data or []
    classes = db.table("classes").select("*").order("name").execute().data or []
    return render_template("super_admin/students.html",
                           students=students_list, depts=depts,
                           classes=classes, error=error,
                           search=search, dept_filter=dept_filter)


# ── Assign Units ──────────────────────────────────────────────────────────────

@super_admin_bp.route("/assign-units", methods=["GET", "POST"])
@super_admin_required
def assign_units():
    db = svc()
    error = None

    if request.method == "POST":
        class_id   = request.form.get("class_id", type=int)
        unit_id    = request.form.get("unit_id", type=int)
        trainer_id = request.form.get("trainer_id", type=int)
        year       = request.form.get("year", type=int)
        term       = request.form.get("term", type=int)
        if not all([class_id, unit_id, trainer_id, year, term]):
            error = "All fields are required."
        else:
            try:
                db.table("class_units").insert({
                    "class_id":   class_id,
                    "unit_id":    unit_id,
                    "trainer_id": trainer_id,
                    "year":       year,
                    "term":       term,
                }).execute()
                write_audit_log("assign_unit", detail={
                    "class_id": class_id, "unit_id": unit_id,
                    "trainer_id": trainer_id, "year": year, "term": term,
                })
                flash("Unit assigned.", "success")
                return redirect(url_for("super_admin.assign_units"))
            except Exception as exc:
                error = f"Assignment failed (may already exist): {exc}"

    classes  = db.table("classes").select("*, departments(name)").order("name").execute().data or []
    units    = db.table("units").select("*").order("code").execute().data or []
    trainers = db.table("trainers").select("*, departments(name)").order("name").execute().data or []
    assigned = (db.table("class_units")
                  .select("*, classes(name), units(code,name), trainers(name)")
                  .order("id", desc=True)
                  .limit(100)
                  .execute().data or [])
    return render_template("super_admin/assign_units.html",
                           classes=classes, units=units,
                           trainers=trainers, assigned=assigned, error=error)


# ── Attendance (read-only view across all departments) ────────────────────────

@super_admin_bp.route("/attendance")
@super_admin_required
def view_attendance():
    db = svc()
    dept_id  = request.args.get("dept_id", type=int)
    class_id = request.args.get("class_id", type=int)
    unit_id  = request.args.get("unit_id", type=int)
    week     = request.args.get("week", type=int)
    year     = request.args.get("year", 2026, type=int)
    term     = request.args.get("term", 1, type=int)

    query = (db.table("attendance")
               .select("*, students(full_name, admission_number), units(code,name), trainers(name)")
               .eq("year", year).eq("term", term)
               .order("attendance_date", desc=True)
               .limit(500))
    if unit_id:  query = query.eq("unit_id", unit_id)
    if week:     query = query.eq("week", week)
    records = query.execute().data or []

    depts   = db.table("departments").select("*").order("name").execute().data or []
    classes = db.table("classes").select("*").order("name").execute().data or []
    units   = db.table("units").select("*").order("code").execute().data or []
    return render_template("super_admin/view_attendance.html",
                           records=records, depts=depts,
                           classes=classes, units=units,
                           dept_id=dept_id, class_id=class_id,
                           unit_id=unit_id, week=week,
                           year=year, term=term)
