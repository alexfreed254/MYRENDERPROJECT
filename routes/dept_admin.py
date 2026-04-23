"""
routes/dept_admin.py — Department Admin blueprint.

Dept Admin manages ONLY their assigned department.
All queries are filtered by current_user()['dept_id'].
Backend isolation is enforced here; RLS enforces it at the DB layer.
"""

from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, abort)
from auth_utils import (dept_admin_required, write_audit_log,
                        current_user, dept_isolation_check)
from db import get_service_client

dept_admin_bp = Blueprint("dept_admin", __name__)


def _dept_id() -> int:
    """Return the current dept admin's department id, or abort 403."""
    user = current_user()
    dept = user.get("dept_id")
    if not dept:
        abort(403)
    return dept


# ── Dashboard ─────────────────────────────────────────────────────────────────

@dept_admin_bp.route("/")
@dept_admin_bp.route("/dashboard")
@dept_admin_required
def dashboard():
    return redirect(url_for("dept_admin.welcome"))


@dept_admin_bp.route("/welcome")
@dept_admin_required
def welcome():
    db      = get_service_client()
    dept_id = _dept_id()

    dept = db.table("departments").select("*").eq("id", dept_id).single().execute().data or {}

    classes_count  = (db.table("classes")
                        .select("id", count="exact")
                        .eq("department_id", dept_id)
                        .execute().count or 0)
    trainers_count = (db.table("trainers")
                        .select("id", count="exact")
                        .eq("department_id", dept_id)
                        .execute().count or 0)
    # Students in this dept
    class_ids = [
        c["id"] for c in
        db.table("classes").select("id").eq("department_id", dept_id).execute().data or []
    ]
    students_count = 0
    if class_ids:
        students_count = (db.table("students")
                            .select("id", count="exact")
                            .in_("class_id", class_ids)
                            .execute().count or 0)

    return render_template("dept_admin/welcome.html",
                           dept=dept,
                           classes_count=classes_count,
                           trainers_count=trainers_count,
                           students_count=students_count)


# ── Classes ───────────────────────────────────────────────────────────────────

@dept_admin_bp.route("/classes", methods=["GET", "POST"])
@dept_admin_required
def classes():
    db      = get_service_client()
    dept_id = _dept_id()
    error   = None

    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "create":
            name = request.form.get("name", "").strip().upper()
            if not name:
                error = "Class name is required."
            else:
                db.table("classes").insert({"name": name, "department_id": dept_id}).execute()
                write_audit_log("create_class", target=name)
                flash("Class added.", "success")
                return redirect(url_for("dept_admin.classes"))
        elif action == "delete":
            class_id = request.form.get("class_id", type=int)
            # Verify class belongs to this dept before deleting
            row = db.table("classes").select("department_id").eq("id", class_id).single().execute().data
            if not row or row["department_id"] != dept_id:
                abort(403)
            db.table("classes").delete().eq("id", class_id).execute()
            write_audit_log("delete_class", target=str(class_id))
            flash("Class deleted.", "success")
            return redirect(url_for("dept_admin.classes"))

    classes_list = (db.table("classes")
                      .select("*")
                      .eq("department_id", dept_id)
                      .order("name")
                      .execute().data or [])
    return render_template("dept_admin/classes.html",
                           classes=classes_list, error=error)


# ── Trainers ──────────────────────────────────────────────────────────────────

@dept_admin_bp.route("/trainers", methods=["GET", "POST"])
@dept_admin_required
def trainers():
    db      = get_service_client()
    dept_id = _dept_id()
    error   = None

    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "create":
            name     = request.form.get("name", "").strip()
            username = request.form.get("username", "").strip()
            email    = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")

            if not all([name, username, email, password]):
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
                    return redirect(url_for("dept_admin.trainers"))
                except Exception as exc:
                    error = f"Could not create trainer: {exc}"

        elif action == "delete":
            trainer_id = request.form.get("trainer_id", type=int)
            row = db.table("trainers").select("department_id").eq("id", trainer_id).single().execute().data
            if not row or row["department_id"] != dept_id:
                abort(403)
            db.table("trainers").delete().eq("id", trainer_id).execute()
            write_audit_log("delete_trainer", target=str(trainer_id))
            flash("Trainer deleted.", "success")
            return redirect(url_for("dept_admin.trainers"))

    trainers_list = (db.table("trainers")
                       .select("*")
                       .eq("department_id", dept_id)
                       .order("name")
                       .execute().data or [])
    return render_template("dept_admin/trainers.html",
                           trainers=trainers_list, error=error)


# ── Students ──────────────────────────────────────────────────────────────────

