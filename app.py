"""ExamDesk - Proctored Computer-Based Test platform with AI Professor.

Lecturers: courses, lecture uploads (AI Professor teaches them), exams (objective + theory),
live proctoring monitor, recordings, marking, monthly records.
Students: AI lectures, camera-proctored exams (3 strikes = banned for the day), results.
"""
import base64
import csv
import io
import json
import os
import re
import secrets
import sys
import tempfile
import urllib.request
import time
import string
import threading
import random
from datetime import datetime, timedelta, date
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for, flash, session, g,
                   abort, jsonify, Response, send_file)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

import mimetypes

try:                                   # load settings from .env if python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

# OFFLINE STUDY MODE: a student's own laptop, no internet, no login, everything stored locally.
OFFLINE = os.environ.get("EXAMDESK_OFFLINE") == "1" or getattr(sys, "frozen", False)
if OFFLINE:
    for k in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        os.environ.pop(k, None)
    if getattr(sys, "frozen", False) and not os.environ.get("EXAMDESK_DATA"):
        os.environ["EXAMDESK_DATA"] = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "AIProfessorStudy")

import ai
import tts
import dictionary
import diagrams
import speech
import db as dbl
from storage import Storage, IS_REMOTE as STORAGE_REMOTE

mimetypes.add_type('text/javascript', '.mjs')
mimetypes.add_type('application/wasm', '.wasm')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("EXAMDESK_DATA", os.path.join(BASE_DIR, "instance"))
DB_PATH = os.environ.get("EXAMDESK_DB", os.path.join(DATA_DIR, "examdesk.db"))
os.makedirs(DATA_DIR, exist_ok=True)
OFFLINE_PORT = int(os.environ.get("OFFLINE_PORT", "5050"))
ONLINE_URL = os.environ.get("ONLINE_URL", "").rstrip("/")      # set for the offline companion, to know when to go back
store = Storage(os.path.join(DATA_DIR, "files"))   # Supabase Storage if configured, else instance/files/

MAX_STRIKES = 3
ALLOWED_LECTURE = {".pdf", ".docx", ".pptx", ".txt", ".md"}
SYNC_JOBS = os.environ.get("EXAMDESK_SYNC_JOBS") == "1"   # run AI jobs inline (tests)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key")
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL CHECK(role IN ('lecturer','student')),
    full_name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, identifier TEXT, department TEXT,
    password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT, lecturer_id INTEGER NOT NULL REFERENCES users(id),
    code TEXT NOT NULL, title TEXT NOT NULL, join_code TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS enrollments (
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id), joined_at TEXT NOT NULL,
    PRIMARY KEY(course_id, student_id));
CREATE TABLE IF NOT EXISTS lectures (
    id INTEGER PRIMARY KEY AUTOINCREMENT, course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    title TEXT NOT NULL, original_name TEXT, stored_name TEXT, text TEXT, script TEXT,
    status TEXT NOT NULL DEFAULT 'preparing', audience TEXT NOT NULL DEFAULT 'all', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lecture_recipients (
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id), sent_at TEXT NOT NULL, PRIMARY KEY(lecture_id, student_id));
CREATE TABLE IF NOT EXISTS personal_lectures (
    id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL, original_name TEXT, stored_name TEXT, text TEXT, script TEXT,
    status TEXT NOT NULL DEFAULT 'preparing', progress INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS personal_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, plecture_id INTEGER NOT NULL REFERENCES personal_lectures(id) ON DELETE CASCADE,
    question TEXT NOT NULL, answer TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lecture_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT, lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id), started_at TEXT NOT NULL, last_seen TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0, UNIQUE(lecture_id, student_id));
CREATE TABLE IF NOT EXISTS lecture_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id), question TEXT NOT NULL, answer TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS exams (
    id INTEGER PRIMARY KEY AUTOINCREMENT, lecturer_id INTEGER NOT NULL REFERENCES users(id),
    course_id INTEGER REFERENCES courses(id) ON DELETE SET NULL,
    title TEXT NOT NULL, course_code TEXT NOT NULL, instructions TEXT,
    duration_minutes INTEGER NOT NULL DEFAULT 60, access_code TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft', results_released INTEGER NOT NULL DEFAULT 0,
    shuffle_questions INTEGER NOT NULL DEFAULT 0, proctored INTEGER NOT NULL DEFAULT 1,
    ai_marking INTEGER NOT NULL DEFAULT 0, exam_type TEXT NOT NULL DEFAULT 'standard',
    created_by TEXT NOT NULL DEFAULT 'lecturer', ai_report TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    qtype TEXT NOT NULL CHECK(qtype IN ('objective','theory')), text TEXT NOT NULL,
    option_a TEXT, option_b TEXT, option_c TEXT, option_d TEXT, correct_option TEXT,
    marking_guide TEXT, marks REAL NOT NULL DEFAULT 1, position INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id), started_at TEXT NOT NULL, submitted_at TEXT,
    status TEXT NOT NULL DEFAULT 'in_progress',   -- in_progress | submitted | marking | graded | banned
    objective_score REAL DEFAULT 0, theory_score REAL DEFAULT 0, lecturer_comment TEXT,
    away_count INTEGER NOT NULL DEFAULT 0, hand_count INTEGER NOT NULL DEFAULT 0,
    banned_until TEXT, times_banned INTEGER NOT NULL DEFAULT 0, attempt INTEGER NOT NULL DEFAULT 1,
    last_seen TEXT, frame_at TEXT, marked_by TEXT, UNIQUE(exam_id, student_id));
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    selected_option TEXT, answer_text TEXT, awarded_marks REAL, feedback TEXT,
    UNIQUE(submission_id, question_id));
CREATE TABLE IF NOT EXISTS violations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    vtype TEXT NOT NULL, detail TEXT, snapshot TEXT, strike INTEGER, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recording_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    attempt INTEGER NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_chunks_sub ON recording_chunks(submission_id, attempt, id);
CREATE TABLE IF NOT EXISTS lecture_checks (
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE, student_id INTEGER NOT NULL REFERENCES users(id),
    part INTEGER NOT NULL, correct INTEGER NOT NULL, choice TEXT, created_at TEXT NOT NULL,
    PRIMARY KEY(lecture_id, student_id, part));
CREATE TABLE IF NOT EXISTS lecture_flags (
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE, student_id INTEGER NOT NULL REFERENCES users(id),
    part INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(lecture_id, student_id, part));
CREATE TABLE IF NOT EXISTS tts_cache (
    key TEXT PRIMARY KEY, path TEXT NOT NULL, mime TEXT NOT NULL, engine TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, lecturer_id INTEGER NOT NULL, kind TEXT NOT NULL,
    message TEXT NOT NULL, link TEXT, created_at TEXT NOT NULL);
