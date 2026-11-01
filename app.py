"""Ngwe Chay Yar — Flask backend core (auth, JSON API, uploads, SPA shell).

Real-use app: no demo or seeded content anywhere. A newly registered
account starts completely empty; the FIRST registered user becomes admin.
Amounts are plain integers (kyat) — no currency text is ever added.
"""
import json
import os
import re
import uuid
from datetime import date, datetime

from flask import (
    Flask,
    abort,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import db
from common import current_user, login_required

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_ROOT, "data")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")

MAX_UPLOAD = 10 * 1024 * 1024  # 10 MB per file

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024

db.init_db()

# ---------------------------------------------------------------------------
# Localised auth strings (EN + MY). Default language: Myanmar.
# ---------------------------------------------------------------------------

AUTH_STRINGS = {
    "en": {
        "login_title": "Welcome back",
        "login_sub": "Sign in to Ngwe Chay Yar",
        "register_title": "Create account",
        "register_sub": "Start tracking your money today",
        "name": "Full Name",
        "email": "Email",
        "password": "Password",
        "confirm_password": "Confirm Password",
        "terms": "I agree to the Terms & Privacy Policy",
        "login_btn": "Sign In",
        "register_btn": "Create Account",
        "no_account": "Don't have an account?",
        "have_account": "Already have an account?",
        "create_one": "Create one",
        "sign_in": "Sign in",
        "invalid": "Invalid email or password.",
        "suspended": "Your account has been suspended. Please contact support.",
        "email_taken": "An account with this email already exists.",
        "password_mismatch": "Passwords do not match.",
        "terms_required": "Please accept the Terms & Privacy Policy.",
        "fill_all": "Please fill in all fields.",
        "short_password": "Password must be at least 6 characters.",
        "bad_email": "Please enter a valid email address.",
        "closed_title": "Registration is closed",
        "closed_msg": "New account registration is currently closed. Please check back later.",
        "admin_note": "Admin sign in",
        "maintenance_title": "Under maintenance",
        "maintenance_msg": "Ngwe Chay Yar is temporarily down for maintenance. Please try again soon.",
    },
    "my": {
        "login_title": "ပြန်လည်ကြိုဆိုပါတယ်",
        "login_sub": "Ngwe Chay Yar သို့ ဝင်ရောက်ပါ",
        "register_title": "အကောင့်ဖွင့်ရန်",
        "register_sub": "ဒီနေ့ကစပြီး သင့်ငွေကို စီမံလိုက်ပါ",
        "name": "အမည်အပြည့်အစုံ",
        "email": "အီးမေးလ်",
        "password": "စကားဝှက်",
        "confirm_password": "စကားဝှက် အတည်ပြုပါ",
        "terms": "စည်းကမ်းချက်များနှင့် ကိုယ်ရေးမူဝါဒကို သဘောတူပါသည်",
        "login_btn": "ဝင်ရောက်မည်",
        "register_btn": "အကောင့်ဖွင့်မည်",
        "no_account": "အကောင့် မရှိသေးဘူးလား?",
        "have_account": "အကောင့် ရှိပြီးသားလား?",
        "create_one": "အသစ်ဖွင့်ရန်",
        "sign_in": "ဝင်ရောက်ရန်",
        "invalid": "အီးမေးလ် သို့မဟုတ် စကားဝှက် မမှန်ပါ။",
        "suspended": "သင့်အကောင့်ကို ရပ်ဆိုင်းထားပါသည်။ အကူအညီဌာနကို ဆက်သွယ်ပါ။",
        "email_taken": "ဤအီးမေးလ်ဖြင့် အကောင့် ရှိပြီးသားဖြစ်ပါသည်။",
        "password_mismatch": "စကားဝှက်နှစ်ခု မတူညီပါ။",
        "terms_required": "စည်းကမ်းချက်များကို သဘောတူရန် လိုအပ်ပါသည်။",
        "fill_all": "အချက်အလက်အားလုံး ဖြည့်စွက်ပါ။",
        "short_password": "စကားဝှက်သည် အနည်းဆုံး စာလုံး ၆ လုံး ရှိရပါမည်။",
        "bad_email": "မှန်ကန်သော အီးမေးလ် လိပ်စာ ထည့်ပါ။",
        "closed_title": "အကောင့်ဖွင့်ခြင်း ပိတ်ထားပါသည်",
        "closed_msg": "အကောင့်အသစ် ဖွင့်ခြင်းကို ယာယီပိတ်ထားပါသည်။ နောက်မှ ပြန်စမ်းကြည့်ပါ။",
        "admin_note": "Admin ဝင်ရောက်ရန်",
        "maintenance_title": "ပြုပြင်ထိန်းသိမ်းနေပါသည်",
        "maintenance_msg": "Ngwe Chay Yar ကို ယာယီပြုပြင်နေပါသည်။ မကြာမီ ပြန်စမ်းကြည့်ပါ။",
    },
}


