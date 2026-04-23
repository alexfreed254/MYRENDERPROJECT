"""
routes/super_admin.py — Super Admin blueprint.
Fixes applied:
  - Removed broken db.rpc("v_department_stats_fn") call
  - Replaced db.auth.admin.create_user with supabase-py v2 correct API
  - All DB calls wrapped in try/except to prevent 500s
"""

from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, abort, jsonify)
from auth_utils import super_admin_required, write_audit_log
from db import get_service_client

super_admin_bp = Blueprint("super_admin", __name__)


def _svc():
    return get_service_client()


# ── Dashboard ─────────────────────────────────────────────────────────────────

@super_admin_bp.route("/")
@super_admin_bp.route("/dashboard")
@super_admin_required
def dashboard():
    return redirect(url_for("super_admin.welcome"))


@super_admin_bp.route("/welcome")
@super_admin_required
def welcome():
    db = _svc()
    try:
        depts_count    = db.table("departments").select("id", count="exact").execute().count or 0
        trainers_count = db.table("trainers").select("id", count="exact").execute().count or 0
        classes_count  = db.table("classes").select("id", count="exact").execute().count or 0
        students_count = db.table("students").select("id", count="exact").execute().count or 0
        units_count    = db.table("units").select("id", count="exact").execute().count or 0
    except Exception:
        depts_count = trainers_count = classes_count = students_count = units_count = 0

    # Build dept stats manually — no RPC, no view dependency
    dept_stats = []
    try:
        depts = db.table("departments").select("id, name").order("name").execute().data or []
        for d in depts:
            did = d["id"]
            cc = db.table("classes").select("id", count="exact").eq("department_id", did).execute().count or 0
            tc = db.table("trainers").select("id", count="exact").eq("department_id", did).execute().count or 0
            class_ids = [c["id"] for c in (db.table("classes").select("id").eq("department_id", did).execute().data or [])]
            sc = 0
            if class_ids:
                sc = db.table("students").select("id", count="exact").in_("class_id", class_ids).execute().count or 0
            dept_stats.append({
                "id": did, "name": d["name"],
                "class_count": cc, "trainer_count": tc, "student_count": sc
            })
    except Exception:
        dept_stats = []

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
    db = _svc()
    error = None

    if request.method == "POST" and request.form.get("add_dept"):
        name = request.form.get("name", "").strip().upper()
        if not name:
            error = "Department name cannot be empty."
        else:
            try:
                existing = db.table("departments").select("id").eq("name", name).execute()
                if existing.data:
                    error = "Department already exists."
                else:
                    db.table("departments").insert({"name": name}).execute()
                    write_audit_log("create_department", target=name)
                    flash("Department added successfully.", "success")
                    return redirect(url_for("super_admin.departments"))
            except Exception as exc:
                error = f"Error: {exc}"

    if request.args.get("delete"):
        try:
            dept_id = int(request.args["delete"])
            db.table("departments").delete().eq("id", dept_id).execute()
            write_audit_log("delete_department", target=str(dept_id))
            flash("Department deleted.", "success")
        except Exception as exc:
            flash(f"Delete failed: {exc}", "error")
        return redirect(url_for("super_admin.departments"))

    try:
        depts = db.table("departments").select("*").order("name").execute().data or []
    except Exception:
        depts = []
    return render_template("super_admin/departments.html", depts=depts, error=error)


# ── Create Auth User helper ───────────────────────────────────────────────────

def _create_auth_user(email: str, password: str, full_name: str, role: str) -> tuple:
    """
    Creates a Supabase Auth user using the Admin API via the service client.
    Returns (user_id, error_string).  error_string is None on success.

    supabase-py v2: client.auth.admin.create_user(...)
    """
    try:
        db = _svc()
        resp = db.auth.admin.create_user({
            "email": email,
            "password": password,
            "email_confirm": True,
            "user_metadata": {
                "full_name": full_name,
                "role": role,
            },
        })
        return resp.user.id, None
    except Exception as exc:
        return None, str(exc)


# ── Dept Admins ───────────────────────────────────────────────────────────────

