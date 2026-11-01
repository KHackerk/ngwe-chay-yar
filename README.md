# Ngwe Chay Yar — real app (Flask backend)

Personal finance app: daily transactions, accounts/savings, assets,
debts, net worth, user-to-user messages with photos/videos/files, and
customer-support chat with the admin. Amounts are plain numbers (kyat) —
no currency label is shown anywhere.

## Run locally

Using the existing personal-finance virtualenv (has Flask + Pillow):

```bash
cd ~/workspace/ngwe-chay-yar-app
~/workspace/personal-finance/venv/bin/python app.py
# → http://127.0.0.1:5000
```

Or with a fresh environment:

```bash
pip install -r requirements.txt
python app.py
```

## Environment variables

| Variable       | Meaning                                                        |
| -------------- | -------------------------------------------------------------- |
| `SECRET_KEY`   | Flask session secret. Set a long random value in production. Defaults to a dev-only value when unset. |
| `DATABASE_URL` | `postgres://...` / `postgresql://...` → Postgres (Neon). `sqlite:////abs/path.db` → that SQLite file. Unset → local `data/app.db`. |
| `PORT`         | Port to bind (set automatically by Render). Default 5000.      |

## First account = admin

The app starts completely empty — no demo users, no sample data.
**The first account you register becomes the admin.** Register your own
account first, before anyone else, on a fresh deployment. The admin
dashboard lives at `/admin` (behind login, admin role only).

## Deploy on Render

- Create a Web Service from this repo (Docker runtime — the included
  `Dockerfile` is used) or a Python service with start command
  `gunicorn --bind 0.0.0.0:$PORT --workers 1 app:app`.
- Set env vars: `SECRET_KEY` (long random), `DATABASE_URL` (Neon
  Postgres connection string — strongly recommended: SQLite on
  Render's container disk is wiped on every redeploy).
- Uploaded files and profile photos live under `data/` on the
  container disk and are likewise ephemeral on the free plan.

## Layout

- `app.py` — Flask app: auth routes, `/api/*` JSON API, upload serving,
  maintenance middleware.
- `db.py` — dual SQLite/Postgres data layer (per-call connections,
  `?` placeholders everywhere).
- `common.py` — `current_user()`, `login_required`, `admin_required`.
- `admin_routes.py` — `/admin/*` dashboard.
- `templates/` — auth pages, SPA shell, admin pages.
- `static/img/` — app logo and payment-brand logos.