def _lang():
    lang = request.args.get("lang") or request.cookies.get("lang") or "my"
    return lang if lang in AUTH_STRINGS else "my"


def _strings():
    return AUTH_STRINGS[_lang()]


def _with_lang_cookie(resp):
    lang = request.args.get("lang")
    if lang in AUTH_STRINGS:
        resp.set_cookie("lang", lang, max_age=365 * 24 * 3600, samesite="Lax")
    return resp


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _today():
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# Maintenance middleware
# ---------------------------------------------------------------------------

@app.before_request
def _maintenance_gate():
    path = request.path
    if path.startswith("/static/") or path in ("/login", "/logout"):
        return None
    if path.startswith("/admin"):
        return None
    if path != "/" and not path.startswith("/api/"):
        return None
    try:
        if db.get_setting("maintenance") != "1":
            return None
    except Exception:
        return None
    user = current_user()
    if user and user.get("role") == "admin":
        return None
    if path.startswith("/api/"):
        return jsonify({"error": "maintenance"}), 503
    return make_response(render_template("maintenance.html", s=_strings()), 503)


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    s = _strings()
    error = None
    admin_note = request.args.get("admin") == "1"
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = db.q1("SELECT * FROM users WHERE email = ?", (email,))
        if not user or not check_password_hash(user["password_hash"], password):
            error = s["invalid"]
        elif user.get("suspended") and user.get("role") != "admin":
            error = s["suspended"]
        else:
            session.clear()
            session["uid"] = user["id"]
            nxt = request.args.get("next") or "/"
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = "/"
            return _with_lang_cookie(make_response(redirect(nxt)))
    resp = make_response(
        render_template("login.html", s=s, error=error, lang=_lang(),
                        admin_note=admin_note)
    )
    return _with_lang_cookie(resp)


@app.route("/register", methods=["GET", "POST"])
def register():
    s = _strings()
    if db.get_setting("registration_open") == "0":
        resp = make_response(
            render_template("register.html", s=s, closed=True, error=None,
                            lang=_lang(), form={})
        )
        return _with_lang_cookie(resp)
    error = None
    form = {}
    if request.method == "POST":
        form = {
            "name": (request.form.get("name") or "").strip(),
            "email": (request.form.get("email") or "").strip().lower(),
        }
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        terms = request.form.get("terms")
        if not form["name"] or not form["email"] or not password:
            error = s["fill_all"]
        elif not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", form["email"]):
            error = s["bad_email"]
        elif len(password) < 6:
            error = s["short_password"]
        elif password != confirm:
            error = s["password_mismatch"]
        elif not terms:
            error = s["terms_required"]
        elif db.q1("SELECT id FROM users WHERE email = ?", (form["email"],)):
            error = s["email_taken"]
        else:
            count = db.q1("SELECT COUNT(*) AS n FROM users")
            role = "admin" if (count and count["n"] == 0) else "user"
            uid = db.insert(
                "INSERT INTO users (name, email, password_hash, role, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (form["name"], form["email"],
                 generate_password_hash(password), role, _now()),
            )
            session.clear()
            session["uid"] = uid
            return _with_lang_cookie(make_response(redirect("/")))
    resp = make_response(
        render_template("register.html", s=s, closed=False, error=error,
                        lang=_lang(), form=form)
    )
    return _with_lang_cookie(resp)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