@dept_admin_bp.route("/students", methods=["GET", "POST"])
@dept_admin_required
def students():
    db      = get_service_client()
    dept_id = _dept_id()
    error   = None

    # Get class ids for this dept
    dept_class_ids = [
        c["id"] for c in
        db.table("classes").select("id").eq("department_id", dept_id).execute().data or []
    ]

    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "create":
            adm      = request.form.get("admission_number", "").strip()
            name     = request.form.get("full_name", "").strip().upper()
            class_id = request.form.get("class_id", type=int)
            if not adm or not name or not class_id:
                error = "All fields are required."
            elif class_id not in dept_class_ids:
                abort(403)
            else:
                db.table("students").insert({
                    "admission_number": adm,
                    "full_name":        name,
                    "class_id":         class_id,
                }).execute()
                write_audit_log("create_student", target=adm)
                flash("Student added.", "success")
                return redirect(url_for("dept_admin.students"))

        elif action == "delete":
            student_id = request.form.get("student_id", type=int)
            row = db.table("students").select("class_id").eq("id", student_id).single().execute().data
            if not row or row["class_id"] not in dept_class_ids:
                abort(403)
            db.table("students").delete().eq("id", student_id).execute()
            write_audit_log("delete_student", target=str(student_id))
            flash("Student deleted.", "success")
            return redirect(url_for("dept_admin.students"))

    search = request.args.get("q", "").strip()
    query  = (db.table("students")
                .select("*, classes(name)")
                .in_("class_id", dept_class_ids or [-1])
                .order("full_name"))
    if search:
        query = query.ilike("full_name", f"%{search}%")
    students_list = query.execute().data or []
    classes_list  = (db.table("classes")
                       .select("*")
                       .eq("department_id", dept_id)
                       .order("name")
                       .execute().data or [])
    return render_template("dept_admin/students.html",
                           students=students_list,
                           classes=classes_list,
                           error=error, search=search)


# ── Units ─────────────────────────────────────────────────────────────────────

@dept_admin_bp.route("/units", methods=["GET", "POST"])
@dept_admin_required
def units():
    db      = get_service_client()
    dept_id = _dept_id()
    error   = None

    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "create":
            code = request.form.get("code", "").strip().upper()
            name = request.form.get("name", "").strip()
            if not code or not name:
                error = "Unit code and name are required."
            else:
                db.table("units").insert({
                    "code": code, "name": name, "department_id": dept_id
                }).execute()
                write_audit_log("create_unit", target=code)
                flash("Unit added.", "success")
                return redirect(url_for("dept_admin.units"))
        elif action == "delete":
            unit_id = request.form.get("unit_id", type=int)
            row = db.table("units").select("department_id").eq("id", unit_id).single().execute().data
            if not row or row["department_id"] != dept_id:
                abort(403)
            db.table("units").delete().eq("id", unit_id).execute()
            write_audit_log("delete_unit", target=str(unit_id))
            flash("Unit deleted.", "success")
            return redirect(url_for("dept_admin.units"))

    units_list = (db.table("units")
                    .select("*")
                    .eq("department_id", dept_id)
                    .order("code")
                    .execute().data or [])
    return render_template("dept_admin/units.html",
                           units=units_list, error=error)


# ── Assign Units ──────────────────────────────────────────────────────────────

@dept_admin_bp.route("/assign-units", methods=["GET", "POST"])
@dept_admin_required
def assign_units():
    db      = get_service_client()
    dept_id = _dept_id()
    error   = None

    dept_class_ids = [
        c["id"] for c in
        db.table("classes").select("id").eq("department_id", dept_id).execute().data or []
    ]

    if request.method == "POST":
        class_id   = request.form.get("class_id", type=int)
        unit_id    = request.form.get("unit_id", type=int)
        trainer_id = request.form.get("trainer_id", type=int)
        year       = request.form.get("year", type=int)
        term       = request.form.get("term", type=int)

        # Backend isolation: class must belong to this dept
        if class_id not in dept_class_ids:
            abort(403)

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
                return redirect(url_for("dept_admin.assign_units"))
            except Exception as exc:
                error = f"Assignment failed (may already exist): {exc}"

    classes  = (db.table("classes")
                  .select("*")
                  .eq("department_id", dept_id)
                  .order("name")
                  .execute().data or [])
    units    = (db.table("units")
                  .select("*")
                  .eq("department_id", dept_id)
                  .order("code")
                  .execute().data or [])
    trainers = (db.table("trainers")
                  .select("*")
                  .eq("department_id", dept_id)
                  .order("name")
                  .execute().data or [])
    assigned = (db.table("class_units")
                  .select("*, classes(name), units(code,name), trainers(name)")
                  .in_("class_id", dept_class_ids or [-1])
                  .order("id", desc=True)
                  .limit(100)
                  .execute().data or [])
    return render_template("dept_admin/assign_units.html",
                           classes=classes, units=units,
                           trainers=trainers, assigned=assigned, error=error)


# ── Attendance (dept-scoped) ──────────────────────────────────────────────────

