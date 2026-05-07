import math
import csv
import re
import os
import sqlite3
from flask import Flask, render_template, request, redirect, session, jsonify, send_file
from database import init_db, get_students, delete_student
from datetime import datetime, time as dtime
from reportlab.pdfgen import canvas

app = Flask(__name__)
app.secret_key = "secret123"

init_db()

permissions = {}
attendance = {}

COLLEGE_LAT = 14.4225626
COLLEGE_LON = 80.0037466
RADIUS = 300

ONLINE_SECONDS = 600
INACTIVE_SECONDS = 3600
OUTSIDE_BUFFER_SECONDS = 300

ATTENDANCE_START_TIME = dtime(9, 0)
ATTENDANCE_END_TIME = dtime(18, 0)


def get_conn():
    conn = sqlite3.connect("students.db")
    conn.row_factory = sqlite3.Row
    return conn


def now_str():
    return datetime.now().strftime("%d %b %Y - %I:%M:%S %p")


def parse_dt(value):
    if not value:
        return None
    for fmt in ("%d %b %Y - %I:%M:%S %p", "%d %b %Y - %I:%M %p"):
        try:
            return datetime.strptime(value, fmt)
        except Exception:
            continue
    return None


def is_inside(lat, lon):
    r = 6371000
    lat = float(lat)
    lon = float(lon)

    phi1 = math.radians(COLLEGE_LAT)
    phi2 = math.radians(lat)

    dphi = math.radians(lat - COLLEGE_LAT)
    dlambda = math.radians(lon - COLLEGE_LON)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return (r * c) <= RADIUS


def login_allowed_now():
    current = datetime.now().time()
    return dtime(9, 0) <= current <= dtime(13, 0)


def compute_attendance_from_seconds(valid_seconds, is_logged_in=True):
    hours = round((valid_seconds or 0) / 3600, 2)

    if hours >= 8:
        return "Full Day", "Attendance counted as full day.", hours

    if hours >= 5:
        return "Half Day", "Attendance counted as half day.", hours

    if is_logged_in:
        return "In Progress", "Attendance is being calculated. Minimum 5 hours required.", hours

    return "Absent", "Attendance is absent because valid hours are below 5.", hours


def get_display_status(row):
    if row["status"] == "rejected":
        return "rejected"

    if row["status"] == "logged_out" or row["is_logged_in"] == 0:
        return "logged_out"

    if row["attendance_stopped"] == 1 or row["campus_status"] == "outside":
        return "outside"

    if row["status"] == "pending":
        return "pending"

    if row["status"] == "approved" and row["is_logged_in"] == 1:
        return "active"

    return "logged_out"


# Root URL opens admin side
@app.route("/")
def home():
    return redirect("/admin")


# Student page URL
@app.route("/student")
def student():
    return render_template("student.html")