def _txn(row):
    return {
        "id": row["id"],
        "kind": row["kind"],
        "amount": row["amount"],
        "category": row["category"],
        "txn_date": row["txn_date"],
        "note": row.get("note") or "",
        "account_id": row.get("account_id"),
    }


def _account_deltas(uid):
    """Per-account signed sum of linked transactions (income +, expense -)."""
    rows = db.q(
        "SELECT account_id, "
        "SUM(CASE WHEN kind = 'income' THEN amount ELSE -amount END) "
        "AS delta FROM transactions WHERE user_id = ? "
        "AND account_id IS NOT NULL GROUP BY account_id", (uid,))
    return {r["account_id"]: int(r["delta"] or 0) for r in rows}


def _account(row, delta=0):
    """accounts.balance in the DB is the OPENING balance (what the user
    typed when creating/editing the account). The API exposes `balance`
    as the computed CURRENT balance (opening + linked income - linked
    expenses) and `opening_balance` for the edit form."""
    opening = int(row["balance"] or 0)
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "brand": row.get("brand") or "",
        "balance": opening + int(delta or 0),
        "opening_balance": opening,
        "note": row.get("note") or "",
    }


def _asset(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "purchase_price": row["purchase_price"],
        "current_value": row["current_value"],
        "location": row.get("location") or "",
        "note": row.get("note") or "",
    }


def _debt(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "amount": row["amount"],
        "note": row.get("note") or "",
    }


def _time_of(created_at):
    if created_at and "T" in created_at:
        return created_at.split("T", 1)[1][:5]
    if created_at and " " in created_at:
        return created_at.split(" ", 1)[1][:5]
    return ""


def _msg(row, my_id):
    card = {}
    if row.get("card"):
        try:
            card = json.loads(row["card"])
        except (ValueError, TypeError):
            card = {}
    return {
        "id": row["id"],
        "from": "me" if row["sender_id"] == my_id else "them",
        "type": row["type"],
        "text": row.get("text") or "",
        "name": row.get("file_name") or "",
        "size": row.get("file_size") or 0,
        "url": ("/uploads/" + row["file_path"]) if row.get("file_path") else "",
        "income": card.get("income"),
        "expense": card.get("expense"),
        "leftover": card.get("leftover"),
        "items": card.get("items"),
        "txn_date": card.get("txn_date", ""),
        "scope": card.get("scope"),
        "month": card.get("month", ""),
        "time": _time_of(row.get("created_at") or ""),
        "ts": row.get("created_at") or "",
    }


def _support_msg(row):
    return {
        "id": row["id"],
        "from": "me" if row["sender"] == "user" else "admin",
        "type": row["type"],
        "text": row.get("text") or "",
        "name": row.get("file_name") or "",
        "size": row.get("file_size") or 0,
        "url": ("/uploads/" + row["file_path"]) if row.get("file_path") else "",
        "income": None,
        "expense": None,
        "leftover": None,
        "items": None,
        "txn_date": "",
        "time": _time_of(row.get("created_at") or ""),
        "ts": row.get("created_at") or "",
        "pinned": bool(row.get("pinned")),
    }