@super_admin_bp.route("/dept-admins", methods=["GET", "POST"])
@super_admin_required
def dept_admins():
    db = _svc()
    error = None

    if request.method == "POST":
        action = request.form.get("action", "")

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
                user_id, err = _create_auth_user(email, password, full_name, "dept_admin")
                if err:
                    error = f"Could not create user: {err}"
                else:
                    try:
                        db.table("user_profiles").upsert({
                            "id": user_id, "full_name": full_name,
                            "role": "dept_admin", "department_id": dept_id,
                            "is_active": True,
                        }).execute()
                        write_audit_log("create_dept_admin", target=email,
                                        detail={"dept_id": dept_id})
                        flash(f"Department Admin '{full_name}' created.", "success")
                        return redirect(url_for("super_admin.dept_admins"))
                    except Exception as exc:
                        error = f"Profile save failed: {exc}"

        elif action == "toggle_active":
            user_id   = request.form.get("user_id")
            is_active = request.form.get("is_active") == "true"
            try:
                db.table("user_profiles").update({"is_active": is_active}).eq("id", user_id).execute()
                write_audit_log("toggle_user_active", target=user_id, detail={"is_active": is_active})
                flash("Account status updated.", "success")
            except Exception as exc:
                flash(f"Update failed: {exc}", "error")
            return redirect(url_for("super_admin.dept_admins"))

        elif action == "assign_dept":
            user_id = request.form.get("user_id")
            dept_id = request.form.get("department_id", type=int)
            try:
                db.table("user_profiles").update({"department_id": dept_id}).eq("id", user_id).execute()
                write_audit_log("assign_dept_to_admin", target=user_id, detail={"dept_id": dept_id})
                flash("Department assigned.", "success")
            except Exception as exc:
                flash(f"Assign failed: {exc}", "error")
            return redirect(url_for("super_admin.dept_admins"))

    try:
        admins = (db.table("user_profiles")
                    .select("*, departments(name)")
                    .eq("role", "dept_admin")
                    .order("full_name")
                    .execute().data or [])
    except Exception:
        admins = []
    try:
        depts = db.table("departments").select("*").order("name").execute().data or []
    except Exception:
        depts = []
    return render_template("super_admin/dept_admins.html",
                           admins=admins, depts=depts, error=error)


# ── All Users ─────────────────────────────────────────────────────────────────

@super_admin_bp.route("/users")
@super_admin_required
def users():
    db = _svc()
    role_filter = request.args.get("role", "")
    try:
        query = db.table("user_profiles").select("*, departments(name)").order("full_name")
        if role_filter:
            query = query.eq("role", role_filter)
        users_list = query.execute().data or []
    except Exception:
        users_list = []
    return render_template("super_admin/users.html",
                           users=users_list, role_filter=role_filter)


@super_admin_bp.route("/users/toggle", methods=["POST"])
@super_admin_required
def toggle_user():
    user_id   = request.form.get("user_id")
    is_active = request.form.get("is_active") == "true"
    if not user_id:
        abort(400)
    try:
        _svc().table("user_profiles").update({"is_active": is_active}).eq("id", user_id).execute()
        write_audit_log("toggle_user_active", target=user_id, detail={"is_active": is_active})
        flash("Account status updated.", "success")
    except Exception as exc:
        flash(f"Update failed: {exc}", "error")
    return redirect(url_for("super_admin.users"))


# ── System Logs ───────────────────────────────────────────────────────────────

@super_admin_bp.route("/logs")
@super_admin_required
def system_logs():
    db     = _svc()
    page   = request.args.get("page", 1, type=int)
    limit  = 50
    offset = (page - 1) * limit
    try:
        logs = (db.table("system_logs")
                  .select("id, actor_id, actor_role, action, target, detail, ip_address, created_at")
                  .order("created_at", desc=True)
                  .range(offset, offset + limit - 1)
                  .execute().data or [])
    except Exception:
        logs = []
    return render_template("super_admin/system_logs.html", logs=logs, page=page)


# ── Trainers ──────────────────────────────────────────────────────────────────

