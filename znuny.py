"""Read-only access to Znuny/OTRS via the Generic Interface (pyotrs).
Only TicketSearch and TicketGet are called - nothing here modifies tickets."""
import os, random
from datetime import datetime, timedelta


def configured_url():
    return (os.getenv("ZNUNY_URL") or "").strip()


def _client(user, pw):
    from pyotrs import Client
    url = configured_url()
    if not url:
        raise RuntimeError("ZNUNY_URL is not set. Copy .env.example to .env and set it (see README).")
    c = Client(url, user, pw)
    if not c.session_create():
        raise RuntimeError("Znuny login failed - check username and password.")
    return c


def verify(user, pw):
    """Raise RuntimeError with a readable message if the login does not work."""
    if os.getenv("DEMO") == "1":
        return
    try:
        _client(user, pw)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError("Could not reach Znuny (%s). Are you on the university network/VPN?" % type(e).__name__)


def _articles(d):
    """Normalise the articles of a TicketGet result, oldest first."""
    out = []
    for a in d.get("Article") or []:
        out.append({"ticket_id": int(d["TicketID"]), "article_id": int(a["ArticleID"]),
                    "sender": a.get("SenderType"), "created": a.get("CreateTime") or a.get("Created"),
                    "from_addr": a.get("From"), "subject": a.get("Subject"), "body": a.get("Body")})
    return sorted(out, key=lambda a: a["article_id"])


def _first_text(arts):
    """The initial request: first customer-written article (falls back to the very first one)."""
    a = next((x for x in arts if x["sender"] == "customer"), arts[0])
    return (a["body"] or "").strip()[:400]


def fetch(date_from, date_to, progress, user=None, pw=None):
    """Fetch tickets created in [date_from, date_to] (YYYY-MM-DD), all queues & states."""
    if os.getenv("DEMO") == "1":
        yield from _demo(date_from, date_to, progress)
        return
    with_articles = os.getenv("FETCH_ARTICLES", "1") != "0"
    limit = int(os.getenv("MAX_TICKETS", "5000"))
    c = _client(user, pw)
    ids = c.ticket_search(
        TicketCreateTimeNewerDate=datetime.strptime(date_from, "%Y-%m-%d"),
        TicketCreateTimeOlderDate=datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1),
        Limit=limit, OrderBy="Down", SortBy="Created")
    if ids is False:
        raise RuntimeError("TicketSearch failed: %s" % getattr(c, "result_error", "unknown error"))
    progress(0, len(ids), "Found %d tickets" % len(ids))
    for n, tid in enumerate(ids, 1):
        t = c.ticket_get_by_id(tid, articles=with_articles, dynamic_fields=False)
        if not t:
            continue
        d = t.to_dct()["Ticket"]
        arts = _articles(d) if with_articles else None
        yield {
            "id": int(d["TicketID"]), "number": d.get("TicketNumber"),
            "title": d.get("Title"), "queue": d.get("Queue"), "state": d.get("State"),
            "priority": d.get("Priority"), "owner": d.get("Owner"),
            "customer": d.get("CustomerUserID") or d.get("CustomerID"),
            "type": d.get("Type"), "created": d.get("Created"),
            "changed": d.get("Changed"),
            "articles": len(arts) if arts is not None else None,
            "first_text": _first_text(arts) if arts else None,
            "article_list": arts,
        }
        if n % 10 == 0 or n == len(ids):
            progress(n, len(ids), "Fetched %d / %d" % (n, len(ids)))


_DEMO_Q = ["Ich kann mich nicht in mein Konto einloggen, bitte um Hilfe.",
           "I forgot my password and the reset mail never arrives.",
           "Wie kann ich mich fuer die Pruefung im Wintersemester anmelden?",
           "Question about the status of my application, no news for weeks.",
           "Mein Zeugnis zeigt eine falsche Note an. Koennen Sie das pruefen?"]


def _demo_articles(rnd, tid, ts, q):
    n = rnd.randint(1, 5)
    out = []
    for k in range(n):
        customer = k % 2 == 0
        t = ts + timedelta(hours=k * rnd.randint(1, 20))
        out.append({"ticket_id": tid, "article_id": tid * 10 + k,
                    "sender": "customer" if customer else "agent",
                    "created": t.strftime("%Y-%m-%d %H:%M:%S"),
                    "from_addr": "student%03d@example.org" % (tid % 400) if customer else "support@example.org",
                    "subject": "Re: Demo ticket" if k else "Demo ticket",
                    "body": rnd.choice(_DEMO_Q) if customer else "Thank you for your message. We are looking into it."})
    return out


def _demo(date_from, date_to, progress):
    """Synthetic data so the UI can be explored without a connection."""
    rnd = random.Random(7)
    queues = [("Support::Account", 5), ("Support::Application", 4), ("Support::Exams", 3),
              ("Support::Basis", 3), ("Spam", 2), ("Misc", 1)]
    states = [("new", 2), ("open", 3), ("pending reminder", 1),
              ("closed successful", 8), ("closed unsuccessful", 1)]
    d0 = datetime.strptime(date_from, "%Y-%m-%d")
    d1 = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
    days = max(1, (d1 - d0).days)
    n = min(days * 6, 3000)
    for i in range(n):
        day = d0 + timedelta(days=rnd.random() ** 0.8 * days)
        if day.weekday() >= 5 and rnd.random() < 0.7:
            day -= timedelta(days=day.weekday() - 4)
        ts = day.replace(hour=rnd.randint(7, 18), minute=rnd.randint(0, 59), second=rnd.randint(0, 59))
        q = rnd.choices([a for a, _ in queues], [w for _, w in queues])[0]
        s = rnd.choices([a for a, _ in states], [w for _, w in states])[0]
        arts = _demo_articles(rnd, 100000 + i, ts, q)
        yield {"id": 100000 + i, "number": "2026%06d" % (100000 + i),
               "title": "Demo ticket %d (%s)" % (i, q.split("::")[-1]),
               "queue": q, "state": s, "priority": rnd.choice(["3 normal"] * 5 + ["4 high", "2 low"]),
               "owner": rnd.choice(["agent1", "agent2", "agent3", "root@localhost"]),
               "customer": "user%03d" % rnd.randint(1, 400), "type": "Unclassified",
               "created": ts.strftime("%Y-%m-%d %H:%M:%S"),
               "changed": (ts + timedelta(hours=rnd.randint(0, 200))).strftime("%Y-%m-%d %H:%M:%S"),
               "articles": len(arts), "first_text": _first_text(arts), "article_list": arts}
        if i % 50 == 0:
            progress(i, n, "Generated %d / %d" % (i, n))
    progress(n, n, "Generated %d demo tickets" % n)
