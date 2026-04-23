"""
db.py — Supabase client factory.

NOTE: We do NOT use lru_cache here. Supabase-py clients can go stale
across requests (especially the auth state). Fresh clients are cheap
to create and avoid hard-to-debug connection errors.
"""

import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL: str  = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY: str  = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def get_anon_client() -> Client:
    """Anon client — honours RLS. Use for public/unauthenticated calls."""
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


def get_service_client() -> Client:
    """
    Service-role client — bypasses RLS.
    Use ONLY for server-side admin operations and audit logging.
    Never expose responses from this client directly to the browser.
    """
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def get_user_client(access_token: str) -> Client:
    """
    Returns a Supabase client with the user's JWT set.
    RLS policies fire using this user's identity.
    """
    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    # set_session requires both access and refresh tokens
    # We pass a dummy refresh token — we only need the access token for queries
    try:
        client.postgrest.auth(access_token)
    except Exception:
        pass
    return client
