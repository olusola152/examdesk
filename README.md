# ExamDesk — Proctored CBT + AI Professor (Python / Flask + Supabase)

## Data storage
| What | With Supabase (production) | Without Supabase (local testing) |
|---|---|---|
| All records (users, courses, lectures, exams, answers, scores, warnings, activity) | Supabase **Postgres** | `instance/examdesk.db` (SQLite) |
| Exam videos, warning snapshots, live camera frames, lecture files | Supabase **Storage**, private bucket `examdesk` | `instance/files/` |

The app picks this automatically: if `DATABASE_URL` is set it uses Supabase Postgres, and if `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` are set it uses Supabase Storage.

## Set up Supabase (about 5 minutes)
1. Create a project at https://supabase.com and note your **database password**.
2. **Database URL:** click **Connect** at the top of the project, choose **Session pooler**, and copy the URI. Put your password in place of `[YOUR-PASSWORD]`.
   Use the *Session pooler* link, not "Direct connection" — Render and most home networks need it.
3. **Storage keys:** go to **Project Settings → API** and copy the **Project URL** and the **service_role** key (keep this one secret).
4. Copy `.env.example` to `.env` and paste those values into it.
5. `pip install -r requirements.txt`, then `python app.py`.

On first start the app **creates all 16 tables and the private storage bucket by itself**, and switches on Row Level Security on every table so nobody can read them through Supabase's public API. (If you'd rather create the tables yourself, run `supabase_schema.sql` in the SQL Editor.)
You can see all the data in Supabase → **Table Editor**, and all the files in Supabase → **Storage → examdesk**.

## Run locally without Supabase
```bash
pip install -r requirements.txt
python app.py                      # http://127.0.0.1:5000  (uses SQLite + instance/files)
```