@super_admin_bp.route("/trainers", methods=["GET", "POST"])
@super_admin_required
def trainers():
    db = _svc()
    error = None

    if request.method == "POST":
        action = request.form.get("action", "create")

        if action == "create":
            name     = request.form.get("name", "").strip()
            username = request.form.get("username", "").strip()
            email    = request.form.get("email", "").strip().lower()
            dept_id  = request.form.get("department_id", type=int)
            password = request.form.get("password", "")

            if not all([name, username, email, dept_id, password]):
                error = "All fields are required."
            elif len(password) < 8:
                error = "Password must be at least 8 characters."
            else:
                user_id, err = _create_auth_user(email, password, name, "trainer")
                if err:
                    error = f"Could not create user: {err}"
                else:
                    try:
                        db.table("user_profiles").upsert({
                            "id": user_id, "full_name": name,
                            "role": "trainer", "department_id": dept_id,
                            "is_active": True,
                        }).execute()
                        db.table("trainers").insert({
                            "user_id": user_id, "name": name,
                            "username": username, "department_id": dept_id,
                        }).execute()
                        write_audit_log("create_trainer", target=email)
                        flash(f"Trainer '{name}' created.", "success")
                        return redirect(url_for("super_admin.trainers"))
                    except Exception as exc:
                        error = f"Profile save failed: {exc}"

        elif action == "delete":
            try:
                trainer_id = request.form.get("trainer_id", type=int)
                db.table("trainers").delete().eq("id", trainer_id).execute()
                write_audit_log("delete_trainer", target=str(trainer_id))
                flash("Trainer deleted.", "success")
            except Exception as exc:
                flash(f"Delete failed: {exc}", "error")
            return redirect(url_for("super_admin.trainers"))

    search = request.args.get("q", "").strip()
    try:
        query = db.table("trainers").select("*, departments(name)").order("name")
        if search:
            query = query.ilike("name", f"%{search}%")
        trainers_list = query.execute().data or []
    except Exception:
        trainers_list = []
    try:
        depts = db.table("departments").select("*").order("name").execute().data or []
    except Exception:
        depts = []
    return render_template("super_admin/trainers.html",
                           trainers=trainers_list, depts=depts,
                           error=error, search=search)


# ── Classes ───────────────────────────────────────────────────────────────────

@super_admin_bp.route("/classes", methods=["GET", "POST"])
@super_admin_required
def classes():
    db = _svc()
    error = None

    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "create":
            name    = request.form.get("name", "").strip().upper()
            dept_id = request.form.get("department_id", type=int)
            if not name or not dept_id:
                error = "Class name and department are required."
            else:
                try:
                    db.table("classes").insert({"name": name, "department_id": dept_id}).execute()
                    write_audit_log("create_class", target=name)
                    flash("Class added.", "success")
                    return redirect(url_for("super_admin.classes"))
                except Exception as exc:
                    error = f"Error: {exc}"
        elif action == "delete":
            try:
                class_id = request.form.get("class_id", type=int)
                db.table("classes").delete().eq("id", class_id).execute()
                write_audit_log("delete_class", target=str(class_id))
                flash("Class deleted.", "success")
            except Exception as exc:
                flash(f"Delete failed: {exc}", "error")
            return redirect(url_for("super_admin.classes"))

    dept_filter = request.args.get("dept_id", type=int)
    try:
        query = db.table("classes").select("*, departments(name)").order("name")
        if dept_filter:
            query = query.eq("department_id", dept_filter)
        classes_list = query.execute().data or []
    except Exception:
        classes_list = []
    try:
        depts = db.table("departments").select("*").order("name").execute().data or []
    except Exception:
        depts = []
    return render_template("super_admin/classes.html",
                           classes=classes_list, depts=depts,
                           error=error, dept_filter=dept_filter)


# ── Units ─────────────────────────────────────────────────────────────────────

@super_admin_bp.route("/units", methods=["GET", "POST"])
@super_admin_required
def units():
    db = _svc()
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
                try:
                    db.table("units").insert({
                        "code": code, "name": name,
                        "department_id": dept_id or None
                    }).execute()
                    write_audit_log("create_unit", target=code)
                    flash("Unit added.", "success")
                    return redirect(url_for("super_admin.units"))
                except Exception as exc:
                    error = f"Error: {exc}"
        elif action == "delete":
            try:
                unit_id = request.form.get("unit_id", type=int)
                db.table("units").delete().eq("id", unit_id).execute()
                write_audit_log("delete_unit", target=str(unit_id))
                flash("Unit deleted.", "success")
            except Exception as exc:
                flash(f"Delete failed: {exc}", "error")
            return redirect(url_for("super_admin.units"))

    try:
        units_list = db.table("units").select("*, departments(name)").order("code").execute().data or []
    except Exception:
        units_list = []
    try:
        depts = db.table("departments").select("*").order("name").execute().data or []
    except Exception:
        depts = []
    return render_template("super_admin/units.html",
                           units=units_list, depts=depts, error=error)


