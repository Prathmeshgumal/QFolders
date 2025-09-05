import os
import uuid
from functools import wraps
from typing import Optional, List
from werkzeug.utils import secure_filename

from flask import (
    Flask, render_template, request, redirect, url_for, session, flash, abort, send_file
)
from supabase import create_client, Client  # supabase-py v2
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "change-me")
SITE_URL = os.getenv("SITE_URL", "https://q-folders.vercel.app")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY environment variables")

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

# File upload configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# Create upload folder if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def get_supabase(access_token: Optional[str] = None) -> Client:
    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    if access_token:
        # Ensure PostgREST uses the user's JWT for RLS
        client.postgrest.auth(access_token)
    return client


def get_supabase_service() -> Client:
    """Get Supabase client with service key (bypasses RLS)"""
    if not SUPABASE_SERVICE_KEY:
        raise RuntimeError("SUPABASE_SERVICE_KEY not found in environment variables")
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def track_contribution(user_id: str, client: Client):
    """Track a user contribution for the current date"""
    try:
        from datetime import date
        today = date.today().isoformat()
        
        # Try to update existing contribution for today
        result = client.table("contributions").update({
            "contribution_count": client.table("contributions")
                .select("contribution_count")
                .eq("user_id", user_id)
                .eq("contribution_date", today)
                .single()
                .execute()
                .data["contribution_count"] + 1,
            "updated_at": "now()"
        }).eq("user_id", user_id).eq("contribution_date", today).execute()
        
        # If no existing record, create a new one
        if not result.data:
            client.table("contributions").insert({
                "user_id": user_id,
                "contribution_date": today,
                "contribution_count": 1
            }).execute()
            
    except Exception as e:
        # If update failed, try to insert
        try:
            client.table("contributions").insert({
                "user_id": user_id,
                "contribution_date": today,
                "contribution_count": 1
            }).execute()
        except Exception as e2:
            print(f"Failed to track contribution: {e2}")
            pass  # Don't break the main functionality


def refresh_jwt_if_needed():
    """Refresh JWT token if it's expired or about to expire"""
    if "access_token" not in session or "refresh_token" not in session:
        return False
    
    try:
        client = get_supabase()
        # Try to refresh the token using the refresh token
        response = client.auth.refresh_session(session["refresh_token"])
        
        if response.session:
            # Update session with new tokens
            session["access_token"] = response.session.access_token
            session["refresh_token"] = response.session.refresh_token
            return True
    except Exception as e:
        print(f"JWT refresh failed: {e}")
        # Clear invalid session data
        session.pop("access_token", None)
        session.pop("refresh_token", None)
        session.pop("user", None)
    
    return False


def login_required(view_fn):
    @wraps(view_fn)
    def wrapper(*args, **kwargs):
        if "access_token" not in session or "user" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        
        # Try to refresh JWT if needed before proceeding
        if not refresh_jwt_if_needed():
            flash("Your session has expired. Please log in again.", "warning")
            return redirect(url_for("login"))
        
        return view_fn(*args, **kwargs)
    return wrapper


def current_user():
    return session.get("user")  # dict with id, email


def handle_jwt_error(error_msg):
    """Handle JWT-related errors consistently"""
    if "JWT expired" in error_msg or "PGRST303" in error_msg:
        flash("Your session has expired. Please log in again.", "warning")
        return redirect(url_for("login"))
    else:
        flash(f"Error: {error_msg}", "danger")
        return None


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def upload_file_to_supabase(file, question_id):
    """Upload file to Supabase storage bucket"""
    try:
        # Generate unique filename
        file_extension = file.filename.rsplit('.', 1)[1].lower()
        unique_filename = f"{question_id}_{uuid.uuid4().hex}.{file_extension}"
        
        # Read file content
        file_content = file.read()
        file.seek(0)  # Reset file pointer
        
        # Upload to Supabase storage using service key (bypasses RLS)
        client = get_supabase_service()
        bucket_name = "question-pdfs"
        
        response = client.storage.from_(bucket_name).upload(
            unique_filename,
            file_content,
            {"content-type": "application/pdf"}
        )
        
        if response:
            return {
                "file_name": file.filename,
                "file_path": unique_filename,
                "file_size": len(file_content)
            }
        else:
            return None
    except Exception as e:
        print(f"Error uploading file to Supabase: {e}")
        return None