def _conversations(my_id):
    rows = db.q(
        "SELECT DISTINCT CASE WHEN sender_id = ? THEN recipient_id "
        "ELSE sender_id END AS other_id FROM messages "
        "WHERE sender_id = ? OR recipient_id = ?",
        (my_id, my_id, my_id),
    )
    convs = []
    for r in rows:
        other = db.q1("SELECT * FROM users WHERE id = ?", (r["other_id"],))
        if not other or not other.get("username"):
            continue
        last = db.q1(
            "SELECT * FROM messages WHERE "
            "((sender_id = ? AND recipient_id = ?) OR "
            " (sender_id = ? AND recipient_id = ?)) "
            "ORDER BY id DESC LIMIT 1",
            (my_id, other["id"], other["id"], my_id),
        )
        convs.append({
            "username": other["username"],
            "name": other["name"],
            "last": _msg(last, my_id) if last else None,
            "_last_id": last["id"] if last else 0,
        })
    convs.sort(key=lambda c: c["_last_id"], reverse=True)
    for c in convs:
        c.pop("_last_id", None)
    return convs


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _save_upload(file_storage, subdir):
    """Save an uploaded file under data/uploads/<subdir>/.

    Returns (relpath, filename, size, mime) where relpath is relative to
    data/uploads/ (this is what gets stored in file_path and appended to
    /uploads/). Raises ValueError('too_large') when over 10 MB.
    """
    file_storage.seek(0, os.SEEK_END)
    size = file_storage.tell()
    file_storage.seek(0)
    if size > MAX_UPLOAD:
        raise ValueError("too_large")
    raw_name = secure_filename(file_storage.filename or "file") or "file"
    stored = uuid.uuid4().hex[:10] + "_" + raw_name
    dest_dir = os.path.join(UPLOADS_DIR, subdir)
    os.makedirs(dest_dir, exist_ok=True)
    file_storage.save(os.path.join(dest_dir, stored))
    relpath = subdir.strip("/") + "/" + stored
    return relpath, (file_storage.filename or raw_name), size, (
        file_storage.mimetype or "")


def _file_type(mime):
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    return "file"


# ---------------------------------------------------------------------------
# SPA shell
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# JSON API — all endpoints require login (401 {"error":"auth"} otherwise)
# ---------------------------------------------------------------------------

@app.route("/api/state")
@login_required
def api_state():
    user = current_user()
    uid = user["id"]
    txns = db.q(
        "SELECT * FROM transactions WHERE user_id = ? "
        "ORDER BY txn_date DESC, id DESC", (uid,))
    accounts = db.q("SELECT * FROM accounts WHERE user_id = ? ORDER BY id", (uid,))
    assets = db.q("SELECT * FROM assets WHERE user_id = ? ORDER BY id", (uid,))
    debts = db.q("SELECT * FROM debts WHERE user_id = ? ORDER BY id", (uid,))
    support = db.q(
        "SELECT * FROM support_messages WHERE user_id = ? ORDER BY id", (uid,))
    deltas = _account_deltas(uid)
    return jsonify({
        "profile": {
            "name": user["name"],
            "email": user["email"],
            "username": user.get("username"),
            "photo": bool(user.get("photo")),
            "role": user["role"],
        },
        "settings": {
            "lang": user.get("lang") or "my",
            "theme": user.get("theme") or "light",
        },
        "accounts": [_account(r, deltas.get(r["id"], 0)) for r in accounts],
        "assets": [_asset(r) for r in assets],
        "debts": [_debt(r) for r in debts],
        "transactions": [_txn(r) for r in txns],
        "conversations": _conversations(uid),
        "support": {"messages": [_support_msg(r) for r in support]},
        "server_today": _today(),
    })


@app.route("/api/transactions", methods=["POST"])
@login_required
def api_add_transaction():
    user = current_user()
    data = request.get_json(silent=True) or {}
    kind = data.get("kind")
    amount = _int(data.get("amount"), 0)
    if kind not in ("income", "expense") or amount <= 0:
        return jsonify({"error": "invalid"}), 400
    # Wallet linkage: account_id is optional (unlinked transactions still
    # count toward totals/charts but move no wallet), but when present it
    # MUST be one of the session user's own accounts.
    account_id = data.get("account_id")
    if account_id is not None:
        account_id = _int(account_id, -1)
        owned = db.q1(
            "SELECT id FROM accounts WHERE id = ? AND user_id = ?",
            (account_id, user["id"]))
        if not owned:
            return jsonify({"error": "bad_account"}), 400
    txn_date = (data.get("txn_date") or _today())[:10]
    new_id = db.insert(
        "INSERT INTO transactions (user_id, kind, amount, category, txn_date, "
        "note, account_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user["id"], kind, amount, str(data.get("category") or ""),
         txn_date, str(data.get("note") or ""), account_id, _now()),
    )
    return jsonify({"id": new_id})


@app.route("/api/transactions/<int:txn_id>", methods=["DELETE"])
@login_required
def api_delete_transaction(txn_id):
    user = current_user()
    row = db.q1("SELECT id FROM transactions WHERE id = ? AND user_id = ?",
                (txn_id, user["id"]))
    if not row:
        return jsonify({"error": "not_found"}), 404
    db.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?",
               (txn_id, user["id"]))
    return jsonify({"ok": True})


