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
    def __init__(self, base=None):
        self.base = base or BASE
        self.jar = CookieJar()
        self.follow = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.nofollow = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), NoRedirect())

    def req(self, method, path, body=None, ctype=None, follow=True):
        url = self.base + path
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

    def put_json(self, path, obj):
        st, body, _ = self.req("PUT", path, json.dumps(obj),
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

    # ---- static SPA structure: share pill + card items ----------------------
    spa = open(os.path.join(APP_DIR, "templates", "index.html"),
               encoding="utf-8").read()
    check("static: share pills (daily + monthly) built inside threadHTML",
          spa.count('class="sharepill"') == 2
          and "msg_share_cost_month" in spa
          and "function threadHTML(conv)" in spa
          and "return sharePillHTML() + conv.messages.map(bubbleHTML)" in spa)
    check("static: scrollable thread container is filled via threadHTML",
          'id="chat-msgs">\'+threadHTML(conv2)' in spa
          and "box.innerHTML = threadHTML(conv);" in spa)
    pill_css = re.search(r"\.sharepill\{([^}]*)\}", spa)
    check("static: share pill itself is not sticky/fixed",
          pill_css is not None and "sticky" not in pill_css.group(1)
          and "fixed" not in pill_css.group(1))
    check("static: card renderer has items branch + totals-only fallback",
          "(m.items && m.items.length)" in spa and "msg_more" in spa)
    bub_m = re.search(r"function bubbleHTML\(m\)\{(.*?)\n\}", spa, re.S)
    bub = bub_m.group(1) if bub_m else ""
    check("static: bubble direction comes from m.from for every type "
          "(cards must not hard-code the own-message class)",
          "m.type==='card' ? 'bub me cardmsg'" not in spa
          and "var cls = 'bub '+(m.from==='me'?'me':'them')"
              "+(m.type==='card'?' cardmsg':'');" in bub,
          bub.strip()[:140])
    strings_m = re.search(r"var STRINGS = (\{.*?\});\n", spa, re.S)
    strings = json.loads(strings_m.group(1)) if strings_m else {}
    key_sets = {k: set(v) for k, v in strings.items()}
    check("static: i18n key sets identical across all 4 languages",
          len(key_sets) == 4
          and all(s == key_sets.get("en") for s in key_sets.values())
          and all("msg_more" in s for s in key_sets.values()))
    new_keys = ("msg_share_cost_month", "msg_month_costs", "wallet_label",
                "add_wallet_hint", "opening_balance_label")
    check("static: wallet/monthly i18n keys present in all 4 languages",
          all(all(k in s for k in new_keys) for s in key_sets.values()))
    check("static: wallet picker + account_id wired in SPA",
          'id="add-wallets"' in spa and "account_id:rec.account_id" in spa
          and "function renderAddWallets()" in spa
          and "function applyWalletDelta(" in spa)
    admin_src = open(os.path.join(APP_DIR, "admin_routes.py"),
                     encoding="utf-8").read()
    app_src = open(os.path.join(APP_DIR, "app.py"), encoding="utf-8").read()
    db_src = open(os.path.join(APP_DIR, "db.py"), encoding="utf-8").read()
    check("static: separate admin gate + account PUT + txn account_id",
          "admin_ok" in admin_src and "def admin_logout" in admin_src
          and "def _admin_session_user" in admin_src
          and 'methods=["PUT"]' in app_src and "account_id" in app_src)

    # ---- static SPA structure: UI13 build (spend card / people / brand
    #      card / chat height / about page / assets-debts split / hero) -----
    spend_i = spa.find('id="h-spend"')
    nw_i = spa.find('id="h-networth"')
    check("static: month-spend hero is a card sharing the net-worth card "
          "class and still binds to the same computed value",
          spend_i > 0 and nw_i > 0
          and 'class="card spendcard"' in spa[max(0, spend_i - 1000):spend_i]
          and 'class="card hero"' in spa[max(0, nw_i - 300):nw_i]
          and "el('h-spend').textContent = mmk(tt.expense)" in spa
          and 'id="h-spend-month"' in spa and "first_month" in spa
          and "spendwrap" not in spa)
    check("static: Message home has no standing People section; search "
          "results are transient and chats remain",
          "msg_people" not in spa and "renderPeople" not in spa
          and "function renderSearchResults()" in spa
          and 'id="msg-results-card"' in spa
          and 'id="msg-search-in"' in spa and 'id="msg-convs"' in spa
          and "/api/users/search" in spa)
    acct_i = spa.find('id="page-account"')
    acct_j = spa.find('id="page-accounts"')
    acct_sec = spa[acct_i:acct_j] if 0 < acct_i < acct_j else ""
    check("static: Account page brand/member card removed; profile card "
          "and menu rows intact",
          "membercard" not in spa and "member_sub" not in spa
          and 'id="ac-avatar"' in acct_sec
          and 'id="profile-form"' in acct_sec
          and "account_information" in acct_sec)
    check("static: chat views are full-height flex columns with a "
          "scrolling bubbles area (Message + Support)",
          ".msg-layout.chat-open{" in spa and "100dvh" in spa
          and ".msg-right .bubbles{flex:1;min-height:0;overflow-y:auto}"
              in spa
          and ".sup-wrap .bubbles{flex:1;min-height:0;overflow-y:auto}"
              in spa
          and ".sup-wrap{display:flex;flex-direction:column}" in spa
          and ".msg-right .composer,.sup-wrap .composer{position:static"
              in spa
          and "max-height:54vh" not in spa)
    about_i = spa.find('id="page-about"')
    about_j = spa.find("</section>", about_i) if about_i > 0 else -1
    about_sec = spa[about_i:about_j] if about_i > 0 and about_j > about_i \
        else ""
    about_keys = ("about_intro", "about_features_title", "about_f1",
                  "about_f2", "about_f3", "about_f4", "about_f5",
                  "about_f6", "about_f7", "about_promise_title",
                  "about_promise", "about_support_text",
                  "about_support_btn", "about_version")
    check("static: dedicated About page exists with only about content",
          about_i > 0 and "about_intro" in about_sec
          and "about_f1" in about_sec and "about_f7" in about_sec
          and "settings-lang" not in about_sec
          and "theme-switch" not in about_sec
          and "settings-msg" not in about_sec
          and "onclick=\"go('support')\"" in about_sec
          and 'id="about-year"' in about_sec)
    check("static: Account About row routes to the About page; about i18n "
          "keys present in all 4 languages",
          "onclick=\"go('about')\"" in spa
          and "function renderAbout()" in spa
          and "about:'about'" in spa
          and all(all(k in s for k in about_keys)
                  for s in key_sets.values()))
    as_i = spa.find('id="page-assets"')
    db_i = spa.find('id="page-debts"')
    st_i = spa.find('id="page-settings"')
    as_sec = spa[as_i:db_i] if 0 < as_i < db_i else ""
    db_sec = spa[db_i:st_i] if 0 < db_i < st_i else ""
    check("static: Assets and Debts are separate views with distinct "
          "Account menu routing",
          as_i > 0 and db_i > as_i and st_i > db_i
          and 'id="as-list"' in as_sec and "addDebt" not in as_sec
          and 'id="db-list"' not in as_sec
          and 'id="db-list"' in db_sec and "addAsset" not in db_sec
          and 'id="as-list"' not in db_sec
          and "onclick=\"go('debts')\"" in spa
          and "function renderDebts()" in spa
          and "debts:'debts'" in spa)
    hg_m = re.search(r"\.hero-grid\{([^}]*)\}", spa)
    check("static: net-worth breakdown is one 3-column row; Home "
          "bindings unchanged",
          hg_m is not None and "1fr 1fr 1fr" in hg_m.group(1)
          and all(x in spa for x in ('id="h-networth"',
                                     'id="h-b-accounts"',
                                     'id="h-b-assets"',
                                     'id="h-b-debts"'))
          and "el('h-b-accounts').textContent" in spa
          and "el('h-b-assets').textContent" in spa
          and "el('h-b-debts').textContent" in spa)
    check("static: desktop refinements (4-across stat grid, centred "
          "About column, debts page width cap)",
          ".statgrid{grid-template-columns:repeat(4,1fr)}" in spa
          and ".about-col{max-width:720px" in spa
          and "#page-debts .card" in spa)

    # ---- static SPA structure: UI15 Add sheet redesign + admin doorway --
    add_i = spa.find('id="page-add"')
    add_j = spa.find('id="page-support"', add_i)
    add_sec = spa[add_i:add_j] if 0 < add_i < add_j else ""
    ui15_i = spa.find("UI15 Add screen")
    ui15 = spa[ui15_i:] if ui15_i > 0 else ""
    check("static: Add view is a fixed sheet between header and bottom "
          "nav; the form area is the only scrollable part and the keypad "
          "+ submit block is pinned (flex:none) at the sheet bottom",
          "#page-add.active{position:fixed;top:76px;bottom:88px" in spa
          and ".addcard{flex:1;min-height:0;display:flex;"
          "flex-direction:column" in spa
          and ".addcard .add-left{display:flex;flex-direction:column;"
          "flex:1 1 auto;min-height:0;overflow-y:auto" in spa
          and ".addcard .add-right{display:block;flex:none}" in spa
          and 'id="keypad"' in add_sec and 'id="add-save"' in add_sec
          and 'class="walletwrap"' in add_sec)
    check("static: UI15 keypad has real presence — 58px circles/rows "
          "(52px in the short-viewport tier), 20px numerals, .96 press",
          "grid-template-rows:repeat(4,58px)" in spa
          and ".addcard .keypad button{width:58px;height:58px;"
          "font-size:20px" in spa
          and "grid-template-rows:repeat(4,52px)" in spa
          and ".addcard .keypad button:active{transform:scale(.96)}" in spa)
    check("static: UI15 category tiles are comfortable and never clipped "
          "(70px floor, height auto — rows grow to fit icon + two "
          "Myanmar lines at lh 1.9; the UI14 squeeze rules are gone)",
          ".addcard .catcell{min-height:70px" in spa
          and ".addcard .catcell{height:" not in spa
          and "body.lang-my .addcard .catcell span:last-child"
          "{line-height:1.9}" in spa
          and "body.lang-th .addcard .catcell span:last-child"
          "{line-height:1.8}" in spa
          and ".addcard .catcell{min-height:0" not in ui15
          and "line-height:1.25" not in ui15
          and "6.2dvh" not in spa)
    check("static: UI15 amount block is centred with the caption above "
          "the 32px amount and a hairline divider under it",
          ".addcard .amtrow{display:flex;flex-direction:column;"
          "align-items:center" in spa
          and ".addcard .amountsub{order:-1" in spa
          and ".addcard .amountbig{font-size:32px" in spa
          and "border-bottom:1px solid var(--hair)" in spa)
    check("static: UI15 wallet is one 44px row (chips side-scroll, hint "
          "is a single ellipsised line in its own box)",
          ".addcard .walletwrap{display:flex;align-items:center;gap:10px;"
          "min-height:44px" in spa
          and "#add-wallet-hint{flex:1;min-width:0" in spa
          and "text-overflow:ellipsis" in spa)
    check("static: UI15 Date/Note — 48px fields, date gets the larger "
          "flex share, inputs width-capped (no overlap/spill)",
          ".daterow .field.date-f{flex:1.12 1 0}" in spa
          and ".daterow .field.note-f{flex:1 1 0}" in spa
          and ".daterow .field input{width:100%;min-width:0;"
          "max-width:100%;height:48px" in spa)
    check("static: UI15 header language pill + bottom-nav labels get "
          "full Myanmar/Thai line-heights (no glyph shaving)",
          "body.lang-my .langbtn{line-height:1.9}" in spa
          and "body.lang-th .langbtn{line-height:1.8}" in spa
          and "body.lang-my .bn-in>button[data-nav] span"
          "{line-height:1.9}" in spa
          and "body.lang-th .bn-in>button[data-nav] span"
          "{line-height:1.8}" in spa)
    check("static: UI15 desktop Add is a centred content-height panel "
          "(880px cap, 60px keys), not a stretched phone form",
          "#page-add .card{max-width:880px" in spa
          and "grid-template-rows:repeat(4,60px)" in spa
          and ".addcard .savebtn{height:52px" in spa)
    check("static: Admin Dashboard doorway rows (Account menu + desktop "
          "sidebar) ship hidden and are revealed only for role=admin; "
          "they are plain links to /admin (separate admin gate untouched)",
          'id="ac-admin-row" style="display:none"' in acct_sec
          and 'id="side-admin-row" style="display:none"' in spa
          and spa.count('data-i18n="admin_dashboard"') == 2
          and spa.count("location.href='/admin'") == 2
          and "state.profile.role==='admin'" in spa
          and "'ac-admin-row','side-admin-row'" in spa)
    check("static: admin_dashboard i18n key present in all 4 languages",
          all("admin_dashboard" in s for s in key_sets.values()))
    check("static: username-or-email login + admin credential editing "
          "surface is wired (identifier lookup helper, session_version "
          "invalidation, admin username/password POST routes)",
          "def find_user_by_identifier" in app_src
          and "email_or_username" in app_src
          and "session_version" in db_src
          and "def admin_user_username" in admin_src
          and "def admin_user_password" in admin_src
          and "session_version=session_version+1" in admin_src
          and "email_or_username" in admin_src)

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

        # ---- 3b. wallet linkage ---------------------------------------------
        acct_id = sa["accounts"][0]["id"]
        check("account exposes opening_balance == balance before linkage",
              sa["accounts"][0]["opening_balance"] == 1500000
              and sa["accounts"][0]["balance"] == 1500000,
              str(sa["accounts"]))
        st, r = a.post_json("/api/transactions", {
            "kind": "income", "amount": 100000, "category": "Salary",
            "txn_date": TODAY, "note": "Bonus", "account_id": acct_id})
        link_id = r.get("id")
        check("A links income 100,000 to KBZ Pay",
              st == 200 and bool(link_id), f"st={st}")
        st, sa = a.api("/api/state")
        check("linked income moves computed balance to 1,600,000",
              sa["accounts"][0]["balance"] == 1600000
              and sa["accounts"][0]["opening_balance"] == 1500000,
              str(sa["accounts"][0]))
        st, r = a.post_json("/api/transactions", {
            "kind": "expense", "amount": 100, "category": "Food",
            "txn_date": TODAY, "note": "bad", "account_id": 999999})
        check("linking to a nonexistent wallet -> 400", st == 400,
              f"st={st}")
        a.delete(f"/api/transactions/{link_id}")
        st, sa = a.api("/api/state")
        check("deleting the linked txn reverses the balance to 1,500,000",
              sa["accounts"][0]["balance"] == 1500000,
              str(sa["accounts"][0]))

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

        # ---- 4b. separate admin session gate ---------------------------------
        st, _, hd = a.get("/admin/overview", follow=False)
        check("gate: app-signed-in admin still blocked from /admin/overview",
              st in (301, 302)
              and "/admin/login" in (hd.get("Location") or ""), f"st={st}")
        st, _, _ = a.post_form("/admin/login",
                               {"email": "alice@test.local",
                                "password": "wrong"}, follow=False)
        check("gate: wrong admin password re-renders the form (no access)",
              st == 200, f"st={st}")
        st, _, hd = a.get("/admin/overview", follow=False)
        check("gate: overview still blocked after wrong password",
              st in (301, 302)
              and "/admin/login" in (hd.get("Location") or ""), f"st={st}")
        st, _, _ = b.post_form("/admin/login",
                               {"email": "bob@test.local",
                                "password": "bob-pass-1"}, follow=False)
        check("gate: non-admin with CORRECT password rejected at /admin/login",
              st == 200, f"st={st}")
        st, _, hd = b.get("/admin/overview", follow=False)
        check("gate: non-admin overview still blocked",
              st in (301, 302)
              and "/admin/login" in (hd.get("Location") or ""), f"st={st}")
        st, _, hd = a.post_form("/admin/login",
                                {"email": "alice@test.local",
                                 "password": "alice-pass-1"}, follow=False)
        check("gate: correct admin login redirects to /admin/overview",
              st in (301, 302)
              and "/admin/overview" in (hd.get("Location") or ""),
              f"st={st} loc={hd.get('Location')}")
        st, _, _ = a.get("/admin/overview")
        check("gate: overview 200 after admin login", st == 200, f"st={st}")
        st, _, hd = a.get("/admin/logout", follow=False)
        check("gate: admin logout redirects to /admin/login",
              st in (301, 302)
              and "/admin/login" in (hd.get("Location") or ""), f"st={st}")
        st, _, hd = a.get("/admin/overview", follow=False)
        check("gate: overview blocked again after admin logout",
              st in (301, 302)
              and "/admin/login" in (hd.get("Location") or ""), f"st={st}")
        a.post_form("/admin/login",
                    {"email": "alice@test.local",
                     "password": "alice-pass-1"})
        st, _ = a.api("/api/state")
        check("gate: app session untouched by admin login/logout cycle",
              st == 200, f"st={st}")

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

        # ---- 5b. login with username OR email --------------------------------
        u1 = Session(BASE)
        u1.post_form("/login", {"email": "bobuser",
                                "password": "bob-pass-1"})
        st, _, _ = u1.get("/api/state")
        check("B logs in with USERNAME instead of email", st == 200,
              f"st={st}")
        u2 = Session(BASE)
        u2.post_form("/login", {"email": "BOBUSER",
                                "password": "bob-pass-1"})
        st, _, _ = u2.get("/api/state")
        check("username login is case-insensitive (BOBUSER)", st == 200,
              f"st={st}")
        u3 = Session(BASE)
        u3.post_form("/login", {"email": "@bobuser",
                                "password": "bob-pass-1"})
        st, _, _ = u3.get("/api/state")
        check("username login tolerates a leading @", st == 200,
              f"st={st}")
        u4 = Session(BASE)
        u4.post_form("/login", {"email": "bob@test.local",
                                "password": "bob-pass-1"})
        st, _, _ = u4.get("/api/state")
        check("email login still works alongside username login",
              st == 200, f"st={st}")
        u5 = Session(BASE)
        u5.post_form("/login", {"email": "nosuchuser",
                                "password": "bob-pass-1"})
        st, _, _ = u5.get("/api/state")
        check("unknown username gets no session", st == 401, f"st={st}")
        u6 = Session(BASE)
        u6.post_form("/login", {"email": "bobuser",
                                "password": "wrong-pass"})
        st, _, _ = u6.get("/api/state")
        check("username + wrong password gets no session", st == 401,
              f"st={st}")
        g1 = Session(BASE)
        g1.post_form("/admin/login", {"email": "aliceadmin",
                                      "password": "alice-pass-1"})
        st, body, _ = g1.get("/admin/overview")
        check("admin logs in at /admin/login with USERNAME",
              st == 200 and len(body) > 1000, f"st={st}")
        g2 = Session(BASE)
        g2.post_form("/admin/login", {"email": "bobuser",
                                      "password": "bob-pass-1"})
        st, _, hd = g2.get("/admin/overview", follow=False)
        check("non-admin rejected at /admin/login via USERNAME too",
              st == 302 and "/admin/login" in (hd.get("Location") or ""),
              f"st={st}")

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
        items = card.get("items") or []
        by_kind = {i.get("kind"): i for i in items}
        check("share-costs card carries itemised today's transactions",
              len(items) == 2
              and by_kind.get("income", {}).get("amount") == 2000000
              and by_kind.get("income", {}).get("category") == "Salary"
              and by_kind.get("income", {}).get("note") == "Monthly salary"
              and by_kind.get("expense", {}).get("amount") == 500000
              and by_kind.get("expense", {}).get("category") == "Food"
              and by_kind.get("expense", {}).get("note") == "Groceries",
              str(items))
        check("share-costs items are chronological as stored",
              [i.get("kind") for i in items] == ["income", "expense"],
              str(items))
        check("daily share card carries scope=day",
              card.get("scope") == "day", str(card.get("scope")))
        st, r = a.post_json("/api/messages",
                            {"to": "bobuser", "type": "card",
                             "scope": "month"})
        mcard = r.get("message", {})
        check("monthly share card: scope/month/totals/items",
              mcard.get("type") == "card" and mcard.get("scope") == "month"
              and mcard.get("month") == TODAY[:7]
              and mcard.get("income") == 2000000
              and mcard.get("expense") == 500000
              and mcard.get("leftover") == 1500000
              and len(mcard.get("items") or []) == 2, str(mcard))
        st, conv = b.api("/api/conversations/aliceadmin/messages")
        msgs = conv.get("messages", [])
        check("B receives all 4 A-messages",
              len(msgs) == 4 and all(m["from"] == "them" for m in msgs),
              f"n={len(msgs)}")
        bcard = next((m for m in msgs if m.get("type") == "card"), {})
        check("B's received card payload includes the same items",
              len(bcard.get("items") or []) == 2
              and {i.get("category") for i in bcard.get("items") or []}
              == {"Salary", "Food"}, str(bcard))
        st, r = b.post_json("/api/messages",
                            {"to": "aliceadmin", "text": "Hi A, got it"})
        reply_id = r.get("message", {}).get("id")
        check("B replies to A", st == 200 and reply_id, f"st={st}")
        last_a_id = max(m["id"] for m in msgs)
        st, conv = a.api(f"/api/conversations/bobuser/messages?after={last_a_id}")
        new = conv.get("messages", [])
        check("A polling after=<id> returns only B's reply",
              len(new) == 1 and new[0]["text"] == "Hi A, got it", str(new))
        # ---- 6b. direction matrix: every type, both perspectives --------------
        # the POST responses are what the sender's SPA appends locally, so
        # their direction field must already say "me" for the sender
        check("POST responses mark the sender's own attachment/cards as me",
              att.get("message", {}).get("from") == "me"
              and card.get("from") == "me" and mcard.get("from") == "me",
              f"att={att.get('message', {}).get('from')} "
              f"card={card.get('from')} mcard={mcard.get('from')}")

        def _dirs(rows):
            out = {}
            for m in rows:
                key = m.get("type") or ""
                if key == "card":
                    key += ":" + (m.get("scope") or "day")
                out.setdefault(key, set()).add(m.get("from"))
            return out

        st, conv_b2 = b.api("/api/conversations/aliceadmin/messages")
        bdirs = _dirs(conv_b2.get("messages", []))
        check("direction matrix (B view): A's text/image/daily+monthly cards "
              "are all 'them', B's own reply is 'me'",
              bdirs.get("image") == {"them"}
              and bdirs.get("card:day") == {"them"}
              and bdirs.get("card:month") == {"them"}
              and bdirs.get("text") == {"them", "me"}, str(bdirs))
        st, conv_a2 = a.api("/api/conversations/bobuser/messages")
        adirs = _dirs(conv_a2.get("messages", []))
        check("direction matrix (A view): A's text/image/daily+monthly cards "
              "are all 'me', B's reply is 'them'",
              adirs.get("image") == {"me"}
              and adirs.get("card:day") == {"me"}
              and adirs.get("card:month") == {"me"}
              and adirs.get("text") == {"me", "them"}, str(adirs))
        # a legacy card stored before items existed (its stored JSON has no
        # "items" key) must still be served with its totals intact; the row
        # is removed again so the DB-exactness section below is unaffected
        con = sqlite3.connect(DB_PATH)
        uid = {e: i for i, e in
               con.execute("SELECT id, email FROM users")}
        legacy_json = json.dumps({"income": 7000, "expense": 2000,
                                  "leftover": 5000, "txn_date": TODAY})
        cur = con.execute(
            "INSERT INTO messages (sender_id, recipient_id, type, text, "
            "file_path, file_name, file_size, card, created_at) "
            "VALUES (?, ?, 'card', '', '', '', 0, ?, ?)",
            (uid["alice@test.local"], uid["bob@test.local"], legacy_json,
             TODAY + "T06:00:00"))
        legacy_id = cur.lastrowid
        con.commit()
        st, conv = b.api("/api/conversations/aliceadmin/messages")
        legacy = next((m for m in conv.get("messages", [])
                       if m.get("id") == legacy_id), {})
        check("legacy card without items is served totals-only",
              legacy.get("type") == "card" and legacy.get("income") == 7000
              and legacy.get("expense") == 2000
              and legacy.get("leftover") == 5000
              and not legacy.get("items"), str(legacy))
        con.execute("DELETE FROM messages WHERE id = ?", (legacy_id,))
        con.commit()
        con.close()

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
                  "debts": 1, "messages": 6, "support_messages": 2}
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
    audit_phase()
    return finish(proc)


