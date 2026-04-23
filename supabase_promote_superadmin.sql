-- ============================================================
-- Promote your account to super_admin
-- Run in Supabase SQL Editor
-- Replace the email with YOUR actual email address
-- ============================================================

UPDATE public.user_profiles
SET 
    role      = 'super_admin',
    is_active = TRUE
WHERE id = (
    SELECT id 
    FROM auth.users 
    WHERE email = 'YOUR_EMAIL_HERE'   -- ← change this
    LIMIT 1
);

-- Verify it worked:
SELECT 
    u.email,
    p.role,
    p.is_active
FROM auth.users u
JOIN public.user_profiles p ON p.id = u.id
ORDER BY u.created_at DESC;
