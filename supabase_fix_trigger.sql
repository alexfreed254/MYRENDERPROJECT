-- ============================================================
-- FIX: Safe auth trigger + Super Admin setup
-- Run this entire file in Supabase SQL Editor
-- ============================================================

-- ── 1. Replace the trigger with a crash-safe version ─────────

CREATE OR REPLACE FUNCTION handle_new_auth_user()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_role      TEXT;
    v_full_name TEXT;
BEGIN
    v_role := COALESCE(
        NULLIF(TRIM(NEW.raw_user_meta_data->>'role'), ''),
        'student'
    );

    IF v_role NOT IN ('super_admin','dept_admin','trainer','student') THEN
        v_role := 'student';
    END IF;

    v_full_name := COALESCE(
        NULLIF(TRIM(NEW.raw_user_meta_data->>'full_name'), ''),
        NEW.email
    );

    INSERT INTO public.user_profiles (id, full_name, role, is_active)
    VALUES (NEW.id, v_full_name, v_role, TRUE)
    ON CONFLICT (id) DO NOTHING;

    RETURN NEW;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'handle_new_auth_user failed for %: %', NEW.id, SQLERRM;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION handle_new_auth_user();


-- ── 2. Backfill any existing auth users missing a profile ────
--    (Runs safely even if user_profiles is empty)

INSERT INTO public.user_profiles (id, full_name, role, is_active)
SELECT
    u.id,
    COALESCE(NULLIF(TRIM(u.raw_user_meta_data->>'full_name'),''), u.email),
    'student',
    TRUE
FROM auth.users u
WHERE NOT EXISTS (
    SELECT 1 FROM public.user_profiles p WHERE p.id = u.id
);


-- ── 3. Promote a user to super_admin ─────────────────────────
--    Replace the email below with your actual admin email,
--    then uncomment and run.

/*
UPDATE public.user_profiles
SET    role = 'super_admin', is_active = TRUE
WHERE  id = (
    SELECT id FROM auth.users
    WHERE  email = 'your-admin@email.com'
    LIMIT  1
);
*/


-- ── 4. Verify ────────────────────────────────────────────────

SELECT
    u.email,
    p.full_name,
    p.role,
    p.is_active
FROM auth.users u
JOIN public.user_profiles p ON p.id = u.id
ORDER BY p.role, u.email;