"""

# columns added after v1 (so an old examdesk.db upgrades in place)
MIGRATIONS = [
    "ALTER TABLE lectures ADD COLUMN audience TEXT NOT NULL DEFAULT 'all'",
    "ALTER TABLE exams ADD COLUMN course_id INTEGER", "ALTER TABLE exams ADD COLUMN proctored INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE exams ADD COLUMN ai_marking INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE exams ADD COLUMN exam_type TEXT NOT NULL DEFAULT 'standard'",
    "ALTER TABLE exams ADD COLUMN created_by TEXT NOT NULL DEFAULT 'lecturer'", "ALTER TABLE exams ADD COLUMN ai_report TEXT",
    "ALTER TABLE questions ADD COLUMN marking_guide TEXT",
    "ALTER TABLE submissions ADD COLUMN away_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE submissions ADD COLUMN hand_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE submissions ADD COLUMN banned_until TEXT",
    "ALTER TABLE submissions ADD COLUMN times_banned INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE submissions ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE submissions ADD COLUMN last_seen TEXT", "ALTER TABLE submissions ADD COLUMN marked_by TEXT",
    "ALTER TABLE submissions ADD COLUMN frame_at TEXT",
    "ALTER TABLE recording_chunks ADD COLUMN size INTEGER NOT NULL DEFAULT 0",
]


def schema_sql():
    """SQLite schema, or the same schema translated for Postgres/Supabase."""
    if not dbl.IS_PG:
        return SCHEMA
    return (SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
                  .replace(" REAL", " DOUBLE PRECISION"))


# ================================================================ database
def connect():
    return dbl.DB(DB_PATH)


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass


def init_db():
    con = connect()
    if not dbl.IS_PG:
        con.execute("PRAGMA journal_mode=WAL")
    con.run_script(schema_sql())
    con.run_script(";".join(MIGRATIONS))
    if dbl.IS_PG:
        # Lock tables away from Supabase's public REST API (anon key). The app connects as the owner, which bypasses RLS.
        tables = [r[0] for r in con.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()]
        con.run_script(";".join(f'ALTER TABLE "{t}" ENABLE ROW LEVEL SECURITY' for t in tables))
    con.close()
    store.ensure_bucket()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def code6():
    return "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))


def run_job(fn, *args):
    if SYNC_JOBS:
        fn(*args)
    else:
        threading.Thread(target=fn, args=args, daemon=True).start()


def log_event(lecturer_id, kind, message, link=None, con=None):
    c = con or get_db()
    c.execute("INSERT INTO events(lecturer_id,kind,message,link,created_at) VALUES(?,?,?,?,?)",
              (lecturer_id, kind, message, link, now()))
    c.commit()


# ================================================================ auth
def offline_student():
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE email='offline@local'").fetchone()
    if not u:
        name = os.environ.get("STUDENT_NAME", "Student")
        db.execute("INSERT INTO users(role,full_name,email,identifier,department,password_hash,created_at) VALUES(?,?,?,?,?,?,?)",
                   ("student", name, "offline@local", "OFFLINE", "", generate_password_hash(secrets.token_hex(16)), now()))
        db.commit()
        u = db.execute("SELECT * FROM users WHERE email='offline@local'").fetchone()
    return u


@app.before_request
def load_user():
    g.user = None
    if OFFLINE:
        g.user = offline_student()
        return
    uid = session.get("user_id")
    if uid:
        g.user = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def login_required(role=None):
    def deco(f):
        @wraps(f)
        def wrapper(*a, **kw):
            if g.user is None:
                if request.is_json or request.path.endswith(".json"):
                    return jsonify(ok=False, reason="auth"), 401
                flash("Please sign in to continue.", "warning")
                return redirect(url_for("login"))
            if role and g.user["role"] != role:
                abort(403)
            return f(*a, **kw)
        return wrapper
    return deco


@app.context_processor
def inject():
    return {"offline": OFFLINE, "online_back_check": bool(OFFLINE and ONLINE_URL),
            "watch_connection": bool(dbl.IS_PG and not OFFLINE and request.remote_addr in ("127.0.0.1", "::1")),
            "offline_port": OFFLINE_PORT, "current_user": g.get("user"), "stt_ready": speech.available(),
            "stt_status": speech.status(), "dict_ready": dictionary.available(), "year": datetime.now().year, "ai_on": ai.ai_enabled(), "ai_mode": ai.provider(), "ai_label": ai.provider_label(), "voice_label": tts.describe()}


def grade_letter(pct):
    for cut, letter in ((70, "A"), (60, "B"), (50, "C"), (45, "D"), (40, "E")):
        if pct >= cut:
            return letter
    return "F"


app.jinja_env.globals["grade_letter"] = grade_letter


@app.template_filter("ago")
def ago(ts):
    if not ts:
        return "—"
    s = int((datetime.now() - parse_dt(ts)).total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return ts[:10]


def offline_url():
    """Where the offline study app is, if this browser is on the same computer."""
    local = request.remote_addr in ("127.0.0.1", "::1")
    return f"http://127.0.0.1:{OFFLINE_PORT}/" if local and dbl.IS_PG and not OFFLINE and _port_open(OFFLINE_PORT) else None


def offline_switch_guard():
    if OFFLINE or not dbl.IS_PG or dbl.is_up():
        return None
    if request.endpoint in ("static", "health"):
        return None
    if time.time() - dbl._health["checked"] > 4:              # re-check now if the last check is old
        if dbl.check(timeout=3):
            return None
    return database_unreachable(None)


@app.route("/health")
def health():
    ok = dbl.is_up() if not dbl.IS_PG or time.time() - dbl._health["checked"] < 6 else dbl.check(timeout=3)
    return jsonify(ok=ok, mode="offline" if OFFLINE else "online"), (200 if ok else 503)


@app.route("/online-status")
def online_status():
    """Used by Offline Study to notice when the internet (and online ExamDesk) is back."""
    if not ONLINE_URL:
        return jsonify(online=False, url=None)
    try:
        with urllib.request.urlopen(ONLINE_URL + "/health", timeout=2.5) as r:
            ok = r.status == 200
    except Exception:
        ok = False
    return jsonify(online=ok, url=ONLINE_URL + "/")


OFFLINE_OK = {"stt", "dictionary_lookup", "index", "student_dashboard", "note_upload", "note_view", "note_heartbeat", "note_ask", "note_simpler",
              "note_notes", "note_delete", "speak", "tts_audio", "teacher_avatar", "static", "health", "online_status"}


@app.before_request
def offline_guard():
    if OFFLINE and request.endpoint and request.endpoint not in OFFLINE_OK:
        return redirect(url_for("student_dashboard"))


@app.route("/")
def index():
    if g.user:
        return redirect(url_for("lecturer_dashboard" if g.user["role"] == "lecturer" else "student_dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = request.form
        role = f.get("role")
        if role not in ("lecturer", "student") or not all(f.get(k) for k in ("full_name", "email", "password", "identifier")):
            flash("All fields are required.", "danger")
        elif len(f["password"]) < 6:
            flash("Password must be at least 6 characters.", "danger")
        elif f["password"] != f.get("confirm"):
            flash("Passwords do not match.", "danger")
        else:
            db = get_db()
            try:
                db.execute("INSERT INTO users(role,full_name,email,identifier,department,password_hash,created_at)"
                           " VALUES(?,?,?,?,?,?,?)",
                           (role, f["full_name"].strip(), f["email"].strip().lower(), f["identifier"].strip().upper(),
                            f.get("department", "").strip(), generate_password_hash(f["password"]), now()))
                db.commit()
                flash("Account created. You can now sign in.", "success")
                return redirect(url_for("login"))
            except dbl.IntegrityError:
                flash("An account with that email already exists.", "danger")
    return render_template("register.html", role=request.args.get("role", "student"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = get_db().execute("SELECT * FROM users WHERE email=?",
                                (request.form.get("email", "").strip().lower(),)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("index"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("login"))


# ================================================================ shared helpers
def own_exam(exam_id):
    exam = get_db().execute("SELECT * FROM exams WHERE id=? AND lecturer_id=?", (exam_id, g.user["id"])).fetchone()
    if not exam:
        abort(404)
    return exam


def own_course(course_id):
    c = get_db().execute("SELECT * FROM courses WHERE id=? AND lecturer_id=?", (course_id, g.user["id"])).fetchone()
    if not c:
        abort(404)
    return c


def own_submission(sid):
    sub = get_db().execute(
        "SELECT s.*, u.full_name, u.identifier, u.department, u.email FROM submissions s "
        "JOIN users u ON u.id=s.student_id JOIN exams e ON e.id=s.exam_id WHERE s.id=? AND e.lecturer_id=?",
        (sid, g.user["id"])).fetchone()
    if not sub:
        abort(404)
    return sub


def exam_totals(exam_id, con=None):
    row = (con or get_db()).execute(
        "SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN qtype='objective' THEN marks END),0) obj,"
        " COALESCE(SUM(CASE WHEN qtype='theory' THEN marks END),0) th,"
        " SUM(CASE WHEN qtype='objective' THEN 1 ELSE 0 END) nobj, SUM(CASE WHEN qtype='theory' THEN 1 ELSE 0 END) nth"
        " FROM questions WHERE exam_id=?", (exam_id,)).fetchone()
    return {"count": row["n"], "objective": row["obj"], "theory": row["th"], "total": row["obj"] + row["th"],
            "nobj": row["nobj"] or 0, "nth": row["nth"] or 0}


def recordings_for(sub_id, con=None):
    """Attempt numbers that have recorded video."""
    rows = (con or get_db()).execute("SELECT DISTINCT attempt FROM recording_chunks WHERE submission_id=? ORDER BY attempt",
                                     (sub_id,)).fetchall()
    return [r["attempt"] for r in rows]


# ================================================================ LECTURER: dashboard / feed
@app.route("/lecturer")
@login_required("lecturer")
def lecturer_dashboard():
    db = get_db()
    uid = g.user["id"]
    exams = db.execute(
        "SELECT e.*, (SELECT COUNT(*) FROM questions q WHERE q.exam_id=e.id) qcount,"
        " (SELECT COUNT(*) FROM submissions s WHERE s.exam_id=e.id AND s.status!='in_progress') subs,"
        " (SELECT COUNT(*) FROM submissions s WHERE s.exam_id=e.id AND s.status='in_progress') writing,"
        " (SELECT COUNT(*) FROM submissions s WHERE s.exam_id=e.id AND s.status='submitted') pending"
        " FROM exams e WHERE lecturer_id=? ORDER BY e.created_at DESC", (uid,)).fetchall()
    courses = db.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM enrollments en WHERE en.course_id=c.id) students,"
        " (SELECT COUNT(*) FROM lectures l WHERE l.course_id=c.id) lectures FROM courses c WHERE lecturer_id=?"
        " ORDER BY c.created_at DESC", (uid,)).fetchall()
    live_lecture = db.execute(
        "SELECT COUNT(*) FROM lecture_views v JOIN lectures l ON l.id=v.lecture_id JOIN courses c ON c.id=l.course_id "
        "WHERE c.lecturer_id=? AND v.last_seen>=?", (uid, (datetime.now() - timedelta(seconds=60)).strftime("%Y-%m-%d %H:%M:%S"))).fetchone()[0]
    banned = db.execute("SELECT COUNT(*) FROM submissions s JOIN exams e ON e.id=s.exam_id WHERE e.lecturer_id=? "
                        "AND s.status='banned'", (uid,)).fetchone()[0]
    stats = {"writing": sum(e["writing"] for e in exams), "learning": live_lecture,
             "pending": sum(e["pending"] for e in exams), "banned": banned,
             "subs": sum(e["subs"] for e in exams), "courses": len(courses)}
    return render_template("lecturer/dashboard.html", exams=exams, courses=courses, stats=stats,
                           rosters=course_rosters(uid))


@app.route("/lecturer/feed.json")
@login_required("lecturer")
def lecturer_feed():
    after = int(request.args.get("after", 0))
    rows = get_db().execute("SELECT * FROM events WHERE lecturer_id=? AND id>? ORDER BY id DESC LIMIT 40",
                            (g.user["id"], after)).fetchall()
    return jsonify(events=[dict(r) for r in rows])


# ================================================================ LECTURER: courses & lectures
@app.route("/lecturer/courses/new", methods=["POST"])
@login_required("lecturer")
def course_new():
    f = request.form
    if not f.get("code") or not f.get("title"):
        flash("Course code and title are required.", "danger")
        return redirect(url_for("lecturer_dashboard"))
    db = get_db()
    new_id = db.insert("INSERT INTO courses(lecturer_id,code,title,join_code,created_at) VALUES(?,?,?,?,?)",
                       (g.user["id"], f["code"].strip().upper(), f["title"].strip(), code6(), now()))
    db.commit()
    flash("Course created. Share the class code with your students.", "success")
    return redirect(url_for("course_page", course_id=new_id))


@app.route("/lecturer/courses/<int:course_id>")
@login_required("lecturer")
def course_page(course_id):
    c = own_course(course_id)
    db = get_db()
    students = db.execute("SELECT u.*, en.joined_at FROM enrollments en JOIN users u ON u.id=en.student_id "
                          "WHERE en.course_id=? ORDER BY u.identifier", (course_id,)).fetchall()
    lectures = db.execute(
        "SELECT l.id,l.title,l.original_name,l.status,l.audience,l.created_at,"
        " (SELECT COUNT(*) FROM lecture_recipients r WHERE r.lecture_id=l.id) recipients,"
        " (SELECT COUNT(*) FROM lecture_views v WHERE v.lecture_id=l.id) views,"
        " (SELECT COUNT(*) FROM lecture_questions q WHERE q.lecture_id=l.id) questions"
        " FROM lectures l WHERE course_id=? ORDER BY l.created_at DESC", (course_id,)).fetchall()
    exams = db.execute("SELECT * FROM exams WHERE course_id=? ORDER BY created_at DESC", (course_id,)).fetchall()
    return render_template("lecturer/course.html", course=c, students=students, lectures=lectures, exams=exams,
                           rosters=course_rosters(g.user["id"]),
                           month=datetime.now().strftime("%B %Y"))


def prepare_lecture(lecture_id):
    con = connect()
    try:
        l = con.execute("SELECT l.*, c.lecturer_id, c.code FROM lectures l JOIN courses c ON c.id=l.course_id "
                        "WHERE l.id=?", (lecture_id,)).fetchone()
        segs = ai.build_lecture(l["title"], l["text"] or "")
        prewarm_voice(segs, con)
        con.execute("UPDATE lectures SET script=?, status='ready' WHERE id=?", (json.dumps(segs), lecture_id))
        con.commit()
        log_event(l["lecturer_id"], "lecture", f"AI Professor prepared the lecture '{l['title']}' ({l['code']}) "
                  f"— {len(segs)} parts, ready for students.", f"/lecturer/lectures/{lecture_id}", con)
    except Exception as e:
        con.execute("UPDATE lectures SET status='failed' WHERE id=?", (lecture_id,))
        con.commit()
        print("prepare_lecture error:", e)
    finally:
        con.close()


def read_upload(file):
    """Save an uploaded lecture file and extract its text. Returns (stored_name, text) or raises ValueError."""
    if not file or not file.filename:
        raise ValueError("Choose a lecture file to upload.")
    if os.path.splitext(file.filename)[1].lower() not in ALLOWED_LECTURE:
        raise ValueError("Upload a PDF, Word (.docx), PowerPoint (.pptx) or text file. Old .doc/.ppt: save as .docx/.pptx first.")
    ext = os.path.splitext(file.filename)[1].lower()
    stored = f"lectures/{secrets.token_hex(8)}_{secure_filename(file.filename)}"
    fd, tmp = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    try:
        file.save(tmp)
        text = ai.extract_text(tmp)
        if len(text.strip()) < 50:
            raise ValueError("Could not read enough text from that file (scanned PDFs need to be typed/OCR'd first).")
        with open(tmp, "rb") as fh:
            store.put(stored, fh.read(), file.mimetype or "application/octet-stream")
    finally:
        os.remove(tmp)
    return stored, text


def set_recipients(db, lid, course_id, form):
    """Apply 'all' or 'selected' audience. Returns number of students who receive the lecture."""
    enrolled = {r["student_id"] for r in db.execute("SELECT student_id FROM enrollments WHERE course_id=?", (course_id,))}
    audience = "selected" if form.get("audience") == "selected" else "all"
    chosen = {int(x) for x in form.getlist("students") if x.isdigit()} & enrolled
    if audience == "selected" and not chosen:
        raise ValueError("Tick at least one student, or choose 'All students in the course'.")
    db.execute("UPDATE lectures SET audience=? WHERE id=?", (audience, lid))
    db.execute("DELETE FROM lecture_recipients WHERE lecture_id=?", (lid,))
    for sid in (chosen if audience == "selected" else []):
        db.execute("INSERT INTO lecture_recipients(lecture_id,student_id,sent_at) VALUES(?,?,?)", (lid, sid, now()))
    db.commit()
    return len(enrolled) if audience == "all" else len(chosen)


def course_rosters(lecturer_id):
    """{course_id: [{id, name, matric}]} for the send-lecture form."""
    out = {}
    for r in get_db().execute("SELECT en.course_id, u.id, u.full_name, u.identifier FROM enrollments en JOIN users u ON "
                              "u.id=en.student_id JOIN courses c ON c.id=en.course_id WHERE c.lecturer_id=? "
                              "ORDER BY u.identifier", (lecturer_id,)):
        out.setdefault(r["course_id"], []).append({"id": r["id"], "name": r["full_name"], "matric": r["identifier"]})
    return out


@app.route("/lecturer/lectures/send", methods=["POST"])
@app.route("/lecturer/courses/<int:course_id>/lectures", methods=["POST"])
@login_required("lecturer")
def lecture_upload(course_id=None):
    course_id = course_id or request.form.get("course_id", type=int)
    back = request.referrer or url_for("lecturer_dashboard")
    if not course_id:
        flash("Choose the course to send the lecture to.", "danger")
        return redirect(back)
    c = own_course(course_id)
    if request.form.get("audience") == "selected" and not request.form.getlist("students"):
        flash("Tick at least one student, or choose 'All students in the course'.", "danger")
        return redirect(back)
    try:
        stored, text = read_upload(request.files.get("file"))
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(back)
    title = request.form.get("title", "").strip() or os.path.splitext(request.files["file"].filename)[0]
    db = get_db()
    lid = db.insert("INSERT INTO lectures(course_id,title,original_name,stored_name,text,status,created_at) "
                    "VALUES(?,?,?,?,?,'preparing',?)", (course_id, title, request.files["file"].filename, stored, text, now()))
    db.commit()
    n = set_recipients(db, lid, course_id, request.form)
    who = "all students" if request.form.get("audience") != "selected" else f"{n} selected student{'s' if n != 1 else ''}"
    log_event(g.user["id"], "lecture", f"You sent '{title}' ({c['code']}) to {who}. AI Professor is preparing it.",
              f"/lecturer/lectures/{lid}")
    run_job(prepare_lecture, lid)
    flash(f"Lecture sent to {who} in {c['code']}. AI Professor is preparing it — students can listen in about a minute.", "success")
    return redirect(back)


@app.route("/lecturer/lectures/<int:lid>/recipients", methods=["POST"])
@login_required("lecturer")
def lecture_recipients(lid):
    db = get_db()
    l = db.execute("SELECT l.*, c.id cid FROM lectures l JOIN courses c ON c.id=l.course_id WHERE l.id=? AND c.lecturer_id=?",
                   (lid, g.user["id"])).fetchone()
    if not l:
        abort(404)
    try:
        n = set_recipients(db, lid, l["cid"], request.form)
        flash(f"Updated: this lecture now goes to {'all students' if request.form.get('audience') != 'selected' else str(n) + ' selected student(s)'}.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("lecture_admin", lid=lid))


@app.route("/lecturer/lectures/<int:lid>")
@login_required("lecturer")
def lecture_admin(lid):
    db = get_db()
    l = db.execute("SELECT l.*, c.code, c.title course_title, c.id cid FROM lectures l JOIN courses c ON c.id=l.course_id "
                   "WHERE l.id=? AND c.lecturer_id=?", (lid, g.user["id"])).fetchone()
    if not l:
        abort(404)
    segs = lecture_segments(l) if l["script"] else []
    difficult = {r["part"]: r["n"] for r in db.execute(
        "SELECT part, COUNT(*) n FROM lecture_flags WHERE lecture_id=? GROUP BY part", (lid,)).fetchall()}
    understanding = {r["part"]: {"pct": round(float(r["pct"]) * 100), "n": r["n"]} for r in db.execute(
        "SELECT part, AVG(correct * 1.0) pct, COUNT(*) n FROM lecture_checks WHERE lecture_id=? GROUP BY part", (lid,)).fetchall()}
    viewers = db.execute("SELECT v.*, u.full_name, u.identifier FROM lecture_views v JOIN users u ON u.id=v.student_id "
                         "WHERE v.lecture_id=? ORDER BY v.last_seen DESC", (lid,)).fetchall()
    qa = db.execute("SELECT q.*, u.full_name, u.identifier FROM lecture_questions q JOIN users u ON u.id=q.student_id "
                    "WHERE q.lecture_id=? ORDER BY q.id DESC", (lid,)).fetchall()
    live_cut = (datetime.now() - timedelta(seconds=60)).strftime("%Y-%m-%d %H:%M:%S")
    roster = db.execute("SELECT u.id, u.full_name, u.identifier FROM enrollments en JOIN users u ON u.id=en.student_id "
                        "WHERE en.course_id=? ORDER BY u.identifier", (l["cid"],)).fetchall()
    chosen = {r["student_id"] for r in db.execute("SELECT student_id FROM lecture_recipients WHERE lecture_id=?", (lid,))}
    return render_template("lecturer/lecture.html", l=l, segs=segs, viewers=viewers, qa=qa, live_cut=live_cut,
                           understanding=understanding, difficult=difficult,
                           roster=roster, chosen=chosen,
                           rosters_one={l["cid"]: [{"id": r["id"], "name": r["full_name"], "matric": r["identifier"]} for r in roster]})


@app.route("/lecturer/lectures/<int:lid>/edit", methods=["GET", "POST"])
@login_required("lecturer")
def lecture_edit(lid):
    """Lecturer corrects what AI Professor will teach: parts, board notes, explanation and quick-check questions."""
    db = get_db()
    l = db.execute("SELECT l.*, c.code, c.id cid, c.lecturer_id FROM lectures l JOIN courses c ON c.id=l.course_id "
                   "WHERE l.id=? AND c.lecturer_id=?", (lid, g.user["id"])).fetchone()
    if not l or not l["script"]:
        abort(404)
    segs = lecture_segments(l)
    if request.method == "POST":
        f = request.form
        new = []
        for i in range(int(f.get("count", 0)) + 1):
            if f.get(f"del_{i}"):
                continue
            title = f.get(f"title_{i}", "").strip()
            speech = f.get(f"speech_{i}", "").strip()
            if not title or not speech:
                continue
            old = segs[i] if i < len(segs) else {}
            points = [x.strip() for x in f.get(f"points_{i}", "").splitlines() if x.strip()][:6]
            part = {"title": title[:90], "kind": old.get("kind", "teach"), "points": points, "speech": speech[:6000],
                    "source": old.get("source", ""),
                    "visual": old.get("visual") if points == old.get("points") and title == old.get("title") else None}
            if part["kind"] == "teach":
                if f.get(f"nocheck_{i}"):
                    part["check"] = False
                else:
                    opts = [f.get(f"opt_{i}_{k}", "").strip() for k in range(4)]
                    opts = [o for o in opts if o]
                    q = {"question": f.get(f"q_{i}", "").strip(), "options": opts,
                         "answer": f.get(f"ans_{i}", "A"), "explain": f.get(f"exp_{i}", "").strip()}
                    part["check"] = q if ai._valid_check(q) else None
            new.append(part)
        if not new:
            flash("A lecture needs at least one part.", "danger")
            return redirect(url_for("lecture_edit", lid=lid))
        new[0].update(edited=True, v=ai.BUILD_VERSION, engine=segs[0].get("engine", "basic") if segs else "basic")
        db.execute("UPDATE lectures SET script=? WHERE id=?", (json.dumps(new), lid))
        db.commit()
        run_job(lambda segs_=new: _prewarm_job(segs_))
        log_event(g.user["id"], "lecture", f"You edited the lecture '{l['title']}' ({len(new)} parts).", f"/lecturer/lectures/{lid}")
        flash("Lecture saved. Students will see your corrections immediately.", "success")
        return redirect(url_for("lecture_admin", lid=lid))
    return render_template("lecturer/lecture_edit.html", l=l, segs=segs)


def lecture_segments(row, table="lectures"):
    """The lecture's parts. Lectures built by an older basic mode are rebuilt automatically (never ones a lecturer edited)."""
    segs = json.loads(row["script"]) if row["script"] else []
    first = segs[0] if segs else {}
    stale = not segs or first.get("v") != ai.BUILD_VERSION
    built_basic = first.get("engine", "basic") == "basic"
    if stale and built_basic and not first.get("edited") and ai.provider() == "basic" and (row["text"] or "").strip():
        segs = ai.build_lecture(row["title"], row["text"])
        db = get_db()
        db.execute(f"UPDATE {table} SET script=? WHERE id=?", (json.dumps(segs), row["id"]))
        db.commit()
        run_job(lambda segs_=segs: _prewarm_job(segs_))
        return segs
    return ai.enrich(segs, row["text"] or "")


