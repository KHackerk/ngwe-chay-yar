# -*- coding: utf-8 -*-
"""Ngwe Chay Yar — Admin module (Agent B).

All /admin/* routes. Every number, user, transaction, conversation and
support thread rendered here comes from SQL over the real tables via
db.py — there is NO fabricated data anywhere in this module.

API used (see CONTRACT.md):
  db.q(sql, params) / db.q1(sql, params) / db.execute(sql, params)
  db.insert(sql, params) / db.get_setting(key) / db.set_setting(key, value)
  common.current_user()  (Flask session key ``uid``)
SQL uses ``?`` placeholders (db.py converts for Postgres).
"""
import os
import time
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (redirect, render_template, request, session, url_for,
                   abort)
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

import db

try:
    from common import current_user
except Exception:  # common.py is built in parallel; fall back to contract spec
    def current_user():
        uid = session.get('uid')
        if not uid:
            return None
        u = db.q1("SELECT * FROM users WHERE id=?", (uid,))
        if not u:
            return None
        # suspended users are treated as logged out, except admins
        if u.get('suspended') and u.get('role') != 'admin':
            return None
        return u

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_UPLOAD = 10 * 1024 * 1024  # 10 MB

# ---------------------------------------------------------------------------
# i18n — ported verbatim from the Admin Preview V3 STRINGS table (96 keys
# per language). The cat_*/note_*/snip*/time_* entries are translations kept
# for table completeness; no fake data is ever rendered from them.
# ---------------------------------------------------------------------------
ADMIN_STRINGS = {
    'en': {
        'nav_overview': 'Overview', 'nav_users': 'Users',
        'nav_transactions': 'Transactions', 'nav_messages': 'Messages',
        'nav_settings': 'Settings', 'nav_support': 'Support',
        'sup_search_ph': 'Search username...',
        'sup_thread_pill': 'Support Thread',
        'sup_conv_sub': 'Customer conversation · replies appear in their Support chat',
        'sup_reply_ph': 'Reply to {u}...',
        'sup_attach_hint': 'Photos, videos and documents up to 10 MB can be attached.',
        'sup_pin': 'Pin', 'sup_admin': 'Admin', 'sup_back': 'Back',
        'sup_photo': 'Photo',
        'role_owner': 'Owner', 'search_ph': 'Search users...',
        'stat_total_users': 'Total Users', 'stat_active_today': 'Active Today',
        'stat_txn_month': 'Transactions This Month',
        'stat_new_users': 'New Users This Month',
        'flow_title': 'Platform Money Flow',
        'flow_sub': 'All users combined — this month',
        'flow_income': 'Income', 'flow_expenses': 'Expenses',
        'flow_leftover': 'Leftover',
        'chart_title': 'Transaction Volume',
        'chart_sub': 'Transactions per month — last 6 months',
        'newest_users': 'Newest Users', 'recent_txn': 'Recent Transactions',
        'users_sub': 'All registered users on the platform',
        'col_user': 'User', 'col_joined': 'Joined', 'col_txns': 'Transactions',
        'col_networth': 'Net Worth', 'col_status': 'Status',
        'col_action': 'Action',
        'status_active': 'Active', 'status_suspended': 'Suspended',
        'act_view': 'View', 'act_suspend': 'Suspend',
        'act_activate': 'Activate',
        'users_search_ph': 'Search by name or @username...',
        'no_results': 'No users found.', 'close': 'Close',
        'txn_sub': 'Latest transactions across all users',
        'txn_all': 'All', 'txn_income': 'Income', 'txn_expenses': 'Expenses',
        'col_date': 'Date', 'col_category': 'Category',
        'col_note': 'Note', 'col_amount': 'Amount',
        'msg_conv': 'Conversations', 'msg_today': 'Messages Today',
        'msg_attach': 'With Attachments', 'msg_recent': 'Recent Conversations',
        'msg_overview_note': 'Usage overview only — message content is not managed here.',
        'settings_sub': 'Platform controls',
        'set_registration': 'Open Registration',
        'set_registration_sub': 'New users can create their own account',
        'set_maintenance': 'Maintenance Mode',
        'set_maintenance_sub': 'When on, only the admin can use the app',
        'set_messages': 'Allow Messages',
        'set_messages_sub': 'Users can send messages to each other',
        'admin_account': 'Admin Account', 'admin_role': 'Role',
        'admin_note_title': 'In the live app',
        'admin_note': 'In the live app this dashboard sits behind an admin login at /admin — only the admin account can open it.',
        'cat_salary': 'Salary', 'cat_food': 'Food',
        'cat_transport': 'Transport', 'cat_business': 'Business',
        'cat_bills': 'Bills', 'cat_shopping': 'Shopping',
        'cat_freelance': 'Freelance', 'cat_other': 'Other',
        'note_salary': 'Monthly salary', 'note_lunch': 'Lunch with friends',
        'note_taxi': 'Taxi fare', 'note_sales': 'Shop sales',
        'note_electric': 'Electricity bill',
        'note_groceries': 'Weekly groceries', 'note_design': 'Design job',
        'note_clothes': 'Clothes', 'note_bus': 'Bus + taxi',
        'note_resale': 'Gold resale', 'note_snacks': 'Snacks & coffee',
        'snip1': "Shared today's costs with you",
        'snip2': 'I will send it via KBZ Pay', 'snip3': 'Sent a photo',
        'snip4': 'How much is left this month?',
        'time_2m': '2 min ago', 'time_26m': '26 min ago',
        'time_1h': '1 hr ago', 'time_3h': '3 hr ago',
        # --- extra keys for the real app (login, empty states, misc) ---
        'email': 'Email', 'password': 'Password', 'sign_in': 'Sign in',
        'admin_login_note': 'Admin sign in — only the admin account can open the dashboard.',
        'login_error_credentials': 'Invalid email or password.',
        'login_error_not_admin': 'This account is not an admin account.',
        'login_error_suspended': 'This account is suspended.',
        'logout': 'Log out', 'save': 'Save', 'saved': 'Saved.',
        'col_email': 'Email', 'col_username': 'Username', 'col_role': 'Role',
        'empty_users': 'No users yet. New registrations will appear here.',
        'empty_txn': 'No transactions yet.',
        'empty_recent': 'Nothing here yet.',
        'empty_convs': 'No conversations yet.',
        'empty_threads': 'No support threads yet. When a customer writes to Support, their thread appears here.',
        'empty_thread': 'No messages in this thread yet.',
        'select_thread': 'Select a conversation',
        'role_admin': 'Admin', 'role_user': 'User',
        'settings_saved': 'Settings saved.',
        'support': 'Support', 'file': 'File', 'video': 'Video',
        'card_shared': 'Shared daily costs',
        'back_to_app': 'Back to app',
    },
    'my': {
        'nav_overview': 'ခြုံငုံအမြင်', 'nav_users': 'အသုံးပြုသူများ',
        'nav_transactions': 'ငွေစာရင်းများ', 'nav_messages': 'မက်ဆေ့ချ်များ',
        'nav_settings': 'ဆက်တင်များ', 'nav_support': 'အကူအညီ',
        'sup_search_ph': 'username ဖြင့် ရှာရန်...',
        'sup_thread_pill': 'အကူအညီ စကားပြောခန်း',
        'sup_conv_sub': 'ဖောက်သည် စကားပြောခန်း · ပြန်စာများကို သူ၏ Support chat တွင် မြင်ရမည်',
        'sup_reply_ph': '{u} သို့ ပြန်စာ ရေးရန်...',
        'sup_attach_hint': 'ဓာတ်ပုံ၊ ဗီဒီယို နှင့် စာရွက်စာတမ်းများ 10 MB အထိ တွဲနိုင်သည်။',
        'sup_pin': 'ပင်ထိုး', 'sup_admin': 'Admin', 'sup_back': 'နောက်သို့',
        'sup_photo': 'ဓာတ်ပုံ',
        'role_owner': 'ပိုင်ရှင်', 'search_ph': 'အသုံးပြုသူ ရှာရန်...',
        'stat_total_users': 'စုစုပေါင်း အသုံးပြုသူ',
        'stat_active_today': 'ယနေ့ အသုံးပြုသူ',
        'stat_txn_month': 'ဒီလ ငွေစာရင်း',
        'stat_new_users': 'ဒီလ အသုံးပြုသူအသစ်',
        'flow_title': 'ပလက်ဖောင်း ငွေစီးဆင်းမှု',
        'flow_sub': 'အသုံးပြုသူ အားလုံး ပေါင်း — ဒီလ',
        'flow_income': 'ဝင်ငွေ', 'flow_expenses': 'ထွက်ငွေ',
        'flow_leftover': 'ကျန်ငွေ',
        'chart_title': 'ငွေစာရင်း ပမာဏ',
        'chart_sub': 'လအလိုက် ငွေစာရင်း — နောက်ဆုံး ၆ လ',
        'newest_users': 'နောက်ဆုံး အသုံးပြုသူများ',
        'recent_txn': 'မကြာသေးမီ ငွေစာရင်းများ',
        'users_sub': 'ပလက်ဖောင်းရှိ စာရင်းသွင်းထားသူ အားလုံး',
        'col_user': 'အသုံးပြုသူ', 'col_joined': 'ဝင်ရောက်သည့်ရက်',
        'col_txns': 'ငွေစာရင်း', 'col_networth': 'စုစုပေါင်း ပိုင်ဆိုင်မှု',
        'col_status': 'အခြေအနေ', 'col_action': 'လုပ်ဆောင်ချက်',
        'status_active': 'အသုံးပြုဆဲ', 'status_suspended': 'ရပ်ဆိုင်းထားသည်',
        'act_view': 'ကြည့်ရန်', 'act_suspend': 'ရပ်ဆိုင်းရန်',
        'act_activate': 'ပြန်ဖွင့်ရန်',
        'users_search_ph': 'နာမည် သို့မဟုတ် @username ဖြင့် ရှာရန်...',
        'no_results': 'ရှာမတွေ့ပါ။', 'close': 'ပိတ်ရန်',
        'txn_sub': 'အသုံးပြုသူ အားလုံးထံမှ နောက်ဆုံး ငွေစာရင်းများ',
        'txn_all': 'အားလုံး', 'txn_income': 'ဝင်ငွေ', 'txn_expenses': 'ထွက်ငွေ',
        'col_date': 'ရက်စွဲ', 'col_category': 'အမျိုးအစား',
        'col_note': 'မှတ်ချက်', 'col_amount': 'ပမာဏ',
        'msg_conv': 'စကားပြောခန်းများ', 'msg_today': 'ယနေ့ စာတိုများ',
        'msg_attach': 'ဖိုင်တွဲ ပါဝင်သော',
        'msg_recent': 'မကြာသေးမီ စကားပြောခန်းများ',
        'msg_overview_note': 'အသုံးပြုမှု ခြုံငုံအမြင်သာ ဖြစ်သည် — စာအကြောင်းအရာကို ဒီနေရာမှ စီမံခြင်း မပြုပါ။',
        'settings_sub': 'ပလက်ဖောင်း ထိန်းချုပ်မှုများ',
        'set_registration': 'အကောင့်ဖွင့်ခွင့်',
        'set_registration_sub': 'အသုံးပြုသူအသစ်များ ကိုယ်တိုင် အကောင့်ဖွင့်နိုင်သည်',
        'set_maintenance': 'ပြုပြင်ထိန်းသိမ်း ပုံစံ',
        'set_maintenance_sub': 'ဖွင့်ထားလျှင် admin မှလွဲ၍ မည်သူမျှ app ကို သုံး၍ မရပါ',
        'set_messages': 'စာပို့ခြင်း ခွင့်ပြုရန်',
        'set_messages_sub': 'အသုံးပြုသူအချင်းချင်း စာပို့နိုင်သည်',
        'admin_account': 'Admin အကောင့်', 'admin_role': 'ရာထူး',
        'admin_note_title': 'တကယ့် app တွင်',
        'admin_note': 'တကယ့် app တွင် ဒီ dashboard ကို admin login ဖြင့်သာ ဝင်နိုင်သော /admin နေရာတွင် ကာကွယ်ထားပါမည် — admin အကောင့်တစ်ခုတည်းသာ ဖွင့်နိုင်ပါသည်။',
        'cat_salary': 'လစာ', 'cat_food': 'အစားအသောက်',
        'cat_transport': 'သွားလာရေး', 'cat_business': 'စီးပွားရေး',
        'cat_bills': 'ဘီလ်များ', 'cat_shopping': 'ဈေးဝယ်ခြင်း',
        'cat_freelance': 'Freelance', 'cat_other': 'အခြား',
        'note_salary': 'လစဉ် လစာ',
        'note_lunch': 'သူငယ်ချင်းများနှင့် နေ့လယ်စာ',
        'note_taxi': 'Taxi ခ', 'note_sales': 'ဆိုင် အရောင်း',
        'note_electric': 'လျှပ်စစ်မီတာခ',
        'note_groceries': 'အပတ်စဉ် ဈေးဝယ်စရာ',
        'note_design': 'Design အလုပ်', 'note_clothes': 'အဝတ်အထည်',
        'note_bus': 'Bus + Taxi', 'note_resale': 'ရွှေ ပြန်ရောင်း',
        'note_snacks': 'မုန့် နှင့် ကော်ဖီ',
        'snip1': 'ဒီနေ့ အသုံးစရိတ် share လိုက်ပြီ',
        'snip2': 'KBZ Pay နဲ့ လွှဲလိုက်မယ်', 'snip3': 'ဓာတ်ပုံ ပို့လိုက်တယ်',
        'snip4': 'ဒီလ ကျန်ငွေ ဘယ်လောက်လဲ',
        'time_2m': 'လွန်ခဲ့သော ၂ မိနစ်', 'time_26m': 'လွန်ခဲ့သော ၂၆ မိနစ်',
        'time_1h': 'လွန်ခဲ့သော ၁ နာရီ', 'time_3h': 'လွန်ခဲ့သော ၃ နာရီ',
        # --- extra keys for the real app (login, empty states, misc) ---
        'email': 'အီးမေးလ်', 'password': 'စကားဝှက်',
        'sign_in': 'ဝင်ရောက်ရန်',
        'admin_login_note': 'Admin ဝင်ရောက်ရန် — admin အကောင့်တစ်ခုတည်းသာ dashboard ကို ဖွင့်နိုင်ပါသည်။',
        'login_error_credentials': 'အီးမေးလ် သို့မဟုတ် စကားဝှက် မမှန်ပါ။',
        'login_error_not_admin': 'ဤအကောင့်သည် admin အကောင့် မဟုတ်ပါ။',
        'login_error_suspended': 'ဤအကောင့်ကို ရပ်ဆိုင်းထားပါသည်။',
        'logout': 'ထွက်ရန်', 'save': 'သိမ်းရန်', 'saved': 'သိမ်းပြီးပါပြီ။',
        'col_email': 'အီးမေးလ်', 'col_username': 'Username',
        'col_role': 'ရာထူး',
        'empty_users': 'အသုံးပြုသူ မရှိသေးပါ။ စာရင်းသွင်းသူအသစ်များ ဒီနေရာတွင် ပေါ်လာပါမည်။',
        'empty_txn': 'ငွေစာရင်း မရှိသေးပါ။',
        'empty_recent': 'ဒီနေရာတွင် ဘာမျှ မရှိသေးပါ။',
        'empty_convs': 'စကားပြောခန်း မရှိသေးပါ။',
        'empty_threads': 'အကူအညီ စကားပြောခန်း မရှိသေးပါ။ ဖောက်သည် တစ်ဦးက Support သို့ စာရေးသောအခါ သူ၏ thread ဒီနေရာတွင် ပေါ်လာပါမည်။',
        'empty_thread': 'ဒီ thread တွင် စာ မရှိသေးပါ။',
        'select_thread': 'စကားပြောခန်း တစ်ခုကို ရွေးပါ',
        'role_admin': 'Admin', 'role_user': 'အသုံးပြုသူ',
        'settings_saved': 'ဆက်တင်များ သိမ်းပြီးပါပြီ။',
        'support': 'အကူအညီ', 'file': 'ဖိုင်', 'video': 'ဗီဒီယို',
        'card_shared': 'နေ့စဉ် အသုံးစရိတ် share ထားသည်',
        'back_to_app': 'App သို့ ပြန်သွားရန်',
    },
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _lang():
    lang = request.args.get('lang') or request.cookies.get('admin_lang') or 'my'
    return lang if lang in ADMIN_STRINGS else 'my'


def _s(lang=None):
    return ADMIN_STRINGS[lang or _lang()]


def initials(name):
    parts = (name or '?').strip().split()
    return ''.join(p[0] for p in parts[:2]).upper() or '?'


def _ctx(page, lang, admin, **kw):
    ctx = {'s': _s(lang), 'lang': lang, 'admin': admin, 'page': page,
           'initials': initials}
    ctx.update(kw)
    return ctx


def admin_guard(view):
    """Signed-out -> /admin/login. Signed-in non-admin -> /admin/login
    with a 'not_admin' error (consistent guard behaviour everywhere)."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for('admin_login'))
        if user.get('role') != 'admin':
            return redirect(url_for('admin_login', error='not_admin'))
        return view(user, *args, **kwargs)
    return wrapped


def _count(sql, params=()):
    row = db.q1(sql, params)
    return int(row['c']) if row and row.get('c') is not None else 0


def _preview(msg, s):
    """Last-message preview text for a messages/support_messages row."""
    if msg.get('text'):
        return msg['text']
    if msg.get('file_path'):
        if msg.get('type') == 'image':
            return s['sup_photo']
        if msg.get('type') == 'video':
            return s['video']
        return msg.get('file_name') or s['file']
    if msg.get('type') == 'card':
        return s['card_shared']
    return ''


def _time_label(created_at):
    return (created_at or '')[11:16]


def _day_label(day_str, lang):
    try:
        d = datetime.strptime(day_str, '%Y-%m-%d').date()
    except Exception:
        return day_str
    if lang == 'my':
        return d.strftime('%a, %b %d, %Y')
    return d.strftime('%a, %b %d, %Y')


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
def register(app):

    @app.route('/admin')
    def admin_root():
        return redirect(url_for('admin_overview'))

    @app.route('/admin/lang/<lang>')
    def admin_lang(lang):
        if lang not in ADMIN_STRINGS:
            lang = 'my'
        resp = redirect(request.referrer or url_for('admin_overview'))
        resp.set_cookie('admin_lang', lang, max_age=365 * 24 * 3600,
                        samesite='Lax')
        return resp

    # ---------------- login ----------------
    @app.route('/admin/login', methods=['GET', 'POST'])
    def admin_login():
        lang = _lang()
        s = _s(lang)
        user = current_user()
        if user and user.get('role') == 'admin' and request.method == 'GET':
            return redirect(url_for('admin_overview'))
        error = None
        err_code = request.args.get('error')
        if err_code == 'not_admin':
            error = s['login_error_not_admin']
        if request.method == 'POST':
            email = (request.form.get('email') or '').strip().lower()
            password = request.form.get('password') or ''
            row = db.q1("SELECT * FROM users WHERE email=?", (email,))
            if not row or not check_password_hash(
                    row.get('password_hash') or '', password):
                error = s['login_error_credentials']
            elif row.get('suspended'):
                error = s['login_error_suspended']
            elif row.get('role') != 'admin':
                error = s['login_error_not_admin']
            else:
                session['uid'] = row['id']
                nxt = request.args.get('next') or ''
                if nxt.startswith('/admin'):
                    return redirect(nxt)
                return redirect(url_for('admin_overview'))
        resp = app.make_response(render_template(
            'admin/login.html', s=s, lang=lang, error=error))
        if request.args.get('lang'):
            resp.set_cookie('admin_lang', lang, max_age=365 * 24 * 3600,
                            samesite='Lax')
        return resp

    # ---------------- overview ----------------
    @app.route('/admin/overview')
    @admin_guard
    def admin_overview(admin):
        lang = _lang()
        s = _s(lang)
        today = date.today()
        today_s = today.isoformat()
        month_s = today_s[:7]

        total_users = _count("SELECT COUNT(*) AS c FROM users")
        active_today = _count(
            "SELECT COUNT(*) AS c FROM ("
            " SELECT user_id AS uid FROM transactions"
            "  WHERE substr(created_at,1,10)=?"
            " UNION SELECT sender_id FROM messages"
            "  WHERE substr(created_at,1,10)=?"
            " UNION SELECT recipient_id FROM messages"
            "  WHERE substr(created_at,1,10)=?"
            " UNION SELECT user_id FROM support_messages"
            "  WHERE substr(created_at,1,10)=?)",
            (today_s, today_s, today_s, today_s))
        flow = db.q1(
            "SELECT COUNT(*) AS c,"
            " COALESCE(SUM(CASE WHEN kind='income' THEN amount ELSE 0 END),0)"
            "  AS income,"
            " COALESCE(SUM(CASE WHEN kind='expense' THEN amount ELSE 0 END),0)"
            "  AS expense"
            " FROM transactions WHERE substr(txn_date,1,7)=?", (month_s,))
        txn_month = int(flow['c']) if flow else 0
        income = int(flow['income']) if flow else 0
        expense = int(flow['expense']) if flow else 0
        leftover = income - expense
        new_users = _count(
            "SELECT COUNT(*) AS c FROM users WHERE substr(created_at,1,7)=?",
            (month_s,))

        # 6-month volume (transaction counts by txn_date month)
        months = []
        y, m = today.year, today.month
        for _i in range(6):
            months.append((y, m))
            m -= 1
            if m == 0:
                m, y = 12, y - 1
        months.reverse()
        first_month = '%04d-%02d' % months[0]
        vol_rows = db.q(
            "SELECT substr(txn_date,1,7) AS mo, COUNT(*) AS c"
            " FROM transactions WHERE substr(txn_date,1,7)>=?"
            " GROUP BY substr(txn_date,1,7)", (first_month,))
        vol_map = {r['mo']: int(r['c']) for r in vol_rows}
        volume = []
        max_vol = 0
        for (yy, mm) in months:
            key = '%04d-%02d' % (yy, mm)
            c = vol_map.get(key, 0)
            max_vol = max(max_vol, c)
            volume.append({'key': key, 'label': date(yy, mm, 1).strftime('%b'),
                           'count': c, 'pct': 0})
        for v in volume:
            v['pct'] = int(round(v['count'] * 100 / max_vol)) if max_vol else 0

        max_flow = max(income, expense, 0)
        flow_pct = {
            'income': 100 if income and max_flow else 0,
            'expense': int(round(expense * 100 / max_flow)) if max_flow else 0,
            'leftover': int(round(max(leftover, 0) * 100 / max_flow))
            if max_flow else 0,
        }

        newest = db.q("SELECT * FROM users ORDER BY id DESC LIMIT 5")
        recent = db.q(
            "SELECT t.*, u.name AS user_name FROM transactions t"
            " JOIN users u ON u.id=t.user_id ORDER BY t.id DESC LIMIT 5")
        return render_template('admin/overview.html', **_ctx(
            'overview', lang, admin, total_users=total_users,
            active_today=active_today, txn_month=txn_month,
            new_users=new_users, income=income, expense=expense,
            leftover=leftover, flow_pct=flow_pct, volume=volume,
            newest=newest, recent=recent))

    # ---------------- users ----------------
    @app.route('/admin/users')
    @admin_guard
    def admin_users(admin):
        lang = _lang()
        q = (request.args.get('q') or '').strip()
        net_expr = (
            "(SELECT COALESCE(SUM(balance),0) FROM accounts"
            "  WHERE user_id=u.id)"
            " + (SELECT COALESCE(SUM(current_value),0) FROM assets"
            "  WHERE user_id=u.id)"
            " - (SELECT COALESCE(SUM(amount),0) FROM debts"
            "  WHERE user_id=u.id)")
        if q:
            like = '%' + q + '%'
            users = db.q(
                "SELECT u.*, (SELECT COUNT(*) FROM transactions"
                " WHERE user_id=u.id) AS txn_count, " + net_expr +
                " AS net_worth FROM users u"
                " WHERE u.name LIKE ? OR u.email LIKE ?"
                " OR u.username LIKE ? ORDER BY u.id DESC",
                (like, like, like))
        else:
            users = db.q(
                "SELECT u.*, (SELECT COUNT(*) FROM transactions"
                " WHERE user_id=u.id) AS txn_count, " + net_expr +
                " AS net_worth FROM users u ORDER BY u.id DESC")
        return render_template('admin/users.html', **_ctx(
            'users', lang, admin, users=users, q=q))

    @app.route('/admin/users/<int:user_id>/suspend', methods=['POST'])
    @admin_guard
    def admin_user_suspend(admin, user_id):
        if user_id != admin['id']:  # never lock the acting admin out
            db.execute("UPDATE users SET suspended=1 WHERE id=?", (user_id,))
        return redirect(url_for('admin_users',
                                q=request.form.get('q') or ''))

    @app.route('/admin/users/<int:user_id>/activate', methods=['POST'])
    @admin_guard
    def admin_user_activate(admin, user_id):
        db.execute("UPDATE users SET suspended=0 WHERE id=?", (user_id,))
        return redirect(url_for('admin_users',
                                q=request.form.get('q') or ''))

    # ---------------- transactions ----------------
    @app.route('/admin/transactions')
    @admin_guard
    def admin_transactions(admin):
        lang = _lang()
        flt = request.args.get('filter') or 'all'
        if flt not in ('all', 'income', 'expense'):
            flt = 'all'
        if flt == 'all':
            txns = db.q(
                "SELECT t.*, u.name AS user_name, u.username AS user_username"
                " FROM transactions t JOIN users u ON u.id=t.user_id"
                " ORDER BY t.txn_date DESC, t.id DESC LIMIT 200")
        else:
            txns = db.q(
                "SELECT t.*, u.name AS user_name, u.username AS user_username"
                " FROM transactions t JOIN users u ON u.id=t.user_id"
                " WHERE t.kind=? ORDER BY t.txn_date DESC, t.id DESC"
                " LIMIT 200", (flt,))
        return render_template('admin/transactions.html', **_ctx(
            'transactions', lang, admin, txns=txns, filter=flt))

    # ---------------- messages ----------------
    @app.route('/admin/messages')
    @admin_guard
    def admin_messages(admin):
        lang = _lang()
        s = _s(lang)
        today_s = date.today().isoformat()
        conv_count = _count(
            "SELECT COUNT(*) AS c FROM ("
            " SELECT DISTINCT"
            "  CASE WHEN sender_id<recipient_id THEN sender_id"
            "       ELSE recipient_id END AS a,"
            "  CASE WHEN sender_id<recipient_id THEN recipient_id"
            "       ELSE sender_id END AS b FROM messages)")
        msg_today = _count(
            "SELECT COUNT(*) AS c FROM messages"
            " WHERE substr(created_at,1,10)=?", (today_s,))
        with_attach = _count(
            "SELECT COUNT(*) AS c FROM messages"
            " WHERE file_path IS NOT NULL AND file_path!=''")
        # recent conversations: dedupe unordered pairs in Python (portable)
        all_msgs = db.q("SELECT * FROM messages ORDER BY id DESC LIMIT 1000")
        users = {u['id']: u for u in db.q("SELECT id, name, username FROM users")}
        seen = {}
        for msg in all_msgs:
            a, b = msg['sender_id'], msg['recipient_id']
            key = (min(a, b), max(a, b))
            if key not in seen:
                seen[key] = msg
        convs = []
        for (a, b), msg in list(seen.items())[:30]:
            ua, ub = users.get(a), users.get(b)
            if not ua or not ub:
                continue
            convs.append({
                'a_name': ua['name'], 'b_name': ub['name'],
                'preview': _preview(msg, s),
                'time': (msg.get('created_at') or '')[:16].replace('T', ' '),
            })
        return render_template('admin/messages.html', **_ctx(
            'messages', lang, admin, conv_count=conv_count,
            msg_today=msg_today, with_attach=with_attach, convs=convs))

    # ---------------- support ----------------
    def _support_threads(q):
        if q:
            like = '%' + q + '%'
            rows = db.q(
                "SELECT u.* FROM users u"
                " WHERE EXISTS (SELECT 1 FROM support_messages sm"
                "  WHERE sm.user_id=u.id)"
                " AND (u.name LIKE ? OR u.username LIKE ?)"
                " ORDER BY u.id DESC", (like, like))
        else:
            rows = db.q(
                "SELECT u.* FROM users u"
                " WHERE EXISTS (SELECT 1 FROM support_messages sm"
                "  WHERE sm.user_id=u.id) ORDER BY u.id DESC")
        threads = []
        for u in rows:
            last = db.q1(
                "SELECT * FROM support_messages WHERE user_id=?"
                " ORDER BY id DESC LIMIT 1", (u['id'],))
            pinned = _count(
                "SELECT COUNT(*) AS c FROM support_messages"
                " WHERE user_id=? AND pinned=1", (u['id'],)) > 0
            threads.append({'user': u, 'last': last, 'pinned_any': pinned})
        # most recently active thread first
        threads.sort(key=lambda t: (t['last']['id'] if t['last'] else 0),
                     reverse=True)
        return threads

    @app.route('/admin/support')
    @admin_guard
    def admin_support(admin):
        threads = _support_threads((request.args.get('q') or '').strip())
        if threads:
            return redirect(url_for('admin_support_thread',
                                    user_id=threads[0]['user']['id'],
                                    q=request.args.get('q') or ''))
        lang = _lang()
        return render_template('admin/support.html', **_ctx(
            'support', lang, admin, threads=[], thread_user=None,
            items=[], q=(request.args.get('q') or '').strip()))

    @app.route('/admin/support/<int:user_id>')
    @admin_guard
    def admin_support_thread(admin, user_id):
        lang = _lang()
        s = _s(lang)
        q = (request.args.get('q') or '').strip()
        threads = _support_threads(q)
        thread_user = db.q1("SELECT * FROM users WHERE id=?", (user_id,))
        if not thread_user:
            abort(404)
        # annotate each thread with its last-message preview for the template
        for t in threads:
            t['preview'] = _preview(t['last'], s) if t['last'] else ''
            t['time'] = _time_label(t['last'].get('created_at')) \
                if t['last'] else ''
            t['day'] = (t['last'].get('created_at') or '')[:10] \
                if t['last'] else ''
        msgs = db.q(
            "SELECT * FROM support_messages WHERE user_id=?"
            " ORDER BY id ASC", (user_id,))
        items = []
        last_day = None
        for m in msgs:
            day = (m.get('created_at') or '')[:10]
            if day != last_day:
                items.append({'type': 'divider',
                              'label': _day_label(day, lang)})
                last_day = day
            items.append({'type': 'msg', 'm': m,
                          'time': _time_label(m.get('created_at'))})
        return render_template('admin/support.html', **_ctx(
            'support', lang, admin, threads=threads,
            thread_user=thread_user, items=items, q=q))

    @app.route('/admin/support/<int:user_id>/reply', methods=['POST'])
    @admin_guard
    def admin_support_reply(admin, user_id):
        thread_user = db.q1("SELECT * FROM users WHERE id=?", (user_id,))
        if not thread_user:
            abort(404)
        text = (request.form.get('text') or '').strip()
        f = request.files.get('file')
        file_path = file_name = ''
        file_size = 0
        mtype = 'text'
        if f and f.filename:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(0)
            if size > MAX_UPLOAD:
                return redirect(url_for('admin_support_thread',
                                        user_id=user_id))
            fname = secure_filename(f.filename) or 'file'
            stored = '%d_%s' % (int(time.time()), fname)
            dest_dir = os.path.join(BASE_DIR, 'data', 'uploads', 'admin')
            os.makedirs(dest_dir, exist_ok=True)
            f.save(os.path.join(dest_dir, stored))
            file_path = 'admin/' + stored
            file_name = f.filename
            file_size = size
            mime = f.mimetype or ''
            if mime.startswith('image/'):
                mtype = 'image'
            elif mime.startswith('video/'):
                mtype = 'video'
            else:
                mtype = 'file'
        if not text and not file_path:
            return redirect(url_for('admin_support_thread',
                                    user_id=user_id))
        now = datetime.now().isoformat(timespec='seconds')
        db.insert(
            "INSERT INTO support_messages"
            " (user_id, sender, type, text, file_path, file_name,"
            "  file_size, pinned, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (user_id, 'admin', mtype, text, file_path, file_name,
             file_size, 0, now))
        return redirect(url_for('admin_support_thread', user_id=user_id))

    @app.route('/admin/support/<int:user_id>/pin/<int:msg_id>',
               methods=['POST'])
    @admin_guard
    def admin_support_pin(admin, user_id, msg_id):
        row = db.q1(
            "SELECT pinned FROM support_messages WHERE id=? AND user_id=?",
            (msg_id, user_id))
        if row:
            db.execute(
                "UPDATE support_messages SET pinned=? WHERE id=?",
                (0 if row.get('pinned') else 1, msg_id))
        return redirect(url_for('admin_support_thread', user_id=user_id))

    # ---------------- settings ----------------
    @app.route('/admin/settings', methods=['GET', 'POST'])
    @admin_guard
    def admin_settings(admin):
        lang = _lang()
        if request.method == 'POST':
            db.set_setting('registration_open',
                           '1' if request.form.get('registration_open')
                           else '0')
            db.set_setting('maintenance',
                           '1' if request.form.get('maintenance') else '0')
            db.set_setting('allow_messages',
                           '1' if request.form.get('allow_messages')
                           else '0')
            return redirect(url_for('admin_settings', saved='1'))
        settings = {
            'registration_open':
                (db.get_setting('registration_open') or '1') == '1',
            'maintenance': (db.get_setting('maintenance') or '0') == '1',
            'allow_messages':
                (db.get_setting('allow_messages') or '1') == '1',
        }
        return render_template('admin/settings.html', **_ctx(
            'settings', lang, admin, settings=settings,
            saved=request.args.get('saved') == '1'))

    return app