# --------------------------------------------------------------------------
# Part C audit: a second server booted on a PRE-MIGRATION database (old
# transactions table WITHOUT account_id) proves the migration, then runs
# the full wallet-linkage scenario where every surface must agree:
# wallet balances, Home totals, month figures, chart/donut inputs, Top
# Spending, daily + monthly share cards, and the admin aggregates.
# --------------------------------------------------------------------------
AUDIT_PORT = 5418
AUDIT_BASE = f"http://127.0.0.1:{AUDIT_PORT}"


def audit_phase():
    old_db = f"/tmp/ncy_audit_{uuid.uuid4().hex[:8]}.db"
    con = sqlite3.connect(old_db)
    con.execute(
        "CREATE TABLE transactions ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " user_id INTEGER NOT NULL,"
        " kind TEXT NOT NULL DEFAULT 'expense',"
        " amount INTEGER NOT NULL DEFAULT 0,"
        " category TEXT NOT NULL DEFAULT '',"
        " txn_date TEXT NOT NULL DEFAULT '',"
        " note TEXT NOT NULL DEFAULT '',"
        " created_at TEXT NOT NULL DEFAULT '')")
    con.commit()
    con.close()

    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{old_db}"
    env["PORT"] = str(AUDIT_PORT)
    env["SECRET_KEY"] = "e2e-audit-secret"
    proc = subprocess.Popen(
        [SERVER_PY, "app.py"], cwd=APP_DIR, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        up = False
        for _ in range(60):
            try:
                st, _, _ = Session(AUDIT_BASE).get("/login")
                up = st == 200
                break
            except Exception:
                time.sleep(0.5)
        check("audit: server boots on pre-migration DB", up)
        if not up:
            return
        con = sqlite3.connect(old_db)
        cols = [r[1] for r in
                con.execute("PRAGMA table_info(transactions)")]
        con.close()
        check("audit: migration adds account_id to the old table",
              "account_id" in cols, str(cols))

        adm = Session(AUDIT_BASE)
        register(adm, "Audit Admin", "admin2@test.local", "admin2-pass")
        carol = Session(AUDIT_BASE)
        register(carol, "Carol Audit", "carol2@test.local", "carol-pass")
        adm.post_json("/api/username", {"username": "adminx"})
        carol.post_json("/api/username", {"username": "carolx"})

        st, r = carol.post_json("/api/accounts", {
            "name": "Wallet A", "type": "cash", "brand": "",
            "balance": 500000, "note": ""})
        wa = r.get("id")
        st, r = carol.post_json("/api/accounts", {
            "name": "Wallet B", "type": "cash", "brand": "",
            "balance": 200000, "note": ""})
        wb = r.get("id")
        check("audit: wallets A (500,000) and B (200,000) created",
              bool(wa) and bool(wb), f"wa={wa} wb={wb}")
        carol.post_json("/api/transactions", {
            "kind": "income", "amount": 100000, "category": "Salary",
            "txn_date": TODAY, "note": "Pay", "account_id": wa})
        carol.post_json("/api/transactions", {
            "kind": "expense", "amount": 220000, "category": "Food",
            "txn_date": TODAY, "note": "Market", "account_id": wa})
        st, r = carol.post_json("/api/transactions", {
            "kind": "expense", "amount": 30000, "category": "Transport",
            "txn_date": TODAY, "note": "Taxi", "account_id": wb})
        taxi_id = r.get("id")
        carol.post_json("/api/assets", {
            "name": "House", "type": "House", "purchase_price": 900000,
            "current_value": 1000000, "location": "", "note": ""})
        carol.post_json("/api/debts", {"name": "Debt", "amount": 100000,
                                       "note": ""})
        st, sc = carol.api("/api/state")
        accs = {x["name"]: x for x in sc["accounts"]}
        check("audit: B = 170,000 before delete (200,000 - 30,000)",
              accs["Wallet B"]["balance"] == 170000,
              str(accs.get("Wallet B")))
        check("audit: A = 380,000 before delete (500,000 + 100,000 - 220,000)",
              accs["Wallet A"]["balance"] == 380000,
              str(accs.get("Wallet A")))

        carol.delete(f"/api/transactions/{taxi_id}")
        st, sc = carol.api("/api/state")
        accs = {x["name"]: x for x in sc["accounts"]}
        check("audit: after delete A = 380,000, B = 200,000 (delete reverses)",
              accs["Wallet A"]["balance"] == 380000
              and accs["Wallet B"]["balance"] == 200000,
              str({k: v["balance"] for k, v in accs.items()}))
        check("audit: opening balances stay 500,000 / 200,000",
              accs["Wallet A"]["opening_balance"] == 500000
              and accs["Wallet B"]["opening_balance"] == 200000)
        inc, exp, left, net = totals(sc)
        acct_total = sum(x["balance"] for x in sc["accounts"])
        check("audit: accounts total = 580,000", acct_total == 580000,
              str(acct_total))
        check("audit: net worth = 580,000 + 1,000,000 - 100,000 = 1,480,000",
              net == 1480000, str(net))
        check("audit: month income/expense/leftover = 100,000/220,000/-120,000",
              (inc, exp, left) == (100000, 220000, -120000),
              str((inc, exp, left)))
        by = {}
        for trow in sc["transactions"]:
            if (trow["kind"] == "expense"
                    and trow["txn_date"].startswith(TODAY[:7])):
                by[trow["category"]] = by.get(trow["category"], 0) \
                    + trow["amount"]
        check("audit: Top Spending sums to 220,000 (Food only, post-delete)",
              sum(by.values()) == 220000 and by.get("Food") == 220000,
              str(by))

        st, r = carol.post_json("/api/messages",
                                {"to": "adminx", "type": "card"})
        card = r.get("message", {})
        check("audit: daily card = today's figures, 2 items",
              card.get("scope") == "day" and card.get("income") == 100000
              and card.get("expense") == 220000
              and card.get("leftover") == -120000
              and len(card.get("items") or []) == 2, str(card))
        st, r = carol.post_json("/api/messages",
                                {"to": "adminx", "type": "card",
                                 "scope": "month"})
        mcard = r.get("message", {})
        check("audit: monthly card = month's figures, scope+month set",
              mcard.get("scope") == "month" and mcard.get("month") == TODAY[:7]
              and mcard.get("income") == 100000
              and mcard.get("expense") == 220000
              and mcard.get("leftover") == -120000
              and len(mcard.get("items") or []) == 2, str(mcard))

        # Admin views — through a FRESH session that only admin-logs in
        # (no app session at all), proving the gate's independence.
        gate = Session(AUDIT_BASE)
        st, _, hd = gate.get("/admin/overview", follow=False)
        check("audit: fresh session blocked from /admin/overview",
              st in (301, 302)
              and "/admin/login" in (hd.get("Location") or ""), f"st={st}")
        gate.post_form("/admin/login", {"email": "admin2@test.local",
                                        "password": "admin2-pass"})
        st, body, _ = gate.get("/admin/overview")
        html = body.decode("utf-8", "ignore")
        stats = re.findall(r'class="sv num">([^<]+)<', html)
        check("audit: admin overview stats = 2/2/2/2",
              stats[:4] == ["2", "2", "2", "2"], f"stats={stats[:4]}")
        check("audit: admin flow shows 100,000 / 220,000 / 120,000",
              "100,000" in html and "220,000" in html
              and "120,000" in html)
        st, body, _ = gate.get("/admin/users")
        html = body.decode("utf-8", "ignore")
        check("audit: admin Users shows Carol net worth 1,480,000",
              "Carol Audit" in html and "1,480,000" in html)
        st, body, _ = gate.get("/admin/transactions")
        html = body.decode("utf-8", "ignore")
        check("audit: admin Transactions = Salary + Food, no Transport",
              "Salary" in html and "Food" in html
              and "Transport" not in html)

        st, sa2 = adm.api("/api/state")
        check("audit: admin's own finance state is empty (isolation)",
              sa2["transactions"] == [] and sa2["accounts"] == [])
        check("audit: Carol's state holds exactly her 2 transactions",
              len(sc["transactions"]) == 2, str(len(sc["transactions"])))

        # overspend -> negative computed balance, no clamping
        st, r = carol.post_json("/api/transactions", {
            "kind": "expense", "amount": 500000, "category": "Bills",
            "txn_date": TODAY, "note": "Big bill", "account_id": wb})
        big_id = r.get("id")
        st, sc = carol.api("/api/state")
        accs = {x["name"]: x for x in sc["accounts"]}
        check("audit: overspend takes B to -300,000 (no clamping)",
              accs["Wallet B"]["balance"] == -300000,
              str(accs.get("Wallet B")))
        carol.delete(f"/api/transactions/{big_id}")
        st, sc = carol.api("/api/state")
        accs = {x["name"]: x for x in sc["accounts"]}
        check("audit: deleting the overspend restores B to 200,000",
              accs["Wallet B"]["balance"] == 200000)

        # cross-user + bogus linkage rejected
        st, r = adm.post_json("/api/accounts", {
            "name": "AdminWallet", "type": "cash", "brand": "",
            "balance": 1000, "note": ""})
        adm_wallet = r.get("id")
        st, _ = carol.post_json("/api/transactions", {
            "kind": "expense", "amount": 100, "category": "Food",
            "txn_date": TODAY, "note": "x", "account_id": adm_wallet})
        check("audit: linking to another user's wallet -> 400",
              st == 400, f"st={st}")
        st, _ = carol.post_json("/api/transactions", {
            "kind": "expense", "amount": 100, "category": "Food",
            "txn_date": TODAY, "note": "x", "account_id": 999999})
        check("audit: linking to a bogus wallet -> 400", st == 400,
              f"st={st}")

        # editing the OPENING balance recomputes the current balance
        st, _ = carol.put_json(f"/api/accounts/{wa}", {"balance": 600000})
        check("audit: PUT opening balance accepted", st == 200, f"st={st}")
        st, sc = carol.api("/api/state")
        accs = {x["name"]: x for x in sc["accounts"]}
        check("audit: A recomputes to 480,000 (600,000 + 100,000 - 220,000)",
              accs["Wallet A"]["balance"] == 480000
              and accs["Wallet A"]["opening_balance"] == 600000,
              str(accs.get("Wallet A")))

        dave = Session(AUDIT_BASE)
        register(dave, "Dave Audit", "dave@test.local", "dave-pass")
        st, _ = dave.put_json(f"/api/accounts/{wa}", {"balance": 1})
        check("audit: PUT another user's account -> 404", st == 404,
              f"st={st}")
        st, r = dave.post_json("/api/transactions", {
            "kind": "expense", "amount": 5000, "category": "Food",
            "txn_date": TODAY, "note": "solo"})
        check("audit: user with no wallets can save an unlinked txn",
              st == 200, f"st={st}")
        st, sd = dave.api("/api/state")
        check("audit: unlinked txn has account_id null",
              len(sd["transactions"]) == 1
              and sd["transactions"][0]["account_id"] is None,
              str(sd["transactions"]))
        st, _ = dave.post_json("/api/transactions", {
            "kind": "expense", "amount": 100, "category": "Food",
            "txn_date": TODAY, "note": "x", "account_id": wa})
        check("audit: Dave linking to Carol's wallet -> 400", st == 400,
              f"st={st}")

        # deleting a wallet UNLINKS its transactions (history survives)
        st, r = carol.post_json("/api/accounts", {
            "name": "Temp Wallet", "type": "cash", "brand": "",
            "balance": 50000, "note": ""})
        tw = r.get("id")
        st, r = carol.post_json("/api/transactions", {
            "kind": "expense", "amount": 10000, "category": "Other",
            "txn_date": TODAY, "note": "temp", "account_id": tw})
        temp_txn = r.get("id")
        st, sc = carol.api("/api/state")
        accs = {x["name"]: x for x in sc["accounts"]}
        check("audit: Temp Wallet computes 40,000; accounts total 720,000",
              accs["Temp Wallet"]["balance"] == 40000
              and sum(x["balance"] for x in sc["accounts"]) == 720000,
              str({k: v["balance"] for k, v in accs.items()}))
        carol.delete(f"/api/accounts/{tw}")
        st, sc = carol.api("/api/state")
        trow = next((x for x in sc["transactions"]
                     if x["id"] == temp_txn), None)
        inc, exp, left, net = totals(sc)
        check("audit: wallet deleted -> txn survives unlinked, total 680,000",
              trow is not None and trow["account_id"] is None
              and sum(x["balance"] for x in sc["accounts"]) == 680000,
              str(trow))
        check("audit: month expense now 230,000 (unlinked still counts)",
              exp == 230000, str(exp))

        # ---- creds: admin edits usernames + resets passwords --------------
        # (runs LAST in this phase: the self-reset retires adm's sessions)
        con = sqlite3.connect(old_db)
        ids = {r[1]: r[0] for r in con.execute(
            "SELECT id, email FROM users").fetchall()}

        def uname(uid):
            row = con.execute("SELECT username FROM users WHERE id=?",
                              (uid,)).fetchone()
            return row[0] if row else None

        adm_id = ids["admin2@test.local"]
        carol_id = ids["carol2@test.local"]
        dave_id = ids["dave@test.local"]
        check("audit setup: carol has username carolx, dave has none",
              uname(carol_id) == "carolx" and uname(dave_id) is None,
              f"carol={uname(carol_id)} dave={uname(dave_id)}")

        # the admin gate itself accepts the username as identifier
        st, _, hd = adm.post_form("/admin/login",
                                  {"email": "adminx",
                                   "password": "admin2-pass"},
                                  follow=False)
        check("audit: admin gate login via USERNAME -> overview",
              st == 302 and "/admin/overview" in (hd.get("Location") or ""),
              f"st={st} loc={hd.get('Location')}")
        st, body, _ = adm.get("/admin/users")
        body = body.decode("utf-8", "ignore")
        check("audit: Users page carries the credential editor forms",
              st == 200 and 'id="mUserForm"' in body
              and 'id="mPassForm"' in body and "data-id=" in body,
              f"st={st}")

        # unauthenticated + app-session-only POSTs are refused, nothing moves
        anon2 = Session(AUDIT_BASE)
        st, _, hd = anon2.post_form(
            f"/admin/users/{dave_id}/username", {"username": "davey"},
            follow=False)
        check("audit: unauthenticated username-edit POST refused",
              st == 302 and "/admin/login" in (hd.get("Location") or "")
              and uname(dave_id) is None, f"st={st}")
        st, _, hd = carol.post_form(
            f"/admin/users/{dave_id}/username", {"username": "davey"},
            follow=False)
        check("audit: app-session-only username-edit POST refused",
              st == 302 and "/admin/login" in (hd.get("Location") or "")
              and uname(dave_id) is None, f"st={st}")
        st, _, hd = carol.post_form(
            f"/admin/users/{dave_id}/password",
            {"password": "hacked-1", "confirm_password": "hacked-1"},
            follow=False)
        check("audit: app-session-only password-reset POST refused",
              st == 302 and "/admin/login" in (hd.get("Location") or ""),
              f"st={st}")

        # validation: invalid format + duplicates change nothing
        st, _, hd = adm.post_form(
            f"/admin/users/{carol_id}/username",
            {"username": "Bad Name!"}, follow=False)
        check("audit: invalid-format username edit rejected (u_invalid)",
              "uerr=u_invalid" in (hd.get("Location") or "")
              and uname(carol_id) == "carolx",
              f"loc={hd.get('Location')}")
        st, _, hd = adm.post_form(
            f"/admin/users/{dave_id}/username", {"username": "adminx"},
            follow=False)
        check("audit: duplicate username edit rejected (u_taken)",
              "uerr=u_taken" in (hd.get("Location") or "")
              and uname(dave_id) is None, f"loc={hd.get('Location')}")
        st, _, hd = adm.post_form(
            f"/admin/users/{dave_id}/username", {"username": "AdminX"},
            follow=False)
        check("audit: case-variant duplicate also rejected",
              "uerr=u_taken" in (hd.get("Location") or "")
              and uname(dave_id) is None, f"loc={hd.get('Location')}")

        # set a username for a user who has none -> login + search use it
        st, _, hd = adm.post_form(
            f"/admin/users/{dave_id}/username", {"username": "davey"},
            follow=False)
        check("audit: admin sets dave's first username (davey)",
              "ok=cred" in (hd.get("Location") or "")
              and uname(dave_id) == "davey", f"loc={hd.get('Location')}")
        dv = Session(AUDIT_BASE)
        dv.post_form("/login", {"email": "davey", "password": "dave-pass"})
        st, _ = dv.api("/api/state")
        check("audit: dave logs in with the admin-set username",
              st == 200, f"st={st}")
        st, res = adm.api("/api/users/search?q=davey")
        check("audit: chat search finds dave by the new username",
              any(u["username"] == "davey" for u in res), str(res))

        # rename carol -> old name stops resolving everywhere, new works
        st, _, hd = adm.post_form(
            f"/admin/users/{carol_id}/username", {"username": "caroly"},
            follow=False)
        check("audit: admin renames carolx -> caroly",
              "ok=cred" in (hd.get("Location") or "")
              and uname(carol_id) == "caroly", f"loc={hd.get('Location')}")
        old = Session(AUDIT_BASE)
        old.post_form("/login", {"email": "carolx",
                                 "password": "carol-pass"})
        st, _ = old.api("/api/state")
        check("audit: login with the OLD username now fails", st == 401,
              f"st={st}")
        new = Session(AUDIT_BASE)
        new.post_form("/login", {"email": "caroly",
                                 "password": "carol-pass"})
        st, _ = new.api("/api/state")
        check("audit: login with the NEW username works", st == 200,
              f"st={st}")
        st, res = adm.api("/api/users/search?q=carolx")
        check("audit: chat search no longer finds the old username",
              not any(u["username"] == "carolx" for u in res), str(res))
        st, res = adm.api("/api/users/search?q=caroly")
        check("audit: chat search finds the new username",
              any(u["username"] == "caroly" for u in res), str(res))
        st, _ = adm.post_json("/api/messages",
                              {"to": "caroly", "text": "hi new name"})
        check("audit: message addressed to NEW username delivers",
              st == 200, f"st={st}")
        st, _ = adm.post_json("/api/messages",
                              {"to": "carolx", "text": "hi old name"})
        check("audit: message addressed to OLD username 404s", st == 404,
              f"st={st}")

        # password reset validation (dave's password must not move)
        st, _, hd = adm.post_form(
            f"/admin/users/{dave_id}/password",
            {"password": "short", "confirm_password": "short"},
            follow=False)
        check("audit: too-short reset rejected (p_short)",
              "perr=p_short" in (hd.get("Location") or ""),
              f"loc={hd.get('Location')}")
        st, _, hd = adm.post_form(
            f"/admin/users/{dave_id}/password",
            {"password": "dave-new-1", "confirm_password": "dave-new-2"},
            follow=False)
        check("audit: mismatched reset rejected (p_mismatch)",
              "perr=p_mismatch" in (hd.get("Location") or ""),
              f"loc={hd.get('Location')}")

        # password reset: old dies everywhere, existing session invalidated
        dv2 = Session(AUDIT_BASE)
        dv2.post_form("/login", {"email": "dave@test.local",
                                 "password": "dave-pass"})
        st, _ = dv2.api("/api/state")
        check("audit: dave pre-reset session is live", st == 200,
              f"st={st}")
        st, _, hd = adm.post_form(
            f"/admin/users/{dave_id}/password",
            {"password": "dave-new-1", "confirm_password": "dave-new-1"},
            follow=False)
        svrow = con.execute("SELECT session_version FROM users WHERE id=?",
                            (dave_id,)).fetchone()
        check("audit: admin resets dave's password (+session_version bump)",
              "ok=cred" in (hd.get("Location") or "")
              and svrow and svrow[0] == 1,
              f"loc={hd.get('Location')} sv={svrow}")
        st, _ = dv2.api("/api/state")
        check("audit: dave's EXISTING session is invalidated by the reset",
              st == 401, f"st={st}")
        dv3 = Session(AUDIT_BASE)
        dv3.post_form("/login", {"email": "dave@test.local",
                                 "password": "dave-pass"})
        st, _ = dv3.api("/api/state")
        check("audit: dave OLD password now fails", st == 401, f"st={st}")
        dv4 = Session(AUDIT_BASE)
        dv4.post_form("/login", {"email": "davey",
                                 "password": "dave-new-1"})
        st, _ = dv4.api("/api/state")
        check("audit: dave NEW password works (username login too)",
              st == 200, f"st={st}")

        # admin edits OWN username: no self-lockout (session is by user id)
        st, _, hd = adm.post_form(
            f"/admin/users/{adm_id}/username", {"username": "adminx2"},
            follow=False)
        check("audit: admin renames OWN username",
              "ok=cred" in (hd.get("Location") or "")
              and uname(adm_id) == "adminx2", f"loc={hd.get('Location')}")
        st, body, _ = adm.get("/admin/overview")
        check("audit: own admin session survives the username change",
              st == 200, f"st={st}")

        # admin resets OWN password: both of their sessions retire, and
        # the new password works at BOTH doors (username identifier too)
        st, _, hd = adm.post_form(
            f"/admin/users/{adm_id}/password",
            {"password": "admin2-new", "confirm_password": "admin2-new"},
            follow=False)
        check("audit: admin resets OWN password",
              "ok=cred" in (hd.get("Location") or ""),
              f"loc={hd.get('Location')}")
        st, _, hd = adm.get("/admin/overview", follow=False)
        check("audit: own ADMIN session retired by self-reset",
              st == 302 and "/admin/login" in (hd.get("Location") or ""),
              f"st={st}")
        st, _ = adm.api("/api/state")
        check("audit: own APP session retired by self-reset", st == 401,
              f"st={st}")
        adm2 = Session(AUDIT_BASE)
        adm2.post_form("/login", {"email": "admin2@test.local",
                                  "password": "admin2-pass"})
        st, _ = adm2.api("/api/state")
        check("audit: admin OLD password fails at app login", st == 401,
              f"st={st}")
        st, _, hd = adm2.post_form("/admin/login",
                                   {"email": "adminx2",
                                    "password": "admin2-new"},
                                   follow=False)
        check("audit: NEW password + NEW username work at /admin/login",
              st == 302 and "/admin/overview" in (hd.get("Location") or ""),
              f"st={st} loc={hd.get('Location')}")
        st, body, _ = adm2.get("/admin/overview")
        check("audit: admin back into the dashboard after self-reset",
              st == 200, f"st={st}")
        con.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def finish(proc):
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n==== {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed "
          f"({len(failed)} failed) ====")
    for name, _, detail in failed:
        print("FAILED:", name, detail)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