def _prewarm_job(segs):
    con = connect()
    try:
        prewarm_voice(segs, con)
    finally:
        con.close()


@app.route("/lecturer/lectures/<int:lid>/action", methods=["POST"])
@login_required("lecturer")
def lecture_action(lid):
    db = get_db()
    l = db.execute("SELECT l.*, c.id cid FROM lectures l JOIN courses c ON c.id=l.course_id WHERE l.id=? AND c.lecturer_id=?",
                   (lid, g.user["id"])).fetchone()
    if not l:
        abort(404)
    if request.form.get("action") == "delete":
        db.execute("DELETE FROM lectures WHERE id=?", (lid,))
        db.commit()
        flash("Lecture deleted.", "info")
        return redirect(url_for("course_page", course_id=l["cid"]))
    db.execute("UPDATE lectures SET status='preparing' WHERE id=?", (lid,))
    db.commit()
    run_job(prepare_lecture, lid)
    flash("AI Professor is re-preparing this lecture.", "info")
    return redirect(url_for("lecture_admin", lid=lid))


# ================================================================ LECTURER: AI monthly exam
def create_ai_exam(course_id, lecturer_id, n_obj, n_theory, duration, theory_marks):
    con = connect()
    try:
        c = con.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
        text = "\n\n".join(r["text"] or "" for r in con.execute(
            "SELECT text FROM lectures WHERE course_id=? ORDER BY created_at", (course_id,)))
        data = ai.generate_exam(c["title"], text, n_obj, n_theory, theory_marks)
        month = datetime.now().strftime("%B %Y")
        eid = con.insert(
            "INSERT INTO exams(lecturer_id,course_id,title,course_code,instructions,duration_minutes,access_code,"
            "exam_type,created_by,ai_marking,proctored,created_at) VALUES(?,?,?,?,?,?,?,'monthly','ai',1,1,?)",
            (lecturer_id, course_id, f"Monthly Test — {month}", c["code"],
             "Answer ALL questions in Section A and Section B. Your camera must stay on and you must remain in "
             "view throughout. This test is set and marked by AI Professor.", duration, code6(), now()))
        pos = 0
        for q in data.get("objective", [])[:n_obj]:
            opts = (list(q.get("options", [])) + ["", "", "", ""])[:4]
            if not opts[0] or q.get("answer") not in ("A", "B", "C", "D"):
                continue
            pos += 1
            con.execute("INSERT INTO questions(exam_id,qtype,text,option_a,option_b,option_c,option_d,correct_option,"
                        "marks,position) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (eid, "objective", q["text"], *opts, q["answer"], 1, pos))
        for q in data.get("theory", [])[:n_theory]:
            pos += 1
            con.execute("INSERT INTO questions(exam_id,qtype,text,marking_guide,marks,position) VALUES(?,?,?,?,?,?)",
                        (eid, "theory", q["text"], q.get("marking_guide", ""), theory_marks, pos))
        con.commit()
        log_event(lecturer_id, "ai", f"AI Professor set the {c['code']} monthly test ({pos} questions). "
                  "Review and open it when ready.", f"/lecturer/exams/{eid}", con)
    except Exception as e:
        print("create_ai_exam error:", e)
        log_event(lecturer_id, "error", f"AI Professor could not set the monthly test: {e}", None, con)
    finally:
        con.close()


@app.route("/lecturer/courses/<int:course_id>/monthly", methods=["POST"])
@login_required("lecturer")
def monthly_exam(course_id):
    own_course(course_id)
    if not get_db().execute("SELECT 1 FROM lectures WHERE course_id=?", (course_id,)).fetchone():
        flash("Upload at least one lecture file first — AI Professor sets the test from your lectures.", "danger")
        return redirect(url_for("course_page", course_id=course_id))
    f = request.form
    run_job(create_ai_exam, course_id, g.user["id"], int(f.get("n_obj") or 10), int(f.get("n_theory") or 3),
            int(f.get("duration") or 45), float(f.get("theory_marks") or 10))
    flash("AI Professor is setting the monthly test from your lectures. It will appear under this course shortly.", "success")
    return redirect(url_for("course_page", course_id=course_id))


# ================================================================ LECTURER: exams
@app.route("/lecturer/exams/new", methods=["GET", "POST"])
@login_required("lecturer")
def exam_new():
    courses = get_db().execute("SELECT * FROM courses WHERE lecturer_id=?", (g.user["id"],)).fetchall()
    if request.method == "POST":
        f = request.form
        if not f.get("title") or not f.get("course_code"):
            flash("Title and course code are required.", "danger")
        else:
            db = get_db()
            new_id = db.insert(
                "INSERT INTO exams(lecturer_id,course_id,title,course_code,instructions,duration_minutes,access_code,"
                "shuffle_questions,proctored,ai_marking,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (g.user["id"], int(f["course_id"]) if f.get("course_id") else None, f["title"].strip(),
                 f["course_code"].strip().upper(), f.get("instructions", "").strip(),
                 max(1, int(f.get("duration_minutes") or 60)), code6(), 1 if f.get("shuffle_questions") else 0,
                 1 if f.get("proctored") else 0, 1 if f.get("ai_marking") else 0, now()))
            db.commit()
            flash("Exam created. Now add your questions.", "success")
            return redirect(url_for("exam_manage", exam_id=new_id))
    return render_template("lecturer/exam_form.html", exam=None, courses=courses,
                           pre_course=request.args.get("course", type=int))


@app.route("/lecturer/exams/<int:exam_id>/edit", methods=["GET", "POST"])
@login_required("lecturer")
def exam_edit(exam_id):
    exam = own_exam(exam_id)
    courses = get_db().execute("SELECT * FROM courses WHERE lecturer_id=?", (g.user["id"],)).fetchall()
    if request.method == "POST":
        f = request.form
        db = get_db()
        db.execute("UPDATE exams SET title=?,course_code=?,instructions=?,duration_minutes=?,shuffle_questions=?,"
                   "proctored=?,ai_marking=?,course_id=? WHERE id=?",
                   (f["title"].strip(), f["course_code"].strip().upper(), f.get("instructions", "").strip(),
                    max(1, int(f.get("duration_minutes") or 60)), 1 if f.get("shuffle_questions") else 0,
                    1 if f.get("proctored") else 0, 1 if f.get("ai_marking") else 0,
                    int(f["course_id"]) if f.get("course_id") else None, exam_id))
        db.commit()
        flash("Exam details updated.", "success")
        return redirect(url_for("exam_manage", exam_id=exam_id))
    return render_template("lecturer/exam_form.html", exam=exam, courses=courses, pre_course=None)