def delete_file_from_supabase(file_path):
    """Delete file from Supabase storage"""
    try:
        client = get_supabase_service()
        bucket_name = "question-pdfs"
        client.storage.from_(bucket_name).remove([file_path])
        return True
    except Exception as e:
        print(f"Error deleting file from Supabase: {e}")
        return False


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
            resp = client.table("folders").insert({
                "user_id": user["id"], 
                "name": name,
                "last_accessed": "now()",
                "last_updated": "now()"
            }).execute()
            flash("Folder created.", "success")
            return redirect(url_for("dashboard"))
        except Exception as e:
            flash(f"Failed to create folder: {str(e)}", "danger")
            return redirect(url_for("dashboard"))

    # List folders for current user (RLS will scope results)
    try:
        folders = client.table("folders").select("*").order("last_accessed", desc=True).execute().data
        
        # For each folder, get its questions with all fields including stars and completion
        for folder in folders:
            try:
                questions = (
                    client.table("questions")
                    .select("*, star1, star2, star3, is_completed")
                    .eq("folder_id", folder["id"])
                    .order("last_updated", desc=True)
                    .execute()
                    .data
                )
                folder["questions"] = questions
            except Exception as e:
                folder["questions"] = []
    except Exception as e:
        error_msg = str(e)
        jwt_error_response = handle_jwt_error(error_msg)
        if jwt_error_response:
            return jwt_error_response
        flash(f"Failed to load folders: {error_msg}", "danger")
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
            # First, create the question to get the ID
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
            
            # Insert question and get the ID
            question_data["last_updated"] = "now()"
            result = client.table("questions").insert(question_data).execute()
            question_id = result.data[0]["id"]
            
            # Track contribution for creating a question
            track_contribution(user["id"], client)
            
            # Handle PDF file upload if provided
            if 'pdf_file' in request.files:
                pdf_file = request.files['pdf_file']
                if pdf_file and pdf_file.filename and allowed_file(pdf_file.filename):
                    # Check file size
                    pdf_file.seek(0, 2)  # Seek to end
                    file_size = pdf_file.tell()
                    pdf_file.seek(0)  # Reset to beginning
                    
                    if file_size <= MAX_FILE_SIZE:
                        # Upload file to Supabase
                        file_info = upload_file_to_supabase(pdf_file, question_id)
                        if file_info:
                            # Update question with file info
                            client.table("questions").update({
                                "pdf_file_name": file_info["file_name"],
                                "pdf_file_path": file_info["file_path"],
                                "pdf_file_size": file_info["file_size"]
                            }).eq("id", question_id).execute()
                        else:
                            flash("Question added, but PDF upload failed.", "warning")
                    else:
                        flash("Question added, but PDF file is too large (max 10MB).", "warning")
                elif pdf_file and pdf_file.filename:
                    flash("Question added, but only PDF files are allowed.", "warning")
            
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
                        "last_updated": "now()"
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
        
        # Update last_accessed timestamp for the folder
        client.table("folders").update({"last_accessed": "now()"}).eq("id", folder_id).execute()
    except Exception:
        abort(404)

    # Load questions for folder
    try:
        questions = (
            client.table("questions")
            .select("*")
            .eq("folder_id", folder_id)
            .order("last_updated", desc=True)
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


@app.route("/questions/<question_id>/update", methods=["POST"])
@login_required
def update_question(question_id: str):
    user = current_user()
    client = get_supabase(session.get("access_token"))
    
    # Get form data
    title = request.form.get("title", "").strip()
    description = request.form.get("description") or None
    notes = request.form.get("notes") or None
    links_raw = request.form.get("links") or ""
    code = request.form.get("code") or None
    terminal_output = request.form.get("terminal_output") or None

    if not title:
        flash("Question title is required.", "danger")
        return redirect(url_for("question_detail", question_id=question_id))

    # Parse links as JSON array of strings (one per line)
    links_list: Optional[List[str]] = None
    if links_raw.strip():
        links_list = [line.strip() for line in links_raw.splitlines() if line.strip()]

    try:
        # Prepare the update data
        update_data = {
            "title": title,
            "description": description,
            "notes": notes,
            "links": links_list,
            "code": code,
        }
        
        # Only add terminal_output if it's provided and not empty
        if terminal_output and terminal_output.strip():
            update_data["terminal_output"] = terminal_output
        
        # Handle PDF file upload if provided
        if 'pdf_file' in request.files:
            pdf_file = request.files['pdf_file']
            if pdf_file and pdf_file.filename and allowed_file(pdf_file.filename):
                # Check file size
                pdf_file.seek(0, 2)  # Seek to end
                file_size = pdf_file.tell()
                pdf_file.seek(0)  # Reset to beginning
                
                if file_size <= MAX_FILE_SIZE:
                    # Get current question to check for existing PDF
                    current_question = client.table("questions").select("pdf_file_path").eq("id", question_id).single().execute().data
                    
                    # Delete old PDF if it exists
                    if current_question and current_question.get("pdf_file_path"):
                        delete_file_from_supabase(current_question["pdf_file_path"])
                    
                    # Upload new file to Supabase
                    file_info = upload_file_to_supabase(pdf_file, question_id)
                    if file_info:
                        update_data.update({
                            "pdf_file_name": file_info["file_name"],
                            "pdf_file_path": file_info["file_path"],
                            "pdf_file_size": file_info["file_size"]
                        })
                    else:
                        flash("Question updated, but PDF upload failed.", "warning")
                else:
                    flash("Question updated, but PDF file is too large (max 10MB).", "warning")
            elif pdf_file and pdf_file.filename:
                flash("Question updated, but only PDF files are allowed.", "warning")
        
        # Update the question with last_updated timestamp
        update_data["last_updated"] = "now()"
        client.table("questions").update(update_data).eq("id", question_id).execute()
        flash("Question updated successfully.", "success")
    except Exception as e:
        # If the error is about terminal_output column not existing, try without it
        if "terminal_output" in str(e):
            try:
                update_data = {
                    "title": title,
                    "description": description,
                    "notes": notes,
                    "links": links_list,
                    "code": code,
                    "last_updated": "now()"
                }
                client.table("questions").update(update_data).eq("id", question_id).execute()
                flash("Question updated (terminal output not saved - column not available).", "warning")
            except Exception as e2:
                flash(f"Failed to update question: {str(e2)}", "danger")
        else:
            flash(f"Failed to update question: {str(e)}", "danger")

    return redirect(url_for("question_detail", question_id=question_id))


@app.route("/questions/<question_id>/view-pdf")
@login_required
def view_pdf(question_id: str):
    """View PDF file for a question (for embedding)"""
    client = get_supabase(session.get("access_token"))
    
    try:
        # Get question with PDF info
        question = client.table("questions").select("pdf_file_name, pdf_file_path").eq("id", question_id).single().execute().data
        
        if not question or not question.get("pdf_file_path"):
            return "PDF file not found.", 404
        
        # Download file from Supabase storage using service key
        client = get_supabase_service()
        bucket_name = "question-pdfs"
        file_data = client.storage.from_(bucket_name).download(question["pdf_file_path"])
        
        if file_data:
            # Return file for viewing (not download)
            from io import BytesIO
            return send_file(
                BytesIO(file_data),
                as_attachment=False,
                download_name=question["pdf_file_name"],
                mimetype='application/pdf'
            )
        else:
            return "Failed to load PDF file.", 404
            
    except Exception as e:
        return f"Error loading PDF: {str(e)}", 500


@app.route("/questions/<question_id>/download-pdf")
@login_required
def download_pdf(question_id: str):
    """Download PDF file for a question"""
    client = get_supabase(session.get("access_token"))
    
    try:
        # Get question with PDF info
        question = client.table("questions").select("pdf_file_name, pdf_file_path").eq("id", question_id).single().execute().data
        
        if not question or not question.get("pdf_file_path"):
            flash("PDF file not found.", "danger")
            return redirect(url_for("question_detail", question_id=question_id))
        
        # Download file from Supabase storage using service key
        client = get_supabase_service()
        bucket_name = "question-pdfs"
        file_data = client.storage.from_(bucket_name).download(question["pdf_file_path"])
        
        if file_data:
            # Return file as download
            from io import BytesIO
            return send_file(
                BytesIO(file_data),
                as_attachment=True,
                download_name=question["pdf_file_name"],
                mimetype='application/pdf'
            )
        else:
            flash("Failed to download PDF file.", "danger")
            return redirect(url_for("question_detail", question_id=question_id))
            
    except Exception as e:
        flash(f"Error downloading PDF: {str(e)}", "danger")
        return redirect(url_for("question_detail", question_id=question_id))


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


@app.route("/questions/<question_id>/move", methods=["POST"])
@login_required
def move_question(question_id: str):
    """Move a question to a different folder"""
    try:
        data = request.get_json()
        new_folder_id = data.get('folder_id')
        
        if not new_folder_id:
            return {"success": False, "error": "Folder ID required"}, 400
        
        client = get_supabase(session.get("access_token"))
        user = current_user()
        
        # Verify the question exists and belongs to the user
        question = client.table("questions").select("id, folder_id").eq("id", question_id).single().execute().data
        if not question:
            return {"success": False, "error": "Question not found"}, 404
        
        # Verify the new folder exists and belongs to the user
        folder = client.table("folders").select("id").eq("id", new_folder_id).single().execute().data
        if not folder:
            return {"success": False, "error": "Folder not found"}, 404
        
        # Update the question's folder
        client.table("questions").update({
            "folder_id": new_folder_id,
            "last_updated": "now()"
        }).eq("id", question_id).execute()
        
        return {"success": True, "message": "Question moved successfully"}
    except Exception as e:
        return {"success": False, "error": str(e)}, 500


@app.route("/questions/<question_id>/delete", methods=["POST"])
@login_required
def delete_question(question_id: str):
    user = current_user()
    client = get_supabase(session.get("access_token"))
    
    try:
        # First verify the question exists and belongs to the user
        question = client.table("questions").select("*, pdf_file_path").eq("id", question_id).single().execute().data
        if not question:
            flash("Question not found.", "danger")
            return redirect(url_for("dashboard"))
        
        # Delete associated PDF file if it exists
        if question.get("pdf_file_path"):
            delete_file_from_supabase(question["pdf_file_path"])
        
        # Delete the question
        client.table("questions").delete().eq("id", question_id).execute()
        flash("Question deleted successfully.", "success")
    except Exception as e:
        flash(f"Failed to delete question: {str(e)}", "danger")
    
    return redirect(url_for("dashboard"))


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
        # First, create the question to get the ID
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
        
        # Insert question and get the ID
        question_data["last_updated"] = "now()"
        result = client.table("questions").insert(question_data).execute()
        question_id = result.data[0]["id"]
        
        # Track contribution for creating a question
        track_contribution(user["id"], client)
        
        # Handle PDF file upload if provided
        if 'pdf_file' in request.files:
            pdf_file = request.files['pdf_file']
            if pdf_file and pdf_file.filename and allowed_file(pdf_file.filename):
                # Check file size
                pdf_file.seek(0, 2)  # Seek to end
                file_size = pdf_file.tell()
                pdf_file.seek(0)  # Reset to beginning
                
                if file_size <= MAX_FILE_SIZE:
                    # Upload file to Supabase
                    file_info = upload_file_to_supabase(pdf_file, question_id)
                    if file_info:
                        # Update question with file info
                        client.table("questions").update({
                            "pdf_file_name": file_info["file_name"],
                            "pdf_file_path": file_info["file_path"],
                            "pdf_file_size": file_info["file_size"]
                        }).eq("id", question_id).execute()
                    else:
                        flash("Question added, but PDF upload failed.", "warning")
                else:
                    flash("Question added, but PDF file is too large (max 10MB).", "warning")
            elif pdf_file and pdf_file.filename:
                flash("Question added, but only PDF files are allowed.", "warning")
        
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
                    "last_updated": "now()"
                }
                client.table("questions").insert(question_data).execute()
                # Track contribution for creating a question
                track_contribution(user["id"], client)
                flash("Question added (terminal output not saved - column not available).", "warning")
            except Exception as e2:
                flash(f"Failed to add question: {str(e2)}", "danger")
        else:
            flash(f"Failed to add question: {str(e)}", "danger")

    return redirect(url_for("dashboard"))


