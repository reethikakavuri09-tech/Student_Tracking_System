import sqlite3
from datetime import datetime


def get_conn():
    conn = sqlite3.connect("students.db")
    conn.row_factory = sqlite3.Row
    return conn


def column_exists(cur, table_name, column_name):
    cur.execute(f"PRAGMA table_info({table_name})")
    cols = [row[1] for row in cur.fetchall()]
    return column_name in cols


def now_str():
    return datetime.now().strftime("%d %b %Y - %I:%M:%S %p")


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        mobile TEXT,
        location TEXT,
        time TEXT,
        status TEXT DEFAULT 'pending',
        login_time TEXT,
        last_active TEXT,
        logout_time TEXT,
        is_logged_in INTEGER DEFAULT 0,
        approved_time TEXT,
        campus_status TEXT DEFAULT 'unknown',
        outside_alert_sent INTEGER DEFAULT 0,
        valid_seconds REAL DEFAULT 0,
        outside_since TEXT,
        attendance_stopped INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        action TEXT,
        time TEXT
    )
    """)

    migrations = {
        "mobile": "TEXT",
        "status": "TEXT DEFAULT 'pending'",
        "login_time": "TEXT",
        "last_active": "TEXT",
        "logout_time": "TEXT",
        "is_logged_in": "INTEGER DEFAULT 0",
        "approved_time": "TEXT",
        "campus_status": "TEXT DEFAULT 'unknown'",
        "outside_alert_sent": "INTEGER DEFAULT 0",
        "valid_seconds": "REAL DEFAULT 0",
        "outside_since": "TEXT",
        "attendance_stopped": "INTEGER DEFAULT 0"
    }

    for col, col_type in migrations.items():
        if not column_exists(cur, "students", col):
            cur.execute(f"ALTER TABLE students ADD COLUMN {col} {col_type}")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_students_name ON students(name)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_students_mobile ON students(mobile)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_logs_name ON logs(name)")

    try:
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_students_mobile_unique
            ON students(mobile)
            WHERE mobile IS NOT NULL AND mobile != ''
        """)
    except Exception:
        pass

    conn.commit()
    conn.close()


def get_students():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            name,
            mobile,
            location,
            time,
            status,
            login_time,
            last_active,
            logout_time,
            is_logged_in,
            approved_time,
            campus_status,
            outside_alert_sent,
            valid_seconds,
            outside_since,
            attendance_stopped
        FROM students
        ORDER BY id DESC
    """)
    data = cur.fetchall()

    conn.close()
    return data


def delete_student(name):
    conn = get_conn()
    cur = conn.cursor()

    now = now_str()

    cur.execute("DELETE FROM students WHERE name=?", (name,))
    cur.execute(
        "INSERT INTO logs (name, action, time) VALUES (?, ?, ?)",
        (name, "Removed", now)
    )

    conn.commit()
    conn.close()


def get_logs(name):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT time, action FROM logs WHERE name=? ORDER BY id DESC LIMIT 20",
        (name,)
    )
    data = cur.fetchall()

    conn.close()
    return data