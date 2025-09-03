# QFolders (Flask + Supabase)

A minimal web app to create folders and add questions inside each folder. Auth via Supabase email/password.

Features:
- Register/Login with email + password (Supabase Auth)
- Create folders
- Inside a folder, create questions with:
  - Required: Que title
  - Optional: Description, Notes, Links (one URL per line), Code snippet
- Data stored in Supabase Postgres with RLS per user

## Prerequisites
- Python 3.10+
- A Supabase project (URL and ANON key)

## Setup
1. Copy env:
   cp .env.example .env
   Fill SUPABASE_URL, SUPABASE_ANON_KEY, FLASK_SECRET_KEY.

2. Create tables and RLS policies (run once in Supabase SQL):
   Open `scripts/sql/001_init.sql` and execute it in your project's SQL editor.

3. Install deps:
   pip install -r requirements.txt

4. Run:
   python app.py
   Open http://localhost:5000

## Notes
- If email confirmation is enabled in your Supabase Auth settings, users must confirm before logging in.
- RLS policies restrict all data by `auth.uid()`, so all requests use the user's access token.