# ── Students ──────────────────────────────────────────────────────────────────

@super_admin_bp.route("/students", methods=["GET", "POST"])
@super_admin_required
def students():
    db = _svc()
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
                try:
                    db.table("students").insert({
                        "admission_number": adm,
                        "full_name": name,
                        "class_id": class_id,
                    }).execute()
                    write_audit_log("create_student", target=adm)
                    flash("Student added.", "success")
                    return redirect(url_for("super_admin.students"))
                except Exception as exc:
                    error = f"Error: {exc}"
        elif action == "delete":
            try:
                student_id = request.form.get("student_id", type=int)
                db.table("students").delete().eq("id", student_id).execute()
                write_audit_log("delete_student", target=str(student_id))
                flash("Student deleted.", "success")
            except Exception as exc:
                flash(f"Delete failed: {exc}", "error")
            return redirect(url_for("super_admin.students"))

    search      = request.args.get("q", "").strip()
    dept_filter = request.args.get("dept_id", type=int)
    try:
        query = (db.table("students")
                   .select("*, classes(name, department_id, departments(name))")
                   .order("full_name"))
        if search:
            query = query.ilike("full_name", f"%{search}%")
        students_list = query.execute().data or []
        if dept_filter:
            students_list = [
                s for s in students_list
                if (s.get("classes") or {}).get("department_id") == dept_filter
            ]
    except Exception:
        students_list = []
    try:
        depts   = db.table("departments").select("*").order("name").execute().data or []
        classes = db.table("classes").select("*").order("name").execute().data or []
    except Exception:
        depts = []; classes = []
    return render_template("super_admin/students.html",
                           students=students_list, depts=depts,
                           classes=classes, error=error,
                           search=search, dept_filter=dept_filter)


# ── Assign Units ──────────────────────────────────────────────────────────────

@super_admin_bp.route("/assign-units", methods=["GET", "POST"])
@super_admin_required
def assign_units():
    db = _svc()
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
                    "class_id": class_id, "unit_id": unit_id,
                    "trainer_id": trainer_id, "year": year, "term": term,
                }).execute()
                write_audit_log("assign_unit", detail={
                    "class_id": class_id, "unit_id": unit_id,
                    "trainer_id": trainer_id, "year": year, "term": term,
                })
                flash("Unit assigned.", "success")
                return redirect(url_for("super_admin.assign_units"))
            except Exception as exc:
                error = f"Assignment failed (may already exist): {exc}"

    try:
        classes  = db.table("classes").select("*, departments(name)").order("name").execute().data or []
        units    = db.table("units").select("*").order("code").execute().data or []
        trainers = db.table("trainers").select("*, departments(name)").order("name").execute().data or []
        assigned = (db.table("class_units")
                      .select("*, classes(name), units(code,name), trainers(name)")
                      .order("id", desc=True).limit(100)
                      .execute().data or [])
    except Exception:
        classes = units = trainers = assigned = []
    return render_template("super_admin/assign_units.html",
                           classes=classes, units=units,
                           trainers=trainers, assigned=assigned, error=error)


# ── Attendance ────────────────────────────────────────────────────────────────

@super_admin_bp.route("/attendance")
@super_admin_required
def view_attendance():
    db = _svc()
    dept_id  = request.args.get("dept_id", type=int)
    unit_id  = request.args.get("unit_id", type=int)
    week     = request.args.get("week", type=int)
    year     = request.args.get("year", 2026, type=int)
    term     = request.args.get("term", 1, type=int)

    try:
        query = (db.table("attendance")
                   .select("*, students(full_name, admission_number), units(code,name), trainers(name)")
                   .eq("year", year).eq("term", term)
                   .order("attendance_date", desc=True).limit(500))
        if unit_id: query = query.eq("unit_id", unit_id)
        if week:    query = query.eq("week", week)
        records = query.execute().data or []
    except Exception:
        records = []
    try:
        depts   = db.table("departments").select("*").order("name").execute().data or []
        classes = db.table("classes").select("*").order("name").execute().data or []
        units   = db.table("units").select("*").order("code").execute().data or []
    except Exception:
        depts = classes = units = []
    return render_template("super_admin/view_attendance.html",
                           records=records, depts=depts,
                           classes=classes, units=units,
                           dept_id=dept_id, unit_id=unit_id,
                           week=week, year=year, term=term)