@app.route("/lecturer/exams/<int:exam_id>")
@login_required("lecturer")
def exam_manage(exam_id):
    exam = own_exam(exam_id)
    qs = get_db().execute("SELECT * FROM questions WHERE exam_id=? ORDER BY qtype DESC, position, id", (exam_id,)).fetchall()
    return render_template("lecturer/exam_manage.html", exam=exam,
                           objective=[q for q in qs if q["qtype"] == "objective"],
                           theory=[q for q in qs if q["qtype"] == "theory"], totals=exam_totals(exam_id))


@app.route("/lecturer/exams/<int:exam_id>/questions", methods=["POST"])
@login_required("lecturer")
def question_add(exam_id):
    own_exam(exam_id)
    f = request.form
    qtype, text = f.get("qtype"), f.get("text", "").strip()
    try:
        marks = float(f.get("marks") or 1)
    except ValueError:
        marks = 1
    if not text:
        flash("Question text is required.", "danger")
        return redirect(url_for("exam_manage", exam_id=exam_id))
    db = get_db()
    pos = db.execute("SELECT COALESCE(MAX(position),0)+1 FROM questions WHERE exam_id=?", (exam_id,)).fetchone()[0]
    if qtype == "objective":
        opts = [f.get(k, "").strip() for k in ("option_a", "option_b", "option_c", "option_d")]
        correct = f.get("correct_option")
        if not all(opts[:2]) or correct not in ("A", "B", "C", "D") or not opts["ABCD".index(correct)]:
            flash("Objective questions need at least options A and B, and a valid correct answer.", "danger")
            return redirect(url_for("exam_manage", exam_id=exam_id) + "#objective")
        db.execute("INSERT INTO questions(exam_id,qtype,text,option_a,option_b,option_c,option_d,correct_option,marks,"
                   "position) VALUES(?,?,?,?,?,?,?,?,?,?)", (exam_id, "objective", text, *opts, correct, marks, pos))
    elif qtype == "theory":
        db.execute("INSERT INTO questions(exam_id,qtype,text,marking_guide,marks,position) VALUES(?,?,?,?,?,?)",
                   (exam_id, "theory", text, f.get("marking_guide", "").strip(), marks, pos))
    else:
        abort(400)
    db.commit()
    flash(f"{qtype.title()} question added.", "success")
    return redirect(url_for("exam_manage", exam_id=exam_id) + f"#{qtype}")


@app.route("/lecturer/questions/<int:qid>/delete", methods=["POST"])
@login_required("lecturer")
def question_delete(qid):
    db = get_db()
    q = db.execute("SELECT q.* FROM questions q JOIN exams e ON e.id=q.exam_id WHERE q.id=? AND e.lecturer_id=?",
                   (qid, g.user["id"])).fetchone()
    if not q:
        abort(404)
    db.execute("DELETE FROM questions WHERE id=?", (qid,))
    db.commit()
    flash("Question removed.", "info")
    return redirect(url_for("exam_manage", exam_id=q["exam_id"]) + f"#{q['qtype']}")


@app.route("/lecturer/exams/<int:exam_id>/status", methods=["POST"])
@login_required("lecturer")
def exam_status(exam_id):
    exam = own_exam(exam_id)
    action = request.form.get("action")
    db = get_db()
    if action == "open":
        if exam_totals(exam_id)["count"] == 0:
            flash("Add at least one question before opening the exam.", "danger")
            return redirect(url_for("exam_manage", exam_id=exam_id))
        db.execute("UPDATE exams SET status='open' WHERE id=?", (exam_id,))
        log_event(g.user["id"], "exam", f"{exam['course_code']} — {exam['title']} is now OPEN (code {exam['access_code']}).",
                  f"/lecturer/exams/{exam_id}/live")
        flash(f"Exam is now open. Students in the course see it on their dashboard; others use code {exam['access_code']}.", "success")
    elif action == "close":
        db.execute("UPDATE exams SET status='closed' WHERE id=?", (exam_id,))
        db.commit()
        for s in db.execute("SELECT id FROM submissions WHERE exam_id=? AND status='in_progress'", (exam_id,)).fetchall():
            finalize_submission(s["id"])
        log_event(g.user["id"], "exam", f"{exam['course_code']} — {exam['title']} was closed.", f"/lecturer/exams/{exam_id}/submissions")
        flash("Exam closed. Unfinished scripts were submitted automatically.", "info")
    elif action == "release":
        db.execute("UPDATE exams SET results_released=1 WHERE id=?", (exam_id,))
        flash("Results released to students.", "success")
    elif action == "withhold":
        db.execute("UPDATE exams SET results_released=0 WHERE id=?", (exam_id,))
        flash("Results hidden from students.", "info")
    elif action == "regen":
        db.execute("UPDATE exams SET access_code=? WHERE id=?", (code6(), exam_id))
        flash("New access code generated.", "info")
    elif action == "delete":
        db.execute("DELETE FROM exams WHERE id=?", (exam_id,))
        db.commit()
        flash("Exam deleted.", "info")
        return redirect(url_for("lecturer_dashboard"))
    db.commit()
    return redirect(request.referrer or url_for("exam_manage", exam_id=exam_id))


@app.route("/lecturer/exams/<int:exam_id>/submissions")
@login_required("lecturer")
def exam_submissions(exam_id):
    exam = own_exam(exam_id)
    subs = get_db().execute("SELECT s.*, u.full_name, u.identifier, u.department FROM submissions s "
                            "JOIN users u ON u.id=s.student_id WHERE s.exam_id=? ORDER BY s.submitted_at DESC",
                            (exam_id,)).fetchall()
    return render_template("lecturer/submissions.html", exam=exam, subs=subs, totals=exam_totals(exam_id))


# ---------------------------------------------------------------- live monitor
@app.route("/lecturer/exams/<int:exam_id>/live")
@login_required("lecturer")
def exam_live(exam_id):
    return render_template("lecturer/live.html", exam=own_exam(exam_id), totals=exam_totals(exam_id))


@app.route("/lecturer/exams/<int:exam_id>/live.json")
@login_required("lecturer")
def exam_live_json(exam_id):
    exam = own_exam(exam_id)
    db = get_db()
    total_q = exam_totals(exam_id)["count"]
    out = []
    for s in db.execute("SELECT s.*, u.full_name, u.identifier FROM submissions s JOIN users u ON u.id=s.student_id "
                        "WHERE s.exam_id=? ORDER BY s.status='in_progress' DESC, u.identifier", (exam_id,)).fetchall():
        answered = db.execute("SELECT COUNT(*) FROM answers WHERE submission_id=? AND (selected_option IS NOT NULL OR "
                              "(answer_text IS NOT NULL AND TRIM(answer_text)!=''))", (s["id"],)).fetchone()[0]
        end = parse_dt(s["started_at"]) + timedelta(minutes=exam["duration_minutes"])
        seen = int((datetime.now() - parse_dt(s["last_seen"])).total_seconds()) if s["last_seen"] else None

        out.append({"id": s["id"], "name": s["full_name"], "matric": s["identifier"], "status": s["status"],
                    "away": s["away_count"], "hand": s["hand_count"], "answered": answered, "total": total_q,
                    "left": max(0, int((end - datetime.now()).total_seconds())) if s["status"] == "in_progress" else 0,
                    "seen": seen, "snap": url_for("live_snap", sid=s["id"], t=s["frame_at"].replace(" ", "T"))
                    if s["frame_at"] else None,
                    "grade_url": url_for("submission_grade", sid=s["id"])})
    last_v = db.execute("SELECT v.*, u.full_name FROM violations v JOIN submissions s ON s.id=v.submission_id "
                        "JOIN users u ON u.id=s.student_id WHERE s.exam_id=? ORDER BY v.id DESC LIMIT 15", (exam_id,)).fetchall()
    return jsonify(candidates=out, violations=[{"name": v["full_name"], "type": v["vtype"], "detail": v["detail"],
                                                "strike": v["strike"], "at": v["created_at"][11:]} for v in last_v])


@app.route("/lecturer/live/<int:sid>.jpg")
@login_required("lecturer")
def live_snap(sid):
    own_submission(sid)
    return store.serve(f"live/sub_{sid}.jpg", "image/jpeg")


@app.route("/lecturer/snapshots/<name>")
@login_required("lecturer")
def violation_snap(name):
    name = secure_filename(name)
    sid = int(name.split("_")[1])
    own_submission(sid)
    return store.serve(f"snapshots/{name}", "image/jpeg")


@app.route("/lecturer/submissions/<int:sid>/recording/<int:attempt>")
@login_required("lecturer")
def recording_file(sid, attempt):
    """Stream the recording: 10-second pieces joined on the fly, with Range support so the video can seek."""
    own_submission(sid)
    chunks = get_db().execute("SELECT path, size FROM recording_chunks WHERE submission_id=? AND attempt=? ORDER BY id",
                              (sid, attempt)).fetchall()
    if not chunks:
        abort(404)
    total = sum(c["size"] for c in chunks)
    start, end, status = 0, total - 1, 200
    rng = request.headers.get("Range", "")
    if rng.startswith("bytes="):
        a_, _, b_ = rng[6:].split(",")[0].partition("-")
        if a_:
            start, end = int(a_), int(b_) if b_ else total - 1
        elif b_:
            start = max(0, total - int(b_))
        end = min(end, total - 1)
        if start > end:
            return Response(status=416, headers={"Content-Range": f"bytes */{total}"})
        status = 206

    def generate():
        pos = 0
        for c in chunks:
            c_start, c_end = pos, pos + c["size"] - 1
            pos += c["size"]
            if c_end < start or c_start > end:
                continue
            data = store.get(c["path"]) or b"\0" * c["size"]
            yield data[max(0, start - c_start): min(c["size"], end - c_start + 1)]

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1), "Cache-Control": "no-store"}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    return Response(generate(), status=status, mimetype="video/webm", headers=headers)


@app.route("/lecturer/submissions/<int:sid>/unban", methods=["POST"])
@login_required("lecturer")
def submission_unban(sid):
    sub = own_submission(sid)
    reset_for_retry(sid)
    log_event(g.user["id"], "proctor", f"Ban lifted for {sub['full_name']} ({sub['identifier']}). They can resume now.")
    flash(f"Ban lifted. {sub['full_name']} can continue the exam with a fresh timer and warnings reset.", "success")
    return redirect(request.referrer or url_for("exam_submissions", exam_id=sub["exam_id"]))


# ---------------------------------------------------------------- grading
@app.route("/lecturer/submissions/<int:sid>", methods=["GET", "POST"])
@login_required("lecturer")
def submission_grade(sid):
    db = get_db()
    sub = own_submission(sid)
    exam = own_exam(sub["exam_id"])
    rows = db.execute("SELECT q.*, a.selected_option, a.answer_text, a.awarded_marks, a.feedback FROM questions q "
                      "LEFT JOIN answers a ON a.question_id=q.id AND a.submission_id=? WHERE q.exam_id=? "
                      "ORDER BY q.qtype DESC, q.position, q.id", (sid, exam["id"])).fetchall()
    if request.method == "POST":
        if sub["status"] == "in_progress":
            flash("This student is still writing.", "warning")
            return redirect(url_for("submission_grade", sid=sid))
        if sub["status"] == "banned" or sub["objective_score"] is None:
            score_objectives(db, sid, exam["id"])
        theory_total = 0.0
        for q in rows:
            if q["qtype"] != "theory":
                continue
            try:
                m = float(request.form.get(f"marks_{q['id']}") or 0)
            except ValueError:
                m = 0
            m = max(0.0, min(m, q["marks"]))
            theory_total += m
            db.execute("INSERT INTO answers(submission_id,question_id,awarded_marks,feedback) VALUES(?,?,?,?) "
                       "ON CONFLICT(submission_id,question_id) DO UPDATE SET awarded_marks=excluded.awarded_marks, "
                       "feedback=excluded.feedback", (sid, q["id"], m, request.form.get(f"feedback_{q['id']}", "").strip()))
        db.execute("UPDATE submissions SET theory_score=?, status='graded', lecturer_comment=?, marked_by='lecturer' "
                   "WHERE id=?", (theory_total, request.form.get("comment", "").strip(), sid))
        db.commit()
        flash(f"Script for {sub['full_name']} graded and saved.", "success")
        nxt = db.execute("SELECT id FROM submissions WHERE exam_id=? AND status='submitted' ORDER BY submitted_at LIMIT 1",
                         (exam["id"],)).fetchone()
        if nxt and request.form.get("next"):
            return redirect(url_for("submission_grade", sid=nxt["id"]))
        return redirect(url_for("exam_submissions", exam_id=exam["id"]))
    violations = db.execute("SELECT * FROM violations WHERE submission_id=? ORDER BY id", (sid,)).fetchall()
    return render_template("lecturer/grade.html", sub=sub, exam=exam, rows=rows, totals=exam_totals(exam["id"]),
                           violations=violations, recordings=recordings_for(sid))