@app.route("/auth/confirmed")
def auth_confirmed():
    return render_template("auth/confirmed.html")


@app.route("/api/autosave/checkbox", methods=["POST"])
@login_required
def autosave_checkbox():
    """Auto-save checkbox state"""
    try:
        data = request.get_json()
        question_id = data.get('question_id')
        is_checked = data.get('checked', False)
        
        if not question_id:
            return {"success": False, "error": "Question ID required"}, 400
        
        client = get_supabase(session.get("access_token"))
        user = current_user()
        
        # Update the question's completion status
        client.table("questions").update({
            "is_completed": is_checked,
            "last_updated": "now()"
        }).eq("id", question_id).execute()
        
        # Track contribution only when question is completed (checked)
        if is_checked:
            track_contribution(user["id"], client)
        
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}, 500


@app.route("/api/autosave/star", methods=["POST"])
@login_required
def autosave_star():
    """Auto-save star rating"""
    try:
        data = request.get_json()
        question_id = data.get('question_id')
        star_type = data.get('star_type')  # 'star1', 'star2', 'star3'
        is_checked = data.get('checked', False)
        
        if not question_id or not star_type:
            return {"success": False, "error": "Question ID and star type required"}, 400
        
        client = get_supabase(session.get("access_token"))
        user = current_user()
        
        # Update the specific star field
        update_data = {star_type: is_checked, "last_updated": "now()"}
        client.table("questions").update(update_data).eq("id", question_id).execute()
        
        # Track contribution only when star is checked
        if is_checked:
            track_contribution(user["id"], client)
        
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}, 500