@app.route("/api/accounts", methods=["POST"])
@login_required
def api_add_account():
    user = current_user()
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "invalid"}), 400
    new_id = db.insert(
        "INSERT INTO accounts (user_id, name, type, brand, balance, note) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user["id"], name, str(data.get("type") or ""),
         str(data.get("brand") or ""), _int(data.get("balance"), 0),
         str(data.get("note") or "")),
    )
    return jsonify({"id": new_id})


@app.route("/api/accounts/<int:item_id>", methods=["PUT"])
@login_required
def api_update_account(item_id):
    """Edit an account. `balance` here is the OPENING balance; the current
    balance shown everywhere recomputes from it plus linked transactions."""
    user = current_user()
    row = db.q1("SELECT * FROM accounts WHERE id = ? AND user_id = ?",
                (item_id, user["id"]))
    if not row:
        return jsonify({"error": "not_found"}), 404
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", row["name"]) or "").strip()
    if not name:
        return jsonify({"error": "invalid"}), 400
    opening = int(row["balance"] or 0)
    if "balance" in data:
        opening = _int(data.get("balance"), opening)
    db.execute(
        "UPDATE accounts SET name = ?, type = ?, brand = ?, balance = ?, "
        "note = ? WHERE id = ? AND user_id = ?",
        (name, str(data.get("type", row["type"]) or ""),
         str(data.get("brand", row.get("brand") or "") or ""),
         opening,
         str(data.get("note", row.get("note") or "") or ""),
         item_id, user["id"]),
    )
    return jsonify({"ok": True})


@app.route("/api/accounts/<int:item_id>", methods=["DELETE"])
@login_required
def api_delete_account(item_id):
    """Deleting a wallet UNLINKS its transactions (they keep counting in
    totals/charts/history, they just move no wallet) and then removes the
    wallet itself. The wallet's computed balance leaves net worth with it."""
    user = current_user()
    row = db.q1("SELECT id FROM accounts WHERE id = ? AND user_id = ?",
                (item_id, user["id"]))
    if not row:
        return jsonify({"error": "not_found"}), 404
    db.execute(
        "UPDATE transactions SET account_id = NULL "
        "WHERE account_id = ? AND user_id = ?", (item_id, user["id"]))
    db.execute("DELETE FROM accounts WHERE id = ? AND user_id = ?",
               (item_id, user["id"]))
    return jsonify({"ok": True})


@app.route("/api/assets", methods=["POST"])
@login_required
def api_add_asset():
    user = current_user()
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "invalid"}), 400
    new_id = db.insert(
        "INSERT INTO assets (user_id, name, type, purchase_price, "
        "current_value, location, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user["id"], name, str(data.get("type") or ""),
         _int(data.get("purchase_price"), 0),
         _int(data.get("current_value"), 0),
         str(data.get("location") or ""), str(data.get("note") or "")),
    )
    return jsonify({"id": new_id})


@app.route("/api/assets/<int:item_id>", methods=["DELETE"])
@login_required
def api_delete_asset(item_id):
    user = current_user()
    row = db.q1("SELECT id FROM assets WHERE id = ? AND user_id = ?",
                (item_id, user["id"]))
    if not row:
        return jsonify({"error": "not_found"}), 404
    db.execute("DELETE FROM assets WHERE id = ? AND user_id = ?",
               (item_id, user["id"]))
    return jsonify({"ok": True})


@app.route("/api/debts", methods=["POST"])
@login_required
def api_add_debt():
    user = current_user()
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "invalid"}), 400
    new_id = db.insert(
        "INSERT INTO debts (user_id, name, amount, note) VALUES (?, ?, ?, ?)",
        (user["id"], name, _int(data.get("amount"), 0),
         str(data.get("note") or "")),
    )
    return jsonify({"id": new_id})


