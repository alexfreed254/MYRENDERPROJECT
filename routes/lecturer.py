"""
routes/lecturer.py — Trainer / Lecturer blueprint.

Trainers can only access classes and units assigned to them.
Department isolation is enforced both here and via RLS.
"""

from flask import (Blueprint, render_template, request,
                   session, redirect, url_for, jsonify, abort)
from auth_utils import (trainer_required, write_audit_log, current_user)
from db import get_service_client
from utils import now_eat_naive

lecturer_bp = Blueprint("lecturer", __name__)


def _trainer_row() -> dict:
    """Return the trainers table row for the current user, or abort 403."""
    user = current_user()
    db   = get_service_client()
    try:
        rows = (db.table("trainers")
                  .select("*")
                  .eq("user_id", user["id"])
                  .limit(1)
                  .execute().data or [])
        if not rows:
            abort(403)
        return rows[0]
    except Exception:
        abort(403)


# ── Dashboard (attendance capture) ───────────────────────────────────────────

@lecturer_bp.route("/")
@lecturer_bp.route("/dashboard")
@trainer_required
def dashboard():
    db      = get_service_client()
    trainer = _trainer_row()
    dept_id = trainer["department_id"]

    # Classes assigned to this trainer
    cu_rows = (db.table("class_units")
                 .select("class_id")
                 .eq("trainer_id", trainer["id"])
                 .execute().data or [])
    class_ids = list({r["class_id"] for r in cu_rows})

    class_list = []
    if class_ids:
        class_list = (db.table("classes")
                        .select("*")
                        .in_("id", class_ids)
                        .eq("department_id", dept_id)   # extra isolation
                        .order("name")
                        .execute().data or [])

    class_id = request.args.get("class_id", 0, type=int)
    unit_id  = request.args.get("unit_id",  0, type=int)
    week     = request.args.get("week",     1, type=int)
    lesson   = request.args.get("lesson",  "L1")
    year     = request.args.get("year",  2026, type=int)
    term     = request.args.get("term",     1, type=int)

    units_list   = []
    students_list = []
    attendance_submitted = False
    active_event = None

    if class_id:
        units_list = (db.table("class_units")
                        .select("*, units(id, code, name)")
                        .eq("class_id", class_id)
                        .eq("trainer_id", trainer["id"])
                        .execute().data or [])

        students_list = (db.table("students")
                           .select("*")
                           .eq("class_id", class_id)
                           .order("admission_number")
                           .execute().data or [])

        if unit_id and week and lesson:
            existing = (db.table("attendance")
                          .select("id", count="exact")
                          .eq("unit_id", unit_id)
                          .eq("trainer_id", trainer["id"])
                          .eq("week", week)
                          .eq("lesson", lesson)
                          .eq("year", year)
                          .eq("term", term)
                          .execute())
            attendance_submitted = (existing.count or 0) > 0

            event_row = (db.table("class_events")
                           .select("*")
                           .eq("class_id", class_id)
                           .eq("trainer_id", trainer["id"])
                           .eq("week", week)
                           .eq("lesson", lesson)
                           .eq("year", year)
                           .eq("term", term)
                           .limit(1)
                           .execute().data)
            active_event = event_row[0] if event_row else None

    dept = db.table("departments").select("name").eq("id", dept_id).single().execute().data or {}

    return render_template("lecturer/dashboard.html",
                           trainer=trainer,
                           dept_name=dept.get("name", ""),
                           class_list=class_list,
                           class_id=class_id,
                           unit_id=unit_id,
                           week=week, lesson=lesson,
                           year=year, term=term,
                           units_list=units_list,
                           students_list=students_list,
                           attendance_submitted=attendance_submitted,
                           active_event=active_event)


# ── Submit Attendance (AJAX) ──────────────────────────────────────────────────

