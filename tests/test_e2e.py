#!/usr/bin/env python3
"""End-to-end tests for the Ngwe Chay Yar real app.

Boots the Flask app on a throwaway SQLite DB and scripts the full user
matrix with stdlib HTTP clients (per-session cookie jars):

  1. auth gating (signed-out / and /admin redirect; /api/state 401)
  2. first registered user = admin, empty state; second user = user role,
     empty state, per-user isolation
  3. finance math (income/expense/monthly, account+asset-debt = net worth)
  4. usernames, real user search, message + attachment + cost-card
     round-trips between two real accounts
  5. support round-trip user -> admin reply -> user, plus pin
  6. admin overview aggregates match the real test data exactly
  7. profile photo upload/serving
  8. suspend blocks login + kills session; activate restores
  9. settings enforcement: registration closed, messages off, maintenance
 10. zero demo data: static greps + DB contains exactly test-created rows

Run:  python3 tests/test_e2e.py
Exit code 0 iff every check passes.
"""
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.cookiejar import CookieJar

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 5417
BASE = f"http://127.0.0.1:{PORT}"
DB_PATH = f"/tmp/ncy_e2e_{uuid.uuid4().hex[:8]}.db"
VENV_PY = os.path.expanduser("~/workspace/personal-finance/venv/bin/python")
SERVER_PY = VENV_PY if os.path.exists(VENV_PY) else sys.executable
TODAY = time.strftime("%Y-%m-%d")

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


# --------------------------------------------------------------------------
# tiny HTTP client
# --------------------------------------------------------------------------
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Session:
    def __init__(self):
        self.jar = CookieJar()
        self.follow = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.nofollow = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), NoRedirect())

    def req(self, method, path, body=None, ctype=None, follow=True):
        url = BASE + path
        data = body.encode() if isinstance(body, str) else body
        r = urllib.request.Request(url, data=data, method=method)
        if ctype:
            r.add_header("Content-Type", ctype)
        opener = self.follow if follow else self.nofollow
        try:
            with opener.open(r, timeout=15) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def get(self, path, follow=True):
        return self.req("GET", path, follow=follow)

    def post_form(self, path, fields, follow=True):
        body = "&".join(
            f"{urllib.parse.quote(str(k))}={urllib.parse.quote(str(v))}"
            for k, v in fields.items())
        return self.req("POST", path, body,
                        "application/x-www-form-urlencoded", follow)

    def post_json(self, path, obj):
        st, body, _ = self.req("POST", path, json.dumps(obj),
                               "application/json")
        try:
            return st, json.loads(body)
        except Exception:
            return st, {}

    def delete(self, path):
        return self.req("DELETE", path)

    def post_multipart(self, path, fields=None, files=None):
        boundary = "----ncy" + uuid.uuid4().hex
        out = io.BytesIO()
        for k, v in (fields or {}).items():
            out.write(f"--{boundary}\r\nContent-Disposition: form-data; "
                      f"name=\"{k}\"\r\n\r\n{v}\r\n".encode())
        for k, (fn, data, mime) in (files or {}).items():
            out.write(f"--{boundary}\r\nContent-Disposition: form-data; "
                      f"name=\"{k}\"; filename=\"{fn}\"\r\n"
                      f"Content-Type: {mime}\r\n\r\n".encode())
            out.write(data)
            out.write(b"\r\n")
        out.write(f"--{boundary}--\r\n".encode())
        return self.req("POST", path, out.getvalue(),
                        f"multipart/form-data; boundary={boundary}")

    def api(self, path):
        st, body, _ = self.get(path)
        try:
            return st, json.loads(body)
        except Exception:
            return st, {}


def register(sess, name, email, password):
    return sess.post_form("/register", {
        "name": name, "email": email, "password": password,
        "confirm_password": password, "terms": "on"})


def login(sess, email, password):
    return sess.post_form("/login", {"email": email, "password": password})