# ── Bulk Import (Excel) ───────────────────────────────────────────────────────

@super_admin_bp.route("/import", methods=["GET", "POST"])
@super_admin_required
def bulk_import():
    db = _svc()
    result = None
    error  = None

    if request.method == "POST":
        import_type = request.form.get("import_type", "")
        file = request.files.get("file")

        if not file or not file.filename.endswith(('.xlsx', '.xls')):
            error = "Please upload a valid Excel file (.xlsx or .xls)"
        elif import_type not in ("students", "trainers", "classes", "units"):
            error = "Invalid import type."
        else:
            try:
                import openpyxl
                wb = openpyxl.load_workbook(file, data_only=True)
                ws = wb.active
                rows = list(ws.iter_rows(min_row=2, values_only=True))  # skip header

                if import_type == "students":
                    result = _import_students(db, rows)
                elif import_type == "trainers":
                    result = _import_trainers(db, rows)
                elif import_type == "classes":
                    result = _import_classes(db, rows)
                elif import_type == "units":
                    result = _import_units(db, rows)

                write_audit_log("bulk_import", target=import_type,
                                detail={"count": result.get("success", 0)})
            except Exception as exc:
                error = f"Import failed: {exc}"

    try:
        depts = db.table("departments").select("*").order("name").execute().data or []
    except Exception:
        depts = []

    return render_template("super_admin/import.html",
                           result=result, error=error, depts=depts)


def _import_students(db, rows):
    success = 0
    errors = []
    for r in rows:
        if not r or not r[0]:  # skip empty rows
            continue
        try:
            adm, name, class_id = r[0], r[1], int(r[2])
            db.table("students").insert({
                "admission_number": str(adm).strip(),
                "full_name": str(name).strip().upper(),
                "class_id": class_id,
            }).execute()
            success += 1
        except Exception as exc:
            errors.append(f"Row {r}: {exc}")
    return {"success": success, "errors": errors[:10]}


def _import_trainers(db, rows):
    success = 0
    errors = []
    for r in rows:
        if not r or not r[0]:
            continue
        try:
            name, username, email, dept_id, password = (
                str(r[0]).strip(), str(r[1]).strip(), str(r[2]).strip().lower(),
                int(r[3]), str(r[4]).strip()
            )
            user_id, err = _create_auth_user(email, password, name, "trainer")
            if err:
                errors.append(f"{email}: {err}")
                continue
            db.table("user_profiles").upsert({
                "id": user_id, "full_name": name, "role": "trainer",
                "department_id": dept_id, "is_active": True,
            }).execute()
            db.table("trainers").insert({
                "user_id": user_id, "name": name,
                "username": username, "department_id": dept_id,
            }).execute()
            success += 1
        except Exception as exc:
            errors.append(f"Row {r}: {exc}")
    return {"success": success, "errors": errors[:10]}


def _import_classes(db, rows):
    success = 0
    errors = []
    for r in rows:
        if not r or not r[0]:
            continue
        try:
            name, dept_id = str(r[0]).strip().upper(), int(r[1])
            db.table("classes").insert({
                "name": name, "department_id": dept_id
            }).execute()
            success += 1
        except Exception as exc:
            errors.append(f"Row {r}: {exc}")
    return {"success": success, "errors": errors[:10]}


def _import_units(db, rows):
    success = 0
    errors = []
    for r in rows:
        if not r or not r[0]:
            continue
        try:
            code, name = str(r[0]).strip().upper(), str(r[1]).strip()
            dept_id = int(r[2]) if len(r) > 2 and r[2] else None
            db.table("units").insert({
                "code": code, "name": name, "department_id": dept_id
            }).execute()
            success += 1
        except Exception as exc:
            errors.append(f"Row {r}: {exc}")
    return {"success": success, "errors": errors[:10]}