@lecturer_bp.route("/submit-attendance", methods=["POST"])
@trainer_required
def submit_attendance():
    db      = get_service_client()
    trainer = _trainer_row()

    class_id = request.form.get("class_id", type=int)
    unit_id  = request.form.get("unit_id",  type=int)
    week     = request.form.get("week",     type=int)
    lesson   = request.form.get("lesson",   "")
    year     = request.form.get("year",     type=int)
    term     = request.form.get("term",     type=int)

    if not all([class_id, unit_id, week, lesson, year, term]):
        return jsonify(success=False, message="Missing required fields.")

    # Verify this trainer is assigned to this class/unit
    cu = (db.table("class_units")
            .select("id")
            .eq("class_id", class_id)
            .eq("unit_id", unit_id)
            .eq("trainer_id", trainer["id"])
            .execute().data)
    if not cu:
        return jsonify(success=False, message="Not authorised for this class/unit.")

    # Prevent duplicate submission
    existing = (db.table("attendance")
                  .select("id", count="exact")
                  .eq("unit_id", unit_id)
                  .eq("trainer_id", trainer["id"])
                  .eq("week", week)
                  .eq("lesson", lesson)
                  .eq("year", year)
                  .eq("term", term)
                  .execute())
    if (existing.count or 0) > 0:
        return jsonify(success=False, message="Attendance already submitted for this session.")

    # Get unit code
    unit_row = db.table("units").select("code").eq("id", unit_id).single().execute().data or {}

    # Build records
    records = []
    students = (db.table("students")
                  .select("id")
                  .eq("class_id", class_id)
                  .execute().data or [])
    for s in students:
        sid    = s["id"]
        status = request.form.get(f"status[{sid}]", "absent")
        if status not in ("present", "absent"):
            status = "absent"
        records.append({
            "student_id":      sid,
            "unit_id":         unit_id,
            "unit_code":       unit_row.get("code", ""),
            "trainer_id":      trainer["id"],
            "lesson":          lesson,
            "week":            week,
            "year":            year,
            "term":            term,
            "status":          status,
            "attendance_date": now_eat_naive().isoformat(),
        })

    if not records:
        return jsonify(success=False, message="No students found in this class.")

    db.table("attendance").insert(records).execute()
    write_audit_log("submit_attendance", detail={
        "class_id": class_id, "unit_id": unit_id,
        "week": week, "lesson": lesson, "year": year, "term": term,
        "count": len(records),
    })
    return jsonify(success=True, message=f"Attendance submitted successfully for {len(records)} student(s).")


# ── View Attendance ───────────────────────────────────────────────────────────

@lecturer_bp.route("/view-attendance")
@trainer_required
def view_attendance():
    db      = get_service_client()
    trainer = _trainer_row()

    class_id = request.args.get("class_id", type=int)
    unit_id  = request.args.get("unit_id",  type=int)
    week     = request.args.get("week",     type=int)
    lesson   = request.args.get("lesson",   "")
    year     = request.args.get("year",  2026, type=int)
    term     = request.args.get("term",     1, type=int)

    records = []
    if class_id and unit_id and week and lesson:
        # Verify trainer owns this class/unit
        cu = (db.table("class_units")
                .select("id")
                .eq("class_id", class_id)
                .eq("unit_id", unit_id)
                .eq("trainer_id", trainer["id"])
                .execute().data)
        if not cu:
            abort(403)

        records = (db.table("attendance")
                     .select("*, students(full_name, admission_number)")
                     .eq("unit_id", unit_id)
                     .eq("trainer_id", trainer["id"])
                     .eq("week", week)
                     .eq("lesson", lesson)
                     .eq("year", year)
                     .eq("term", term)
                     .execute().data or [])

    return render_template("lecturer/view_attendance.html",
                           trainer=trainer,
                           records=records,
                           class_id=class_id, unit_id=unit_id,
                           week=week, lesson=lesson,
                           year=year, term=term)


# ── Update single attendance record ──────────────────────────────────────────

@lecturer_bp.route("/update-attendance", methods=["POST"])
@trainer_required
def update_attendance():
    db      = get_service_client()
    trainer = _trainer_row()

    att_id = request.form.get("att_id", type=int)
    status = request.form.get("status", "")
    if not att_id or status not in ("present", "absent"):
        return jsonify(success=False, message="Invalid data.")

    # Verify ownership before update
    row = (db.table("attendance")
             .select("trainer_id")
             .eq("id", att_id)
             .single()
             .execute().data)
    if not row or row["trainer_id"] != trainer["id"]:
        return jsonify(success=False, message="Not authorised.")

    db.table("attendance").update({"status": status}).eq("id", att_id).execute()
    return jsonify(success=True, message="Updated.")


