"""
db.py — Supabase client factory.

Two clients are exposed:
  • get_anon_client()    — uses the anon/public key (respects RLS)
  • get_service_client() — uses the service_role key (bypasses RLS, for
                           admin operations and audit logging only)

The user-scoped client (with the JWT from the logged-in user) is built
on-demand in auth_utils.py so that RLS policies fire correctly.
"""

import os
from functools import lru_cache
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY: str = os.environ["SUPABASE_ANON_KEY"]
SUPABASE_SERVICE_KEY: str = os.environ["SUPABASE_SERVICE_ROLE_KEY"]


@lru_cache(maxsize=1)
def get_anon_client() -> Client:
    """Anon client — honours RLS. Use for public/unauthenticated calls."""
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


@lru_cache(maxsize=1)
def get_service_client() -> Client:
    """
    Service-role client — bypasses RLS.
    Use ONLY for:
      - Creating / disabling auth users (super_admin actions)
      - Writing audit logs
      - Backend-only data validation
    Never expose this client's responses directly to the browser.
    """
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def get_user_client(access_token: str) -> Client:
    """
    Returns a Supabase client authenticated as the given user.
    RLS policies will fire using this user's JWT.
    """
    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    client.auth.set_session(access_token, "")   # sets Authorization header
    return client