## Deploy on Render
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app --workers 2 --threads 8 --timeout 120`
- Environment variables: the same ones as in `.env.example`.
- Render gives you HTTPS, which the camera needs. Because the data lives in Supabase, nothing is lost when Render restarts.

## AI Professor — online or fully offline
The app picks the AI engine automatically:

| Mode | When | Internet | Cost | Quality |
|---|---|---|---|---|
| **Claude** | `ANTHROPIC_API_KEY` is set | needed | pay per use | best |
| **Ollama (offline AI)** | no API key, and Ollama is running | **not needed** | **free** | good |
| Basic | neither | not needed | free | weak (keyword matching) |

### Set up offline AI (one time, needs internet only for the download)
1. Download and install **Ollama** from https://ollama.com (Windows/Mac/Linux).
2. Open Command Prompt and download a model:
   ```
   ollama pull llama3.2:3b
   ```
   This is about 2 GB and works on laptops with 8 GB of RAM. With 16 GB of RAM or more, `ollama pull llama3.1:8b` gives better answers; set `OLLAMA_MODEL=llama3.1:8b` in `.env` if you use it.
3. Leave Ollama running in the system tray and start the app. The lecturer dashboard shows **"Offline AI — llama3.2:3b"**.

Local models are slower than online ones. On an ordinary laptop, expect about 1–5 minutes to prepare a lecture and 10–30 seconds to mark each theory answer. This happens in the background while students do other things. It is faster with an NVIDIA graphics card.

### Dictionary, voice questions and live diagrams (all offline)
- **Dictionary:** on the lecture page, type or say any English word and press **📖 Look up**. About 147,000 words (WordNet, in `data/dictionary.db`) work with no internet. If the word is in the lecture notes, he gives the notes' meaning first.
- **Ask with your voice:** press **🎤**, speak, and it stops by itself when you go quiet. It works in any accent and with Pidgin. For example, "ticha wetin be fotosintesis" is understood as "teacher what is photosynthesis". It compares sounds (vowels and diphthongs are ignored, and th/t, v/f, z/s, sh/ch are treated as the same) and checks them against the words in the lecture.
  Run this once with internet to enable offline speech recognition (about 150 MB):
  ```
  python get_speech_model.py          # Whisper base.en (recommended)
  python get_speech_model.py --small  # smaller and faster, for weak laptops
  python get_speech_model.py --vosk   # alternative engine
  ```
  Without the model it uses the browser's own speech recognition, which needs internet.
- **Diagrams drawn by Python:** every lecture part, answer and dictionary word gets a diagram that is built box by box in time with what he is saying.

### Make camera proctoring work offline too
Run this once while you have internet:
```
python download_models.py
```
It saves the face and hand detection models to `static/models/`, so exams no longer need internet. Everything else (database, video recording, lectures) already works offline when you run without Supabase.

### How an AI Professor lecture runs
For every part of the lecture:
1. **✍ Copy notes:** the teacher writes the notes on the board, letter by letter, and tells the class to copy. He **waits** until the student says **"done"**, presses **Space**, or clicks **I'm done copying**. If they take long, he asks "Are you done?" every minute.
2. **🎬 Explain:** he explains in his own words while an **animated diagram** plays on the projector screen in step with his voice, like a short video. There are six kinds: steps (flow), cycle, timeline, concept map, types (hierarchy) and comparison.
3. Then he moves straight to the next part. He asks **no questions during the lecture**.

**After the lecture (optional):** if the notes contain proper definitions, or real AI prepared questions, a **📝 Practice quiz** button appears. Students can test themselves and the lecturer sees the results. Weak fill-in-the-gap questions are never used.

**Extra controls:** Repeat, **Explain simpler** (re-explains the part with a new example), Previous/Next, speed, full-screen class mode, **⭐ Difficult** (marks a part as hard; the lecturer sees how many students marked each part), **📖 Key terms** (a glossary built from the notes; click a term to hear it explained), **Continue from where you stopped**, and **My notes**, a printable page with the board notes, diagrams, key terms, the full explanation and practice questions (Print → Save as PDF).

**Reading lecture files:** PDF headers, footers and page numbers are removed, lines broken across a page are joined back into sentences, numbered and question headings are recognised, and bullet points from Word and PowerPoint are kept. Lectures prepared by an older version rebuild themselves automatically the next time they're opened, unless a lecturer has edited them.
**Voice commands** (Chrome/Edge, needs internet): "done", "A/B/C/D", "repeat", "next", "pause", "continue", "slower", "I don't understand", "Teacher, what is …?"
**Keyboard:** Space = done / pause · R = repeat · N = next · D = mark difficult · F = full screen · A–D = answer (practice quiz only).
**For the lecturer:** each lecture page shows **which parts students marked difficult**, practice-quiz results, the questions students asked, and who asked for simpler explanations. **✏ Edit lecture** lets you correct the board notes, explanations and practice questions. Edited lectures are never rebuilt automatically.

### Automatic online ↔ offline switching
Start the app with `python app.py` as usual. It also starts **Offline Study** in the background, on `http://127.0.0.1:5050`.
- **Internet goes off:** within about 10 seconds the page shows "📴 No internet" and moves you to Offline Study. Any page you open then also goes straight there, with no error screen.
- **Internet comes back:** Offline Study shows "✅ Internet is back" and returns you to online ExamDesk. During a lecture it waits for you to click, so the lesson isn't cut off.
- **No internet at start-up:** the app still starts and opens in Offline Study.
- **Separate data:** Offline Study keeps its own notes on your laptop. Online data (courses, exams, scores) stays in Supabase.
- **Other computers:** the switch works on the computer running the app. Students using a hosted (Render) version should install the Offline Study app, as described below.