@app.route("/student_login", methods=["POST"])
def student_login():
    name = request.form.get("name", "").strip()
    mobile = request.form.get("mobile", "").strip()
    location = request.form.get("location", "0,0").strip()

    if not name or not mobile:
        return jsonify({"success": False, "message": "Name and mobile number are required."}), 400

    if not re.match(r"^[6-9]\d{9}$", mobile):
        return jsonify({
            "success": False,
            "message": "Enter a valid 10-digit Indian mobile number."
        }), 400

    if not login_allowed_now():
        return jsonify({
            "success": False,
            "message": "Login is allowed only between 9:00 AM and 1:00 PM."
        }), 400

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM students WHERE mobile=?", (mobile,))
    mobile_row = cur.fetchone()

    cur.execute("SELECT * FROM students WHERE name=?", (name,))
    name_row = cur.fetchone()

    if mobile_row and mobile_row["name"].strip().lower() != name.lower():
        conn.close()
        return jsonify({
            "success": False,
            "message": "This mobile number is already linked to another student name."
        }), 400

    if name_row and name_row["mobile"] and name_row["mobile"].strip() != mobile:
        conn.close()
        return jsonify({
            "success": False,
            "message": "This student name is already linked to another mobile number."
        }), 400

    now = now_str()
    campus_status = "unknown"

    try:
        lat, lon = location.split(",")
        campus_status = "inside" if is_inside(lat, lon) else "outside"
    except Exception:
        campus_status = "unknown"

    if not mobile_row and not name_row:
        cur.execute("""
            INSERT INTO students
            (name, mobile, location, time, status, login_time, last_active, logout_time,
             is_logged_in, approved_time, campus_status, outside_alert_sent,
             valid_seconds, outside_since, attendance_stopped)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, mobile, location, now, "pending", now, now, None, 1, None, campus_status, 0, 0, None, 0))

        cur.execute(
            "INSERT INTO logs (name, action, time) VALUES (?, ?, ?)",
            (name, "Request Submitted", now)
        )
    else:
        student = mobile_row if mobile_row else name_row

        cur.execute("""
            UPDATE students
            SET name=?, mobile=?, location=?, time=?, status='pending',
                login_time=?, last_active=?, logout_time=NULL,
                is_logged_in=1, approved_time=NULL,
                campus_status=?, outside_alert_sent=0,
                valid_seconds=0, outside_since=NULL, attendance_stopped=0
            WHERE id=?
        """, (name, mobile, location, now, now, now, campus_status, student["id"]))

        cur.execute(
            "INSERT INTO logs (name, action, time) VALUES (?, ?, ?)",
            (name, "Request Submitted", now)
        )

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "redirect": f"/student_tracking?mobile={mobile}"
    })


@app.route("/student_tracking")
def student_tracking():
    mobile = request.args.get("mobile", "").strip()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name, mobile FROM students WHERE mobile=?", (mobile,))
    row = cur.fetchone()
    conn.close()

    name = row["name"] if row else ""
    mobile_value = row["mobile"] if row else mobile

    return render_template("student_tracking.html", name=name, mobile=mobile_value)


@app.route("/student_status")
def student_status():
    mobile = request.args.get("mobile", "").strip()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT name, mobile, status, login_time, last_active, logout_time,
               is_logged_in, approved_time, location, campus_status,
               valid_seconds, attendance_stopped
        FROM students
        WHERE mobile=?
    """, (mobile,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return jsonify({"success": False, "message": "Student not found."}), 404

    attendance_status, attendance_note, total_hours = compute_attendance_from_seconds(
        row["valid_seconds"],
        bool(row["is_logged_in"])
    )
    live_status = get_display_status(row)

    if row["status"] == "rejected":
        message = "Permission rejected by admin. Attendance remains stopped."
    elif row["attendance_stopped"] == 1 and row["status"] == "pending":
        message = "You are outside the campus. Login hours stopped. Admin approval is required to resume."
    elif row["status"] == "pending":
        message = "Waiting for admin approval..."
    elif live_status == "logged_out":
        message = "You are logged out."
    else:
        message = attendance_note

    return jsonify({
        "success": True,
        "name": row["name"],
        "mobile": row["mobile"],
        "approval_status": row["status"],
        "login_time": row["login_time"] or "-",
        "approved_time": row["approved_time"] or "-",
        "last_active": row["last_active"] or "-",
        "logout_time": row["logout_time"] or "-",
        "is_logged_in": bool(row["is_logged_in"]),
        "live_status": live_status,
        "attendance_status": attendance_status,
        "attendance_note": message,
        "total_hours": total_hours,
        "campus_status": row["campus_status"] or "unknown",
        "attendance_stopped": bool(row["attendance_stopped"])
    })


@app.route("/student_logout", methods=["POST"])
def student_logout():
    mobile = request.form.get("mobile", "").strip()
    if not mobile:
        return jsonify({"success": False, "message": "Mobile is required."}), 400

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, name, last_active, valid_seconds, status, attendance_stopped
        FROM students
        WHERE mobile=?
    """, (mobile,))
    row = cur.fetchone()

    if not row:
        conn.close()
        return jsonify({"success": False, "message": "Student not found."}), 404

    now = now_str()
    valid_seconds = row["valid_seconds"] or 0

    last_active_dt = parse_dt(row["last_active"])
    now_dt = parse_dt(now)
    current_time = datetime.now().time()
    within_time = ATTENDANCE_START_TIME <= current_time <= ATTENDANCE_END_TIME

    if (
        row["status"] in ("approved", "pending")
        and row["attendance_stopped"] == 0
        and last_active_dt
        and now_dt
        and within_time
    ):
        valid_seconds += max(0, (now_dt - last_active_dt).total_seconds())

    cur.execute("""
        UPDATE students
        SET is_logged_in=0,
            logout_time=?,
            last_active=?,
            time=?,
            status='logged_out',
            valid_seconds=?,
            outside_since=NULL,
            attendance_stopped=0
        WHERE mobile=?
    """, (now, now, now, valid_seconds, mobile))

    cur.execute(
        "INSERT INTO logs (name, action, time) VALUES (?, ?, ?)",
        (row["name"], "Logged Out", now)
    )

    conn.commit()
    conn.close()

    attendance_status, _, _ = compute_attendance_from_seconds(valid_seconds, False)
    attendance[row["name"]] = attendance_status

    return jsonify({"success": True})


@app.route("/admin", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if username == "Admin" and password == "1234":
            session["user"] = "Admin"
            return redirect("/dashboard")
        return render_template("login.html", error="Invalid Credentials")

    return render_template("login.html")


@app.route("/approve_student", methods=["POST"])
def approve_student():
    name = request.form.get("name", "").strip()
    if not name:
        return ("", 204)

    conn = get_conn()
    cur = conn.cursor()

    now = now_str()

    cur.execute("""
        SELECT campus_status, attendance_stopped
        FROM students
        WHERE name=?
    """, (name,))
    existing = cur.fetchone()

    if existing and (existing["campus_status"] == "outside" or existing["attendance_stopped"] == 1):
        permissions[name] = True

    cur.execute("""
        UPDATE students
        SET status='approved',
            approved_time=?,
            is_logged_in=1,
            time=?,
            last_active=?,
            outside_alert_sent=0,
            outside_since=NULL,
            attendance_stopped=0
        WHERE name=?
    """, (now, now, now, name))

    cur.execute(
        "INSERT INTO logs (name, action, time) VALUES (?, ?, ?)",
        (name, "Permission Granted / Approved by Admin", now)
    )

    conn.commit()
    conn.close()

    return ("", 204)


@app.route("/reject_student", methods=["POST"])
def reject_student():
    name = request.form.get("name", "").strip()
    if not name:
        return ("", 204)

    conn = get_conn()
    cur = conn.cursor()

    now = now_str()

    cur.execute("""
        UPDATE students
        SET status='rejected',
            attendance_stopped=1,
            time=?,
            last_active=?
        WHERE name=?
    """, (now, now, name))

    cur.execute(
        "INSERT INTO logs (name, action, time) VALUES (?, ?, ?)",
        (name, "Permission Rejected by Admin", now)
    )

    permissions.pop(name, None)

    conn.commit()
    conn.close()

    return ("", 204)


@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/admin")

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, name, mobile, location, time, status, login_time, last_active,
               logout_time, is_logged_in, approved_time, campus_status,
               outside_alert_sent, valid_seconds, outside_since, attendance_stopped
        FROM students
        ORDER BY id DESC
    """)
    data = cur.fetchall()
    conn.close()

    students = []
    live_count = 0

    for row in data:
        display_status = get_display_status(row)
        if display_status == "active":
            live_count += 1

        students.append({
            "name": row["name"],
            "mobile": row["mobile"] or "-",
            "location": row["location"] if row["location"] != "0,0" else "Waiting for live location",
            "time": row["time"] or "-",
            "status": display_status,
            "approval_status": row["status"] or "pending",
            "login_time": row["login_time"] or "-",
            "last_active": row["last_active"] or "-",
            "logout_time": row["logout_time"] or "-",
            "approved_time": row["approved_time"] or "-",
            "campus_status": row["campus_status"] or "unknown"
        })

    return render_template(
        "dashboard.html",
        students=students,
        total=len(students),
        live=live_count
    )


@app.route("/dashboard_data")
def dashboard_data():
    if "user" not in session:
        return jsonify({"success": False}), 401

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, mobile, location, time, status, login_time, last_active,
               logout_time, is_logged_in, approved_time, campus_status,
               outside_alert_sent, valid_seconds, outside_since, attendance_stopped
        FROM students
        ORDER BY id DESC
    """)
    data = cur.fetchall()

    students = []
    live_count = 0
    alerts = []

    for row in data:
        display_status = get_display_status(row)
        if display_status == "active":
            live_count += 1

        if row["status"] == "pending" and row["attendance_stopped"] == 1 and row["outside_alert_sent"] == 0:
            alerts.append(f'🚨 {row["name"]} is outside campus and needs permission')

        students.append({
            "name": row["name"],
            "mobile": row["mobile"] or "-",
            "location": row["location"] if row["location"] != "0,0" else "Waiting for live location",
            "time": row["time"] or "-",
            "status": display_status,
            "approval_status": row["status"] or "pending",
            "login_time": row["login_time"] or "-",
            "last_active": row["last_active"] or "-",
            "logout_time": row["logout_time"] or "-",
            "approved_time": row["approved_time"] or "-",
            "campus_status": row["campus_status"] or "unknown"
        })

    if alerts:
        cur.execute("""
            UPDATE students
            SET outside_alert_sent=1
            WHERE status='pending' AND attendance_stopped=1
        """)
        conn.commit()

    conn.close()

    return jsonify({
        "success": True,
        "total": len(students),
        "live": live_count,
        "students": students,
        "alerts": alerts
    })


@app.route("/auto_update", methods=["POST"])
def auto_update():
    name = request.form.get("name", "").strip()
    mobile = request.form.get("mobile", "").strip()
    location = request.form.get("location", "").strip()

    if not mobile or not location or "," not in location:
        return jsonify({"alert": False, "approved": False, "waiting": True})

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, name, status, is_logged_in, login_time, campus_status,
               last_active, valid_seconds, outside_since, attendance_stopped
        FROM students
        WHERE mobile=?
    """, (mobile,))
    row = cur.fetchone()

    if not row:
        conn.close()
        return jsonify({"alert": False, "approved": False, "waiting": True})

    if name and row["name"].strip().lower() != name.lower():
        conn.close()
        return jsonify({"alert": False, "approved": False, "waiting": True})

    time_now = now_str()
    now_dt = parse_dt(time_now)

    try:
        lat, lon = location.split(",")
        inside = is_inside(lat, lon)
    except Exception:
        inside = False

    campus_status = "inside" if inside else "outside"

    if inside:
        permissions.pop(row["name"], None)

    last_active_dt = parse_dt(row["last_active"])
    delta_seconds = 0
    if last_active_dt and now_dt:
        delta_seconds = max(0, (now_dt - last_active_dt).total_seconds())

    valid_seconds = row["valid_seconds"] or 0
    outside_since = row["outside_since"]
    attendance_stopped = row["attendance_stopped"] or 0
    current_status = row["status"]
    alert = False

    current_time = datetime.now().time()
    within_time = ATTENDANCE_START_TIME <= current_time <= ATTENDANCE_END_TIME

    if row["is_logged_in"] == 1 and current_status != "rejected":
        if within_time:
            if attendance_stopped == 0:
                if inside or permissions.get(row["name"]):
                    valid_seconds += delta_seconds
                    outside_since = None
                else:
                    if not outside_since:
                        outside_since = time_now
                        valid_seconds += delta_seconds
                    else:
                        outside_since_dt = parse_dt(outside_since)
                        outside_age = 0
                        if outside_since_dt and now_dt:
                            outside_age = max(0, (now_dt - outside_since_dt).total_seconds())

                        if outside_age <= OUTSIDE_BUFFER_SECONDS:
                            valid_seconds += delta_seconds
                        else:
                            attendance_stopped = 1
                            current_status = "pending"
                            alert = True
            else:
                if current_status != "rejected":
                    current_status = "pending"
        else:
            outside_since = None

    cur.execute("""
        UPDATE students
        SET location=?,
            time=?,
            last_active=?,
            campus_status=?,
            valid_seconds=?,
            outside_since=?,
            attendance_stopped=?,
            status=?,
            outside_alert_sent=CASE
                WHEN ? = 1 THEN 0
                ELSE outside_alert_sent
            END
        WHERE id=?
    """, (
        location,
        time_now,
        time_now,
        campus_status,
        valid_seconds,
        outside_since,
        attendance_stopped,
        current_status,
        1 if alert else 0,
        row["id"]
    ))

    if alert:
        cur.execute(
            "INSERT INTO logs (name, action, time) VALUES (?, ?, ?)",
            (row["name"], "🚨 Outside Campus - Permission Required", time_now)
        )

    conn.commit()
    conn.close()

    attendance_status, attendance_note, total_hours = compute_attendance_from_seconds(
        valid_seconds,
        True
    )
    attendance[row["name"]] = attendance_status

    if current_status == "rejected":
        return jsonify({
            "alert": False,
            "approved": False,
            "waiting": False,
            "attendance_status": attendance_status,
            "attendance_note": "Permission rejected by admin. Attendance remains stopped.",
            "campus_status": campus_status,
            "total_hours": total_hours,
            "attendance_stopped": True
        })

    if current_status == "logged_out":
        return jsonify({
            "alert": False,
            "approved": False,
            "waiting": False,
            "attendance_status": attendance_status,
            "attendance_note": "You are logged out.",
            "campus_status": campus_status,
            "total_hours": total_hours,
            "attendance_stopped": bool(attendance_stopped)
        })

    if attendance_stopped == 1:
        return jsonify({
            "alert": alert,
            "alert_name": row["name"],
            "approved": False,
            "waiting": True,
            "attendance_status": attendance_status,
            "attendance_note": "You are outside the campus. Login hours stopped. Admin approval is required to resume.",
            "campus_status": "outside",
            "total_hours": total_hours,
            "attendance_stopped": True
        })

    if current_status == "pending":
        return jsonify({
            "alert": False,
            "approved": False,
            "waiting": True,
            "attendance_status": attendance_status,
            "attendance_note": "Waiting for admin approval.",
            "campus_status": campus_status,
            "total_hours": total_hours,
            "attendance_stopped": False
        })

    return jsonify({
        "alert": False,
        "alert_name": "",
        "approved": True,
        "waiting": False,
        "attendance_status": attendance_status,
        "attendance_note": attendance_note,
        "campus_status": campus_status,
        "total_hours": total_hours,
        "attendance_stopped": False
    })