@app.route("/api/debts/<int:item_id>", methods=["DELETE"])
@login_required
def api_delete_debt(item_id):
    user = current_user()
    row = db.q1("SELECT id FROM debts WHERE id = ? AND user_id = ?",
                (item_id, user["id"]))
    if not row:
        return jsonify({"error": "not_found"}), 404
    db.execute("DELETE FROM debts WHERE id = ? AND user_id = ?",
               (item_id, user["id"]))
    return jsonify({"ok": True})


@app.route("/api/profile", methods=["POST"])
@login_required
def api_profile():
    user = current_user()
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "invalid"}), 400
    db.execute("UPDATE users SET name = ? WHERE id = ?", (name, user["id"]))
    return jsonify({"ok": True})


@app.route("/api/profile/photo", methods=["POST"])
@login_required
def api_profile_photo_upload():
    user = current_user()
    file_storage = request.files.get("photo")
    if not file_storage or not file_storage.filename:
        return jsonify({"error": "no_file"}), 400
    try:
        from PIL import Image

        img = Image.open(file_storage.stream)
        img.load()
    except Exception:
        return jsonify({"error": "not_image"}), 400
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    img = img.convert("RGB")
    img.thumbnail((256, 256))
    relpath = "photos/%d.jpg" % user["id"]
    img.save(os.path.join(DATA_DIR, relpath), "JPEG", quality=88)
    db.execute("UPDATE users SET photo = ? WHERE id = ?",
               (relpath, user["id"]))
    return jsonify({"ok": True, "url": "/api/profile/photo"})


@app.route("/api/profile/photo", methods=["GET"])
@login_required
def api_profile_photo_get():
    user = current_user()
    if not user.get("photo"):
        abort(404)
    path = os.path.join(DATA_DIR, user["photo"])
    if not os.path.isfile(path):
        abort(404)
    return send_file(path)


@app.route("/api/profile/photo", methods=["DELETE"])
@login_required
def api_profile_photo_delete():
    user = current_user()
    if user.get("photo"):
        path = os.path.join(DATA_DIR, user["photo"])
        if os.path.isfile(path):
            os.remove(path)
        db.execute("UPDATE users SET photo = NULL WHERE id = ?", (user["id"],))
    return jsonify({"ok": True})


@app.route("/api/users/<username>/photo")
@login_required
def api_user_photo(username):
    other = db.q1("SELECT photo FROM users WHERE username = ?", (username,))
    if not other or not other.get("photo"):
        abort(404)
    path = os.path.join(DATA_DIR, other["photo"])
    if not os.path.isfile(path):
        abort(404)
    return send_file(path)


@app.route("/api/username", methods=["POST"])
@login_required
def api_username():
    user = current_user()
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    if not re.match(r"^[a-z0-9_]{3,20}$", username):
        return jsonify({"error": "invalid"}), 400
    taken = db.q1("SELECT id FROM users WHERE username = ?", (username,))
    if taken and taken["id"] != user["id"]:
        return jsonify({"error": "taken"}), 409
    db.execute("UPDATE users SET username = ? WHERE id = ?",
               (username, user["id"]))
    return jsonify({"ok": True, "username": username})


@app.route("/api/settings", methods=["POST"])
@login_required
def api_settings():
    user = current_user()
    data = request.get_json(silent=True) or {}
    lang = data.get("lang")
    theme = data.get("theme")
    if lang is not None:
        if lang not in ("my", "en", "zh", "th"):
            return jsonify({"error": "invalid"}), 400
        db.execute("UPDATE users SET lang = ? WHERE id = ?", (lang, user["id"]))
    if theme is not None:
        if theme not in ("light", "dark", "day", "night"):
            return jsonify({"error": "invalid"}), 400
        db.execute("UPDATE users SET theme = ? WHERE id = ?",
                   (theme, user["id"]))
    return jsonify({"ok": True})


@app.route("/api/users/search")
@login_required
def api_users_search():
    user = current_user()
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify([])
    like = "%" + query + "%"
    rows = db.q(
        "SELECT username, name FROM users WHERE username IS NOT NULL "
        "AND id != ? AND (username LIKE ? OR name LIKE ?) "
        "ORDER BY username LIMIT 20",
        (user["id"], like, like),
    )
    return jsonify([{"username": r["username"], "name": r["name"]}
                    for r in rows])