@app.route("/lecturer/submissions/<int:sid>/ai-suggest.json", methods=["POST"])
@login_required("lecturer")
def ai_suggest(sid):
    sub = own_submission(sid)
    items = theory_items(get_db(), sid, sub["exam_id"])
    res = ai.mark_theory(items, course_context(get_db(), sub["exam_id"]))
    return jsonify(results={str(k): {"marks": v[0], "feedback": v[1]} for k, v in res.items()})


# ---------------------------------------------------------------- reports & records
def exam_stats(db, exam_id):
    totals = exam_totals(exam_id, db)
    subs = db.execute("SELECT s.*, u.full_name, u.identifier FROM submissions s JOIN users u ON u.id=s.student_id "
                      "WHERE s.exam_id=? AND s.status IN ('graded','submitted','banned','marking')", (exam_id,)).fetchall()
    pcts = [((s["objective_score"] or 0) + (s["theory_score"] or 0)) / totals["total"] * 100 if totals["total"] else 0
            for s in subs]
    stats = {"count": len(subs), "avg": round(sum(pcts) / len(pcts), 1) if pcts else 0,
             "high": round(max(pcts), 1) if pcts else 0, "low": round(min(pcts), 1) if pcts else 0,
             "pass_rate": round(sum(p >= 40 for p in pcts) / len(pcts) * 100, 1) if pcts else 0,
             "banned": sum(1 for s in subs if s["times_banned"]),
             "dist": {L: sum(grade_letter(p) == L for p in pcts) for L in "ABCDEF"}}
    qstats = []
    for q in db.execute("SELECT * FROM questions WHERE exam_id=? ORDER BY qtype DESC, position", (exam_id,)).fetchall():
        r = db.execute("SELECT AVG(COALESCE(a.awarded_marks,0)) av, COUNT(a.id) n FROM answers a JOIN submissions s "
                       "ON s.id=a.submission_id WHERE a.question_id=? AND s.status!='in_progress'", (q["id"],)).fetchone()
        qstats.append({"id": q["id"], "qtype": q["qtype"], "text": q["text"],
                       "pct": round((r["av"] or 0) / q["marks"] * 100, 1) if q["marks"] else 0})
    ranked = sorted(zip(subs, pcts), key=lambda x: -x[1])
    return totals, stats, qstats, ranked


@app.route("/lecturer/exams/<int:exam_id>/report", methods=["GET", "POST"])
@login_required("lecturer")
def exam_report(exam_id):
    exam = own_exam(exam_id)
    db = get_db()
    totals, stats, qstats, ranked = exam_stats(db, exam_id)
    if request.method == "POST":
        weak = sorted(qstats, key=lambda q: q["pct"])[:3]
        db.execute("UPDATE exams SET ai_report=? WHERE id=?", (ai.class_report(exam["title"], stats, weak), exam_id))
        db.commit()
        return redirect(url_for("exam_report", exam_id=exam_id))
    return render_template("lecturer/report.html", exam=exam, totals=totals, stats=stats, qstats=qstats, ranked=ranked)


@app.route("/lecturer/records")
@login_required("lecturer")
def records():
    db = get_db()
    courses = db.execute("SELECT * FROM courses WHERE lecturer_id=?", (g.user["id"],)).fetchall()
    month = request.args.get("month") or datetime.now().strftime("%Y-%m")
    course_id = request.args.get("course", type=int)
    q = "SELECT * FROM exams WHERE lecturer_id=? AND substr(created_at,1,7)=?"
    params = [g.user["id"], month]
    if course_id:
        q += " AND course_id=?"
        params.append(course_id)
    exams = db.execute(q + " ORDER BY created_at", params).fetchall()
    students, totals = {}, {}
    for e in exams:
        totals[e["id"]] = exam_totals(e["id"])["total"]
        for s in db.execute("SELECT s.*, u.full_name, u.identifier FROM submissions s JOIN users u ON u.id=s.student_id "
                            "WHERE s.exam_id=?", (e["id"],)).fetchall():
            st = students.setdefault(s["identifier"], {"name": s["full_name"], "matric": s["identifier"], "scores": {}})
            st["scores"][e["id"]] = {"score": (s["objective_score"] or 0) + (s["theory_score"] or 0),
                                     "status": s["status"], "strikes": s["away_count"] + s["hand_count"],
                                     "banned": s["times_banned"]}
    rows = sorted(students.values(), key=lambda r: r["matric"])
    if request.args.get("format") == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Matric No", "Name"] + [f"{e['course_code']} {e['title']} (/{totals[e['id']]:g})" for e in exams]
                   + ["Total", "Out of", "Percent", "Grade"])
        for r in rows:
            got = sum(r["scores"].get(e["id"], {}).get("score", 0) for e in exams)
            out_of = sum(totals[e["id"]] for e in exams)
            pct = got / out_of * 100 if out_of else 0
            w.writerow([r["matric"], r["name"]] + [r["scores"].get(e["id"], {}).get("score", "ABS") for e in exams]
                       + [got, out_of, f"{pct:.1f}", grade_letter(pct)])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=records_{month}.csv"})
    return render_template("lecturer/records.html", courses=courses, exams=exams, rows=rows, totals=totals,
                           month=month, course_id=course_id)