@app.route("/add", methods=["POST"])
def add():
    if "user" not in session:
        return redirect("/admin")

    name = request.form.get("name", "").strip()
    location = request.form.get("location", "").strip() or "0,0"

    if not name:
        return redirect("/dashboard")

    conn = get_conn()
    cur = conn.cursor()

    now = now_str()

    cur.execute("SELECT * FROM students WHERE name=?", (name,))
    exists = cur.fetchone()

    if not exists:
        cur.execute("""
            INSERT INTO students
            (name, mobile, location, time, status, login_time, last_active, logout_time,
             is_logged_in, approved_time, campus_status, outside_alert_sent,
             valid_seconds, outside_since, attendance_stopped)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, "", location, now, "approved", now, now, None, 0, now, "unknown", 0, 0, None, 0))

        cur.execute(
            "INSERT INTO logs (name, action, time) VALUES (?, ?, ?)",
            (name, "Added by Admin", now)
        )

    conn.commit()
    conn.close()

    return redirect("/dashboard")


@app.route("/update", methods=["POST"])
def update():
    if "user" not in session:
        return redirect("/admin")

    name = request.form.get("name", "").strip()
    new_location = request.form.get("new_location", "").strip()

    if not name or not new_location:
        return redirect("/dashboard")

    conn = get_conn()
    cur = conn.cursor()
    now = now_str()

    cur.execute("""
        UPDATE students
        SET location=?, time=?, last_active=?
        WHERE name=?
    """, (new_location, now, now, name))

    cur.execute(
        "INSERT INTO logs (name, action, time) VALUES (?, ?, ?)",
        (name, "Manual Location Updated by Admin", now)
    )

    conn.commit()
    conn.close()

    return redirect("/dashboard")


@app.route("/delete", methods=["POST"])
def delete():
    if "user" not in session:
        return redirect("/admin")

    name = request.form.get("name", "").strip()
    if name:
        delete_student(name)
        permissions.pop(name, None)
        attendance.pop(name, None)

    return redirect("/dashboard")


@app.route("/all_locations")
def all_locations():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT name, location, is_logged_in, status, last_active, campus_status, attendance_stopped
        FROM students
        WHERE is_logged_in=1
    """)
    data = cur.fetchall()
    conn.close()

    result = []

    for row in data:
        loc = row["location"]
        if not loc or loc == "0,0" or "," not in loc:
            continue

        if row["status"] not in ("approved", "pending"):
            continue

        try:
            lat, lon = loc.split(",")
            result.append({
                "name": row["name"],
                "lat": float(lat),
                "lon": float(lon),
                "campus_status": row["campus_status"] or "unknown"
            })
        except Exception:
            continue

    return jsonify(result)


