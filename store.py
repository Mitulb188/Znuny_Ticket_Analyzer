"""Tiny SQLite cache so the dashboard is fast and Znuny is only hit on Sync."""
import sqlite3, os, threading

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tickets.db")
_lock = threading.Lock()

COLS = ["id", "number", "title", "queue", "state", "priority", "owner",
        "customer", "type", "created", "changed", "articles", "first_text"]
ART_COLS = ["ticket_id", "article_id", "sender", "created", "from_addr", "subject", "body"]


def _conn():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY, number TEXT, title TEXT, queue TEXT, state TEXT,
        priority TEXT, owner TEXT, customer TEXT, type TEXT,
        created TEXT, changed TEXT, articles INTEGER, first_text TEXT)""")
    have = {r[1] for r in c.execute("PRAGMA table_info(tickets)")}
    if "first_text" not in have:                      # cache from the previous version
        c.execute("ALTER TABLE tickets ADD COLUMN first_text TEXT")
    c.execute("""CREATE TABLE IF NOT EXISTS articles (
        ticket_id INTEGER, article_id INTEGER, sender TEXT, created TEXT,
        from_addr TEXT, subject TEXT, body TEXT, PRIMARY KEY (ticket_id, article_id))""")
    c.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    return c


def upsert(rows):
    with _lock, _conn() as c:
        for r in rows:
            c.execute(f"INSERT OR REPLACE INTO tickets ({','.join(COLS)}) VALUES ({','.join('?'*len(COLS))})",
                      [r.get(k) for k in COLS])
            arts = r.get("article_list")
            if arts is not None:                      # None = articles were not fetched, keep old ones
                c.execute("DELETE FROM articles WHERE ticket_id=?", (r["id"],))
                c.executemany(
                    f"INSERT OR REPLACE INTO articles ({','.join(ART_COLS)}) VALUES ({','.join('?'*len(ART_COLS))})",
                    [[a.get(k) for k in ART_COLS] for a in arts])


def clear():
    with _lock, _conn() as c:
        c.execute("DELETE FROM tickets"); c.execute("DELETE FROM articles")


def _range(date_from, date_to, col="created"):
    sql, args = "", []
    if date_from:
        sql += f" AND {col} >= ?"; args.append(date_from)
    if date_to:
        sql += f" AND {col} <= ?"; args.append(date_to + " 23:59:59")
    return sql, args


def query(date_from=None, date_to=None):
    w, args = _range(date_from, date_to)
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM tickets WHERE 1=1" + w + " ORDER BY created DESC", args)]


def articles_for(ticket_id):
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM articles WHERE ticket_id=? ORDER BY article_id", (ticket_id,))]


def articles_export(date_from=None, date_to=None):
    w, args = _range(date_from, date_to, "t.created")
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT t.number, t.title, t.queue, t.state, t.created AS ticket_created, a.article_id, a.sender, "
            "a.created AS article_created, a.from_addr, a.subject, a.body "
            "FROM articles a JOIN tickets t ON t.id=a.ticket_id WHERE 1=1" + w +
            " ORDER BY t.created DESC, a.article_id", args)]


def set_meta(k, v):
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, v))


def get_meta(k, default=None):
    with _lock, _conn() as c:
        r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default