@app.route("/api/conversations")
@login_required
def api_conversations():
    user = current_user()
    return jsonify(_conversations(user["id"]))


@app.route("/api/conversations/<username>/messages")
@login_required
def api_conversation_messages(username):
    user = current_user()
    other = db.q1("SELECT * FROM users WHERE username = ?", (username,))
    if not other:
        return jsonify({"error": "not_found"}), 404
    after = _int(request.args.get("after"), 0)
    rows = db.q(
        "SELECT * FROM messages WHERE id > ? AND "
        "((sender_id = ? AND recipient_id = ?) OR "
        " (sender_id = ? AND recipient_id = ?)) ORDER BY id",
        (after, user["id"], other["id"], other["id"], user["id"]),
    )
    return jsonify({"messages": [_msg(r, user["id"]) for r in rows]})


def _costs_card(uid, scope):
    """Build a share-costs card payload for one user, computed server-side.

    scope "day":   the server's today (txn_date == _today()).
    scope "month": the server's current calendar month
                   (substr(txn_date,1,7) == _today()[:7]).
    The date definitions match what /api/state exposes as server_today,
    which the SPA also uses for its Home/monthly figures — one source of
    truth (the server date) everywhere."""
    today = _today()
    if scope == "month":
        month = today[:7]
        rows = db.q(
            "SELECT kind, category, note, amount FROM transactions "
            "WHERE user_id = ? AND substr(txn_date, 1, 7) = ? ORDER BY id",
            (uid, month),
        )
        card = {"scope": "month", "month": month, "txn_date": today}
    else:
        rows = db.q(
            "SELECT kind, category, note, amount FROM transactions "
            "WHERE user_id = ? AND txn_date = ? ORDER BY id",
            (uid, today),
        )
        card = {"scope": "day", "txn_date": today}
    items = [
        {
            "kind": r["kind"],
            "category": r["category"],
            "note": r.get("note") or "",
            "amount": int(r["amount"]),
        }
        for r in rows
    ]
    income = sum(i["amount"] for i in items if i["kind"] == "income")
    expense = sum(i["amount"] for i in items if i["kind"] == "expense")
    card.update({
        "income": income,
        "expense": expense,
        "leftover": income - expense,
        "items": items,
    })
    return card


def _message_gate(user):
    """Shared gates for sending user-to-user messages. Returns error tuple
    or None."""
    if not user.get("username"):
        return jsonify({"error": "no_username"}), 403
    if db.get_setting("allow_messages") == "0":
        return jsonify({"error": "messages_off"}), 403
    return None


@app.route("/api/messages", methods=["POST"])
@login_required
def api_send_message():
    user = current_user()
    gate = _message_gate(user)
    if gate:
        return gate

    if request.files and request.files.get("file") is not None and \
            request.files["file"].filename:
        to_username = (request.form.get("to") or "").strip()
        other = db.q1("SELECT * FROM users WHERE username = ?", (to_username,))
        if not other or not other.get("username"):
            return jsonify({"error": "not_found"}), 404
        try:
            relpath, fname, size, mime = _save_upload(
                request.files["file"], str(user["id"]))
        except ValueError:
            return jsonify({"error": "too_large"}), 413
        new_id = db.insert(
            "INSERT INTO messages (sender_id, recipient_id, type, text, "
            "file_path, file_name, file_size, card, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user["id"], other["id"], _file_type(mime), "",
             relpath, fname, size, "", _now()),
        )
        row = db.q1("SELECT * FROM messages WHERE id = ?", (new_id,))
        return jsonify({"message": _msg(row, user["id"])})

    data = request.get_json(silent=True) or {}
    to_username = str(data.get("to") or "").strip()
    other = db.q1("SELECT * FROM users WHERE username = ?", (to_username,))
    if not other or not other.get("username"):
        return jsonify({"error": "not_found"}), 404

    if data.get("type") == "card":
        scope = "month" if data.get("scope") == "month" else "day"
        card = _costs_card(user["id"], scope)
        new_id = db.insert(
            "INSERT INTO messages (sender_id, recipient_id, type, text, "
            "file_path, file_name, file_size, card, created_at) "
            "VALUES (?, ?, 'card', '', '', '', 0, ?, ?)",
            (user["id"], other["id"], json.dumps(card), _now()),
        )
    else:
        text = str(data.get("text") or "").strip()
        if not text:
            return jsonify({"error": "invalid"}), 400
        new_id = db.insert(
            "INSERT INTO messages (sender_id, recipient_id, type, text, "
            "file_path, file_name, file_size, card, created_at) "
            "VALUES (?, ?, 'text', ?, '', '', 0, '', ?)",
            (user["id"], other["id"], text, _now()),
        )
    row = db.q1("SELECT * FROM messages WHERE id = ?", (new_id,))
    return jsonify({"message": _msg(row, user["id"])})