def totals(state):
    inc = sum(t["amount"] for t in state["transactions"]
              if t["kind"] == "income" and t["txn_date"].startswith(TODAY[:7]))
    exp = sum(t["amount"] for t in state["transactions"]
              if t["kind"] == "expense" and t["txn_date"].startswith(TODAY[:7]))
    net = (sum(a["balance"] for a in state["accounts"])
           + sum(a["current_value"] for a in state["assets"])
           - sum(d["amount"] for d in state["debts"]))
    return inc, exp, inc - exp, net


# --------------------------------------------------------------------------
def main():
    # ---- static zero-demo greps -------------------------------------------
    demo_pat = re.compile(
        r"May Thiri|Aung Khant|Su Myat|Thiri Nanda|peterpeter|demo@example|"
        r"sampleData|seed_", re.I)
    hits = []
    for root, dirs, files in os.walk(APP_DIR):
        dirs[:] = [d for d in dirs if d not in ("data", "__pycache__",
                                                "tests", ".git")]
        for fn in files:
            p = os.path.join(root, fn)
            if fn.endswith((".py", ".html", ".md", ".txt")):
                try:
                    txt = open(p, encoding="utf-8", errors="ignore").read()
                except Exception:
                    continue
                if demo_pat.search(txt):
                    hits.append(os.path.relpath(p, APP_DIR))
    check("static: no demo names/seed code in app files", not hits,
          f"hits={hits}")

    # ---- boot --------------------------------------------------------------
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
    env["PORT"] = str(PORT)
    env["SECRET_KEY"] = "e2e-test-secret"
    proc = subprocess.Popen(
        [SERVER_PY, "app.py"], cwd=APP_DIR, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        up = False
        for _ in range(60):
            try:
                st, _, _ = Session().get("/login")
                up = st == 200
                break
            except Exception:
                time.sleep(0.5)
        check("server boots on temp DB", up)
        if not up:
            return finish(proc)

        anon = Session()
        # ---- 1. gating ------------------------------------------------------
        st, _, hd = anon.get("/", follow=False)
        check("signed-out / redirects to /login",
              st in (301, 302) and "/login" in (hd.get("Location") or ""),
              f"st={st}")
        st, _, hd = anon.get("/admin", follow=False)
        check("signed-out /admin redirects (toward login)",
              st in (301, 302), f"st={st} loc={hd.get('Location')}")
        st, _, hd = anon.get("/admin/overview", follow=False)
        check("signed-out /admin/overview redirects to /admin/login",
              st in (301, 302) and "/admin/login" in (hd.get("Location") or ""),
              f"st={st} loc={hd.get('Location')}")
        st, body, _ = anon.get("/admin")
        check("signed-out /admin chain lands on the admin login page",
              st == 200 and b'name="password"' in body, f"st={st}")
        st, _ = anon.api("/api/state")
        check("signed-out /api/state is 401", st == 401, f"st={st}")
        st, _, _ = anon.get("/login")
        check("/login renders 200", st == 200)
        st, _, _ = anon.get("/register")
        check("/register renders 200", st == 200)

        # ---- 2. register A (first -> admin) ---------------------------------
        a = Session()
        register(a, "Alice Admin", "alice@test.local", "alice-pass-1")
        st, sa = a.api("/api/state")
        check("A /api/state 200 after register", st == 200, f"st={st}")
        check("A is admin (first user)", sa["profile"]["role"] == "admin",
              sa["profile"].get("role", "?"))
        check("A state completely empty",
              sa["transactions"] == [] and sa["accounts"] == []
              and sa["assets"] == [] and sa["debts"] == []
              and sa["conversations"] == []
              and sa["support"]["messages"] == [])
        check("A totals are zero", totals(sa) == (0, 0, 0, 0), str(totals(sa)))

        # ---- 3. A finance ---------------------------------------------------
        st, r = a.post_json("/api/transactions", {
            "kind": "income", "amount": 2000000, "category": "Salary",
            "txn_date": TODAY, "note": "Monthly salary"})
        check("A adds income 2,000,000", st == 200 and "id" in r, f"st={st}")
        st, r = a.post_json("/api/transactions", {
            "kind": "expense", "amount": 500000, "category": "Food",
            "txn_date": TODAY, "note": "Groceries"})
        check("A adds expense 500,000", st == 200 and "id" in r, f"st={st}")
        st, r = a.post_json("/api/accounts", {
            "name": "KBZ Pay", "type": "ewallet", "brand": "kbzpay",
            "balance": 1500000, "note": ""})
        check("A adds KBZ Pay account (brand kbzpay)",
              st == 200 and "id" in r, f"st={st}")
        a.post_json("/api/assets", {
            "name": "Land", "type": "Land", "purchase_price": 80000000,
            "current_value": 100000000, "location": "", "note": ""})
        a.post_json("/api/debts", {"name": "Loan", "amount": 10000000,
                                   "note": ""})
        st, sa = a.api("/api/state")
        inc, exp, left, net = totals(sa)
        check("A month income = 2,000,000", inc == 2000000, str(inc))
        check("A month expense = 500,000", exp == 500000, str(exp))
        check("A month leftover = 1,500,000", left == 1500000, str(left))
        check("A net worth = 1,500,000 + 100,000,000 - 10,000,000 = 91,500,000",
              net == 91500000, str(net))
        a_txn_ids = {t["id"] for t in sa["transactions"]}

        # ---- 4. register B ---------------------------------------------------
        b = Session()
        register(b, "Bob User", "bob@test.local", "bob-pass-1")
        st, sb = b.api("/api/state")
        check("B role is user", sb["profile"]["role"] == "user",
              sb["profile"].get("role", "?"))
        check("B state completely empty",
              sb["transactions"] == [] and sb["accounts"] == []
              and sb["assets"] == [] and sb["debts"] == [])
        check("B sees none of A's data",
              not ({t["id"] for t in sb["transactions"]} & a_txn_ids))
        # wrong password rejected
        bad = Session()
        login(bad, "alice@test.local", "wrong-password")
        st, _ = bad.api("/api/state")
        check("wrong-password login gets no session", st == 401, f"st={st}")

        # ---- 5. usernames + search -------------------------------------------
        st, r = b.post_json("/api/username", {"username": "bobuser"})
        check("B sets username bobuser", st == 200, f"st={st} {r}")
        st, r = a.post_json("/api/username", {"username": "aliceadmin"})
        check("A sets username aliceadmin", st == 200, f"st={st} {r}")
        st, r = b.post_json("/api/username", {"username": "aliceadmin"})
        check("taken username rejected 409", st == 409, f"st={st}")
        st, body, _ = a.get("/api/users/search?q=bob")
        res = json.loads(body)
        check("A searches and finds B by username",
              any(u["username"] == "bobuser" for u in res), str(res))
        st, body, _ = a.get("/api/users/search?q=alice")
        res = json.loads(body)
        check("search excludes self",
              not any(u["username"] == "aliceadmin" for u in res), str(res))

        # ---- 6. messages ------------------------------------------------------
        st, r = a.post_json("/api/messages",
                            {"to": "bobuser", "text": "Hello B"})
        check("A -> B text message", st == 200 and "message" in r,
              f"st={st} {r}")
        # a real PNG attachment
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (48, 48), (20, 160, 60)).save(buf, "PNG")
        png = buf.getvalue()
        st, body, _ = a.post_multipart(
            "/api/messages", {"to": "bobuser"},
            {"file": ("shot.png", png, "image/png")})
        att = json.loads(body) if st == 200 else {}
        check("A -> B image attachment", st == 200
              and att.get("message", {}).get("type") == "image",
              f"st={st} {body[:200]}")
        att_url = att.get("message", {}).get("url", "")
        check("attachment has /uploads/ url", att_url.startswith("/uploads/"),
              att_url)
        st, body, _ = b.get(att_url)
        check("B can fetch the attachment bytes", st == 200 and body == png,
              f"st={st}")
        st, r = a.post_json("/api/messages",
                            {"to": "bobuser", "type": "card"})
        card = r.get("message", {})
        check("share-costs card computed server-side",
              card.get("type") == "card" and card.get("income") == 2000000
              and card.get("expense") == 500000
              and card.get("leftover") == 1500000, str(card))
        st, conv = b.api("/api/conversations/aliceadmin/messages")
        msgs = conv.get("messages", [])
        check("B receives all 3 A-messages",
              len(msgs) == 3 and all(m["from"] == "them" for m in msgs),
              f"n={len(msgs)}")
        st, r = b.post_json("/api/messages",
                            {"to": "aliceadmin", "text": "Hi A, got it"})
        reply_id = r.get("message", {}).get("id")
        check("B replies to A", st == 200 and reply_id, f"st={st}")
        last_a_id = max(m["id"] for m in msgs)
        st, conv = a.api(f"/api/conversations/bobuser/messages?after={last_a_id}")
        new = conv.get("messages", [])
        check("A polling after=<id> returns only B's reply",
              len(new) == 1 and new[0]["text"] == "Hi A, got it", str(new))

        # ---- 7. support round-trip -------------------------------------------
        st, r = b.post_json("/api/support", {"text": "I need help"})
        check("B posts support message", st == 200, f"st={st} {r}")
        st, body, _ = a.get("/admin/support")
        html = body.decode("utf-8", "ignore")
        check("admin Support lists B's thread",
              "Bob User" in html or "bobuser" in html)
        m = re.search(r"/admin/support/(\d+)", html)
        b_id = int(m.group(1)) if m else None
        st, body, _ = a.get(f"/admin/support/{b_id}")
        check("admin thread view shows B's message",
              "I need help" in body.decode("utf-8", "ignore"))
        st, _, _ = a.post_form(f"/admin/support/{b_id}/reply",
                               {"text": "Admin here"})
        check("admin reply posts", st in (200, 302), f"st={st}")
        st, sup = b.api("/api/support")
        smsgs = sup.get("messages", [])
        adm = [x for x in smsgs if x.get("text") == "Admin here"]
        check("B sees admin reply in support (from=admin)",
              len(adm) == 1 and adm[0].get("from") == "admin", str(smsgs))
        help_id = [x["id"] for x in smsgs if x.get("text") == "I need help"][0]
        a.post_form(f"/admin/support/{b_id}/pin/{help_id}", {})
        st, sup = b.api("/api/support")
        pinned = [x for x in sup["messages"] if x["id"] == help_id]
        check("admin pin persists to user support view",
              pinned and pinned[0].get("pinned") is True, str(pinned))

        # ---- 8. admin overview aggregates --------------------------------------
        st, body, _ = a.get("/admin/overview")
        html = body.decode("utf-8", "ignore")
        stats = re.findall(r'class="sv num">([^<]+)<', html)
        check("admin overview stats = users 2 / active 2 / txn-month 2 / new 2",
              stats[:4] == ["2", "2", "2", "2"], f"stats={stats[:4]}")
        check("admin flow shows real 2,000,000 / 500,000 / 1,500,000",
              ">2,000,000<" in html and ">500,000<" in html
              and ">1,500,000<" in html)
        st, body, _ = a.get("/admin/users")
        html = body.decode("utf-8", "ignore")
        check("admin Users lists both real users",
              "Alice Admin" in html and "Bob User" in html)
        st, body, _ = a.get("/admin/transactions?filter=expense")
        html = body.decode("utf-8", "ignore")
        check("admin expense filter excludes income rows",
              "Food" in html and "Salary" not in html)
        st, body, _ = a.get("/admin/messages")
        check("admin Messages page renders", st == 200)

        # ---- 9. profile photo ---------------------------------------------------
        st, body, _ = b.post_multipart(
            "/api/profile/photo", {}, {"photo": ("me.png", png, "image/png")})
        check("B uploads profile photo", st == 200, f"st={st}")
        st, sb = b.api("/api/state")
        check("B profile.photo flag true", sb["profile"]["photo"] is True)
        st, body, hd = b.get("/api/profile/photo")
        check("B photo served as image",
              st == 200 and body[:2] == b"\xff\xd8", f"st={st}")
        st, body, _ = a.get("/api/users/bobuser/photo")
        check("A can fetch B's avatar photo", st == 200, f"st={st}")

        # ---- 10. suspend / activate ----------------------------------------------
        a.post_form(f"/admin/users/{b_id}/suspend", {})
        st, _ = b.api("/api/state")
        check("suspended B's live session is cut", st == 401, f"st={st}")
        b2 = Session()
        login(b2, "bob@test.local", "bob-pass-1")
        st, _ = b2.api("/api/state")
        check("suspended B cannot log in", st == 401, f"st={st}")
        a.post_form(f"/admin/users/{b_id}/activate", {})
        login(b2, "bob@test.local", "bob-pass-1")
        st, _ = b2.api("/api/state")
        check("activated B can log in again", st == 200, f"st={st}")
        b = b2

        # ---- 11. settings enforcement ----------------------------------------------
        # checkboxes: absent = off. This POST = registration CLOSED,
        # maintenance off, messages on.
        a.post_form("/admin/settings", {"allow_messages": "1"})
        c = Session()
        register(c, "Carol Third", "carol@test.local", "carol-pass-1")
        st, _ = c.api("/api/state")
        check("registration closed: C gets no account/session",
              st == 401, f"st={st}")
        # registration back on, allow_messages absent -> messages OFF
        a.post_form("/admin/settings", {"registration_open": "1"})
        st, r = a.post_json("/api/messages",
                            {"to": "bobuser", "text": "blocked?"})
        check("messages off: send returns 403", st == 403, f"st={st}")
        a.post_form("/admin/settings",
                    {"registration_open": "1", "maintenance": "1",
                     "allow_messages": "1"})
        st, _, _ = b.get("/")
        check("maintenance: non-admin gets 503", st == 503, f"st={st}")
        st, _, _ = a.get("/")
        check("maintenance: admin still gets the app", st == 200, f"st={st}")
        a.post_form("/admin/settings",
                    {"registration_open": "1", "allow_messages": "1"})
        st, r = a.post_json("/api/messages",
                            {"to": "bobuser", "text": "back on"})
        check("settings restored: messaging works again",
              st == 200, f"st={st}")

        # ---- 12. DB exactness ---------------------------------------------------------
        con = sqlite3.connect(DB_PATH)
        counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("users", "transactions", "accounts", "assets",
                            "debts", "messages", "support_messages")}
        expect = {"users": 2, "transactions": 2, "accounts": 1, "assets": 1,
                  "debts": 1, "messages": 5, "support_messages": 2}
        check("DB holds exactly the test-created rows",
              counts == expect, f"counts={counts} expect={expect}")
        roles = con.execute(
            "SELECT email, role FROM users ORDER BY id").fetchall()
        check("DB roles: first user admin, second user",
              roles == [("alice@test.local", "admin"),
                        ("bob@test.local", "user")], str(roles))
        con.close()

        # ---- 13. smoke renders -----------------------------------------------------------
        st, body, _ = a.get("/")
        html = body.decode("utf-8", "ignore")
        check("SPA / renders for signed-in user with real wiring",
              st == 200 and "/api/state" in html
              and "brands/kbzpay.png" in html, f"st={st}")
        for path in ("/admin/overview", "/admin/users", "/admin/transactions",
                     "/admin/messages", "/admin/support", "/admin/settings"):
            st, _, _ = a.get(path)
            check(f"smoke GET {path} -> 200", st == 200, f"st={st}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    return finish(proc)


def finish(proc):
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n==== {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed "
          f"({len(failed)} failed) ====")
    for name, _, detail in failed:
        print("FAILED:", name, detail)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