@app.route("/lecturer/exams/<int:exam_id>/export.csv")
@login_required("lecturer")
def export_csv(exam_id):
    exam = own_exam(exam_id)
    totals = exam_totals(exam_id)
    subs = get_db().execute("SELECT s.*, u.full_name, u.identifier, u.department FROM submissions s JOIN users u ON "
                            "u.id=s.student_id WHERE s.exam_id=? AND s.status!='in_progress' ORDER BY u.identifier",
                            (exam_id,)).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Matric No", "Name", "Department", "Objective", "Theory", "Total", "Out of", "Percent", "Grade",
                "Status", "Away warnings", "Hand warnings", "Times banned", "Marked by", "Submitted At"])
    for s in subs:
        tot = (s["objective_score"] or 0) + (s["theory_score"] or 0)
        pct = (tot / totals["total"] * 100) if totals["total"] else 0
        w.writerow([s["identifier"], s["full_name"], s["department"], s["objective_score"], s["theory_score"], tot,
                    totals["total"], f"{pct:.1f}", grade_letter(pct), s["status"], s["away_count"], s["hand_count"],
                    s["times_banned"], s["marked_by"] or "", s["submitted_at"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={exam['course_code'].replace(' ', '_')}_results.csv"})


# ================================================================ AI Professor voice
def speak_cached(text, con=None):
    """Return (key, engine) for server-made audio of `text`, creating and caching it if needed. None = use browser voice."""
    c = con or get_db()
    key = tts.cache_key(text)
    row = c.execute("SELECT engine FROM tts_cache WHERE key=?", (key,)).fetchone()
    if row:
        return key, row["engine"]
    res = tts.synthesize(text)
    if not res:
        return None
    audio, mime, ext, engine = res
    path = f"tts/{key}.{ext}"
    store.put(path, audio, mime)
    c.execute("INSERT INTO tts_cache(key,path,mime,engine,created_at) VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING",
              (key, path, mime, engine, now()))
    c.commit()
    return key, engine


def prewarm_voice(segs, con):
    """Make the audio for every part of a lecture in advance so it plays instantly."""
    for sgm in segs:
        try:
            if not speak_cached(sgm.get("speech", ""), con):
                return          # no server voice on this machine; the browser will speak
        except Exception as e:
            print("voice prewarm failed:", e)
            return


TEACHER_DIR = os.path.join(BASE_DIR, "static", "teacher")


@app.route("/teacher-avatar.json")
def teacher_avatar():
    """Which 3D teacher model (and Mixamo animations) are in static/teacher/."""
    files = {f.lower(): f for f in (os.listdir(TEACHER_DIR) if os.path.isdir(TEACHER_DIR) else [])}
    pick = lambda names: next((files[n] for n in names if n in files), None)
    exts = (".glb", ".gltf", ".fbx")
    model = pick([f"teacher{e}" for e in exts] + ["scene.gltf", "scene.glb"])
    anims = {k: f for k in ("idle", "talking", "writing") if (f := pick([f"{k}{e}" for e in (".fbx", ".glb")]))}
    url = lambda f: url_for("static", filename=f"teacher/{f}")
    return jsonify(model=url(model) if model else None, animations={k: url(f) for k, f in anims.items()},
                   credit=os.environ.get("TEACHER_CREDIT", ""),
                   turn=float(os.environ.get("TEACHER_TURN", "12") or 12), zoom=float(os.environ.get("TEACHER_ZOOM", "1") or 1),
                   gesture=os.environ.get("TEACHER_GESTURE", "1") != "0")


@app.route("/speak", methods=["POST"])
@login_required()
def speak():
    text = ((request.get_json(silent=True) or {}).get("text") or "")[:5000]
    try:
        r = speak_cached(text)
    except Exception as e:
        print("speak failed:", e)
        r = None
    if not r:
        return jsonify(url=None, engine="browser")
    return jsonify(url=url_for("tts_audio", key=r[0]), engine=r[1])


@app.route("/tts/<key>")
@login_required()
def tts_audio(key):
    row = get_db().execute("SELECT path, mime FROM tts_cache WHERE key=?", (secure_filename(key),)).fetchone()
    data = store.get(row["path"]) if row else None
    if not data:
        abort(404)
    return send_file(io.BytesIO(data), mimetype=row["mime"], conditional=True, max_age=86400)


# ================================================================ STUDENT
@app.route("/student")
@login_required("student")
def student_dashboard():
    db = get_db()
    uid = g.user["id"]
    subs = db.execute("SELECT s.*, e.title, e.course_code, e.results_released, e.status exam_status, u.full_name lecturer "
                      "FROM submissions s JOIN exams e ON e.id=s.exam_id JOIN users u ON u.id=e.lecturer_id "
                      "WHERE s.student_id=? ORDER BY s.started_at DESC", (uid,)).fetchall()
    enriched = [{**dict(s), "max": exam_totals(s["exam_id"])["total"]} for s in subs]
    courses = db.execute("SELECT c.*, u.full_name lecturer FROM enrollments en JOIN courses c ON c.id=en.course_id "
                         "JOIN users u ON u.id=c.lecturer_id WHERE en.student_id=? ORDER BY c.code", (uid,)).fetchall()
    lectures = db.execute("SELECT l.id,l.title,l.status,l.created_at,c.code,c.title course_title,lu.full_name lecturer,"
                          "v.progress FROM lectures l JOIN courses c ON c.id=l.course_id JOIN users lu ON lu.id=c.lecturer_id "
                          "JOIN enrollments en ON en.course_id=c.id AND en.student_id=? LEFT JOIN lecture_views v ON "
                          "v.lecture_id=l.id AND v.student_id=? WHERE l.status='ready' AND (l.audience='all' OR EXISTS(SELECT 1 FROM lecture_recipients r WHERE r.lecture_id=l.id AND r.student_id=?)) "
                          "ORDER BY l.created_at DESC", (uid, uid, uid)).fetchall()
    my_notes = db.execute("SELECT id,title,original_name,status,progress,created_at FROM personal_lectures WHERE student_id=? "
                          "ORDER BY created_at DESC", (uid,)).fetchall()
    open_exams = db.execute("SELECT e.* FROM exams e JOIN enrollments en ON en.course_id=e.course_id AND en.student_id=? "
                            "WHERE e.status='open' AND e.id NOT IN (SELECT exam_id FROM submissions WHERE student_id=? "
                            "AND status IN ('submitted','graded','marking')) ORDER BY e.created_at DESC", (uid, uid)).fetchall()
    return render_template("student/dashboard.html", subs=enriched, courses=courses, lectures=lectures, open_exams=open_exams,
                           my_notes=my_notes)


@app.route("/student/courses/join", methods=["POST"])
@login_required("student")
def course_join():
    db = get_db()
    c = db.execute("SELECT * FROM courses WHERE join_code=?", (request.form.get("join_code", "").strip().upper(),)).fetchone()
    if not c:
        flash("No course matches that class code.", "danger")
    else:
        cur = db.execute("INSERT INTO enrollments(course_id,student_id,joined_at) VALUES(?,?,?) ON CONFLICT DO NOTHING", (c["id"], g.user["id"], now()))
        db.commit()
        if cur.rowcount:
            log_event(c["lecturer_id"], "course", f"{g.user['full_name']} ({g.user['identifier']}) joined {c['code']}.")
        flash(f"You have joined {c['code']} — {c['title']}.", "success")
    return redirect(url_for("student_dashboard"))


# ---------------------------------------------------------------- AI lectures
def student_lecture(lid):
    l = get_db().execute("SELECT l.*, c.code, c.title course_title, c.lecturer_id, u.full_name lecturer FROM lectures l "
                         "JOIN courses c ON c.id=l.course_id JOIN users u ON u.id=c.lecturer_id JOIN enrollments en "
                         "ON en.course_id=c.id AND en.student_id=? WHERE l.id=? AND (l.audience='all' OR EXISTS(SELECT 1 FROM lecture_recipients r WHERE r.lecture_id=l.id AND r.student_id=?))", (g.user["id"], lid, g.user["id"])).fetchone()
    if not l or l["status"] != "ready":
        abort(404)
    return l


@app.route("/student/lectures/<int:lid>")
@login_required("student")
def lecture_view(lid):
    l = student_lecture(lid)
    db = get_db()
    existing = db.execute("SELECT * FROM lecture_views WHERE lecture_id=? AND student_id=?", (lid, g.user["id"])).fetchone()
    if not existing:
        db.execute("INSERT INTO lecture_views(lecture_id,student_id,started_at,last_seen) VALUES(?,?,?,?)",
                   (lid, g.user["id"], now(), now()))
        db.commit()
        log_event(l["lecturer_id"], "lecture", f"{g.user['full_name']} started the lecture '{l['title']}'.",
                  f"/lecturer/lectures/{lid}")
    qa = db.execute("SELECT * FROM lecture_questions WHERE lecture_id=? AND student_id=? ORDER BY id",
                    (lid, g.user["id"])).fetchall()
    return render_template("student/lecture.html", l=l, segs=with_diagrams(lecture_segments(l)), qa=qa, glossary=ai.glossary(l["text"] or ""),
                           flags=[r["part"] for r in db.execute("SELECT part FROM lecture_flags WHERE lecture_id=? AND student_id=?",
                                                                 (lid, g.user["id"]))],
                           resume=existing["progress"] if existing else 0,
                           subtitle=f"{l['code']} — {l['course_title']} · Lecture notes by {l['lecturer']} · Taught by AI Professor",
                           urls={"beat": url_for("lecture_heartbeat", lid=lid), "ask": url_for("lecture_ask", lid=lid),
                                 "check": url_for("lecture_check", lid=lid), "simpler": url_for("lecture_simpler", lid=lid),
                                 "notes": url_for("lecture_notes", lid=lid), "file": url_for("lecture_download", lid=lid),
                                 "flag": url_for("lecture_flag", lid=lid)})


def send_lecture_file(l):
    data = store.get(l["stored_name"]) if l["stored_name"] else None
    if data is None and l["stored_name"]:                          # files saved by older versions
        old = os.path.join(DATA_DIR, "lectures", os.path.basename(l["stored_name"]))
        if os.path.exists(old):
            with open(old, "rb") as f:
                data = f.read()
    if data is None:
        abort(404)
    name = l["original_name"] or "lecture"
    return send_file(io.BytesIO(data), as_attachment=True, download_name=name,
                     mimetype=mimetypes.guess_type(name)[0] or "application/octet-stream")


@app.route("/student/lectures/<int:lid>/download")
@login_required("student")
def lecture_download(lid):
    l = student_lecture(lid)
    db = get_db()
    log_event(l["lecturer_id"], "lecture", f"{g.user['full_name']} downloaded '{l['title']}' for offline study.",
              f"/lecturer/lectures/{lid}")
    return send_lecture_file(l)


@app.route("/lecturer/lectures/<int:lid>/download")
@login_required("lecturer")
def lecture_download_admin(lid):
    l = get_db().execute("SELECT l.* FROM lectures l JOIN courses c ON c.id=l.course_id WHERE l.id=? AND c.lecturer_id=?",
                         (lid, g.user["id"])).fetchone()
    if not l:
        abort(404)
    return send_lecture_file(l)


def with_diagrams(segs):
    """Each part gets its diagram drawn in Python (SVG) plus cues: when the teacher says each box's words."""
    out = []
    for sg in segs:
        sg = dict(sg)
        try:
            d = diagrams.render(sg.get("visual"), sg.get("speech", ""))
            sg["svg"], sg["cues"] = d["svg"], d["cues"]
        except Exception as e:
            print("diagram failed:", e)
            sg["svg"], sg["cues"] = "", []
        out.append(sg)
    return out


def respond(lecture_title, text, question):
    """Answer a student's question. Word meanings come from the offline dictionary (and the notes);
    other questions from AI Professor. Every answer comes with a diagram drawn in Python."""
    vocab = ai.lecture_vocab(text)
    understood, changes = dictionary.correct_text(question, vocab)
    word = ai.word_question(understood)
    if word:
        nd = ai.notes_definition(text, word)
        entry = dictionary.lookup_any(word, vocab)
        if entry or nd:
            say = dictionary.explain(entry, nd)
            corrected = [m for h, m in changes if m.lower() == (entry or {}).get("word", "").lower()]
            if entry and (entry.get("did_you_mean") or corrected):
                say = f"I think you mean {entry['word']}. " + say
            vis = dictionary.visual(entry, nd)
            if not vis["title"]:
                vis["title"] = word.capitalize()
            d = diagrams.render(vis, say)
            return {"answer": say, "svg": d["svg"], "cues": d["cues"], "understood": understood, "changes": changes,
                    "kind": "word", "word": (entry or {}).get("word", word)}
    ans = ai.answer_question(lecture_title, text, understood)
    d = diagrams.render(ai.answer_visual(understood, ans), ans)
    return {"answer": ans, "svg": d["svg"], "cues": d["cues"], "understood": understood, "changes": changes, "kind": "answer"}


def _context_text():
    """Lecture/notes text for the current request (?lid= or ?pid=), so answers and voice use the lecture's words."""
    lid, pid = request.values.get("lid", type=int), request.values.get("pid", type=int)
    if lid and g.user["role"] == "student":
        try:
            l = student_lecture(lid)
            return l["title"], l["text"] or ""
        except Exception:
            pass
    if pid:
        p = get_db().execute("SELECT * FROM personal_lectures WHERE id=? AND student_id=?", (pid, g.user["id"])).fetchone()
        if p:
            return p["title"], p["text"] or ""
    return "", ""


@app.route("/dictionary")
@login_required()
def dictionary_lookup():
    """Explain any English word (offline). ?word=...&lid= or &pid= to include the lecture's own definition."""
    word = (request.args.get("word") or "").strip()[:60]
    if not word:
        return jsonify(ok=False, error="Type or say a word."), 400
    # The box is for a word, but students type questions too: "what is a noun", "meaning of osmosis", "define catalyst"
    word = ai.word_question(word) or re.sub(r"[?.!]+$", "", word)
    word = re.sub(r"^(please |can you |could you )?(tell me about|explain|describe|meaning of|define)\s+", "", word.strip(), flags=re.I)
    word = re.sub(r"^(a|an|the)\s+", "", word.strip(), flags=re.I) or word
    title, text = _context_text()
    r = respond(title, text, f'what does "{word}" mean')       # quoted, so "word processor" keeps its first word
    if r.get("kind") != "word":
        say = f"Sorry, I could not find the word {word} in my dictionary. Check the spelling, or ask me a question about it."
        return jsonify(ok=False, answer=say, svg="", cues=[], understood=r["understood"])
    return jsonify(ok=True, **r)


@app.route("/stt", methods=["POST"])
@login_required()
def stt():
    """Offline speech-to-text for a spoken question, then accent correction using the lecture's words."""
    if not speech.available():
        return jsonify(ok=False, engine=None, error=speech.status()), 503
    audio = request.files.get("audio")
    data = audio.read() if audio else request.get_data()
    if not data or len(data) < 2000:
        return jsonify(ok=False, error="I did not hear anything. Hold the mic button and speak clearly."), 400
    title, text = _context_text()
    vocab = ai.lecture_vocab(text)
    try:
        raw, eng = speech.transcribe(data, hints=vocab[:60])
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify(ok=False, error=f"Voice recognition failed ({type(e).__name__}: {str(e)[:160]}). "
                                       "Type your question for now, and send this message to your admin."), 500
    fixed, changes = dictionary.correct_text(raw, vocab)
    return jsonify(ok=bool(fixed), raw=raw, text=fixed, changes=changes, engine=eng)


@app.route("/student/lectures/<int:lid>/flag", methods=["POST"])
@login_required("student")
def lecture_flag(lid):
    """Student marks a part as difficult (or un-marks it). The lecturer sees which parts the class found hard."""
    l = student_lecture(lid)
    d = request.get_json(silent=True) or {}
    part, on = int(d.get("part", 0)), bool(d.get("on"))
    db = get_db()
    if on:
        db.execute("INSERT INTO lecture_flags(lecture_id,student_id,part,created_at) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
                   (lid, g.user["id"], part, now()))
    else:
        db.execute("DELETE FROM lecture_flags WHERE lecture_id=? AND student_id=? AND part=?", (lid, g.user["id"], part))
    db.commit()
    return jsonify(ok=True)


@app.route("/student/lectures/<int:lid>/check", methods=["POST"])
@login_required("student")
def lecture_check(lid):
    student_lecture(lid)
    d = request.get_json(silent=True) or {}
    db = get_db()
    db.execute("INSERT INTO lecture_checks(lecture_id,student_id,part,correct,choice,created_at) VALUES(?,?,?,?,?,?) "
               "ON CONFLICT(lecture_id,student_id,part) DO UPDATE SET correct=excluded.correct, choice=excluded.choice, "
               "created_at=excluded.created_at", (lid, g.user["id"], int(d.get("part", 0)), 1 if d.get("correct") else 0,
                                                  str(d.get("choice", ""))[:2], now()))
    db.commit()
    return jsonify(ok=True)


def _simpler_reply(title, text, segs):
    d = request.get_json(silent=True) or {}
    i = max(0, min(int(d.get("part", 0)), len(segs) - 1))
    return segs[i], ai.simpler(title, segs[i].get("title", ""), segs[i].get("speech", ""), text)


@app.route("/student/lectures/<int:lid>/simpler", methods=["POST"])
@login_required("student")
def lecture_simpler(lid):
    l = student_lecture(lid)
    seg, ans = _simpler_reply(l["title"], l["text"] or "", lecture_segments(l))
    db = get_db()
    db.execute("INSERT INTO lecture_questions(lecture_id,student_id,question,answer,created_at) VALUES(?,?,?,?,?)",
               (lid, g.user["id"], f"(Explain simpler) {seg.get('title', '')}", ans, now()))
    db.commit()
    log_event(l["lecturer_id"], "question", f"{g.user['full_name']} asked AI Professor to explain '{seg.get('title', '')}' "
              f"more simply in '{l['title']}'.", f"/lecturer/lectures/{lid}")
    d = diagrams.render(seg.get("visual") or ai.answer_visual(seg.get("title", ""), ans, seg.get("title")), ans)
    return jsonify(ok=True, answer=ans, svg=d["svg"], cues=d["cues"])


@app.route("/student/lectures/<int:lid>/notes")
@login_required("student")
def lecture_notes(lid):
    l = student_lecture(lid)
    return render_template("student/notes.html", title=l["title"], subtitle=f"{l['code']} — {l['course_title']} · {l['lecturer']}",
                           segs=with_diagrams(lecture_segments(l)), glossary=ai.glossary(l["text"] or ""))


@app.route("/student/lectures/<int:lid>/heartbeat", methods=["POST"])
@login_required("student")
def lecture_heartbeat(lid):
    student_lecture(lid)
    p = int((request.get_json(silent=True) or {}).get("progress", 0))
    db = get_db()
    p = max(0, min(100, p))
    db.execute("UPDATE lecture_views SET last_seen=?, progress=CASE WHEN progress>? THEN progress ELSE ? END "
               "WHERE lecture_id=? AND student_id=?", (now(), p, p, lid, g.user["id"]))
    db.commit()
    return jsonify(ok=True)


@app.route("/student/lectures/<int:lid>/ask", methods=["POST"])
@login_required("student")
def lecture_ask(lid):
    l = student_lecture(lid)
    q = ((request.get_json(silent=True) or {}).get("question") or "").strip()[:1000]
    if not q:
        return jsonify(ok=False), 400
    r = respond(l["title"], l["text"] or "", q)
    ans = r["answer"]
    db = get_db()
    db.execute("INSERT INTO lecture_questions(lecture_id,student_id,question,answer,created_at) VALUES(?,?,?,?,?)",
               (lid, g.user["id"], r["understood"], ans, now()))
    db.commit()
    log_event(l["lecturer_id"], "question", f"{g.user['full_name']} asked AI Professor in '{l['title']}': “{q[:120]}”",
              f"/lecturer/lectures/{lid}")
    return jsonify(ok=True, **r)


# ---------------------------------------------------------------- student's own notes -> AI Professor
def prepare_personal(pid):
    con = connect()
    try:
        p = con.execute("SELECT * FROM personal_lectures WHERE id=?", (pid,)).fetchone()
        segs = ai.build_lecture(p["title"], p["text"] or "")
        prewarm_voice(segs, con)
        con.execute("UPDATE personal_lectures SET script=?, status='ready' WHERE id=?", (json.dumps(segs), pid))
    except Exception as e:
        print("prepare_personal error:", e)
        con.execute("UPDATE personal_lectures SET status='failed' WHERE id=?", (pid,))
    con.commit()
    con.close()


def own_note(pid):
    p = get_db().execute("SELECT * FROM personal_lectures WHERE id=? AND student_id=?", (pid, g.user["id"])).fetchone()
    if not p:
        abort(404)
    return p


@app.route("/student/my-notes", methods=["POST"])
@login_required("student")
def note_upload():
    try:
        stored, text = read_upload(request.files.get("file"))
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("student_dashboard"))
    f = request.files["file"]
    title = request.form.get("title", "").strip() or os.path.splitext(f.filename)[0]
    db = get_db()
    pid = db.insert("INSERT INTO personal_lectures(student_id,title,original_name,stored_name,text,created_at) "
                    "VALUES(?,?,?,?,?,?)", (g.user["id"], title, f.filename, stored, text, now()))
    db.commit()
    run_job(prepare_personal, pid)
    flash("Uploaded. AI Professor is preparing your lecture — it will be ready in about a minute.", "success")
    return redirect(url_for("student_dashboard") + "#mynotes")


@app.route("/student/my-notes/<int:pid>")
@login_required("student")
def note_view(pid):
    p = own_note(pid)
    if p["status"] != "ready":
        flash("AI Professor is still preparing this lecture. Try again shortly.", "info")
        return redirect(url_for("student_dashboard") + "#mynotes")
    qa = get_db().execute("SELECT * FROM personal_questions WHERE plecture_id=? ORDER BY id", (pid,)).fetchall()
    return render_template("student/lecture.html", l=p, segs=with_diagrams(lecture_segments(p, "personal_lectures")), qa=qa,
                           glossary=ai.glossary(p["text"] or ""), flags=[],
                           resume=p["progress"], subtitle=f"My notes · {p['original_name']} · Taught by AI Professor",
                           urls={"beat": url_for("note_heartbeat", pid=pid), "ask": url_for("note_ask", pid=pid),
                                 "check": None, "simpler": url_for("note_simpler", pid=pid),
                                 "notes": url_for("note_notes", pid=pid), "file": None, "flag": None})


@app.route("/student/my-notes/<int:pid>/simpler", methods=["POST"])
@login_required("student")
def note_simpler(pid):
    p = own_note(pid)
    seg, ans = _simpler_reply(p["title"], p["text"] or "", lecture_segments(p, "personal_lectures"))
    d = diagrams.render(seg.get("visual") or ai.answer_visual(seg.get("title", ""), ans, seg.get("title")), ans)
    return jsonify(ok=True, answer=ans, svg=d["svg"], cues=d["cues"])


@app.route("/student/my-notes/<int:pid>/notes")
@login_required("student")
def note_notes(pid):
    p = own_note(pid)
    if p["status"] != "ready":
        abort(404)
    return render_template("student/notes.html", title=p["title"], subtitle=f"My notes · {p['original_name']}",
                           segs=with_diagrams(lecture_segments(p, "personal_lectures")), glossary=ai.glossary(p["text"] or ""))


@app.route("/student/my-notes/<int:pid>/heartbeat", methods=["POST"])
@login_required("student")
def note_heartbeat(pid):
    own_note(pid)
    p = int((request.get_json(silent=True) or {}).get("progress", 0))
    db = get_db()
    p = max(0, min(100, p))
    db.execute("UPDATE personal_lectures SET progress=CASE WHEN progress>? THEN progress ELSE ? END WHERE id=?", (p, p, pid))
    db.commit()
    return jsonify(ok=True)


@app.route("/student/my-notes/<int:pid>/ask", methods=["POST"])
@login_required("student")
def note_ask(pid):
    p = own_note(pid)
    q = ((request.get_json(silent=True) or {}).get("question") or "").strip()[:1000]
    if not q:
        return jsonify(ok=False), 400
    r = respond(p["title"], p["text"] or "", q)
    ans = r["answer"]
    db = get_db()
    db.execute("INSERT INTO personal_questions(plecture_id,question,answer,created_at) VALUES(?,?,?,?)",
               (pid, r["understood"], ans, now()))
    db.commit()
    return jsonify(ok=True, **r)


@app.route("/student/my-notes/<int:pid>/delete", methods=["POST"])
@login_required("student")
def note_delete(pid):
    own_note(pid)
    db = get_db()
    db.execute("DELETE FROM personal_lectures WHERE id=?", (pid,))
    db.commit()
    flash("Removed.", "info")
    return redirect(url_for("student_dashboard") + "#mynotes")


# ---------------------------------------------------------------- exams
@app.route("/student/join", methods=["POST"])
@login_required("student")
def student_join():
    code = request.form.get("access_code", "").strip().upper()
    exam = get_db().execute("SELECT * FROM exams WHERE access_code=?", (code,)).fetchone()
    if not exam:
        flash("No exam matches that access code.", "danger")
        return redirect(url_for("student_dashboard"))
    return redirect(url_for("exam_lobby", exam_id=exam["id"], code=code))


def ban_expired(sub):
    return sub["banned_until"] and date.today().isoformat() >= sub["banned_until"]


def reset_for_retry(sid, con=None):
    c = con or get_db()
    c.execute("UPDATE submissions SET status='in_progress', started_at=?, submitted_at=NULL, away_count=0, hand_count=0, "
              "banned_until=NULL, attempt=attempt+1, objective_score=0 WHERE id=?", (now(), sid))
    c.commit()


@app.route("/student/exam/<int:exam_id>/lobby")
@login_required("student")
def exam_lobby(exam_id):
    db = get_db()
    exam = db.execute("SELECT e.*, u.full_name lecturer FROM exams e JOIN users u ON u.id=e.lecturer_id WHERE e.id=?",
                      (exam_id,)).fetchone()
    if not exam or request.args.get("code", "").upper() != exam["access_code"]:
        abort(404)
    sub = db.execute("SELECT * FROM submissions WHERE exam_id=? AND student_id=?", (exam_id, g.user["id"])).fetchone()
    return render_template("student/lobby.html", exam=exam, sub=sub, totals=exam_totals(exam_id),
                           ban_over=bool(sub and sub["status"] == "banned" and ban_expired(sub)))


@app.route("/student/exam/<int:exam_id>/start", methods=["POST"])
@login_required("student")
def exam_start(exam_id):
    db = get_db()
    exam = db.execute("SELECT * FROM exams WHERE id=?", (exam_id,)).fetchone()
    if not exam or request.form.get("code", "").upper() != exam["access_code"]:
        abort(404)
    if exam["status"] != "open":
        flash("This exam is not open at the moment.", "warning")
        return redirect(url_for("student_dashboard"))
    sub = db.execute("SELECT * FROM submissions WHERE exam_id=? AND student_id=?", (exam_id, g.user["id"])).fetchone()
    if sub and sub["status"] == "banned":
        if not ban_expired(sub):
            flash("You are banned from this exam for today. Try again tomorrow or contact your lecturer.", "danger")
            return redirect(url_for("student_dashboard"))
        reset_for_retry(sub["id"])
    elif sub and sub["status"] != "in_progress":
        flash("You have already submitted this exam.", "info")
        return redirect(url_for("student_dashboard"))
    if not sub:
        db.execute("INSERT INTO submissions(exam_id,student_id,started_at,last_seen) VALUES(?,?,?,?)",
                   (exam_id, g.user["id"], now(), now()))
        db.commit()
    log_event(exam["lecturer_id"], "exam", f"{g.user['full_name']} ({g.user['identifier']}) started {exam['course_code']} — {exam['title']}.",
              f"/lecturer/exams/{exam_id}/live")
    return redirect(url_for("exam_take", exam_id=exam_id))


def active_submission(exam_id):
    db = get_db()
    exam = db.execute("SELECT e.*, u.full_name lecturer FROM exams e JOIN users u ON u.id=e.lecturer_id WHERE e.id=?",
                      (exam_id,)).fetchone()
    sub = db.execute("SELECT * FROM submissions WHERE exam_id=? AND student_id=?", (exam_id, g.user["id"])).fetchone()
    if not exam or not sub:
        abort(404)
    return exam, sub


def deadline(exam, sub):
    return parse_dt(sub["started_at"]) + timedelta(minutes=exam["duration_minutes"])


def save_answers(sub_id, form):
    db = get_db()
    valid = {r["id"] for r in db.execute("SELECT q.id FROM questions q JOIN submissions s ON s.exam_id=q.exam_id "
                                         "WHERE s.id=?", (sub_id,))}
    for key, val in form.items():
        if key.startswith("obj_") and int(key[4:]) in valid:
            db.execute("INSERT INTO answers(submission_id,question_id,selected_option) VALUES(?,?,?) ON CONFLICT"
                       "(submission_id,question_id) DO UPDATE SET selected_option=excluded.selected_option",
                       (sub_id, int(key[4:]), val if val in ("A", "B", "C", "D") else None))
        elif key.startswith("th_") and int(key[3:]) in valid:
            db.execute("INSERT INTO answers(submission_id,question_id,answer_text) VALUES(?,?,?) ON CONFLICT"
                       "(submission_id,question_id) DO UPDATE SET answer_text=excluded.answer_text",
                       (sub_id, int(key[3:]), val[:20000]))
    db.commit()


def score_objectives(con, sub_id, exam_id):
    score = 0.0
    for r in con.execute("SELECT q.id, q.marks, q.correct_option, a.selected_option FROM questions q LEFT JOIN answers a "
                         "ON a.question_id=q.id AND a.submission_id=? WHERE q.exam_id=? AND q.qtype='objective'",
                         (sub_id, exam_id)).fetchall():
        awarded = r["marks"] if r["selected_option"] and r["selected_option"] == r["correct_option"] else 0
        score += awarded
        con.execute("INSERT INTO answers(submission_id,question_id,awarded_marks) VALUES(?,?,?) ON CONFLICT"
                    "(submission_id,question_id) DO UPDATE SET awarded_marks=excluded.awarded_marks", (sub_id, r["id"], awarded))
    con.execute("UPDATE submissions SET objective_score=? WHERE id=?", (score, sub_id))
    con.commit()
    return score


def theory_items(con, sub_id, exam_id):
    return [{"id": r["id"], "text": r["text"], "marks": r["marks"], "guide": r["marking_guide"], "answer": r["answer_text"]}
            for r in con.execute("SELECT q.*, a.answer_text FROM questions q LEFT JOIN answers a ON a.question_id=q.id "
                                 "AND a.submission_id=? WHERE q.exam_id=? AND q.qtype='theory'", (sub_id, exam_id))]


def course_context(con, exam_id):
    rows = con.execute("SELECT l.text FROM lectures l JOIN exams e ON e.course_id=l.course_id WHERE e.id=?", (exam_id,))
    return "\n\n".join(r["text"] or "" for r in rows)


def ai_mark_submission(sub_id):
    con = connect()
    try:
        sub = con.execute("SELECT s.*, u.full_name, u.identifier, e.lecturer_id, e.course_code, e.title FROM submissions s "
                          "JOIN users u ON u.id=s.student_id JOIN exams e ON e.id=s.exam_id WHERE s.id=?", (sub_id,)).fetchone()
        res = ai.mark_theory(theory_items(con, sub_id, sub["exam_id"]), course_context(con, sub["exam_id"]))
        total = 0.0
        for qid, (m, fb) in res.items():
            total += m
            con.execute("INSERT INTO answers(submission_id,question_id,awarded_marks,feedback) VALUES(?,?,?,?) ON CONFLICT"
                        "(submission_id,question_id) DO UPDATE SET awarded_marks=excluded.awarded_marks, feedback=excluded.feedback",
                        (sub_id, qid, m, fb))
        con.execute("UPDATE submissions SET theory_score=?, status='graded', marked_by='ai', lecturer_comment="
                    "COALESCE(lecturer_comment,'Marked by AI Professor.') WHERE id=?", (total, sub_id))
        con.commit()
        tot = exam_totals(sub["exam_id"], con)
        got = (sub["objective_score"] or 0) + total
        log_event(sub["lecturer_id"], "ai", f"AI Professor marked {sub['full_name']} ({sub['identifier']}) in "
                  f"{sub['course_code']}: {got:g}/{tot['total']:g}.", f"/lecturer/submissions/{sub_id}", con)
    except Exception as e:
        print("ai_mark error:", e)
        con.execute("UPDATE submissions SET status='submitted' WHERE id=?", (sub_id,))
        con.commit()
    finally:
        con.close()


def finalize_submission(sub_id):
    db = get_db()
    sub = db.execute("SELECT s.*, e.ai_marking, e.lecturer_id, e.course_code, u.full_name FROM submissions s "
                     "JOIN exams e ON e.id=s.exam_id JOIN users u ON u.id=s.student_id WHERE s.id=?", (sub_id,)).fetchone()
    if sub["status"] != "in_progress":
        return
    score_objectives(db, sub_id, sub["exam_id"])
    has_theory = exam_totals(sub["exam_id"])["nth"]
    status = "graded" if not has_theory else ("marking" if sub["ai_marking"] else "submitted")
    db.execute("UPDATE submissions SET status=?, submitted_at=?, marked_by=? WHERE id=?",
               (status, now(), "auto" if not has_theory else None, sub_id))
    db.commit()
    log_event(sub["lecturer_id"], "submit", f"{sub['full_name']} submitted {sub['course_code']}.",
              f"/lecturer/submissions/{sub_id}")
    if status == "marking":
        run_job(ai_mark_submission, sub_id)


@app.route("/student/exam/<int:exam_id>/take", methods=["GET", "POST"])
@login_required("student")
def exam_take(exam_id):
    exam, sub = active_submission(exam_id)
    if sub["status"] == "banned":
        return redirect(url_for("exam_banned", exam_id=exam_id))
    if sub["status"] != "in_progress":
        return redirect(url_for("student_dashboard"))
    end = deadline(exam, sub)
    expired = datetime.now() > end + timedelta(seconds=30) or exam["status"] == "closed"
    if request.method == "POST" or expired:
        if not expired:
            save_answers(sub["id"], request.form)
        finalize_submission(sub["id"])
        flash(f"Your script has been submitted to {exam['lecturer']}."
              + (" Time had elapsed, so it was submitted automatically." if expired else ""), "success")
        return redirect(url_for("submission_receipt", exam_id=exam_id))
    db = get_db()
    qs = db.execute("SELECT * FROM questions WHERE exam_id=? ORDER BY qtype DESC, position, id", (exam_id,)).fetchall()
    if exam["shuffle_questions"]:
        qs = list(qs)
        random.Random(f"{exam_id}-{g.user['id']}").shuffle(qs)
    saved = {r["question_id"]: r for r in db.execute("SELECT * FROM answers WHERE submission_id=?", (sub["id"],)).fetchall()}
    return render_template("student/take.html", exam=exam, sub=sub,
                           objective=[q for q in qs if q["qtype"] == "objective"],
                           theory=[q for q in qs if q["qtype"] == "theory"], saved=saved,
                           seconds_left=max(0, int((end - datetime.now()).total_seconds())), max_strikes=MAX_STRIKES)


def _writable(exam, sub):
    return sub["status"] == "in_progress" and datetime.now() <= deadline(exam, sub) + timedelta(seconds=30) \
        and exam["status"] == "open"


@app.route("/student/exam/<int:exam_id>/autosave", methods=["POST"])
@login_required("student")
def exam_autosave(exam_id):
    exam, sub = active_submission(exam_id)
    if not _writable(exam, sub):
        return jsonify(ok=False, reason=sub["status"]), 409
    save_answers(sub["id"], request.form)
    return jsonify(ok=True, saved_at=datetime.now().strftime("%H:%M:%S"))


def _save_jpeg(data_url, key):
    if not data_url or "," not in data_url:
        return False
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1])
    except Exception:
        return False
    if len(raw) > 2_000_000:
        return False
    try:
        store.put(key, raw, "image/jpeg")
    except Exception as e:
        print("snapshot upload failed:", e)
        return False
    return True