# ── Delete single attendance record ──────────────────────────────────────────

@lecturer_bp.route("/delete-single-attendance", methods=["POST"])
@trainer_required
def delete_single_attendance():
    db      = get_service_client()
    trainer = _trainer_row()

    att_id = request.form.get("att_id", type=int)
    if not att_id:
        return jsonify(success=False, message="Missing record ID.")

    # Verify ownership before deleting
    row = (db.table("attendance")
             .select("trainer_id")
             .eq("id", att_id)
             .single()
             .execute().data)
    if not row or row["trainer_id"] != trainer["id"]:
        return jsonify(success=False, message="Not authorised.")

    db.table("attendance").delete().eq("id", att_id).execute()
    write_audit_log("delete_single_attendance", detail={"att_id": att_id})
    return jsonify(success=True, message="Record deleted.")


# ── Delete lesson attendance ──────────────────────────────────────────────────

@lecturer_bp.route("/delete-lesson", methods=["POST"])
@trainer_required
def delete_lesson():
    db      = get_service_client()
    trainer = _trainer_row()

    unit_id = request.form.get("unit_id", type=int)
    week    = request.form.get("week",    type=int)
    lesson  = request.form.get("lesson",  "")
    year    = request.form.get("year",    type=int)
    term    = request.form.get("term",    type=int)

    if not all([unit_id, week, lesson, year, term]):
        return jsonify(success=False, message="Missing fields.")

    db.table("attendance").delete()\
        .eq("unit_id",    unit_id)\
        .eq("trainer_id", trainer["id"])\
        .eq("week",       week)\
        .eq("lesson",     lesson)\
        .eq("year",       year)\
        .eq("term",       term)\
        .execute()
    write_audit_log("delete_lesson_attendance", detail={
        "unit_id": unit_id, "week": week, "lesson": lesson,
        "year": year, "term": term,
    })
    return jsonify(success=True, message="Lesson attendance deleted.")


# ── Mark Event (holiday / academic trip) ─────────────────────────────────────

@lecturer_bp.route("/mark-event", methods=["POST"])
@trainer_required
def mark_event():
    db      = get_service_client()
    trainer = _trainer_row()

    class_id   = request.form.get("class_id",   type=int)
    unit_id    = request.form.get("unit_id",    0, type=int)
    event_type = request.form.get("event_type", "")
    week       = request.form.get("week",       type=int)
    lesson     = request.form.get("lesson",     "")
    year       = request.form.get("year",       type=int)
    term       = request.form.get("term",       type=int)
    note       = request.form.get("note",       "").strip()

    if event_type not in ("holiday", "academic_trip"):
        return jsonify(success=False, message="Invalid event type.")
    if not all([class_id, week, lesson, year, term]):
        return jsonify(success=False, message="Missing required fields.")

    try:
        db.table("class_events").upsert({
            "class_id":   class_id,
            "unit_id":    unit_id or 0,
            "trainer_id": trainer["id"],
            "event_type": event_type,
            "week":       week,
            "lesson":     lesson,
            "year":       year,
            "term":       term,
            "note":       note or None,
        }).execute()
        return jsonify(success=True, message="Event recorded.")
    except Exception as exc:
        return jsonify(success=False, message=str(exc))


# ── Delete Event ──────────────────────────────────────────────────────────────

@lecturer_bp.route("/delete-event", methods=["POST"])
@trainer_required
def delete_event():
    db      = get_service_client()
    trainer = _trainer_row()

    event_id = request.form.get("event_id", type=int)
    if not event_id:
        return redirect(url_for("lecturer.dashboard"))

    row = (db.table("class_events")
             .select("trainer_id")
             .eq("id", event_id)
             .single()
             .execute().data)
    if not row or row["trainer_id"] != trainer["id"]:
        abort(403)

    db.table("class_events").delete().eq("id", event_id).execute()
    return redirect(request.referrer or url_for("lecturer.dashboard"))


# ── Trainee Search ────────────────────────────────────────────────────────────