@app.route("/download_csv")
def download_csv():
    filename = "attendance.csv"
    data = get_students()

    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Name",
            "Mobile",
            "Approval Status",
            "Campus Status",
            "Attendance",
            "Login Date",
            "Login Time",
            "Last Active",
            "Logout Time",
            "Total Login Hours"
        ])

        for row in data:
            name = row["name"]
            mobile = row["mobile"]
            approval_status = row["status"]
            campus_status = row["campus_status"]
            login_time = row["login_time"]
            last_active = row["last_active"]
            logout_time = row["logout_time"]
            valid_seconds = row["valid_seconds"] or 0
            is_logged_in = bool(row["is_logged_in"])

            att_status, _, total_hours = compute_attendance_from_seconds(valid_seconds, is_logged_in)

            login_date = "-"
            login_clock = "-"
            login_dt = parse_dt(login_time)
            if login_dt:
                login_date = login_dt.strftime("%d-%m-%Y")
                login_clock = login_dt.strftime("%I:%M:%S %p")

            writer.writerow([
                name,
                mobile,
                approval_status,
                campus_status,
                att_status,
                login_date,
                login_clock,
                last_active,
                logout_time or "-",
                total_hours
            ])

    return send_file(filename, as_attachment=True)


@app.route("/download_pdf")
def download_pdf():
    file = "attendance.pdf"
    data = get_students()

    c = canvas.Canvas(file)
    y = 800
    c.drawString(180, y, "Attendance Report")
    y -= 30

    for row in data:
        name = row["name"]
        valid_seconds = row["valid_seconds"] or 0
        is_logged_in = bool(row["is_logged_in"])

        att_status, _, total_hours = compute_attendance_from_seconds(valid_seconds, is_logged_in)

        c.drawString(50, y, f"{name} - {att_status} - {total_hours} hrs")
        y -= 20

        if y < 50:
            c.showPage()
            y = 800

    c.save()
    return send_file(file, as_attachment=True)


@app.route("/get_logs/<name>")
def logs(name):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT time, action
        FROM logs
        WHERE name=?
        ORDER BY id DESC
        LIMIT 10
    """, (name,))
    data = cur.fetchall()

    cur.execute("""
        SELECT login_time, last_active, logout_time, approved_time, status, campus_status
        FROM students
        WHERE name=?
    """, (name,))
    student = cur.fetchone()

    conn.close()

    return jsonify({
        "logs": [[row["time"], row["action"]] for row in data],
        "login_time": student["login_time"] if student else "-",
        "last_active": student["last_active"] if student else "-",
        "logout_time": student["logout_time"] if student else "-",
        "approved_time": student["approved_time"] if student else "-",
        "approval_status": student["status"] if student else "-",
        "campus_status": student["campus_status"] if student else "-"
    })


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/admin")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
