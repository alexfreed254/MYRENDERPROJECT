"""
routes/main.py — Public landing page.
"""

from flask import Blueprint, render_template, redirect, url_for
from auth_utils import current_user, is_authenticated

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    # If already logged in, redirect to the correct dashboard
    if is_authenticated():
        user = current_user()
        role = user.get("role", "")
        if role == "super_admin":
            return redirect(url_for("super_admin.welcome"))
        elif role == "dept_admin":
            return redirect(url_for("dept_admin.welcome"))
        elif role == "trainer":
            return redirect(url_for("lecturer.dashboard"))
        elif role == "student":
            return redirect(url_for("student.dashboard"))
    return render_template("main/index.html")