@dept_admin_bp.route("/attendance")
@dept_admin_required
def view_attendance():
    db      = get_service_client()
    dept_id = _dept_id()

    dept_class_ids = [
        c["id"] for c in
        db.table("classes").select("id").eq("department_id", dept_id).execute().data or []
    ]
    dept_student_ids = [
        s["id"] for s in
        db.table("students")
          .select("id")
          .in_("class_id", dept_class_ids or [-1])
          .execute().data or []
    ]

    class_id = request.args.get("class_id", type=int)
    unit_id  = request.args.get("unit_id", type=int)
    week     = request.args.get("week", type=int)
    year     = request.args.get("year", 2026, type=int)
    term     = request.args.get("term", 1, type=int)

    query = (db.table("attendance")
               .select("*, students(full_name, admission_number), units(code,name), trainers(name)")
               .in_("student_id", dept_student_ids or [-1])
               .eq("year", year).eq("term", term)
               .order("attendance_date", desc=True)
               .limit(500))
    if unit_id: query = query.eq("unit_id", unit_id)
    if week:    query = query.eq("week", week)
    records = query.execute().data or []

    classes = (db.table("classes")
                 .select("*")
                 .eq("department_id", dept_id)
                 .order("name")
                 .execute().data or [])
    units   = (db.table("units")
                 .select("*")
                 .eq("department_id", dept_id)
                 .order("code")
                 .execute().data or [])
    return render_template("dept_admin/view_attendance.html",
                           records=records, classes=classes, units=units,
                           class_id=class_id, unit_id=unit_id,
                           week=week, year=year, term=term)


# ── Bulk Import (Excel) ───────────────────────────────────────────────────────

@dept_admin_bp.route("/import", methods=["GET", "POST"])
@dept_admin_required
def bulk_import():
    db      = get_service_client()
    dept_id = _dept_id()
    result  = None
    error   = None

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
                rows = list(ws.iter_rows(min_row=2, values_only=True))

                if import_type == "students":
                    result = _dept_import_students(db, rows, dept_id)
                elif import_type == "trainers":
                    result = _dept_import_trainers(db, rows, dept_id)
                elif import_type == "classes":
                    result = _dept_import_classes(db, rows, dept_id)
                elif import_type == "units":
                    result = _dept_import_units(db, rows, dept_id)

                from auth_utils import write_audit_log
                write_audit_log("bulk_import", target=import_type,
                                detail={"count": result.get("success", 0)})
            except Exception as exc:
                error = f"Import failed: {exc}"

    # Classes in this dept for the student import hint
    try:
        classes = (db.table("classes").select("id, name")
                     .eq("department_id", dept_id).order("name")
                     .execute().data or [])
    except Exception:
        classes = []

    return render_template("dept_admin/import.html",
                           result=result, error=error, classes=classes)


def _dept_import_students(db, rows, dept_id):
    # Get valid class ids for this dept
    valid_ids = {
        c["id"] for c in
        db.table("classes").select("id").eq("department_id", dept_id).execute().data or []
    }
    success = 0
    errors  = []
    for r in rows:
        if not r or not r[0]:
            continue
        try:
            adm, name, class_id = str(r[0]).strip(), str(r[1]).strip().upper(), int(r[2])
            if class_id not in valid_ids:
                errors.append(f"{adm}: class_id {class_id} not in your department")
                continue
            db.table("students").insert({
                "admission_number": adm,
                "full_name": name,
                "class_id": class_id,
            }).execute()
            success += 1
        except Exception as exc:
            errors.append(f"Row {r}: {exc}")
    return {"success": success, "errors": errors[:10]}


def _dept_import_trainers(db, rows, dept_id):
    from routes.super_admin import _create_auth_user
    success = 0
    errors  = []
    for r in rows:
        if not r or not r[0]:
            continue
        try:
            name, username, email, password = (
                str(r[0]).strip(), str(r[1]).strip(),
                str(r[2]).strip().lower(), str(r[3]).strip()
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


def _dept_import_classes(db, rows, dept_id):
    success = 0
    errors  = []
    for r in rows:
        if not r or not r[0]:
            continue
        try:
            name = str(r[0]).strip().upper()
            db.table("classes").insert({
                "name": name, "department_id": dept_id
            }).execute()
            success += 1
        except Exception as exc:
            errors.append(f"Row {r}: {exc}")
    return {"success": success, "errors": errors[:10]}


def _dept_import_units(db, rows, dept_id):
    success = 0
    errors  = []
    for r in rows:
        if not r or not r[0]:
            continue
        try:
            code, name = str(r[0]).strip().upper(), str(r[1]).strip()
            db.table("units").insert({
                "code": code, "name": name, "department_id": dept_id
            }).execute()
            success += 1
        except Exception as exc:
            errors.append(f"Row {r}: {exc}")
    return {"success": success, "errors": errors[:10]}
