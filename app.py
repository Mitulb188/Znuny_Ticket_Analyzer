import csv, io, os, secrets, threading
from datetime import date, datetime, timedelta
from functools import wraps
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for

load_dotenv()
import store, znuny

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)        # new key per start -> everyone is logged out on restart
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

CREDS = {}   # session token -> (user, password); kept in server memory only, never in the cookie
status = {"running": False, "done": 0, "total": 0, "msg": "", "error": None}
_slock = threading.Lock()


def login_required(f):
    @wraps(f)
    def w(*a, **kw):
        if session.get("tok") not in CREDS:
            if request.path.startswith("/api/"):
                return jsonify(error="Not logged in"), 401
            return redirect(url_for("login"))
        return f(*a, **kw)
    return w


@app.route("/login", methods=["GET", "POST"])
def login():
    err = None
    if request.method == "POST":
        user = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        if not user or not pw:
            err = "Please enter username and password."
        else:
            try:
                znuny.verify(user, pw)
                # the cache belongs to one account; don't mix data across users
                if store.get_meta("user") not in (None, user):
                    store.clear(); store.set_meta("synced", ""); store.set_meta("range", "")
                store.set_meta("user", user)
                tok = secrets.token_hex(16)
                CREDS[tok] = (user, pw)
                session.clear(); session["tok"] = tok; session["user"] = user
                return redirect(url_for("index"))
            except RuntimeError as e:
                err = str(e)
    return render_template("login.html", error=err, demo=os.getenv("DEMO") == "1",
                           url=znuny.configured_url())


@app.route("/logout", methods=["POST", "GET"])
def logout():
    CREDS.pop(session.get("tok"), None)
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return render_template("dashboard.html", demo=os.getenv("DEMO") == "1", user=session.get("user"))


@app.route("/api/tickets")
@login_required
def tickets():
    rows = store.query(request.args.get("from"), request.args.get("to"))
    return jsonify(tickets=rows, synced=store.get_meta("synced") or None,
                   synced_range=store.get_meta("range"))


@app.route("/api/tickets/<int:tid>/articles")
@login_required
def ticket_articles(tid):
    return jsonify(articles=store.articles_for(tid))


@app.route("/api/export/articles.csv")
@login_required
def export_articles():
    rows = store.articles_export(request.args.get("from"), request.args.get("to"))
    buf = io.StringIO(); buf.write("\ufeff")
    w = csv.writer(buf)
    w.writerow(["ticket_number", "title", "queue", "state", "ticket_created", "article_id", "sender",
                "article_created", "from", "subject", "body"])
    for r in rows:
        w.writerow([r["number"], r["title"], r["queue"], r["state"], r["ticket_created"], r["article_id"],
                    r["sender"], r["article_created"], r["from_addr"], r["subject"], r["body"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=articles.csv"})


def _progress(done, total, msg):
    status.update(done=done, total=total, msg=msg)


def _sync(date_from, date_to, user, pw):
    try:
        buf = []
        for row in znuny.fetch(date_from, date_to, _progress, user, pw):
            buf.append(row)
            if len(buf) >= 100:
                store.upsert(buf); buf = []
        store.upsert(buf)
        store.set_meta("synced", datetime.now().strftime("%Y-%m-%d %H:%M"))
        store.set_meta("range", f"{date_from} to {date_to}")
        status["msg"] = "Done"
    except Exception as e:  # shown in the UI
        status["error"] = str(e)
    finally:
        status["running"] = False


@app.route("/api/sync", methods=["POST"])
@login_required
def sync():
    with _slock:
        if status["running"]:
            return jsonify(error="Sync already running"), 409
        d_to = request.args.get("to") or date.today().isoformat()
        d_from = request.args.get("from") or (date.today() - timedelta(days=30)).isoformat()
        status.update(running=True, done=0, total=0, msg="Starting...", error=None)
    user, pw = CREDS[session["tok"]]
    threading.Thread(target=_sync, args=(d_from, d_to, user, pw), daemon=True).start()
    return jsonify(ok=True)


@app.route("/api/status")
@login_required
def get_status():
    return jsonify(status)


if __name__ == "__main__":
    # Bound to localhost on purpose: this app holds your Znuny credentials in memory.
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=False)
