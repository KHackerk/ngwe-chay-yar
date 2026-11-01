"""Ngwe Chay Yar — session / auth helpers shared by app and admin routes."""
from functools import wraps

from flask import jsonify, redirect, request, session, url_for

import db


def wants_json():
    if request.path.startswith("/api/"):
        return True
    accept = request.headers.get("Accept", "") or ""
    return "application/json" in accept and "text/html" not in accept


def current_user():
    """Return the signed-in user's row dict, or None.

    A suspended user is treated as logged out for app use — except an
    admin account, which is never locked out by the suspended flag.
    """
    uid = session.get("uid")
    if not uid:
        return None
    user = db.q1("SELECT * FROM users WHERE id = ?", (uid,))
    if not user:
        return None
    if user.get("suspended") and user.get("role") != "admin":
        return None
    return user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            if wants_json():
                return jsonify({"error": "auth"}), 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            if wants_json():
                return jsonify({"error": "auth"}), 401
            return redirect("/admin/login")
        if user.get("role") != "admin":
            if wants_json():
                return jsonify({"error": "forbidden"}), 403
            return redirect("/admin/login")
        return view(*args, **kwargs)

    return wrapped
