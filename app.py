import os
import sqlite3
from datetime import date
from functools import wraps
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, session, url_for

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "dukacredit.db"

app = Flask(__name__)
app.secret_key = "dukacredit-student-demo-change-me"


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            shop TEXT NOT NULL,
            phone TEXT NOT NULL UNIQUE,
            pin TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            party TEXT NOT NULL,
            amount REAL NOT NULL,
            balance REAL NOT NULL,
            tx_date TEXT NOT NULL,
            due_date TEXT,
            note TEXT,
            status TEXT NOT NULL,
            target_id INTEGER,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """
    )
    demo = conn.execute("SELECT id FROM users WHERE phone = ?", ("0712345678",)).fetchone()
    if not demo:
        conn.execute(
            "INSERT INTO users (name, shop, phone, pin, created_at) VALUES (?,?,?,?,?)",
            ("Demo Merchant", "Demo Duka", "0712345678", "1234", "2026-01-15"),
        )
        uid = conn.execute("SELECT id FROM users WHERE phone = ?", ("0712345678",)).fetchone()["id"]
        rows = [
            (uid, "supplier", "Bidco Depot", 8500, 3500, "2026-08-12", "2026-09-12", "Cooking oil crate", "partial", None),
            (uid, "customer", "Mama Njeri", 1200, 400, "2026-09-01", "2026-09-20", "Maize flour deni", "partial", None),
            (uid, "customer", "Juma Boda", 600, 0, "2026-08-20", "2026-08-27", "Airtime + bread", "settled", None),
        ]
        conn.executemany(
            """INSERT INTO transactions
               (user_id, kind, party, amount, balance, tx_date, due_date, note, status, target_id)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
    conn.commit()
    conn.close()


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)

    return wrapper


def current_user():
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    conn.close()
    return user


def compute_score(user_id):
    conn = db()
    txs = conn.execute(
        "SELECT * FROM transactions WHERE user_id = ? AND kind != 'repayment'",
        (user_id,),
    ).fetchall()
    conn.close()
    if not txs:
        return {"score": 0, "label": "No history yet", "count": 0, "settled": 0, "overdue": 0}
    today = date.today().isoformat()
    settled = sum(1 for t in txs if t["status"] == "settled")
    overdue = sum(1 for t in txs if t["balance"] > 0 and t["due_date"] and t["due_date"] < today)
    on_time = settled / len(txs)
    activity = min(len(txs) / 20, 1)
    score = round(40 + on_time * 40 + activity * 20 - min(overdue * 8, 40))
    score = max(0, min(100, score))
    if score >= 80:
        label = "Strong"
    elif score >= 60:
        label = "Fair"
    elif score >= 40:
        label = "Watch"
    else:
        label = "Weak"
    return {
        "score": score,
        "label": label,
        "count": len(txs),
        "settled": settled,
        "overdue": overdue,
    }


def totals(user_id):
    conn = db()
    supplier = conn.execute(
        "SELECT COALESCE(SUM(balance),0) AS s FROM transactions WHERE user_id=? AND kind='supplier'",
        (user_id,),
    ).fetchone()["s"]
    customer = conn.execute(
        "SELECT COALESCE(SUM(balance),0) AS s FROM transactions WHERE user_id=? AND kind='customer'",
        (user_id,),
    ).fetchone()["s"]
    today = date.today().isoformat()
    overdue_rows = conn.execute(
        """SELECT * FROM transactions
           WHERE user_id=? AND kind != 'repayment' AND balance > 0 AND due_date < ?""",
        (user_id, today),
    ).fetchall()
    recent = conn.execute(
        "SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 6",
        (user_id,),
    ).fetchall()
    conn.close()
    return supplier, customer, overdue_rows, recent


