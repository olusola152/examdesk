-- ExamDesk schema for Supabase (Postgres).
-- OPTIONAL: the app creates these tables automatically on first start.
-- Paste into Supabase > SQL Editor > Run if you prefer to create them yourself.

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    role TEXT NOT NULL CHECK(role IN ('lecturer','student')),
    full_name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, identifier TEXT, department TEXT,
    password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS courses (
    id SERIAL PRIMARY KEY, lecturer_id INTEGER NOT NULL REFERENCES users(id),
    code TEXT NOT NULL, title TEXT NOT NULL, join_code TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS enrollments (
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id), joined_at TEXT NOT NULL,
    PRIMARY KEY(course_id, student_id));
CREATE TABLE IF NOT EXISTS lectures (
    id SERIAL PRIMARY KEY, course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    title TEXT NOT NULL, original_name TEXT, stored_name TEXT, text TEXT, script TEXT,
    status TEXT NOT NULL DEFAULT 'preparing', audience TEXT NOT NULL DEFAULT 'all', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lecture_recipients (
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id), sent_at TEXT NOT NULL, PRIMARY KEY(lecture_id, student_id));
CREATE TABLE IF NOT EXISTS personal_lectures (
    id SERIAL PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL, original_name TEXT, stored_name TEXT, text TEXT, script TEXT,
    status TEXT NOT NULL DEFAULT 'preparing', progress INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS personal_questions (
    id SERIAL PRIMARY KEY, plecture_id INTEGER NOT NULL REFERENCES personal_lectures(id) ON DELETE CASCADE,
    question TEXT NOT NULL, answer TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lecture_views (
    id SERIAL PRIMARY KEY, lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id), started_at TEXT NOT NULL, last_seen TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0, UNIQUE(lecture_id, student_id));
CREATE TABLE IF NOT EXISTS lecture_questions (
    id SERIAL PRIMARY KEY, lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id), question TEXT NOT NULL, answer TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS exams (
    id SERIAL PRIMARY KEY, lecturer_id INTEGER NOT NULL REFERENCES users(id),
    course_id INTEGER REFERENCES courses(id) ON DELETE SET NULL,
    title TEXT NOT NULL, course_code TEXT NOT NULL, instructions TEXT,
    duration_minutes INTEGER NOT NULL DEFAULT 60, access_code TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft', results_released INTEGER NOT NULL DEFAULT 0,
    shuffle_questions INTEGER NOT NULL DEFAULT 0, proctored INTEGER NOT NULL DEFAULT 1,
    ai_marking INTEGER NOT NULL DEFAULT 0, exam_type TEXT NOT NULL DEFAULT 'standard',
    created_by TEXT NOT NULL DEFAULT 'lecturer', ai_report TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS questions (
    id SERIAL PRIMARY KEY, exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    qtype TEXT NOT NULL CHECK(qtype IN ('objective','theory')), text TEXT NOT NULL,
    option_a TEXT, option_b TEXT, option_c TEXT, option_d TEXT, correct_option TEXT,
    marking_guide TEXT, marks DOUBLE PRECISION NOT NULL DEFAULT 1, position INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS submissions (
    id SERIAL PRIMARY KEY, exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id), started_at TEXT NOT NULL, submitted_at TEXT,
    status TEXT NOT NULL DEFAULT 'in_progress',   -- in_progress | submitted | marking | graded | banned
    objective_score DOUBLE PRECISION DEFAULT 0, theory_score DOUBLE PRECISION DEFAULT 0, lecturer_comment TEXT,
    away_count INTEGER NOT NULL DEFAULT 0, hand_count INTEGER NOT NULL DEFAULT 0,
    banned_until TEXT, times_banned INTEGER NOT NULL DEFAULT 0, attempt INTEGER NOT NULL DEFAULT 1,
    last_seen TEXT, frame_at TEXT, marked_by TEXT, UNIQUE(exam_id, student_id));
CREATE TABLE IF NOT EXISTS answers (
    id SERIAL PRIMARY KEY, submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    selected_option TEXT, answer_text TEXT, awarded_marks DOUBLE PRECISION, feedback TEXT,
    UNIQUE(submission_id, question_id));
CREATE TABLE IF NOT EXISTS violations (
    id SERIAL PRIMARY KEY, submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    vtype TEXT NOT NULL, detail TEXT, snapshot TEXT, strike INTEGER, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recording_chunks (
    id SERIAL PRIMARY KEY, submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    attempt INTEGER NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_chunks_sub ON recording_chunks(submission_id, attempt, id);
CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY, lecturer_id INTEGER NOT NULL, kind TEXT NOT NULL,
    message TEXT NOT NULL, link TEXT, created_at TEXT NOT NULL);

-- Block Supabase's public REST API from these tables (the app connects as owner and is unaffected)
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE courses ENABLE ROW LEVEL SECURITY;
ALTER TABLE enrollments ENABLE ROW LEVEL SECURITY;
ALTER TABLE lectures ENABLE ROW LEVEL SECURITY;
ALTER TABLE lecture_recipients ENABLE ROW LEVEL SECURITY;
ALTER TABLE personal_lectures ENABLE ROW LEVEL SECURITY;
ALTER TABLE personal_questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE lecture_views ENABLE ROW LEVEL SECURITY;
ALTER TABLE lecture_questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE exams ENABLE ROW LEVEL SECURITY;
ALTER TABLE questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE answers ENABLE ROW LEVEL SECURITY;
ALTER TABLE violations ENABLE ROW LEVEL SECURITY;
ALTER TABLE recording_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE events ENABLE ROW LEVEL SECURITY;