@lecturer_bp.route("/trainee-search")
@trainer_required
def trainee_search():
    db      = get_service_client()
    trainer = _trainer_row()
    query   = request.args.get("q", "").strip()
    results = []

    if query:
        # Only students in classes assigned to this trainer
        cu_rows = (db.table("class_units")
                     .select("class_id")
                     .eq("trainer_id", trainer["id"])
                     .execute().data or [])
        class_ids = list({r["class_id"] for r in cu_rows})
        if class_ids:
            results = (db.table("students")
                         .select("*, classes(name)")
                         .in_("class_id", class_ids)
                         .or_(f"full_name.ilike.%{query}%,admission_number.ilike.%{query}%")
                         .execute().data or [])

    return render_template("lecturer/trainee_search.html",
                           trainer=trainer, results=results, query=query)


# ── Class Attendance Report ───────────────────────────────────────────────────

@lecturer_bp.route("/class-report")
@trainer_required
def class_report():
    """Full attendance report for a class/unit — view + PDF download link."""
    db      = get_service_client()
    trainer = _trainer_row()

    class_id = request.args.get("class_id", type=int)
    unit_id  = request.args.get("unit_id",  type=int)
    year     = request.args.get("year",  2026, type=int)
    term     = request.args.get("term",     1, type=int)

    # Trainer's classes and units for the filter dropdowns
    cu_rows = (db.table("class_units")
                 .select("class_id")
                 .eq("trainer_id", trainer["id"])
                 .execute().data or [])
    class_ids = list({r["class_id"] for r in cu_rows})

    class_list = []
    if class_ids:
        class_list = (db.table("classes")
                        .select("*")
                        .in_("id", class_ids)
                        .order("name")
                        .execute().data or [])

    units_list = []
    records    = []
    cls        = None
    unit       = None
    summary    = []   # per-student summary rows

    if class_id and unit_id:
        # Verify trainer owns this class/unit
        cu = (db.table("class_units")
                .select("id")
                .eq("class_id", class_id)
                .eq("unit_id",  unit_id)
                .eq("trainer_id", trainer["id"])
                .execute().data)
        if not cu:
            abort(403)

        cls  = next((c for c in class_list if c["id"] == class_id), None)
        unit = (db.table("units").select("*").eq("id", unit_id)
                  .limit(1).execute().data or [{}])[0]

        # All attendance records for this class/unit/year/term
        records = (db.table("attendance")
                     .select("*, students(id, full_name, admission_number)")
                     .eq("unit_id",    unit_id)
                     .eq("trainer_id", trainer["id"])
                     .eq("year",       year)
                     .eq("term",       term)
                     .execute().data or [])

        # Build per-student summary
        student_map = {}
        for r in records:
            sid = r["student_id"]
            if sid not in student_map:
                student_map[sid] = {
                    "id":               sid,
                    "full_name":        (r.get("students") or {}).get("full_name", "—"),
                    "admission_number": (r.get("students") or {}).get("admission_number", "—"),
                    "present": 0, "absent": 0, "total": 0,
                }
            student_map[sid]["total"] += 1
            if r["status"] == "present":
                student_map[sid]["present"] += 1
            else:
                student_map[sid]["absent"] += 1

        for s in student_map.values():
            s["pct"] = round((s["present"] / s["total"]) * 100, 1) if s["total"] else 0

        summary = sorted(student_map.values(), key=lambda x: x["admission_number"])

    if class_id:
        units_list = (db.table("class_units")
                        .select("*, units(id, code, name)")
                        .eq("class_id",   class_id)
                        .eq("trainer_id", trainer["id"])
                        .execute().data or [])

    return render_template("lecturer/class_report.html",
                           trainer=trainer,
                           class_list=class_list,
                           units_list=units_list,
                           class_id=class_id,
                           unit_id=unit_id,
                           year=year, term=term,
                           cls=cls, unit=unit,
                           records=records,
                           summary=summary)


# ── Class Attendance PDF ──────────────────────────────────────────────────────