def apply_payment(user_id, target_id, amount):
    conn = db()
    target = conn.execute(
        "SELECT * FROM transactions WHERE id=? AND user_id=?",
        (target_id, user_id),
    ).fetchone()
    if not target or target["kind"] == "repayment":
        conn.close()
        return "Select a valid debt."
    if amount <= 0:
        conn.close()
        return "Enter an amount greater than 0."
    if amount > target["balance"]:
        conn.close()
        return "Amount is more than the outstanding balance."
    new_bal = target["balance"] - amount
    status = "settled" if new_bal <= 0 else "partial"
    conn.execute(
        "UPDATE transactions SET balance=?, status=? WHERE id=?",
        (new_bal, status, target_id),
    )
    conn.execute(
        """INSERT INTO transactions
           (user_id, kind, party, amount, balance, tx_date, due_date, note, status, target_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            user_id,
            "repayment",
            target["party"],
            amount,
            0,
            date.today().isoformat(),
            target["due_date"],
            "Repayment",
            "settled",
            target_id,
        ),
    )
    conn.commit()
    conn.close()
    return None


@app.route("/")
def home():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        shop = request.form["shop"].strip()
        phone = request.form["phone"].replace(" ", "")
        pin = request.form["pin"].strip()
        conn = db()
        exists = conn.execute("SELECT id FROM users WHERE phone=?", (phone,)).fetchone()
        if exists:
            conn.close()
            flash("That phone is already registered.")
            return render_template("register.html")
        conn.execute(
            "INSERT INTO users (name, shop, phone, pin, created_at) VALUES (?,?,?,?,?)",
            (name, shop, phone, pin, date.today().isoformat()),
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
        conn.close()
        session["user_id"] = user["id"]
        return redirect(url_for("dashboard"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        phone = request.form["phone"].replace(" ", "")
        pin = request.form["pin"].strip()
        conn = db()
        user = conn.execute(
            "SELECT * FROM users WHERE phone=? AND pin=?", (phone, pin)
        ).fetchone()
        conn.close()
        if not user:
            flash("Wrong phone or PIN.")
            return render_template("login.html")
        session["user_id"] = user["id"]
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    score = compute_score(user["id"])
    supplier, customer, overdue, recent = totals(user["id"])
    return render_template(
        "dashboard.html",
        user=user,
        score=score,
        supplier=supplier,
        customer=customer,
        overdue=overdue,
        recent=recent,
    )


@app.route("/ledger")
@login_required
def ledger():
    user = current_user()
    kind = request.args.get("kind", "all")
    q = request.args.get("q", "").strip()
    sql = "SELECT * FROM transactions WHERE user_id=?"
    params = [user["id"]]
    if kind != "all":
        sql += " AND kind=?"
        params.append(kind)
    if q:
        sql += " AND party LIKE ?"
        params.append(f"%{q}%")
    sql += " ORDER BY tx_date DESC, id DESC"
    conn = db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return render_template(
        "ledger.html",
        user=user,
        rows=rows,
        kind=kind,
        q=q,
        today=date.today().isoformat(),
    )


@app.route("/ledger/pay", methods=["POST"])
@login_required
def ledger_pay():
    user = current_user()
    try:
        target_id = int(request.form["target_id"])
        amount = float(request.form["amount"])
    except (KeyError, ValueError):
        flash("Enter a valid amount.")
        return redirect(url_for("ledger"))
    error = apply_payment(user["id"], target_id, amount)
    flash(error or "Payment saved.")
    return redirect(url_for("ledger"))


@app.route("/ledger/settle", methods=["POST"])
@login_required
def ledger_settle():
    user = current_user()
    try:
        target_id = int(request.form["target_id"])
    except (KeyError, ValueError):
        flash("Select a valid debt.")
        return redirect(url_for("ledger"))
    conn = db()
    target = conn.execute(
        "SELECT * FROM transactions WHERE id=? AND user_id=?",
        (target_id, user["id"]),
    ).fetchone()
    conn.close()
    if not target:
        flash("Select a valid debt.")
        return redirect(url_for("ledger"))
    error = apply_payment(user["id"], target_id, target["balance"])
    flash(error or "Settled in full.")
    return redirect(url_for("ledger"))


@app.route("/add", methods=["GET", "POST"])
@login_required
def add():
    user = current_user()
    conn = db()
    open_rows = conn.execute(
        """SELECT * FROM transactions
           WHERE user_id=? AND kind != 'repayment' AND balance > 0
           ORDER BY party""",
        (user["id"],),
    ).fetchall()
    if request.method == "POST":
        form_kind = request.form["form_kind"]
        if form_kind in ("supplier", "customer"):
            amount = float(request.form["amount"])
            conn.execute(
                """INSERT INTO transactions
                   (user_id, kind, party, amount, balance, tx_date, due_date, note, status)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    user["id"],
                    form_kind,
                    request.form["party"].strip(),
                    amount,
                    amount,
                    request.form["tx_date"],
                    request.form["due_date"],
                    request.form.get("note", ""),
                    "open",
                ),
            )
            conn.commit()
            flash("Transaction saved.")
        elif form_kind == "repayment":
            target_id = int(request.form["target_id"])
            amount = float(request.form["amount"])
            error = apply_payment(user["id"], target_id, amount)
            flash(error or "Repayment recorded.")
        conn.close()
        return redirect(url_for("add"))
    conn.close()
    return render_template(
        "add.html", user=user, open_rows=open_rows, today=date.today().isoformat()
    )


@app.route("/report")
@login_required
def report():
    user = current_user()
    score = compute_score(user["id"])
    supplier, customer, overdue, _ = totals(user["id"])
    return render_template(
        "report.html",
        user=user,
        score=score,
        supplier=supplier,
        customer=customer,
        overdue=overdue,
        today=date.today().isoformat(),
    )


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