@app.route("/student/exam/<int:exam_id>/frame", methods=["POST"])
@login_required("student")
def exam_frame(exam_id):
    exam, sub = active_submission(exam_id)
    if sub["status"] != "in_progress":
        return jsonify(ok=False, reason=sub["status"]), 409
    ok = _save_jpeg((request.get_json(silent=True) or {}).get("image"), f"live/sub_{sub['id']}.jpg")
    db = get_db()
    db.execute("UPDATE submissions SET last_seen=?" + (", frame_at=?" if ok else "") + " WHERE id=?",
               (now(), now(), sub["id"]) if ok else (now(), sub["id"]))
    db.commit()
    return jsonify(ok=True)


@app.route("/student/exam/<int:exam_id>/violation", methods=["POST"])
@login_required("student")
def exam_violation(exam_id):
    exam, sub = active_submission(exam_id)
    if sub["status"] != "in_progress":
        return jsonify(ok=False, banned=sub["status"] == "banned", reason=sub["status"]), 409
    data = request.get_json(silent=True) or {}
    vtype = data.get("type")
    if vtype not in ("away", "hand"):
        return jsonify(ok=False), 400
    db = get_db()
    last = db.execute("SELECT created_at FROM violations WHERE submission_id=? AND vtype=? ORDER BY id DESC LIMIT 1",
                      (sub["id"], vtype)).fetchone()
    col = "away_count" if vtype == "away" else "hand_count"
    if last and (datetime.now() - parse_dt(last["created_at"])).total_seconds() < 8:   # debounce
        return jsonify(ok=True, ignored=True, away=sub["away_count"], hand=sub["hand_count"], banned=False)
    strike = sub[col] + 1
    snap_name = f"sub_{sub['id']}_{secrets.token_hex(4)}.jpg"
    has_snap = _save_jpeg(data.get("image"), f"snapshots/{snap_name}")
    detail = (data.get("detail") or "")[:200]
    db.execute("INSERT INTO violations(submission_id,vtype,detail,snapshot,strike,created_at) VALUES(?,?,?,?,?,?)",
               (sub["id"], vtype, detail, snap_name if has_snap else None, strike, now()))
    db.execute(f"UPDATE submissions SET {col}=? WHERE id=?", (strike, sub["id"]))
    label = "moved away from the camera" if vtype == "away" else "moved hands without typing"
    banned = strike >= MAX_STRIKES
    if banned:
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        db.execute("UPDATE submissions SET status='banned', banned_until=?, submitted_at=?, times_banned=times_banned+1 "
                   "WHERE id=?", (tomorrow, now(), sub["id"]))
        db.commit()
        score_objectives(db, sub["id"], exam_id)
        log_event(exam["lecturer_id"], "ban", f"BANNED: {g.user['full_name']} ({g.user['identifier']}) {label} "
                  f"{MAX_STRIKES} times in {exam['course_code']}. Locked until tomorrow.", f"/lecturer/submissions/{sub['id']}")
    else:
        db.commit()
        log_event(exam["lecturer_id"], "warning", f"Warning {strike}/{MAX_STRIKES}: {g.user['full_name']} {label} "
                  f"({detail}) — {exam['course_code']}.", f"/lecturer/exams/{exam_id}/live")
    row = db.execute("SELECT away_count, hand_count FROM submissions WHERE id=?", (sub["id"],)).fetchone()
    return jsonify(ok=True, strike=strike, away=row["away_count"], hand=row["hand_count"], banned=banned)


