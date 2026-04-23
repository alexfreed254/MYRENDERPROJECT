"""
app.py — Flask application entry point.
Hosted on Render.  Database + Auth via Supabase.
"""

import os
from datetime import timedelta
from flask import Flask
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]

# ── Session / Cookie config ───────────────────────────────────────────────────
app.config["SESSION_COOKIE_SAMESITE"]    = "None"
app.config["SESSION_COOKIE_SECURE"]      = True
app.config["SESSION_COOKIE_HTTPONLY"]    = True
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=1)

# ── Reverse-proxy support (Render sits behind a load balancer) ────────────────
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# ── Refresh JWT before every request ─────────────────────────────────────────
from auth_utils import refresh_session_if_needed

@app.before_request
def before_request():
    refresh_session_if_needed()

# ── Blueprints ────────────────────────────────────────────────────────────────
from routes.main       import main_bp
from routes.auth       import auth_bp
from routes.super_admin import super_admin_bp
from routes.dept_admin  import dept_admin_bp
from routes.lecturer   import lecturer_bp
from routes.student    import student_bp

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
        "LOGO_URL":      "/static/assets/THIKATTILOGO.jpg",
        "current_user":  current_user(),
    }

# ── Error handlers ────────────────────────────────────────────────────────────
@app.errorhandler(403)
def forbidden(e):
    return "<h2>403 — Access Denied</h2><p>You do not have permission to view this page.</p><a href='/'>Go Home</a>", 403

@app.errorhandler(404)
def not_found(e):
    return "<h2>404 — Page Not Found</h2><a href='/'>Go Home</a>", 404

@app.errorhandler(500)
def server_error(e):
    return f"<h2>500 — Server Error</h2><p>{e}</p>", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
