-- ============================================================
-- THIKA TECHNICAL TRAINING INSTITUTE
-- Attendance Management System — Supabase Schema
-- Run this entire file in the Supabase SQL Editor
-- ============================================================

-- ────────────────────────────────────────────────────────────
-- 0. EXTENSIONS
-- ────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ────────────────────────────────────────────────────────────
-- 1. CORE LOOKUP TABLES
-- ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS departments (
    id   SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS classes (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(100) NOT NULL,
    department_id INT NOT NULL REFERENCES departments(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS units (
    id            SERIAL PRIMARY KEY,
    code          VARCHAR(50)  NOT NULL UNIQUE,
    name          VARCHAR(200) NOT NULL,
    department_id INT REFERENCES departments(id) ON DELETE SET NULL
);

-- ────────────────────────────────────────────────────────────
-- 2. USER PROFILE TABLE
--    Mirrors auth.users; one row per Supabase Auth user.
--    role: 'super_admin' | 'dept_admin' | 'trainer' | 'student'
-- ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS user_profiles (
    id            UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    full_name     VARCHAR(200),
    role          VARCHAR(20)  NOT NULL CHECK (role IN ('super_admin','dept_admin','trainer','student')),
    department_id INT REFERENCES departments(id) ON DELETE SET NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Keep updated_at current automatically
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$;

CREATE TRIGGER trg_user_profiles_updated_at
    BEFORE UPDATE ON user_profiles
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ────────────────────────────────────────────────────────────
-- 3. TRAINERS
--    Linked to a Supabase Auth user via user_id.
-- ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS trainers (
    id            SERIAL PRIMARY KEY,
    user_id       UUID UNIQUE REFERENCES auth.users(id) ON DELETE SET NULL,
    name          VARCHAR(200) NOT NULL,
    username      VARCHAR(100) NOT NULL UNIQUE,
    department_id INT REFERENCES departments(id) ON DELETE SET NULL
);

-- ────────────────────────────────────────────────────────────
-- 4. STUDENTS
--    Linked to a Supabase Auth user via user_id.
-- ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS students (
    id               SERIAL PRIMARY KEY,
    user_id          UUID UNIQUE REFERENCES auth.users(id) ON DELETE SET NULL,
    admission_number VARCHAR(50)  UNIQUE,
    full_name        VARCHAR(200),
    email            VARCHAR(100),
    class_id         INT REFERENCES classes(id) ON DELETE CASCADE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ────────────────────────────────────────────────────────────
-- 5. CLASS–UNIT ASSIGNMENTS
-- ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS class_units (
    id         SERIAL PRIMARY KEY,
    class_id   INT NOT NULL REFERENCES classes(id)   ON DELETE CASCADE,
    unit_id    INT NOT NULL REFERENCES units(id)     ON DELETE CASCADE,
    trainer_id INT NOT NULL REFERENCES trainers(id)  ON DELETE CASCADE,
    year       INT NOT NULL DEFAULT EXTRACT(YEAR FROM NOW())::INT,
    term       INT NOT NULL DEFAULT 1,
    UNIQUE (class_id, unit_id, trainer_id, year, term)
);

-- ────────────────────────────────────────────────────────────
-- 6. ATTENDANCE
-- ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS attendance (
    id              SERIAL PRIMARY KEY,
    student_id      INT NOT NULL REFERENCES students(id)  ON DELETE CASCADE,
    unit_id         INT NOT NULL REFERENCES units(id)     ON DELETE CASCADE,
    unit_code       VARCHAR(50),
    trainer_id      INT NOT NULL REFERENCES trainers(id)  ON DELETE CASCADE,
    lesson          VARCHAR(10) NOT NULL,
    week            INT NOT NULL,
    year            INT NOT NULL DEFAULT EXTRACT(YEAR FROM NOW())::INT,
    term            INT NOT NULL DEFAULT 1,
    status          VARCHAR(10) NOT NULL CHECK (status IN ('present','absent')),
    attendance_date TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ────────────────────────────────────────────────────────────
-- 7. CLASS EVENTS  (holidays / academic trips)
-- ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS class_events (
    id         SERIAL PRIMARY KEY,
    class_id   INT NOT NULL REFERENCES classes(id)   ON DELETE CASCADE,
    unit_id    INT NOT NULL DEFAULT 0,               -- 0 = applies to whole class
    trainer_id INT NOT NULL REFERENCES trainers(id)  ON DELETE CASCADE,
    event_type VARCHAR(30) NOT NULL CHECK (event_type IN ('holiday','academic_trip')),
    week       INT NOT NULL,
    lesson     VARCHAR(10) NOT NULL,
    year       INT NOT NULL DEFAULT EXTRACT(YEAR FROM NOW())::INT,
    term       INT NOT NULL DEFAULT 1,
    note       TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (class_id, unit_id, trainer_id, week, lesson, year, term)
);

-- ────────────────────────────────────────────────────────────
-- 8. SYSTEM AUDIT LOG
-- ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS system_logs (
    id         BIGSERIAL PRIMARY KEY,
    actor_id   UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    actor_role VARCHAR(20),
    action     VARCHAR(100) NOT NULL,
    target     VARCHAR(200),
    detail     JSONB,
    ip_address INET,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ────────────────────────────────────────────────────────────
-- 9. HELPER FUNCTIONS (used by RLS policies)
-- ────────────────────────────────────────────────────────────

-- Returns the role of the currently authenticated user
CREATE OR REPLACE FUNCTION current_user_role()
RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER AS $$
    SELECT role FROM user_profiles WHERE id = auth.uid();
$$;

-- Returns the department_id of the currently authenticated user
CREATE OR REPLACE FUNCTION current_user_dept()
RETURNS INT LANGUAGE sql STABLE SECURITY DEFINER AS $$
    SELECT department_id FROM user_profiles WHERE id = auth.uid();
$$;

-- Returns TRUE if the current user is active
CREATE OR REPLACE FUNCTION current_user_active()
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER AS $$
    SELECT COALESCE(is_active, FALSE) FROM user_profiles WHERE id = auth.uid();
$$;

-- ────────────────────────────────────────────────────────────
-- 10. ROW LEVEL SECURITY
-- ────────────────────────────────────────────────────────────

ALTER TABLE departments   ENABLE ROW LEVEL SECURITY;
ALTER TABLE classes       ENABLE ROW LEVEL SECURITY;
ALTER TABLE units         ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE trainers      ENABLE ROW LEVEL SECURITY;
ALTER TABLE students      ENABLE ROW LEVEL SECURITY;
ALTER TABLE class_units   ENABLE ROW LEVEL SECURITY;
ALTER TABLE attendance    ENABLE ROW LEVEL SECURITY;
ALTER TABLE class_events  ENABLE ROW LEVEL SECURITY;
ALTER TABLE system_logs   ENABLE ROW LEVEL SECURITY;

-- ── departments ──────────────────────────────────────────────

-- Super admin: full access
CREATE POLICY dept_super_admin ON departments
    FOR ALL TO authenticated
    USING (current_user_role() = 'super_admin' AND current_user_active())
    WITH CHECK (current_user_role() = 'super_admin' AND current_user_active());

-- Dept admin / trainer / student: read their own department only
CREATE POLICY dept_read_own ON departments
    FOR SELECT TO authenticated
    USING (
        current_user_active()
        AND current_user_role() IN ('dept_admin','trainer','student')
        AND id = current_user_dept()
    );

-- ── classes ──────────────────────────────────────────────────

CREATE POLICY classes_super_admin ON classes
    FOR ALL TO authenticated
    USING (current_user_role() = 'super_admin' AND current_user_active())
    WITH CHECK (current_user_role() = 'super_admin' AND current_user_active());

CREATE POLICY classes_dept_admin ON classes
    FOR ALL TO authenticated
    USING (
        current_user_active()
        AND current_user_role() = 'dept_admin'
        AND department_id = current_user_dept()
    )
    WITH CHECK (
        current_user_active()
        AND current_user_role() = 'dept_admin'
        AND department_id = current_user_dept()
    );

CREATE POLICY classes_read_own_dept ON classes
    FOR SELECT TO authenticated
    USING (
        current_user_active()
        AND current_user_role() IN ('trainer','student')
        AND department_id = current_user_dept()
    );

-- ── units ────────────────────────────────────────────────────

CREATE POLICY units_super_admin ON units
    FOR ALL TO authenticated
    USING (current_user_role() = 'super_admin' AND current_user_active())
    WITH CHECK (current_user_role() = 'super_admin' AND current_user_active());

CREATE POLICY units_dept_admin ON units
    FOR ALL TO authenticated
    USING (
        current_user_active()
        AND current_user_role() = 'dept_admin'
        AND department_id = current_user_dept()
    )
    WITH CHECK (
        current_user_active()
        AND current_user_role() = 'dept_admin'
        AND department_id = current_user_dept()
    );

CREATE POLICY units_read_own_dept ON units
    FOR SELECT TO authenticated
    USING (
        current_user_active()
        AND current_user_role() IN ('trainer','student')
        AND department_id = current_user_dept()
    );

-- ── user_profiles ────────────────────────────────────────────

-- Super admin: full access
CREATE POLICY profiles_super_admin ON user_profiles
    FOR ALL TO authenticated
    USING (current_user_role() = 'super_admin' AND current_user_active())
    WITH CHECK (current_user_role() = 'super_admin' AND current_user_active());

-- Dept admin: read/update profiles in their department
CREATE POLICY profiles_dept_admin ON user_profiles
    FOR SELECT TO authenticated
    USING (
        current_user_active()
        AND current_user_role() = 'dept_admin'
        AND department_id = current_user_dept()
    );

-- Any user: read/update their own profile
CREATE POLICY profiles_own ON user_profiles
    FOR ALL TO authenticated
    USING (id = auth.uid())
    WITH CHECK (id = auth.uid());

-- ── trainers ─────────────────────────────────────────────────

CREATE POLICY trainers_super_admin ON trainers
    FOR ALL TO authenticated
    USING (current_user_role() = 'super_admin' AND current_user_active())
    WITH CHECK (current_user_role() = 'super_admin' AND current_user_active());

CREATE POLICY trainers_dept_admin ON trainers
    FOR ALL TO authenticated
    USING (
        current_user_active()
        AND current_user_role() = 'dept_admin'
        AND department_id = current_user_dept()
    )
    WITH CHECK (
        current_user_active()
        AND current_user_role() = 'dept_admin'
        AND department_id = current_user_dept()
    );

CREATE POLICY trainers_read_own_dept ON trainers
    FOR SELECT TO authenticated
    USING (
        current_user_active()
        AND current_user_role() = 'trainer'
        AND department_id = current_user_dept()
    );

CREATE POLICY trainers_own_row ON trainers
    FOR SELECT TO authenticated
    USING (user_id = auth.uid() AND current_user_active());

-- ── students ─────────────────────────────────────────────────

CREATE POLICY students_super_admin ON students
    FOR ALL TO authenticated
    USING (current_user_role() = 'super_admin' AND current_user_active())
    WITH CHECK (current_user_role() = 'super_admin' AND current_user_active());

CREATE POLICY students_dept_admin ON students
    FOR ALL TO authenticated
    USING (
        current_user_active()
        AND current_user_role() = 'dept_admin'
        AND class_id IN (
            SELECT id FROM classes WHERE department_id = current_user_dept()
        )
    )
    WITH CHECK (
        current_user_active()
        AND current_user_role() = 'dept_admin'
        AND class_id IN (
            SELECT id FROM classes WHERE department_id = current_user_dept()
        )
    );

CREATE POLICY students_trainer_read ON students
    FOR SELECT TO authenticated
    USING (
        current_user_active()
        AND current_user_role() = 'trainer'
        AND class_id IN (
            SELECT cu.class_id FROM class_units cu
            JOIN trainers t ON t.id = cu.trainer_id
            WHERE t.user_id = auth.uid()
        )
    );

CREATE POLICY students_own_row ON students
    FOR SELECT TO authenticated
    USING (user_id = auth.uid() AND current_user_active());

-- ── class_units ──────────────────────────────────────────────

CREATE POLICY cu_super_admin ON class_units
    FOR ALL TO authenticated
    USING (current_user_role() = 'super_admin' AND current_user_active())
    WITH CHECK (current_user_role() = 'super_admin' AND current_user_active());

CREATE POLICY cu_dept_admin ON class_units
    FOR ALL TO authenticated
    USING (
        current_user_active()
        AND current_user_role() = 'dept_admin'
        AND class_id IN (
            SELECT id FROM classes WHERE department_id = current_user_dept()
        )
    )
    WITH CHECK (
        current_user_active()
        AND current_user_role() = 'dept_admin'
        AND class_id IN (
            SELECT id FROM classes WHERE department_id = current_user_dept()
        )
    );

CREATE POLICY cu_trainer_own ON class_units
    FOR SELECT TO authenticated
    USING (
        current_user_active()
        AND current_user_role() = 'trainer'
        AND trainer_id IN (SELECT id FROM trainers WHERE user_id = auth.uid())
    );

CREATE POLICY cu_student_read ON class_units
    FOR SELECT TO authenticated
    USING (
        current_user_active()
        AND current_user_role() = 'student'
        AND class_id IN (SELECT class_id FROM students WHERE user_id = auth.uid())
    );

-- ── attendance ───────────────────────────────────────────────

CREATE POLICY att_super_admin ON attendance
    FOR ALL TO authenticated
    USING (current_user_role() = 'super_admin' AND current_user_active())
    WITH CHECK (current_user_role() = 'super_admin' AND current_user_active());

CREATE POLICY att_dept_admin ON attendance
    FOR ALL TO authenticated
    USING (
        current_user_active()
        AND current_user_role() = 'dept_admin'
        AND student_id IN (
            SELECT s.id FROM students s
            JOIN classes c ON c.id = s.class_id
            WHERE c.department_id = current_user_dept()
        )
    )
    WITH CHECK (
        current_user_active()
        AND current_user_role() = 'dept_admin'
        AND student_id IN (
            SELECT s.id FROM students s
            JOIN classes c ON c.id = s.class_id
            WHERE c.department_id = current_user_dept()
        )
    );

CREATE POLICY att_trainer_own ON attendance
    FOR ALL TO authenticated
    USING (
        current_user_active()
        AND current_user_role() = 'trainer'
        AND trainer_id IN (SELECT id FROM trainers WHERE user_id = auth.uid())
    )
    WITH CHECK (
        current_user_active()
        AND current_user_role() = 'trainer'
        AND trainer_id IN (SELECT id FROM trainers WHERE user_id = auth.uid())
    );

CREATE POLICY att_student_own ON attendance
    FOR SELECT TO authenticated
    USING (
        current_user_active()
        AND current_user_role() = 'student'
        AND student_id IN (SELECT id FROM students WHERE user_id = auth.uid())
    );

-- ── class_events ─────────────────────────────────────────────

CREATE POLICY events_super_admin ON class_events
    FOR ALL TO authenticated
    USING (current_user_role() = 'super_admin' AND current_user_active())
    WITH CHECK (current_user_role() = 'super_admin' AND current_user_active());

CREATE POLICY events_dept_admin ON class_events
    FOR ALL TO authenticated
    USING (
        current_user_active()
        AND current_user_role() = 'dept_admin'
        AND class_id IN (
            SELECT id FROM classes WHERE department_id = current_user_dept()
        )
    )
    WITH CHECK (
        current_user_active()
        AND current_user_role() = 'dept_admin'
        AND class_id IN (
            SELECT id FROM classes WHERE department_id = current_user_dept()
        )
    );

CREATE POLICY events_trainer_own ON class_events
    FOR ALL TO authenticated
    USING (
        current_user_active()
        AND current_user_role() = 'trainer'
        AND trainer_id IN (SELECT id FROM trainers WHERE user_id = auth.uid())
    )
    WITH CHECK (
        current_user_active()
        AND current_user_role() = 'trainer'
        AND trainer_id IN (SELECT id FROM trainers WHERE user_id = auth.uid())
    );

-- ── system_logs ──────────────────────────────────────────────

-- Only super_admin can read logs; backend service role writes them
CREATE POLICY logs_super_admin_read ON system_logs
    FOR SELECT TO authenticated
    USING (current_user_role() = 'super_admin' AND current_user_active());

-- ────────────────────────────────────────────────────────────
-- 11. AUTO-CREATE user_profile ON SIGNUP
--     Triggered by Supabase Auth new user event.
--     Default role is 'student'; admins must be promoted manually.
-- ────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION handle_new_auth_user()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    INSERT INTO user_profiles (id, full_name, role, is_active)
    VALUES (
        NEW.id,
        COALESCE(NEW.raw_user_meta_data->>'full_name', NEW.email),
        COALESCE(NEW.raw_user_meta_data->>'role', 'student'),
        TRUE
    )
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$;

CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION handle_new_auth_user();

-- ────────────────────────────────────────────────────────────
-- 12. SEED: INITIAL SUPER ADMIN
--     Replace the email/password with your real credentials.
--     Run ONCE after setting up Supabase Auth.
-- ────────────────────────────────────────────────────────────

-- Step 1: Create the user via Supabase Auth Dashboard or API, then run:
-- (Replace 'YOUR-SUPER-ADMIN-UUID' with the UUID from auth.users)

-- INSERT INTO user_profiles (id, full_name, role, is_active)
-- VALUES ('YOUR-SUPER-ADMIN-UUID', 'Super Admin', 'super_admin', TRUE)
-- ON CONFLICT (id) DO UPDATE SET role = 'super_admin', is_active = TRUE;

-- ────────────────────────────────────────────────────────────
-- 13. USEFUL VIEWS
-- ────────────────────────────────────────────────────────────

-- Department summary (used by super_admin dashboard)
CREATE OR REPLACE VIEW v_department_stats AS
SELECT
    d.id,
    d.name,
    COUNT(DISTINCT c.id)  AS class_count,
    COUNT(DISTINCT s.id)  AS student_count,
    COUNT(DISTINCT t.id)  AS trainer_count
FROM departments d
LEFT JOIN classes  c ON c.department_id = d.id
LEFT JOIN students s ON s.class_id = c.id
LEFT JOIN trainers t ON t.department_id = d.id
GROUP BY d.id, d.name
ORDER BY d.name;

-- Student attendance summary per unit
CREATE OR REPLACE VIEW v_student_attendance_summary AS
SELECT
    s.id          AS student_id,
    s.admission_number,
    s.full_name,
    u.id          AS unit_id,
    u.code        AS unit_code,
    u.name        AS unit_name,
    COUNT(*)      AS total_records,
    SUM(CASE WHEN a.status = 'present' THEN 1 ELSE 0 END) AS attended,
    MAX(a.attendance_date) AS last_update
FROM attendance a
JOIN students s ON s.id = a.student_id
JOIN units    u ON u.id = a.unit_id
GROUP BY s.id, s.admission_number, s.full_name, u.id, u.code, u.name;

-- ────────────────────────────────────────────────────────────
-- 14. INDEXES FOR PERFORMANCE
-- ────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_attendance_student   ON attendance(student_id);
CREATE INDEX IF NOT EXISTS idx_attendance_unit      ON attendance(unit_id);
CREATE INDEX IF NOT EXISTS idx_attendance_trainer   ON attendance(trainer_id);
CREATE INDEX IF NOT EXISTS idx_attendance_week_year ON attendance(week, year, term);
CREATE INDEX IF NOT EXISTS idx_students_class       ON students(class_id);
CREATE INDEX IF NOT EXISTS idx_classes_dept         ON classes(department_id);
CREATE INDEX IF NOT EXISTS idx_trainers_dept        ON trainers(department_id);
CREATE INDEX IF NOT EXISTS idx_user_profiles_role   ON user_profiles(role);
CREATE INDEX IF NOT EXISTS idx_user_profiles_dept   ON user_profiles(department_id);
CREATE INDEX IF NOT EXISTS idx_system_logs_actor    ON system_logs(actor_id);
CREATE INDEX IF NOT EXISTS idx_system_logs_created  ON system_logs(created_at DESC);