@lecturer_bp.route("/class-report-pdf")
@trainer_required
def class_report_pdf():
    db      = get_service_client()
    trainer = _trainer_row()

    class_id = request.args.get("class_id", type=int)
    unit_id  = request.args.get("unit_id",  type=int)
    year     = request.args.get("year",  2026, type=int)
    term     = request.args.get("term",     1, type=int)

    if not class_id or not unit_id:
        abort(400)

    # Verify ownership
    cu = (db.table("class_units")
            .select("id")
            .eq("class_id",   class_id)
            .eq("unit_id",    unit_id)
            .eq("trainer_id", trainer["id"])
            .execute().data)
    if not cu:
        abort(403)

    cls  = (db.table("classes").select("*").eq("id", class_id)
              .limit(1).execute().data or [{}])[0]
    unit = (db.table("units").select("*").eq("id", unit_id)
              .limit(1).execute().data or [{}])[0]
    dept = (db.table("departments").select("name")
              .eq("id", trainer.get("department_id", 0))
              .limit(1).execute().data or [{}])[0]

    records = (db.table("attendance")
                 .select("*, students(id, full_name, admission_number)")
                 .eq("unit_id",    unit_id)
                 .eq("trainer_id", trainer["id"])
                 .eq("year",       year)
                 .eq("term",       term)
                 .execute().data or [])

    # Per-student summary
    student_map = {}
    for r in records:
        sid = r["student_id"]
        if sid not in student_map:
            student_map[sid] = {
                "full_name":        (r.get("students") or {}).get("full_name", "—"),
                "admission_number": (r.get("students") or {}).get("admission_number", "—"),
                "present": 0, "absent": 0, "total": 0,
            }
        student_map[sid]["total"] += 1
        if r["status"] == "present":
            student_map[sid]["present"] += 1
        else:
            student_map[sid]["absent"] += 1

    for s in student_map.values():
        s["pct"] = round((s["present"] / s["total"]) * 100, 1) if s["total"] else 0

    summary = sorted(student_map.values(), key=lambda x: x["admission_number"])

    from utils import now_eat
    return render_template("lecturer/class_report_pdf.html",
                           trainer=trainer,
                           cls=cls, unit=unit, dept=dept,
                           summary=summary,
                           year=year, term=term,
                           generated=now_eat().strftime("%d %b %Y %H:%M"))


# ── Individual Trainee Report ─────────────────────────────────────────────────

@lecturer_bp.route("/trainee-report")
@trainer_required
def trainee_report():
    db         = get_service_client()
    trainer    = _trainer_row()
    student_id = request.args.get("student_id", type=int)
    query      = request.args.get("q", "").strip()

    if not student_id:
        return redirect(url_for("lecturer.trainee_search"))

    # Verify student is in one of trainer's classes
    cu_rows = (db.table("class_units")
                 .select("class_id")
                 .eq("trainer_id", trainer["id"])
                 .execute().data or [])
    class_ids = list({r["class_id"] for r in cu_rows})

    student = None
    if class_ids:
        rows = (db.table("students")
                  .select("*, classes(name)")
                  .eq("id", student_id)
                  .in_("class_id", class_ids)
                  .limit(1)
                  .execute().data or [])
        student = rows[0] if rows else None

    if not student:
        abort(403)

    # All attendance for this student under this trainer
    records = (db.table("attendance")
                 .select("*, units(code, name)")
                 .eq("student_id", student_id)
                 .eq("trainer_id", trainer["id"])
                 .order("year").order("term").order("week").order("lesson")
                 .execute().data or [])

    # Group by unit
    unit_map = {}
    for r in records:
        uid = r["unit_id"]
        if uid not in unit_map:
            unit_map[uid] = {
                "code":    (r.get("units") or {}).get("code", "—"),
                "name":    (r.get("units") or {}).get("name", "—"),
                "present": 0, "absent": 0, "total": 0,
                "records": [],
            }
        unit_map[uid]["total"] += 1
        if r["status"] == "present":
            unit_map[uid]["present"] += 1
        else:
            unit_map[uid]["absent"] += 1
        unit_map[uid]["records"].append(r)

    for u in unit_map.values():
        u["pct"] = round((u["present"] / u["total"]) * 100, 1) if u["total"] else 0

    units_summary = list(unit_map.values())

    return render_template("lecturer/trainee_report.html",
                           trainer=trainer,
                           student=student,
                           units_summary=units_summary,
                           query=query,
                           student_id=student_id)


