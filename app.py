"""
app.py — Flask application entry point.
Hosted on Render. Database + Auth via Supabase.
"""

import os
import traceback
from datetime import timedelta
from flask import Flask, render_template
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

app = Flask(__name__)
# Fallback secret key for local dev — always set SECRET_KEY in production
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")

# ── Session / Cookie config ───────────────────────────────────────────────────
app.config["SESSION_COOKIE_SAMESITE"]    = "Lax"   # "None" requires HTTPS everywhere
app.config["SESSION_COOKIE_SECURE"]      = False    # Set True when behind HTTPS on Render
app.config["SESSION_COOKIE_HTTPONLY"]    = True
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=1)

# ── Reverse-proxy support (Render sits behind a load balancer) ────────────────
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# ── Refresh JWT before every request ─────────────────────────────────────────
from auth_utils import refresh_session_if_needed

@app.before_request
def before_request():
    try:
        refresh_session_if_needed()
    except Exception:
        pass  # never block a request due to token refresh failure

# ── Blueprints ────────────────────────────────────────────────────────────────
from routes.main        import main_bp
from routes.auth        import auth_bp
from routes.super_admin import super_admin_bp
from routes.dept_admin  import dept_admin_bp
from routes.lecturer    import lecturer_bp
from routes.student     import student_bp

app.register_blueprint(main_bp)
app.register_blueprint(auth_bp,        url_prefix="/auth")
app.register_blueprint(super_admin_bp, url_prefix="/super-admin")
app.register_blueprint(dept_admin_bp,  url_prefix="/dept-admin")
app.register_blueprint(lecturer_bp,    url_prefix="/lecturer")
app.register_blueprint(student_bp,     url_prefix="/student")

# ── Template globals ──────────────────────────────────────────────────────────
@app.context_processor
def inject_globals():
    from auth_utils import current_user
    return {
        "LOGO_URL":     "/static/assets/THIKATTILOGO.jpg",
        "current_user": current_user(),
    }

# ── Jinja2 filter: convert UTC ISO string → EAT display string ───────────────
import pytz
from datetime import datetime as _dt

_EAT = pytz.timezone('Africa/Nairobi')

@app.template_filter('to_eat')
def to_eat_filter(value, fmt='%d %b %Y %H:%M'):
    """
    Convert a UTC ISO datetime string (from Supabase) to EAT (Africa/Nairobi).
    Usage in templates:  {{ r.attendance_date | to_eat }}
                         {{ r.created_at | to_eat('%d %b %Y') }}
    Returns '—' if value is falsy or unparseable.
    """
    if not value:
        return '—'
    try:
        # Handle both 'Z' suffix and '+00:00' offset
        s = str(value).replace('Z', '+00:00')
        # Try with microseconds first, then without
        for fmt_parse in ('%Y-%m-%dT%H:%M:%S.%f%z', '%Y-%m-%dT%H:%M:%S%z',
                          '%Y-%m-%d %H:%M:%S.%f%z', '%Y-%m-%d %H:%M:%S%z'):
            try:
                utc_dt = _dt.strptime(s, fmt_parse)
                eat_dt = utc_dt.astimezone(_EAT)
                return eat_dt.strftime(fmt)
            except ValueError:
                continue
        # Fallback: treat as naive local, just slice
        return str(value)[:16].replace('T', ' ')
    except Exception:
        return str(value)[:16].replace('T', ' ')

# ── Error handlers ────────────────────────────────────────────────────────────
@app.errorhandler(400)
def bad_request(e):
    return render_template("errors/400.html"), 400

@app.errorhandler(403)
def forbidden(e):
    return render_template("errors/403.html"), 403

@app.errorhandler(404)
def not_found(e):
    return render_template("errors/404.html"), 404

@app.errorhandler(500)
def server_error(e):
    # Print full traceback to Render logs
    traceback.print_exc()
    return render_template("errors/500.html", error=str(e)), 500

@app.errorhandler(Exception)
def unhandled_exception(e):
    traceback.print_exc()
    return render_template("errors/500.html", error=str(e)), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
