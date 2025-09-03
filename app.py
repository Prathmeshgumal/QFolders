import os
from functools import wraps
from typing import Optional, List

from flask import (
    Flask, render_template, request, redirect, url_for, session, flash, abort
)
from supabase import create_client, Client  # supabase-py v2
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "change-me")
SITE_URL = os.getenv("SITE_URL", "https://q-folders.vercel.app")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY environment variables")

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY


def get_supabase(access_token: Optional[str] = None) -> Client:
    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    if access_token:
        # Ensure PostgREST uses the user's JWT for RLS
        client.postgrest.auth(access_token)
    return client


def login_required(view_fn):
    @wraps(view_fn)
    def wrapper(*args, **kwargs):
        if "access_token" not in session or "user" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        return view_fn(*args, **kwargs)
    return wrapper


def current_user():
    return session.get("user")  # dict with id, email


@app.route("/")
def index():
    if session.get("access_token"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not email or not password:
            flash("Email and password are required.", "danger")
            return redirect(url_for("register"))

        client = get_supabase()
        try:
            confirm_path = url_for('auth_confirmed')
            redirect_url = f"{SITE_URL.rstrip('/')}{confirm_path}"
            # Optional: log to server console to verify what we send
            print(f"[v0] Using email redirect URL: {redirect_url}")

            # Some client libs expect camelCase (emailRedirectTo); others accept snake_case.
            signup_payload = {
                "email": email,
                "password": password,
                "options": {
                    "email_redirect_to": redirect_url,
                    "emailRedirectTo": redirect_url,  # compatibility
                },
            }

            res = client.auth.sign_up(signup_payload)

            flash("Registration successful. Check your email to confirm, then log in.", "success")
            return redirect(url_for("login"))
        except Exception as e:
            flash(f"Registration failed: {str(e)}", "danger")
            return redirect(url_for("register"))

    return render_template("auth/register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not email or not password:
            flash("Email and password are required.", "danger")
            return redirect(url_for("login"))

        client = get_supabase()
        try:
            res = client.auth.sign_in_with_password({"email": email, "password": password})
            user = res.user
            session_data = res.session
            session["access_token"] = session_data.access_token
            session["refresh_token"] = session_data.refresh_token
            session["user"] = {"id": user.id, "email": user.email}
            flash("Logged in successfully.", "success")
            return redirect(url_for("dashboard"))
        except Exception as e:
            flash(f"Login failed: {str(e)}", "danger")
            return redirect(url_for("login"))

    return render_template("auth/login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("login"))


@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    user = current_user()
    client = get_supabase(session.get("access_token"))

    if request.method == "POST":
        # Create a new folder
        name = request.form.get("name", "").strip()
        if not name:
            flash("Folder name is required.", "danger")
            return redirect(url_for("dashboard"))
        try:
            # user_id is enforced by RLS, but we explicitly set it for clarity
            resp = client.table("folders").insert({"user_id": user["id"], "name": name}).execute()
            flash("Folder created.", "success")
            return redirect(url_for("dashboard"))
        except Exception as e:
            flash(f"Failed to create folder: {str(e)}", "danger")
            return redirect(url_for("dashboard"))

    # List folders for current user (RLS will scope results)
    try:
        folders = client.table("folders").select("*").order("created_at", desc=True).execute().data
        
        # For each folder, get its questions
        for folder in folders:
            try:
                questions = (
                    client.table("questions")
                    .select("*")
                    .eq("folder_id", folder["id"])
                    .order("created_at", desc=True)
                    .execute()
                    .data
                )
                folder["questions"] = questions
            except Exception as e:
                folder["questions"] = []
    except Exception as e:
        flash(f"Failed to load folders: {str(e)}", "danger")
        folders = []

    return render_template("dashboard.html", folders=folders)


@app.route("/folders/<folder_id>", methods=["GET", "POST"])
@login_required
def folder_detail(folder_id: str):
    user = current_user()
    client = get_supabase(session.get("access_token"))

    # Create a question in this folder
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description") or None
        notes = request.form.get("notes") or None
        links_raw = request.form.get("links") or ""
        code = request.form.get("code") or None
        terminal_output = request.form.get("terminal_output") or None

        if not title:
            flash("Question title is required.", "danger")
            return redirect(url_for("folder_detail", folder_id=folder_id))

        # Parse links as JSON array of strings (one per line)
        links_list: Optional[List[str]] = None
        if links_raw.strip():
            links_list = [line.strip() for line in links_raw.splitlines() if line.strip()]

        try:
            # Prepare the question data
            question_data = {
                "user_id": user["id"],
                "folder_id": folder_id,
                "title": title,
                "description": description,
                "notes": notes,
                "links": links_list,
                "code": code,
            }
            
            # Only add terminal_output if it's provided and not empty
            if terminal_output and terminal_output.strip():
                question_data["terminal_output"] = terminal_output
            
            client.table("questions").insert(question_data).execute()
            flash("Question added.", "success")
        except Exception as e:
            # If the error is about terminal_output column not existing, try without it
            if "terminal_output" in str(e):
                try:
                    question_data = {
                        "user_id": user["id"],
                        "folder_id": folder_id,
                        "title": title,
                        "description": description,
                        "notes": notes,
                        "links": links_list,
                        "code": code,
                    }
                    client.table("questions").insert(question_data).execute()
                    flash("Question added (terminal output not saved - column not available).", "warning")
                except Exception as e2:
                    flash(f"Failed to add question: {str(e2)}", "danger")
            else:
                flash(f"Failed to add question: {str(e)}", "danger")

        return redirect(url_for("folder_detail", folder_id=folder_id))

    # Load folder (RLS ensures user owns it)
    try:
        folder = client.table("folders").select("*").eq("id", folder_id).single().execute().data
        if not folder:
            abort(404)
    except Exception:
        abort(404)

    # Load questions for folder
    try:
        questions = (
            client.table("questions")
            .select("*")
            .eq("folder_id", folder_id)
            .order("created_at", desc=True)
            .execute()
            .data
        )
    except Exception as e:
        flash(f"Failed to load questions: {str(e)}", "danger")
        questions = []

    return render_template("folder.html", folder=folder, questions=questions)


@app.route("/questions/<question_id>")
@login_required
def question_detail(question_id: str):
    client = get_supabase(session.get("access_token"))
    try:
        question = client.table("questions").select("*").eq("id", question_id).single().execute().data
        if not question:
            abort(404)
    except Exception:
        abort(404)
    # Also load folder to enable back navigation
    folder = None
    try:
        folder = client.table("folders").select("id,name").eq("id", question["folder_id"]).single().execute().data
    except Exception:
        pass
    return render_template("question.html", question=question, folder=folder)


@app.route("/auth/resend-confirmation", methods=["POST"])
def resend_confirmation():
    email = request.form.get("email", "").strip().lower()
    if not email:
        flash("Email is required to resend confirmation.", "warning")
        return redirect(url_for("login"))
    try:
        client = get_supabase()
        confirm_path = url_for('auth_confirmed')
        redirect_url = f"{SITE_URL.rstrip('/')}{confirm_path}"
        print(f"[v0] Resend confirm email redirect: {redirect_url}")
        client.auth.resend({
            "type": "signup",
            "email": email,
            "options": {
                "email_redirect_to": redirect_url,
                "emailRedirectTo": redirect_url
            }
        })
        flash("Confirmation email resent. Please check your inbox.", "info")
    except Exception as e:
        flash(f"Could not resend confirmation: {str(e)}", "danger")
    return redirect(url_for("login"))


@app.route("/folders/<folder_id>/delete", methods=["POST"])
@login_required
def delete_folder(folder_id: str):
    user = current_user()
    client = get_supabase(session.get("access_token"))
    
    try:
        # First verify the folder exists and belongs to the user
        folder = client.table("folders").select("*").eq("id", folder_id).single().execute().data
        if not folder:
            flash("Folder not found.", "danger")
            return redirect(url_for("dashboard"))
        
        # Delete the folder (this will cascade delete all questions in the folder due to foreign key constraint)
        client.table("folders").delete().eq("id", folder_id).execute()
        flash(f"Folder '{folder['name']}' deleted successfully.", "success")
    except Exception as e:
        flash(f"Failed to delete folder: {str(e)}", "danger")
    
    return redirect(url_for("dashboard"))


@app.route("/folders/<folder_id>/add-question", methods=["POST"])
@login_required
def add_question_to_folder(folder_id: str):
    user = current_user()
    client = get_supabase(session.get("access_token"))
    
    title = request.form.get("title", "").strip()
    description = request.form.get("description") or None
    notes = request.form.get("notes") or None
    links_raw = request.form.get("links") or ""
    code = request.form.get("code") or None
    terminal_output = request.form.get("terminal_output") or None

    if not title:
        flash("Question title is required.", "danger")
        return redirect(url_for("dashboard"))

    # Parse links as JSON array of strings (one per line)
    links_list: Optional[List[str]] = None
    if links_raw.strip():
        links_list = [line.strip() for line in links_raw.splitlines() if line.strip()]

    try:
        # Prepare the question data
        question_data = {
            "user_id": user["id"],
            "folder_id": folder_id,
            "title": title,
            "description": description,
            "notes": notes,
            "links": links_list,
            "code": code,
        }
        
        # Only add terminal_output if it's provided and not empty
        if terminal_output and terminal_output.strip():
            question_data["terminal_output"] = terminal_output
        
        client.table("questions").insert(question_data).execute()
        flash("Question added successfully.", "success")
    except Exception as e:
        # If the error is about terminal_output column not existing, try without it
        if "terminal_output" in str(e):
            try:
                question_data = {
                    "user_id": user["id"],
                    "folder_id": folder_id,
                    "title": title,
                    "description": description,
                    "notes": notes,
                    "links": links_list,
                    "code": code,
                }
                client.table("questions").insert(question_data).execute()
                flash("Question added (terminal output not saved - column not available).", "warning")
            except Exception as e2:
                flash(f"Failed to add question: {str(e2)}", "danger")
        else:
            flash(f"Failed to add question: {str(e)}", "danger")

    return redirect(url_for("dashboard"))


@app.route("/auth/confirmed")
def auth_confirmed():
    return render_template("auth/confirmed.html")


if __name__ == "__main__":
    app.run(debug=True)