@app.route("/api/support", methods=["GET"])
@login_required
def api_support_get():
    user = current_user()
    rows = db.q(
        "SELECT * FROM support_messages WHERE user_id = ? ORDER BY id",
        (user["id"],),
    )
    return jsonify({"messages": [_support_msg(r) for r in rows]})


@app.route("/api/support", methods=["POST"])
@login_required
def api_support_post():
    user = current_user()
    if db.get_setting("allow_messages") == "0":
        return jsonify({"error": "messages_off"}), 403
    if request.files and request.files.get("file") is not None and \
            request.files["file"].filename:
        try:
            relpath, fname, size, mime = _save_upload(
                request.files["file"], str(user["id"]))
        except ValueError:
            return jsonify({"error": "too_large"}), 413
        new_id = db.insert(
            "INSERT INTO support_messages (user_id, sender, type, text, "
            "file_path, file_name, file_size, pinned, created_at) "
            "VALUES (?, 'user', ?, '', ?, ?, ?, 0, ?)",
            (user["id"], _file_type(mime), relpath, fname, size, _now()),
        )
    else:
        data = request.get_json(silent=True)
        if data is None:
            data = {"text": request.form.get("text")}
        text = str((data or {}).get("text") or "").strip()
        if not text:
            return jsonify({"error": "invalid"}), 400
        new_id = db.insert(
            "INSERT INTO support_messages (user_id, sender, type, text, "
            "file_path, file_name, file_size, pinned, created_at) "
            "VALUES (?, 'user', 'text', ?, '', '', 0, 0, ?)",
            (user["id"], text, _now()),
        )
    row = db.q1("SELECT * FROM support_messages WHERE id = ?", (new_id,))
    return jsonify({"message": _support_msg(row)})


@app.route("/api/reset", methods=["POST"])
@login_required
def api_reset():
    user = current_user()
    for table in ("transactions", "accounts", "assets", "debts"):
        db.execute("DELETE FROM %s WHERE user_id = ?" % table, (user["id"],))
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Upload serving — participant-checked
# ---------------------------------------------------------------------------

@app.route("/uploads/<path:relpath>")
@login_required
def serve_upload(relpath):
    user = current_user()
    relpath = relpath.replace("\\", "/").lstrip("/")
    if ".." in relpath.split("/"):
        abort(404)
    allowed = False
    if user.get("role") == "admin":
        hit = db.q1(
            "SELECT id FROM messages WHERE file_path = ? "
            "UNION SELECT id FROM support_messages WHERE file_path = ? "
            "LIMIT 1", (relpath, relpath))
        allowed = bool(hit)
    if not allowed:
        hit = db.q1(
            "SELECT id FROM messages WHERE file_path = ? AND "
            "(sender_id = ? OR recipient_id = ?) LIMIT 1",
            (relpath, user["id"], user["id"]))
        allowed = bool(hit)
    if not allowed:
        hit = db.q1(
            "SELECT id FROM support_messages WHERE file_path = ? AND "
            "user_id = ? LIMIT 1", (relpath, user["id"]))
        allowed = bool(hit)
    if not allowed:
        abort(404)
    path = os.path.join(UPLOADS_DIR, relpath)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path)


# ---------------------------------------------------------------------------
# Admin routes (Agent B) — guarded so the app runs before that module lands
# ---------------------------------------------------------------------------

try:
    import admin_routes

    admin_routes.register(app)
except ImportError:
    pass


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