@app.route("/api/autosave/content", methods=["POST"])
@login_required
def autosave_content():
    """Auto-save question content"""
    try:
        data = request.get_json()
        question_id = data.get('question_id')
        field = data.get('field')  # 'title', 'description', 'code', 'notes', 'links', 'terminal_output'
        value = data.get('value', '')
        
        if not question_id or not field:
            return {"success": False, "error": "Question ID and field required"}, 400
        
        client = get_supabase(session.get("access_token"))
        user = current_user()
        
        # Handle links field specially (convert from string to array)
        if field == 'links' and value:
            links_list = [line.strip() for line in value.splitlines() if line.strip()]
            update_data = {field: links_list, "last_updated": "now()"}
        else:
            update_data = {field: value if value else None, "last_updated": "now()"}
        
        client.table("questions").update(update_data).eq("id", question_id).execute()
        
        # Track contribution (only for significant content changes)
        if field in ['title', 'description', 'code', 'notes'] and value.strip():
            track_contribution(user["id"], client)
        
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}, 500


@app.route("/api/contributions")
@login_required
def get_contributions():
    """Get user's contribution data for the last year"""
    try:
        from datetime import date, timedelta
        import calendar
        
        client = get_supabase(session.get("access_token"))
        user = current_user()
        
        # Get contributions from the last 6 months (approximately 180 days)
        end_date = date.today()
        start_date = end_date - timedelta(days=180)
        
        contributions = client.table("contributions").select("*").eq("user_id", user["id"]).gte("contribution_date", start_date.isoformat()).execute().data
        
        # Create a dictionary for quick lookup
        contribution_dict = {c["contribution_date"]: c["contribution_count"] for c in contributions}
        
        # Generate the last 6 months of data
        contribution_data = []
        current_date = start_date
        
        while current_date <= end_date:
            date_str = current_date.isoformat()
            count = contribution_dict.get(date_str, 0)
            
            # Determine darkness level based on count
            if count == 0:
                level = 0
            elif count <= 1:
                level = 1
            elif count <= 4:
                level = 2
            elif count <= 7:
                level = 3
            elif count <= 10:
                level = 4
            elif count <= 14:
                level = 5
            elif count <= 17:
                level = 6
            elif count <= 21:
                level = 7
            else:
                level = 8
            
            contribution_data.append({
                "date": date_str,
                "count": count,
                "level": level
            })
            
            current_date += timedelta(days=1)
        
        return {"success": True, "data": contribution_data}
        
    except Exception as e:
        return {"success": False, "error": str(e)}, 500


if __name__ == "__main__":
    app.run(debug=True)