### Offline study (students with no internet)
1. **With internet (online ExamDesk):** the lecturer sends a lecture file. Students click **⬇ File** on the dashboard or on the lecture page, which downloads the original PDF, Word or PowerPoint file. The lecturer sees who downloaded it.
2. **Without internet (student's laptop):** the student opens **AI Professor Offline Study**, chooses the downloaded file, and clicks **Upload & let AI Professor teach me**. Everything then runs on the laptop: board notes, copy time, voice, diagrams and quick checks. There's no login, and the files stay on the laptop.

**How students get the offline app:**
- **Easiest:** run `build_offline_exe.bat` once on your PC. It creates `dist\AI Professor Offline Study\`. Zip that folder and give it to students, who just double-click **AI Professor Offline Study.exe**. They need no Python and no internet.
- **With Python:** double-click `start_offline.bat`, or run `python offline.py`.
- **Offline voice:** Windows voices are used automatically. **Offline AI:** if a student has Ollama installed (see below), the teaching is much better; without it, the basic mode is used.
- **3D teacher:** if `static\teacher\teacher.glb` is there when you build the .exe, the 3D teacher is included.

### 3D teacher (optional)
To replace the cartoon teacher with a realistic 3D person, for example a Sketchfab model of a man in a suit, put `teacher.glb` in `static/teacher/`.
For arm gestures, rig the model for free on **mixamo.com** and add `teacher.fbx`, `idle.fbx` and `talking.fbx`. Step-by-step instructions are in `static/teacher/README.txt`.
His head nods and turns with his voice, his **jaw moves** as he speaks, and his **free hand lifts and gestures** while he explains or writes. The app finds his mouth and the arm that isn't in a pocket automatically, with no Mixamo needed. He is stood upright, sized to the stage, slightly turned towards the board, and given a floor shadow. You can adjust this in `.env` with `TEACHER_TURN`, `TEACHER_ZOOM` and `TEACHER_GESTURE`. If you rig him on Mixamo, the real Talking, Idle and Writing animations are used instead. If there's no model, the cartoon teacher is used.

### AI Professor's voice
The voice is made on the server and played to every student as real audio, and the teacher's mouth moves with the sound:
1. **Natural Nigerian voice** (`en-NG-AbeoNeural`) when there is internet. You can change it with `TTS_VOICE` in `.env`, for example `en-NG-EzinneNeural` for a female voice.
2. **Windows voices** when offline. These work with no internet; pywin32 is needed and is installed automatically on Windows.
3. **Browser voice** as a last resort.

Each sentence is made once and saved, so replays are instant, and a lecture's audio is prepared together with the lecture.
To check the voice on your PC, run `python voice_test.py`. It saves sample files you can listen to.

## What it does
**Lecturer: Command Centre**
- Live activity feed: students starting or submitting, warnings, bans, AI marking, students asking AI Professor questions.
- **Courses**: each course has a class code that students use to join.
- **Send lectures**: upload a PDF, Word (.docx) or PowerPoint (.pptx) file from the dashboard or a course page, then choose **All students** or **Selected students** (with search, select all and a count). You can change who receives it later. AI Professor turns the file into a spoken lecture, explained in its own words.
- Each lecturer has their own dashboard and sees only their own courses, students, lectures and exams.
- **Monthly test by AI Professor**: it sets the test from your lectures (you review it and open it), marks every script, and reports back.
- **Your own exams**: objective and theory questions, a marking guide, camera proctoring on or off, and AI marking on or off.
- **Live monitor**: camera snapshots of every candidate (refreshed every 5 s), warning counts, progress and time left. You can lift a ban.
- **Script review**: the full video recording, a snapshot for each warning, an "AI Professor: suggest marks" button, and manual marking.
- **Report** (averages, grade distribution, hardest questions, AI-written summary) and **Monthly records** (all scores for the month, exportable to CSV).

**Student**
- **Own dashboard**: "Lectures sent to me" shows each lecture with the course and the lecturer who sent it. "My notes → AI Professor" lets the student upload their own notes and have them taught; these are private to them.
- Joins a course with the class code, then watches AI Professor lectures (spoken aloud, with an animated teacher and a blackboard). They can ask questions by typing or with the microphone.
- **Proctored exam**: the camera opens full-screen as soon as the exam starts and is recorded. The questions sit in a panel on the right.
  - Away from the camera (face missing, turned sideways, too far back, another person in view, leaving the window or full screen) = warning.
  - Hands moving in view with no typing = warning.
  - **3 warnings of the same kind = banned from that exam for today.** Answers are kept. The student can continue the next day, or sooner if the lecturer lifts the ban.

## Notes
- Face and hand detection use Google MediaPipe and run in the student's browser. The library is bundled in `static/mediapipe/`. The two small model files download from Google the first time. To run fully offline, put `blaze_face_short_range.tflite` and `hand_landmarker.task` in `static/models/`.
- Recordings are uploaded in 10-second pieces (about 0.4 MB each, so roughly 2.5 MB per minute) and played back as one video that you can seek through. Because each piece is small, they stay well under Supabase's 50 MB free-plan file limit, however long the exam is.
- Free-plan Supabase storage is 1 GB, which is about 6–7 hours of exam video. Upgrade the plan or delete old recordings as needed.
- Lecture files must contain text. Scanned PDFs (pictures of pages) need OCR first. Old .doc and .ppt files need to be saved as .docx or .pptx.
- Detection sensitivity is set in `CFG` at the top of `static/proctor.js`.