# ── Individual Trainee PDF ────────────────────────────────────────────────────

@lecturer_bp.route("/trainee-report-pdf")
@trainer_required
def trainee_report_pdf():
    db         = get_service_client()
    trainer    = _trainer_row()
    student_id = request.args.get("student_id", type=int)

    if not student_id:
        abort(400)

    cu_rows = (db.table("class_units")
                 .select("class_id")
                 .eq("trainer_id", trainer["id"])
                 .execute().data or [])
    class_ids = list({r["class_id"] for r in cu_rows})

    student = None
    if class_ids:
        rows = (db.table("students")
                  .select("*, classes(name)")
                  .eq("id", student_id)
                  .in_("class_id", class_ids)
                  .limit(1)
                  .execute().data or [])
        student = rows[0] if rows else None

    if not student:
        abort(403)

    records = (db.table("attendance")
                 .select("*, units(code, name)")
                 .eq("student_id", student_id)
                 .eq("trainer_id", trainer["id"])
                 .order("year").order("term").order("week").order("lesson")
                 .execute().data or [])

    unit_map = {}
    for r in records:
        uid = r["unit_id"]
        if uid not in unit_map:
            unit_map[uid] = {
                "code":    (r.get("units") or {}).get("code", "—"),
                "name":    (r.get("units") or {}).get("name", "—"),
                "present": 0, "absent": 0, "total": 0,
                "records": [],
            }
        unit_map[uid]["total"] += 1
        if r["status"] == "present":
            unit_map[uid]["present"] += 1
        else:
            unit_map[uid]["absent"] += 1
        unit_map[uid]["records"].append(r)

    for u in unit_map.values():
        u["pct"] = round((u["present"] / u["total"]) * 100, 1) if u["total"] else 0

    units_summary = list(unit_map.values())
    dept = (db.table("departments").select("name")
              .eq("id", trainer.get("department_id", 0))
              .limit(1).execute().data or [{}])[0]

    from utils import now_eat
    return render_template("lecturer/trainee_report_pdf.html",
                           trainer=trainer,
                           student=student,
                           units_summary=units_summary,
                           dept=dept,
                           generated=now_eat().strftime("%d %b %Y %H:%M"))


# ── Per-session Attendance PDF (Week/Lesson) ──────────────────────────────────

@lecturer_bp.route("/download-attendance-pdf")
@trainer_required
def download_attendance_pdf():
    db      = get_service_client()
    trainer = _trainer_row()

    class_id = request.args.get("class_id", type=int)
    unit_id  = request.args.get("unit_id",  type=int)
    week     = request.args.get("week",     type=int)
    lesson   = request.args.get("lesson",   "")
    year     = request.args.get("year",  2026, type=int)
    term     = request.args.get("term",     1, type=int)

    if not all([class_id, unit_id, week, lesson]):
        abort(400)

    # Verify ownership
    cu = (db.table("class_units")
            .select("id")
            .eq("class_id",   class_id)
            .eq("unit_id",    unit_id)
            .eq("trainer_id", trainer["id"])
            .execute().data)
    if not cu:
        abort(403)

    cls  = (db.table("classes").select("*").eq("id", class_id)
              .limit(1).execute().data or [{}])[0]
    unit = (db.table("units").select("*").eq("id", unit_id)
              .limit(1).execute().data or [{}])[0]
    dept = (db.table("departments").select("name")
              .eq("id", trainer.get("department_id", 0))
              .limit(1).execute().data or [{}])[0]

    records = (db.table("attendance")
                 .select("*, students(full_name, admission_number)")
                 .eq("unit_id",    unit_id)
                 .eq("trainer_id", trainer["id"])
                 .eq("week",       week)
                 .eq("lesson",     lesson)
                 .eq("year",       year)
                 .eq("term",       term)
                 .execute().data or [])

    from utils import now_eat
    now = now_eat()
    return render_template("lecturer/download_attendance_pdf.html",
                           trainer=trainer,
                           cls=cls, unit=unit, dept=dept,
                           records=records,
                           week=week, lesson=lesson,
                           year=year, term=term,
                           date=now.strftime("%d %b %Y"),
                           generated=now.strftime("%d %b %Y, %H:%M"))