@app.route("/student/exam/<int:exam_id>/chunk", methods=["POST"])
@login_required("student")
def exam_chunk(exam_id):
    exam, sub = active_submission(exam_id)
    if sub["status"] not in ("in_progress", "banned", "submitted", "marking", "graded"):
        return jsonify(ok=False), 409
    attempt = request.args.get("attempt", type=int) or sub["attempt"]
    data = request.get_data()
    if data:
        key = f"recordings/sub_{sub['id']}_{attempt}/{time.time_ns():020d}.webm"
        store.put(key, data, "video/webm")
        db = get_db()
        db.execute("INSERT INTO recording_chunks(submission_id,attempt,path,size,created_at) VALUES(?,?,?,?,?)",
                   (sub["id"], attempt, key, len(data), now()))
        db.commit()
    return jsonify(ok=True)


@app.route("/student/exam/<int:exam_id>/banned")
@login_required("student")
def exam_banned(exam_id):
    exam, sub = active_submission(exam_id)
    vs = get_db().execute("SELECT * FROM violations WHERE submission_id=? ORDER BY id", (sub["id"],)).fetchall()
    return render_template("student/banned.html", exam=exam, sub=sub, violations=vs)


@app.route("/student/exam/<int:exam_id>/receipt")
@login_required("student")
def submission_receipt(exam_id):
    exam, sub = active_submission(exam_id)
    answered = get_db().execute("SELECT COUNT(*) FROM answers WHERE submission_id=? AND (selected_option IS NOT NULL OR "
                                "(answer_text IS NOT NULL AND TRIM(answer_text)!=''))", (sub["id"],)).fetchone()[0]
    return render_template("student/receipt.html", exam=exam, sub=sub, answered=answered, totals=exam_totals(exam_id))


@app.route("/student/exam/<int:exam_id>/result")
@login_required("student")
def student_result(exam_id):
    exam, sub = active_submission(exam_id)
    if not exam["results_released"] or sub["status"] in ("in_progress", "marking"):
        flash("Results for this exam have not been released yet.", "info")
        return redirect(url_for("student_dashboard"))
    rows = get_db().execute("SELECT q.*, a.selected_option, a.answer_text, a.awarded_marks, a.feedback FROM questions q "
                            "LEFT JOIN answers a ON a.question_id=q.id AND a.submission_id=? WHERE q.exam_id=? "
                            "ORDER BY q.qtype DESC, q.position, q.id", (sub["id"], exam_id)).fetchall()
    return render_template("student/result.html", exam=exam, sub=sub, rows=rows, totals=exam_totals(exam_id))


def database_unreachable(e):
    if e is not None:
        print("Database unreachable:", (str(e).splitlines() or [""])[0][:200])
        dbl._health.update(ok=False, checked=time.time())
    g.pop("db", None)
    if request.is_json or request.path.endswith(".json") or request.method == "POST" and request.path.endswith(
            ("autosave", "frame", "violation", "chunk", "heartbeat", "ask", "check", "simpler")):
        return jsonify(ok=False, reason="offline", offline_url=offline_url()), 503
    target = offline_url()
    if target and request.method == "GET":
        return redirect(target)
    return render_template("offline_error.html", offline_link=target), 503


for _err in dbl.CONNECTION_ERRORS:
    app.register_error_handler(_err, database_unreachable)
app.before_request_funcs.setdefault(None, []).insert(0, offline_switch_guard)


@app.errorhandler(403)
def forbidden(_e):
    return render_template("error.html", code=403, msg="You don't have access to this page."), 403


@app.errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404, msg="The page you requested was not found."), 404


_needs_init = False
try:
    init_db()
except dbl.CONNECTION_ERRORS as e:
    _needs_init = True
    dbl._health.update(ok=False)
    print("Online database unreachable at start-up (no internet?) - offline study will be used until it is back.")


def _on_db_change(ok):
    global _needs_init
    if ok and _needs_init:
        try:
            init_db()
            _needs_init = False
        except Exception as e:
            print("init after reconnect failed:", e)


dbl.on_change(_on_db_change)

if os.environ.get("WERKZEUG_RUN_MAIN") != "false" and speech.available():
    speech.warm_up()


def _port_open(port):
    import socket
    with socket.socket() as sk:
        sk.settimeout(0.5)
        return sk.connect_ex(("127.0.0.1", port)) == 0


def start_offline_companion(online_port):
    """Run Offline Study beside the online app, so we can switch to it the moment the internet drops."""
    import atexit
    import subprocess
    if _port_open(OFFLINE_PORT):
        print(f"Offline Study already running on port {OFFLINE_PORT}.")
        return
    env = dict(os.environ, EXAMDESK_OFFLINE="1", OFFLINE_PORT=str(OFFLINE_PORT), NO_BROWSER="1",
               ONLINE_URL=f"http://127.0.0.1:{online_port}")
    env.pop("EXAMDESK_DATA", None)                       # offline study keeps its own local data
    if os.environ.get("EXAMDESK_OFFLINE_DATA"):
        env["EXAMDESK_DATA"] = os.environ["EXAMDESK_OFFLINE_DATA"]
    proc = subprocess.Popen([sys.executable, os.path.join(BASE_DIR, "offline.py")], env=env, cwd=BASE_DIR)
    atexit.register(lambda: proc.poll() is None and proc.terminate())
    print(f"Offline Study ready on http://127.0.0.1:{OFFLINE_PORT} (used automatically when there is no internet).")


if __name__ == "__main__":
    # Camera access requires HTTPS or localhost. On a LAN use: flask run --cert=adhoc (pip install pyopenssl)
    PORT = int(os.environ.get("PORT", "5000"))
    if dbl.IS_PG and not OFFLINE:
        dbl.start_monitor()
        if os.environ.get("WERKZEUG_RUN_MAIN") != "true":      # only once, not in the debug reloader's child
            start_offline_companion(PORT)
    app.run(debug=True, host="0.0.0.0", port=PORT, threaded=True)
