import os
import sys
import json
import math
import re
import uuid
import shutil
import subprocess
import tempfile
import threading
import time
import random
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from contextlib import asynccontextmanager

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

# Windows UTF-8 Terminal Fix
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from fastapi import FastAPI, Depends, HTTPException, status, APIRouter, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field
from passlib.context import CryptContext
from jose import JWTError, jwt
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey,
    Float, Text, create_engine
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
import uvicorn

# Storage configuration for large syllabus files (up to 1 GB)
UPLOAD_DIR = os.path.join(os.getcwd(), "uploaded_materials")
os.makedirs(UPLOAD_DIR, exist_ok=True)
MAX_FILE_SIZE_BYTES = 1024 * 1024 * 1024  # 1 GB
CHUNK_SIZE_BYTES = 5 * 1024 * 1024        # 5 MB chunk buffer for memory-efficient streaming

# ==============================================================================
# 1. DATABASE & SECURITY CONFIGURATION
# ==============================================================================

SECRET_KEY = os.getenv("SECRET_KEY", "edunova-enterprise-jwt-token-production-2026")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./edunova_core.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# ==============================================================================
# 2. SQLALCHEMY ORM MODELS
# ==============================================================================

# ==============================================================================
# 2. SQLALCHEMY ORM MODELS (UPGRADED WITH THREE ROLES & PROFILE EXTENSIONS)
# ==============================================================================

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    username = Column(String(100), unique=True, index=True, nullable=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False, default="student")  # "student", "teacher", "admin"
    phone_number = Column(String(20), nullable=True)
    avatar_url = Column(Text, nullable=True)
    bio = Column(Text, nullable=True)
    city = Column(String(100), nullable=True)
    gender = Column(String(50), nullable=True)
    date_of_birth = Column(String(50), nullable=True)
    email_verified = Column(Boolean, default=False)
    is_profile_complete = Column(Boolean, default=False)
    approval_status = Column(String(50), default="approved")  # "approved", "pending", "rejected"
    rejection_reason = Column(Text, nullable=True)
    application_id = Column(String(50), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    student_profile = relationship("StudentProfile", back_populates="user", uselist=False)
    teacher_profile = relationship("TeacherProfile", back_populates="user", uselist=False)


class StudentProfile(Base):
    __tablename__ = "student_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    enrollment_no = Column(String(50), unique=True, index=True, nullable=False)
    usn = Column(String(50), nullable=True)
    college_name = Column(String(200), default="University Institute of Technology")
    branch = Column(String(100), default="Computer Science & Engineering")
    current_semester = Column(Integer, default=3)
    year_of_completion = Column(Integer, default=2026)
    academic_interests = Column(Text, nullable=True)
    technical_skills = Column(Text, nullable=True)
    career_interests = Column(Text, nullable=True)
    completion_percentage = Column(Integer, default=0)
    study_streak_days = Column(Integer, default=14)
    overall_attendance = Column(Float, default=94.5)
    overall_progress = Column(Float, default=82.0)
    average_score = Column(Float, default=86.5)

    user = relationship("User", back_populates="student_profile")


class TeacherProfile(Base):
    __tablename__ = "teacher_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    teacher_id_code = Column(String(50), nullable=True)  # Unique Teacher ID e.g. TEA-2026-001
    college_name = Column(String(200), default="University Institute of Technology")
    department = Column(String(100), default="Computer Science & Engineering")
    designation = Column(String(100), default="Assistant Professor")
    contact_number = Column(String(20), nullable=True)
    experience_years = Column(Integer, default=5)
    invitation_code = Column(String(100), nullable=True)
    assigned_semesters = Column(String(100), default="1,2,3,4,5,6,7,8")
    assigned_subjects = Column(Text, default="AI & Machine Learning, Database Management Systems, Python for Data Science")

    user = relationship("User", back_populates="teacher_profile")


class AcademicClass(Base):
    __tablename__ = "classes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    code = Column(String(50), unique=True, index=True, nullable=False)
    semester = Column(Integer, default=3)
    student_count = Column(Integer, default=0)


class StudyMaterial(Base):
    __tablename__ = "study_materials"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    subject = Column(String(100), nullable=False)
    unit = Column(String(50), default="Unit 1")
    pages = Column(Integer, default=12)
    file_path = Column(String(500), nullable=False)
    content_text = Column(Text, nullable=True)
    semester = Column(Integer, default=3)  # 1 to 8, or 0 for universal
    branch = Column(String(100), default="Computer Science & Engineering")
    teacher_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    is_published = Column(Boolean, default=True)
    is_ai_approved = Column(Boolean, default=True)
    uploaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AttendanceRecord(Base):
    __tablename__ = "attendance"

    id = Column(Integer, primary_key=True, index=True)
    student_name = Column(String(255), nullable=False)
    class_name = Column(String(100), nullable=False)
    date = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False)


class TestAssessment(Base):
    __tablename__ = "tests"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    subject = Column(String(100), nullable=False)
    questions_count = Column(Integer, default=5)
    duration_minutes = Column(Integer, default=15)
    difficulty = Column(String(50), default="Medium")
    max_marks = Column(Integer, default=100)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    category = Column(String(50), default="System")
    timestamp = Column(String(50), default="Just now")
    is_read = Column(Boolean, default=False)


class AssessmentQuestion(Base):
    __tablename__ = "assessment_questions"

    id = Column(Integer, primary_key=True, index=True)
    test_id = Column(Integer, ForeignKey("tests.id"), nullable=False)
    prompt = Column(Text, nullable=False)
    options_json = Column(Text, nullable=False)
    correct_option_index = Column(Integer, nullable=False)
    explanation = Column(Text, nullable=True)


class UserTestAttempt(Base):
    __tablename__ = "user_test_attempts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    test_id = Column(Integer, ForeignKey("tests.id"), nullable=False)
    score = Column(Float, nullable=False)
    total_questions = Column(Integer, nullable=False)
    correct_count = Column(Integer, nullable=False)
    submitted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class FlashcardDeck(Base):
    __tablename__ = "flashcard_decks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    subject = Column(String(100), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)


class Flashcard(Base):
    __tablename__ = "flashcards"

    id = Column(Integer, primary_key=True, index=True)
    deck_id = Column(Integer, ForeignKey("flashcard_decks.id"), nullable=False)
    front = Column(Text, nullable=False)
    back = Column(Text, nullable=False)
    mastery_level = Column(Integer, default=0)


class TimetableEntry(Base):
    __tablename__ = "timetable_entries"

    id = Column(Integer, primary_key=True, index=True)
    day_of_week = Column(String(20), nullable=False)
    start_time = Column(String(10), nullable=False)
    end_time = Column(String(10), nullable=False)
    subject = Column(String(100), nullable=False)
    classroom = Column(String(50), nullable=False)
    faculty_name = Column(String(100), nullable=False)


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String(100), nullable=False)
    target_user = Column(String(255), nullable=True)
    details = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AcademicBranch(Base):
    __tablename__ = "academic_branches"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    code = Column(String(50), unique=True, index=True, nullable=False)


class AcademicSubject(Base):
    __tablename__ = "academic_subjects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    code = Column(String(50), unique=True, index=True, nullable=False)
    semester = Column(Integer, default=3)
    branch = Column(String(100), default="Computer Science & Engineering")
    teacher_id = Column(Integer, nullable=True)

# ==============================================================================
# 3. SCHEMAS
# ==============================================================================

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    role: str
    full_name: str
    email: str
    username: Optional[str] = None
    is_profile_complete: bool = False
    approval_status: str = "approved"
    semester: Optional[int] = 3
    branch: Optional[str] = "Computer Science & Engineering"
    teacher_id_code: Optional[str] = None
    application_id: Optional[str] = None

class LoginPayload(BaseModel):
    email: str  # Can be institutional email or username
    password: str
    role: Optional[str] = None

class RegisterPayload(BaseModel):
    email: EmailStr
    password: str = Field(min_length=5, max_length=128)
    full_name: str
    role: Optional[str] = "student"

class StudentRegisterPayload(BaseModel):
    full_name: str
    username: str
    email: EmailStr
    password: str = Field(min_length=5, max_length=128)

class TeacherRegisterPayload(BaseModel):
    full_name: str
    username: str
    email: EmailStr
    password: str = Field(min_length=5, max_length=128)
    contact_number: Optional[str] = ""
    college_name: Optional[str] = "University Institute of Technology"
    department: Optional[str] = "Computer Science & Engineering"
    designation: Optional[str] = "Assistant Professor"
    experience_years: Optional[int] = 5
    invitation_code: Optional[str] = "EDUNOVA-FACULTY-2026"

class PersonalProfilePayload(BaseModel):
    full_name: str
    username: str
    bio: Optional[str] = ""
    city: Optional[str] = ""
    gender: Optional[str] = ""
    date_of_birth: Optional[str] = ""
    avatar_url: Optional[str] = ""

class AcademicProfilePayload(BaseModel):
    college_name: str
    branch: str
    usn: str
    current_semester: int
    year_of_completion: int
    academic_interests: Optional[str] = ""
    technical_skills: Optional[str] = ""
    career_interests: Optional[str] = ""

class TeacherApprovalPayload(BaseModel):
    teacher_id_code: Optional[str] = None
    assigned_semesters: Optional[str] = "1,2,3,4,5,6,7,8"
    assigned_subjects: Optional[str] = ""

class TeacherRejectionPayload(BaseModel):
    reason: str

class StudentSemesterUpdatePayload(BaseModel):
    semester: int
    branch: Optional[str] = None

class SubjectCreatePayload(BaseModel):
    name: str
    code: str
    semester: int
    branch: str

class VerifyEmailPayload(BaseModel):
    code: str

class AIChatPayload(BaseModel):
    message: str
    material_title: Optional[str] = "General Curriculum"

class MaterialCreate(BaseModel):
    title: str
    subject: str
    unit: str
    pages: int
    semester: Optional[int] = 3
    branch: Optional[str] = "Computer Science & Engineering"

class AttendanceItem(BaseModel):
    student_name: str
    class_name: str
    date: str
    status: str

class AttendanceBatchPayload(BaseModel):
    records: List[AttendanceItem]

class TestSubmitPayload(BaseModel):
    test_id: int
    answers: dict

class FlashcardAnswerPayload(BaseModel):
    card_id: int
    remembered: bool

class CodeRunPayload(BaseModel):
    language: str
    code: str
    question_id: Optional[str] = None
    stdin_text: Optional[str] = ""

class CodeSubmitPayload(BaseModel):
    language: str
    code: str
    question_id: str

class AptitudeSubmitPayload(BaseModel):
    answers: dict

class TeacherMCQPayload(BaseModel):
    question: str
    options: List[str]
    answer: int
    topic: Optional[str] = "Problem Solving"

class TeacherCodingPayload(BaseModel):
    title: str
    difficulty: Optional[str] = "Medium"
    description: str
    input: Optional[str] = ""
    expected_output: Optional[str] = ""
    tests_raw: Optional[str] = ""

class TeacherPDFPublishPayload(BaseModel):
    mcq: List[dict] = []
    coding: List[dict] = []

# ==============================================================================
# EDUNOVA CODE RUNNER & QUESTION BANK ENGINE
# ==============================================================================

DATA_FILE = "edunova_question_bank.json"
TEST_DURATION_SECONDS = 30 * 60
APTITUDE_COUNT = 50
CODE_TIMEOUT_SECONDS = 5

DEFAULT_CODING = [
    {
        "id": "C001",
        "title": "Print Number Grid (Two Digits)",
        "difficulty": "Easy",
        "description": (
            "Write a program to print a number grid from 01 to N × N. "
            "Every number must be displayed using exactly two digits."
        ),
        "input": "4",
        "expected_output": "01 02 03 04\n05 06 07 08\n09 10 11 12\n13 14 15 16",
        "tests": [
            {"input": "3", "output": "01 02 03\n04 05 06\n07 08 09"},
            {"input": "2", "output": "01 02\n03 04"},
        ],
    },
    {
        "id": "C002",
        "title": "Sum of Digits",
        "difficulty": "Easy",
        "description": "Read a positive integer and print the sum of its digits.",
        "input": "12345",
        "expected_output": "15",
        "tests": [
            {"input": "12345", "output": "15"},
            {"input": "900", "output": "9"},
            {"input": "7", "output": "7"},
        ],
    },
]

DEFAULT_MCQ = [
    {
        "id": "M001",
        "question": "Which data structure follows FIFO order?",
        "options": ["Stack", "Queue", "Tree", "Graph"],
        "answer": 1,
        "topic": "Data Structures",
    },
    {
        "id": "M002",
        "question": "What is the time complexity of binary search on a sorted array?",
        "options": ["O(1)", "O(log n)", "O(n)", "O(n²)"],
        "answer": 1,
        "topic": "Algorithms",
    },
    {
        "id": "M003",
        "question": "Which keyword is used to define a function in Python?",
        "options": ["func", "function", "def", "define"],
        "answer": 2,
        "topic": "Python",
    },
    {
        "id": "M004",
        "question": "Which sorting algorithm repeatedly swaps adjacent out-of-order elements?",
        "options": ["Merge Sort", "Bubble Sort", "Heap Sort", "Quick Sort"],
        "answer": 1,
        "topic": "Algorithms",
    },
    {
        "id": "M005",
        "question": "What does CPU stand for?",
        "options": [
            "Central Processing Unit",
            "Computer Personal Unit",
            "Central Program Utility",
            "Control Processing User",
        ],
        "answer": 0,
        "topic": "Computer Fundamentals",
    },
]

def normalize_output(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines).strip()

def load_bank():
    if not os.path.exists(DATA_FILE):
        return {"coding": list(DEFAULT_CODING), "mcq": list(DEFAULT_MCQ)}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("Invalid question bank")
        data.setdefault("coding", [])
        data.setdefault("mcq", [])
        return data
    except Exception:
        return {"coding": list(DEFAULT_CODING), "mcq": list(DEFAULT_MCQ)}

def save_bank(bank):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(bank, f, indent=2, ensure_ascii=False)

def find_executable(*names):
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None

def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default

def parse_teacher_pdf(text):
    mcqs = []
    codings = []

    # MCQ blocks
    mcq_blocks = re.split(
        r"(?im)^\s*(?:MCQ|QUESTION)\s*(?:\d+)?\s*[:.-]",
        text,
    )

    for block in mcq_blocks[1:]:
        q_match = re.search(
            r"(?is)^(.*?)(?=\n\s*A[\)\.:])",
            block,
        )
        options = re.findall(
            r"(?im)^\s*([A-D])[\)\.:]\s*(.+)$",
            block,
        )
        ans_match = re.search(
            r"(?im)^\s*(?:Answer|Correct Answer)\s*[:=-]\s*([A-D])",
            block,
        )

        if q_match and len(options) >= 4 and ans_match:
            question = q_match.group(1).strip()
            option_map = {letter: value.strip() for letter, value in options}

            if all(x in option_map for x in "ABCD"):
                mcqs.append({
                    "id": f"PDF-M{len(mcqs)+1:03d}",
                    "question": question,
                    "options": [
                        option_map["A"],
                        option_map["B"],
                        option_map["C"],
                        option_map["D"],
                    ],
                    "answer": ord(ans_match.group(1).upper()) - ord("A"),
                    "topic": "Problem Solving",
                })

    # Coding blocks
    coding_blocks = re.split(
        r"(?im)^\s*CODING\s*(?:\d+)?\s*[:.-]",
        text,
    )

    def field_from_block(block, name):
        pattern = (
            rf"(?ims)^\s*{re.escape(name)}\s*:\s*"
            rf"(.*?)(?=\n\s*[A-Za-z ]+\s*:|\Z)"
        )
        match = re.search(pattern, block)
        return match.group(1).strip() if match else ""

    for block in coding_blocks[1:]:
        title = field_from_block(block, "Title")
        description = field_from_block(block, "Description")
        inp = field_from_block(block, "Input")
        out = field_from_block(block, "Output")

        if title and description and inp and out:
            codings.append({
                "id": f"PDF-C{len(codings)+1:03d}",
                "title": title,
                "difficulty": "Medium",
                "description": description,
                "input": inp,
                "expected_output": out,
                "tests": [
                    {
                        "input": inp,
                        "output": out,
                    }
                ],
            })

    return {"mcq": mcqs, "coding": codings}

class CodeRunner:
    def run(self, language, source, stdin_text):
        language = language.lower().strip()

        if language == "python":
            return self._run_python(source, stdin_text)
        if language == "c":
            return self._run_compiled("c", source, stdin_text)
        if language == "c++":
            return self._run_compiled("cpp", source, stdin_text)
        if language == "java":
            return self._run_java(source, stdin_text)

        return {"ok": False, "output": "", "error": "Unsupported language."}

    def _subprocess(self, cmd, cwd, stdin_text):
        try:
            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

            result = subprocess.run(
                cmd,
                input=stdin_text,
                text=True,
                capture_output=True,
                cwd=cwd,
                timeout=CODE_TIMEOUT_SECONDS,
                creationflags=creationflags,
            )

            output = result.stdout
            error = result.stderr

            if result.returncode != 0:
                return {
                    "ok": False,
                    "output": output,
                    "error": error or f"Process exited with code {result.returncode}.",
                }

            return {"ok": True, "output": output, "error": ""}

        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "output": "",
                "error": f"Time limit exceeded ({CODE_TIMEOUT_SECONDS}s).",
            }
        except FileNotFoundError as exc:
            return {
                "ok": False,
                "output": "",
                "error": f"Required compiler/runtime not found: {exc}",
            }
        except Exception as exc:
            return {"ok": False, "output": "", "error": str(exc)}

    def _run_python(self, source, stdin_text):
        python = find_executable("python", "python3")
        if not python:
            return {"ok": False, "output": "", "error": "Python executable not found."}

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "main.py")
            with open(path, "w", encoding="utf-8") as f:
                f.write(source)
            return self._subprocess([python, path], td, stdin_text)

    def _run_compiled(self, kind, source, stdin_text):
        if kind == "c":
            compiler = find_executable("gcc")
            filename = "main.c"
            exe_name = "main.exe" if os.name == "nt" else "main"
        else:
            compiler = find_executable("g++")
            filename = "main.cpp"
            exe_name = "main.exe" if os.name == "nt" else "main"

        if not compiler:
            return {
                "ok": False,
                "output": "",
                "error": (
                    f"{'GCC' if kind == 'c' else 'G++'} was not found on this computer."
                ),
            }

        with tempfile.TemporaryDirectory() as td:
            source_path = os.path.join(td, filename)
            exe_path = os.path.join(td, exe_name)

            with open(source_path, "w", encoding="utf-8") as f:
                f.write(source)

            compile_result = self._subprocess(
                [compiler, source_path, "-o", exe_path],
                td,
                "",
            )

            if not compile_result["ok"]:
                return {
                    "ok": False,
                    "output": "",
                    "error": "Compilation Error:\n" + compile_result["error"],
                }

            return self._subprocess([exe_path], td, stdin_text)

    def _run_java(self, source, stdin_text):
        javac = find_executable("javac")
        java = find_executable("java")

        if not javac or not java:
            return {
                "ok": False,
                "output": "",
                "error": "JDK (javac/java) was not found on this computer.",
            }

        with tempfile.TemporaryDirectory() as td:
            source_path = os.path.join(td, "Main.java")

            with open(source_path, "w", encoding="utf-8") as f:
                f.write(source)

            compile_result = self._subprocess(
                [javac, source_path],
                td,
                "",
            )

            if not compile_result["ok"]:
                return {
                    "ok": False,
                    "output": "",
                    "error": "Compilation Error:\n" + compile_result["error"],
                }

            return self._subprocess(
                [java, "-cp", td, "Main"],
                td,
                stdin_text,
            )

code_runner = CodeRunner()

# ==============================================================================
# 4. AUTH UTILITIES & DEPENDENCIES
# ==============================================================================

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify only against the password hash saved for this account."""
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Session invalid or token expired",
        headers={"WWW-Authenticate": "Bearer"}
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if not email:
            raise exc
    except JWTError:
        raise exc

    user = db.query(User).filter(User.email == email).first()
    if not user or not user.is_active:
        raise exc
    return user

# ==============================================================================
# 5. DEMO DATABASE SEEDER (Default password: 12345)
# ==============================================================================

def seed_demo_database():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        default_pw = get_password_hash("12345")

        # 1. Ensure Super Administrator exists
        admin = db.query(User).filter(User.email == "admin@edunova.edu").first()
        if not admin:
            admin = User(
                email="admin@edunova.edu",
                username="admin",
                hashed_password=default_pw,
                full_name="System Administrator",
                role="admin",
                is_active=True,
                email_verified=True,
                is_profile_complete=True,
                approval_status="approved"
            )
            db.add(admin)
            db.flush()
            db.add(AdminAuditLog(
                admin_id=admin.id,
                action="SYSTEM_INIT",
                target_user="admin@edunova.edu",
                details="EduNova Enterprise Security & Platform initialization completed."
            ))
            db.commit()

        # 2. Ensure Demo Student: Alex Rivera (Semester 3, Complete Profile)
        alex = db.query(User).filter(User.email == "alex.student@edunova.edu").first()
        if not alex:
            alex = User(
                email="alex.student@edunova.edu",
                username="alexrivera",
                hashed_password=default_pw,
                full_name="Alex Rivera",
                role="student",
                phone_number="+1 (555) 234-5678",
                city="Bengaluru",
                gender="Male",
                date_of_birth="2004-05-14",
                bio="Computer science sophomore passionate about relational algebra, neural networks and distributed systems.",
                is_active=True,
                email_verified=True,
                is_profile_complete=True,
                approval_status="approved"
            )
            db.add(alex)
            db.flush()
            db.add(StudentProfile(
                user_id=alex.id,
                enrollment_no="STU-2026-088",
                usn="STU-2026-088",
                college_name="University Institute of Technology",
                branch="Computer Science & Engineering",
                current_semester=3,
                year_of_completion=2026,
                academic_interests="Distributed Systems, Relational DBMS, Vector Embeddings",
                technical_skills="Python, C++, SQL, React, Docker",
                career_interests="Software Development Engineer, Systems Architect",
                completion_percentage=100,
                study_streak_days=14,
                overall_attendance=94.6,
                overall_progress=85.0,
                average_score=94.6
            ))
            db.commit()

        # 3. Ensure Demo Approved Teacher: Prof. Sarah Chen
        sarah = db.query(User).filter(User.email == "sarah.mentor@edunova.edu").first()
        if not sarah:
            sarah = User(
                email="sarah.mentor@edunova.edu",
                username="sarahchen",
                hashed_password=default_pw,
                full_name="Prof. Sarah Chen",
                role="teacher",
                phone_number="+1 (555) 987-6543",
                is_active=True,
                email_verified=True,
                is_profile_complete=True,
                approval_status="approved"
            )
            db.add(sarah)
            db.flush()
            db.add(TeacherProfile(
                user_id=sarah.id,
                teacher_id_code="TEA-2026-001",
                college_name="University Institute of Technology",
                department="Computer Science & Engineering",
                designation="Professor & Lead Mentor",
                contact_number="+1 (555) 987-6543",
                experience_years=12,
                invitation_code="EDUNOVA-FACULTY-2026",
                assigned_semesters="1,2,3,4,5,6,7,8",
                assigned_subjects="AI & Machine Learning, Database Management Systems, Python for Data Science"
            ))
            db.commit()

        # 4. Ensure Pending Teacher Applicant: Prof. Marcus Vance (for testing approval flow)
        marcus = db.query(User).filter(User.email == "marcus.pending@university.edu").first()
        if not marcus:
            marcus = User(
                email="marcus.pending@university.edu",
                username="marcusvance",
                hashed_password=default_pw,
                full_name="Prof. Marcus Vance",
                role="teacher",
                phone_number="+1 (555) 432-1098",
                is_active=True,
                email_verified=True,
                is_profile_complete=True,
                approval_status="pending",
                application_id="APP-2026-042"
            )
            db.add(marcus)
            db.flush()
            db.add(TeacherProfile(
                user_id=marcus.id,
                college_name="Metropolitan Institute of Technology",
                department="Computer Science & Engineering",
                designation="Associate Professor",
                contact_number="+1 (555) 432-1098",
                experience_years=8,
                invitation_code="EDUNOVA-FACULTY-2026",
                assigned_semesters="3,4",
                assigned_subjects="Design & Analysis of Algorithms, Operating Systems"
            ))
            db.commit()

        # 5. Ensure Incomplete Student: Jordan Reed (for testing profile onboarding)
        jordan = db.query(User).filter(User.email == "jordan.new@university.edu").first()
        if not jordan:
            jordan = User(
                email="jordan.new@university.edu",
                username="jordanreed",
                hashed_password=default_pw,
                full_name="Jordan Reed",
                role="student",
                is_active=True,
                email_verified=False,
                is_profile_complete=False,
                approval_status="approved"
            )
            db.add(jordan)
            db.flush()
            db.add(StudentProfile(
                user_id=jordan.id,
                enrollment_no="STU-2026-105",
                current_semester=1,
                completion_percentage=20
            ))
            db.commit()

        # 6. Ensure Academic Classes
        if db.query(AcademicClass).count() == 0:
            db.add_all([
                AcademicClass(name="AI & Machine Learning", code="AIML-301", semester=3, student_count=42),
                AcademicClass(name="Database Management Systems", code="DBMS-302", semester=3, student_count=48),
                AcademicClass(name="Python for Data Science", code="PYDS-303", semester=3, student_count=39),
                AcademicClass(name="Discrete Mathematics", code="MATH-304", semester=3, student_count=52),
            ])
            db.commit()

        # 7. Ensure Branches
        if db.query(AcademicBranch).count() == 0:
            db.add_all([
                AcademicBranch(name="Computer Science & Engineering", code="CSE"),
                AcademicBranch(name="Information Science & Engineering", code="ISE"),
                AcademicBranch(name="Artificial Intelligence & Machine Learning", code="AIML"),
                AcademicBranch(name="Data Science & Engineering", code="DSE"),
                AcademicBranch(name="Electronics & Communication", code="ECE"),
                AcademicBranch(name="Mechanical Engineering", code="MECH"),
                AcademicBranch(name="Civil Engineering", code="CIVIL"),
            ])
            db.commit()

        # 8. Ensure Subjects
        if db.query(AcademicSubject).count() == 0:
            db.add_all([
                AcademicSubject(name="Engineering Mathematics I", code="MATH-101", semester=1, branch="All Branches"),
                AcademicSubject(name="Engineering Physics", code="PHY-102", semester=1, branch="All Branches"),
                AcademicSubject(name="Engineering Mathematics II", code="MATH-201", semester=2, branch="All Branches"),
                AcademicSubject(name="Data Structures & Algorithms", code="DSA-202", semester=2, branch="Computer Science & Engineering"),
                AcademicSubject(name="Artificial Intelligence", code="AIML-301", semester=3, branch="Computer Science & Engineering"),
                AcademicSubject(name="Database Management Systems", code="DBMS-302", semester=3, branch="Computer Science & Engineering"),
                AcademicSubject(name="Python for Data Science", code="PYDS-303", semester=3, branch="Computer Science & Engineering"),
                AcademicSubject(name="Design & Analysis of Algorithms", code="DAA-401", semester=4, branch="Computer Science & Engineering"),
                AcademicSubject(name="Operating Systems & Concurrency", code="OS-402", semester=4, branch="Computer Science & Engineering"),
                AcademicSubject(name="Computer Networks & Protocols", code="CN-501", semester=5, branch="Computer Science & Engineering"),
                AcademicSubject(name="Machine Learning & Neural Nets", code="ML-601", semester=6, branch="Computer Science & Engineering"),
                AcademicSubject(name="Cloud Computing & DevOps", code="CLOUD-602", semester=6, branch="Computer Science & Engineering"),
                AcademicSubject(name="Distributed Systems & Security", code="DIST-702", semester=7, branch="Computer Science & Engineering"),
                AcademicSubject(name="Cap-Stone Project & Deep Learning", code="CAP-801", semester=8, branch="Computer Science & Engineering"),
            ])
            db.commit()

        # 9. Ensure Semester-Partitioned Materials (Semester 3, 4, 5)
        if db.query(StudyMaterial).count() == 0:
            db.add_all([
                StudyMaterial(
                    title="DBMS Unit 3 - Relational Algebra & Normalization", subject="Database Management Systems",
                    unit="Unit 3", pages=24, file_path="/docs/dbms_unit3.pdf", is_ai_approved=True,
                    semester=3, branch="Computer Science & Engineering", is_published=True,
                    content_text=(
                        "Relational algebra operations include Selection, Projection, Union, "
                        "Cartesian Product, and Joins. 1NF eliminates duplicate columns. "
                        "2NF removes partial dependencies. 3NF removes transitive dependencies "
                        "(X -> Y where Y is non-prime). BCNF requires that for every functional "
                        "dependency X -> Y, X must be a super key."
                    )
                ),
                StudyMaterial(
                    title="Neural Networks & Backpropagation Blueprint", subject="Artificial Intelligence",
                    unit="Unit 2", pages=36, file_path="/docs/ai_nn.pdf", is_ai_approved=True,
                    semester=3, branch="Computer Science & Engineering", is_published=True,
                    content_text=(
                        "Gradient descent minimizes loss function. Backpropagation uses chain rule "
                        "of calculus to compute gradient of cost with respect to each weight. "
                        "Activation functions include ReLU, Sigmoid, and GELU."
                    )
                ),
                StudyMaterial(
                    title="DAA Unit 2 - Dynamic Programming & Greedy Algorithms", subject="Design & Analysis of Algorithms",
                    unit="Unit 2", pages=32, file_path="/docs/daa_unit2_dp.pdf", is_ai_approved=True,
                    semester=4, branch="Computer Science & Engineering", is_published=True,
                    content_text=(
                        "Optimal substructure and overlapping subproblems in Dynamic Programming. "
                        "Bellman equation and memoization tables for Floyd-Warshall and Knapsack."
                    )
                ),
                StudyMaterial(
                    title="OS Unit 3 - Virtual Memory & Page Replacement Strategies", subject="Operating Systems & Concurrency",
                    unit="Unit 3", pages=28, file_path="/docs/os_unit3_paging.pdf", is_ai_approved=True,
                    semester=4, branch="Computer Science & Engineering", is_published=True,
                    content_text=(
                        "Page replacement algorithms: LRU, Optimal, FIFO, and Working Set model. "
                        "Handling page faults and thrashing in virtual memory subsystems."
                    )
                ),
                StudyMaterial(
                    title="Computer Networks - TCP Congestion Control & Flow Control", subject="Computer Networks & Protocols",
                    unit="Unit 4", pages=30, file_path="/docs/cn_tcp_congestion.pdf", is_ai_approved=True,
                    semester=5, branch="Computer Science & Engineering", is_published=True,
                    content_text=(
                        "TCP AIMD congestion avoidance, slow start, fast retransmit and fast recovery algorithms. "
                        "Sliding window protocols and round-trip time estimation."
                    )
                ),
            ])
            db.commit()

        # Seed flashcards if not present
        if db.query(FlashcardDeck).count() == 0:
            deck = FlashcardDeck(title="Core Computer Science & DBMS", subject="Computer Science", created_by=1)
            db.add(deck)
            db.flush()
            db.add_all([
                Flashcard(deck_id=deck.id, front="What is Boyce-Codd Normal Form (BCNF)?",
                          back="A relation is in BCNF if and only if for every non-trivial functional dependency X -> Y, X is a Super Key.", mastery_level=0),
                Flashcard(deck_id=deck.id, front="What mathematical theorem powers Backpropagation?",
                          back="The Chain Rule of Differential Calculus, allowing reverse propagation of loss gradients to update network weights.", mastery_level=1),
                Flashcard(deck_id=deck.id, front="How does NumPy achieve SIMD acceleration?",
                          back="By using contiguous memory blocks in C and hardware SIMD registers, removing Python loop interpretation overhead.", mastery_level=2),
            ])
            db.commit()

    finally:
        db.close()

# ==============================================================================
# 6. API ROUTERS (AUTH, PROFILE, MATERIALS, ADMIN)
# ==============================================================================

auth_router = APIRouter(prefix="/api/auth", tags=["Auth"])
portal_router = APIRouter(prefix="/api", tags=["Portal"])

def get_user_metadata(user: User, db: Session):
    semester = 3
    branch = "Computer Science & Engineering"
    teacher_id_code = None
    if user.role == "student" and user.student_profile:
        semester = user.student_profile.current_semester or 3
        branch = user.student_profile.branch or "Computer Science & Engineering"
    elif user.role == "teacher" and user.teacher_profile:
        teacher_id_code = user.teacher_profile.teacher_id_code
        branch = user.teacher_profile.department or "Computer Science & Engineering"
    return {
        "username": user.username,
        "is_profile_complete": bool(user.is_profile_complete),
        "approval_status": user.approval_status or "approved",
        "semester": semester,
        "branch": branch,
        "teacher_id_code": teacher_id_code,
        "application_id": user.application_id,
    }

@auth_router.get("/quick-token", response_model=TokenResponse)
def get_quick_token(role: str = "student", db: Session = Depends(get_db)):
    """Provides an immediate access token for development demo personas."""
    user = db.query(User).filter(User.role == role).first()
    if not user:
        user = db.query(User).first()
    token = create_access_token(data={"sub": user.email, "role": user.role, "id": user.id})
    meta = get_user_metadata(user, db)
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role,
        "full_name": user.full_name,
        "email": user.email,
        **meta
    }

@auth_router.post("/login", response_model=TokenResponse)
def login(payload: LoginPayload, db: Session = Depends(get_db)):
    ident = payload.email.strip()
    user = db.query(User).filter((User.email == ident) | (User.username == ident)).first()
    if not user or not user.is_active or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials. Please verify your email/username and password.")

    token = create_access_token(data={"sub": user.email, "role": user.role, "id": user.id})
    meta = get_user_metadata(user, db)
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role,
        "full_name": user.full_name,
        "email": user.email,
        **meta
    }

@auth_router.post("/register", response_model=TokenResponse)
def register_general(payload: RegisterPayload, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="An account with this email already exists.")
    username = payload.email.split("@")[0].lower()
    user = User(
        email=payload.email,
        username=username,
        hashed_password=get_password_hash(payload.password),
        full_name=payload.full_name or username,
        role=payload.role or "student",
        is_profile_complete=False,
        approval_status="approved" if payload.role != "teacher" else "pending"
    )
    db.add(user)
    db.flush()
    if user.role == "student":
        db.add(StudentProfile(
            user_id=user.id,
            enrollment_no=f"STU-2026-{user.id:03d}",
            usn=f"STU-2026-{user.id:03d}",
            current_semester=1,
            completion_percentage=20
        ))
    else:
        user.application_id = f"APP-2026-{uuid.uuid4().hex[:6].upper()}"
        db.add(TeacherProfile(
            user_id=user.id,
            department="Computer Science & Engineering",
            designation="Faculty Member"
        ))
    db.commit()
    token = create_access_token(data={"sub": user.email, "role": user.role, "id": user.id})
    meta = get_user_metadata(user, db)
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role,
        "full_name": user.full_name,
        "email": user.email,
        **meta
    }

@auth_router.post("/register/student", response_model=TokenResponse)
def register_student(payload: StudentRegisterPayload, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="An account with this email already exists.")
    if db.query(User).filter(User.username == payload.username.lower()).first():
        raise HTTPException(status_code=400, detail="This username is already taken. Please choose another.")

    user = User(
        email=payload.email,
        username=payload.username.lower().strip(),
        hashed_password=get_password_hash(payload.password),
        full_name=payload.full_name.strip(),
        role="student",
        email_verified=False,
        is_profile_complete=False,
        approval_status="approved",
        is_active=True
    )
    db.add(user)
    db.flush()
    db.add(StudentProfile(
        user_id=user.id,
        enrollment_no=f"STU-2026-{user.id:03d}",
        usn=f"STU-2026-{user.id:03d}",
        current_semester=1,
        completion_percentage=20
    ))
    db.commit()
    token = create_access_token(data={"sub": user.email, "role": user.role, "id": user.id})
    meta = get_user_metadata(user, db)
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role,
        "full_name": user.full_name,
        "email": user.email,
        **meta
    }

@auth_router.post("/register/teacher", response_model=TokenResponse)
def register_teacher(payload: TeacherRegisterPayload, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="An account with this email already exists.")
    if db.query(User).filter(User.username == payload.username.lower()).first():
        raise HTTPException(status_code=400, detail="This username is already taken. Please choose another.")

    app_id = f"APP-2026-{uuid.uuid4().hex[:6].upper()}"
    user = User(
        email=payload.email,
        username=payload.username.lower().strip(),
        hashed_password=get_password_hash(payload.password),
        full_name=payload.full_name.strip(),
        phone_number=payload.contact_number,
        role="teacher",
        approval_status="pending",
        application_id=app_id,
        email_verified=False,
        is_profile_complete=True,
        is_active=True
    )
    db.add(user)
    db.flush()
    db.add(TeacherProfile(
        user_id=user.id,
        college_name=payload.college_name,
        department=payload.department,
        designation=payload.designation,
        contact_number=payload.contact_number,
        experience_years=payload.experience_years or 5,
        invitation_code=payload.invitation_code
    ))
    db.add(AdminAuditLog(
        action="TEACHER_APPLICATION_SUBMITTED",
        target_user=user.email,
        details=f"Faculty application {app_id} submitted by {user.full_name} for department {payload.department}."
    ))
    db.commit()
    token = create_access_token(data={"sub": user.email, "role": user.role, "id": user.id})
    meta = get_user_metadata(user, db)
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role,
        "full_name": user.full_name,
        "email": user.email,
        **meta
    }

@auth_router.post("/verify-email")
def verify_email_api(payload: VerifyEmailPayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not payload.code or len(payload.code.strip()) < 4:
        raise HTTPException(status_code=400, detail="Please enter a valid 4-digit verification code.")
    user.email_verified = True
    db.commit()
    return {"status": "success", "message": "Email address verified successfully!"}

# ==============================================================================
# PROFILE MANAGEMENT APIS (PERSONAL & ACADEMIC)
# ==============================================================================

@portal_router.get("/profile/me")
def get_my_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    res = {
        "id": user.id,
        "full_name": user.full_name,
        "username": user.username or user.email.split("@")[0],
        "email": user.email,
        "role": user.role,
        "phone_number": user.phone_number or "",
        "avatar_url": user.avatar_url or "",
        "bio": user.bio or "",
        "city": user.city or "",
        "gender": user.gender or "",
        "date_of_birth": user.date_of_birth or "",
        "email_verified": bool(user.email_verified),
        "is_profile_complete": bool(user.is_profile_complete),
        "approval_status": user.approval_status or "approved",
        "rejection_reason": user.rejection_reason,
        "application_id": user.application_id,
        "academic": {},
        "teacher": {}
    }

    if user.role == "student" and user.student_profile:
        sp = user.student_profile
        res["academic"] = {
            "college_name": sp.college_name or "University Institute of Technology",
            "branch": sp.branch or "Computer Science & Engineering",
            "usn": sp.usn or sp.enrollment_no,
            "current_semester": sp.current_semester or 3,
            "year_of_completion": sp.year_of_completion or 2026,
            "academic_interests": sp.academic_interests or "",
            "technical_skills": sp.technical_skills or "",
            "career_interests": sp.career_interests or "",
            "completion_percentage": sp.completion_percentage or 0,
            "study_streak_days": sp.study_streak_days,
            "attendance": sp.overall_attendance,
            "progress": sp.overall_progress
        }
    elif user.role == "teacher" and user.teacher_profile:
        tp = user.teacher_profile
        res["teacher"] = {
            "teacher_id_code": tp.teacher_id_code,
            "college_name": tp.college_name,
            "department": tp.department,
            "designation": tp.designation,
            "contact_number": tp.contact_number,
            "experience_years": tp.experience_years,
            "assigned_semesters": tp.assigned_semesters,
            "assigned_subjects": tp.assigned_subjects
        }
    return res

def calculate_student_completion(user: User, sp: StudentProfile) -> int:
    fields = [
        bool(user.full_name),
        bool(user.username),
        bool(user.gender),
        bool(user.city),
        bool(user.date_of_birth),
        bool(sp.college_name),
        bool(sp.branch),
        bool(sp.usn),
        bool(sp.current_semester),
        bool(sp.year_of_completion),
    ]
    completed = sum(1 for f in fields if f)
    pct = int((completed / len(fields)) * 100)
    return pct

@portal_router.put("/profile/personal")
def update_personal_profile(payload: PersonalProfilePayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    new_uname = payload.username.lower().strip()
    if new_uname != (user.username or ""):
        existing = db.query(User).filter(User.username == new_uname, User.id != user.id).first()
        if existing:
            raise HTTPException(status_code=400, detail="This username is already taken.")

    user.full_name = payload.full_name.strip()
    user.username = new_uname
    user.bio = (payload.bio or "").strip()
    user.city = (payload.city or "").strip()
    user.gender = payload.gender or user.gender
    user.date_of_birth = payload.date_of_birth or user.date_of_birth
    if payload.avatar_url:
        user.avatar_url = payload.avatar_url

    if user.student_profile:
        user.student_profile.completion_percentage = calculate_student_completion(user, user.student_profile)

    db.commit()
    return {"status": "success", "message": "Personal profile updated successfully."}

@portal_router.put("/profile/academic")
def update_academic_profile(payload: AcademicProfilePayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role != "student":
        raise HTTPException(status_code=403, detail="Academic profile is reserved for student accounts.")

    sp = user.student_profile
    if not sp:
        sp = StudentProfile(user_id=user.id, enrollment_no=f"STU-2026-{user.id:03d}")
        db.add(sp)

    # Validate compulsory fields
    if not payload.college_name.strip():
        raise HTTPException(status_code=400, detail="College / Institution Name is compulsory.")
    if not payload.branch.strip():
        raise HTTPException(status_code=400, detail="Branch / Department is compulsory.")
    if not payload.usn.strip():
        raise HTTPException(status_code=400, detail="USN / Student Number is compulsory.")
    if payload.current_semester < 1 or payload.current_semester > 8:
        raise HTTPException(status_code=400, detail="Current Semester must be between Semester 1 and 8.")
    if payload.year_of_completion < 2024 or payload.year_of_completion > 2035:
        raise HTTPException(status_code=400, detail="Please select a valid completion year.")

    sp.college_name = payload.college_name.strip()
    sp.branch = payload.branch.strip()
    sp.usn = payload.usn.strip()
    sp.current_semester = payload.current_semester
    sp.year_of_completion = payload.year_of_completion
    sp.academic_interests = payload.academic_interests or ""
    sp.technical_skills = payload.technical_skills or ""
    sp.career_interests = payload.career_interests or ""

    sp.completion_percentage = calculate_student_completion(user, sp)
    # Check if compulsory fields are satisfied
    if sp.college_name and sp.branch and sp.usn and sp.current_semester and sp.year_of_completion and user.gender:
        user.is_profile_complete = True
    else:
        user.is_profile_complete = False

    db.commit()
    return {
        "status": "success",
        "message": "Academic details saved successfully.",
        "is_profile_complete": user.is_profile_complete,
        "completion_percentage": sp.completion_percentage
    }

@portal_router.post("/profile/avatar")
def update_avatar_api(payload: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    avatar = payload.get("avatar_url")
    if not avatar:
        raise HTTPException(status_code=400, detail="No avatar provided.")
    user.avatar_url = avatar
    db.commit()
    return {"status": "success", "avatar_url": user.avatar_url}

# ==============================================================================

@portal_router.get("/dashboard/student")
def student_dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()
    return {
        "overall_progress": p.overall_progress if p else 0.0,
        "attendance": p.overall_attendance if p else 100.0,
        "tests_completed": 12,
        "average_score": p.average_score if p else 0.0,
        "study_streak_days": p.study_streak_days if p else 0,
        "pending_assignments": 2,
        "weak_areas": ["Relational Calculus", "Loss Convergence Functions"],
        "subjects": [
            {"name": "Artificial Intelligence", "progress": 82, "code": "AIML-301"},
            {"name": "Database Management Systems", "progress": 84, "code": "DBMS-302"},
            {"name": "Python Programming", "progress": 76, "code": "PYDS-303"},
            {"name": "Discrete Mathematics", "progress": 91, "code": "MATH-304"},
        ],
    }

@portal_router.get("/dashboard/teacher")
def teacher_dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role not in ["teacher", "admin"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    classes = db.query(AcademicClass).all()
    return {
        "total_students": sum(c.student_count for c in classes),
        "total_classes": len(classes),
        "average_attendance": 94.2,
        "pending_tests": 4,
        "system_alerts": 2,
        "average_performance": 82.5,
        "weak_topics": ["B-Tree Indexes", "Eigenvalues in PCA"],
        "classes": [{"id": c.id, "name": c.name, "code": c.code, "students": c.student_count} for c in classes],
    }

@portal_router.get("/materials")
def list_materials(
    semester: Optional[int] = None,
    branch: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    query = db.query(StudyMaterial).filter(StudyMaterial.is_published == True)

    if user.role == "student":
        sp = user.student_profile
        if not user.is_profile_complete or not sp:
            raise HTTPException(
                status_code=403,
                detail="Student profile incomplete. Please complete your academic profile to access semester materials."
            )
        stud_sem = sp.current_semester or 3
        stud_branch = sp.branch or "Computer Science & Engineering"
        query = query.filter((StudyMaterial.semester == stud_sem) | (StudyMaterial.semester == 0))
    elif user.role in ["teacher", "admin"]:
        if semester is not None and semester > 0:
            query = query.filter((StudyMaterial.semester == semester) | (StudyMaterial.semester == 0))
        if branch and branch != "all":
            query = query.filter((StudyMaterial.branch == branch) | (StudyMaterial.branch == "All Branches"))

    return query.order_by(StudyMaterial.id.desc()).all()

@portal_router.get("/materials/{material_id}")
def get_single_material(material_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    mat = db.query(StudyMaterial).filter(StudyMaterial.id == material_id).first()
    if not mat:
        raise HTTPException(status_code=404, detail="Material not found.")

    if user.role == "student":
        sp = user.student_profile
        stud_sem = sp.current_semester if sp else 3
        if mat.semester != 0 and mat.semester != stud_sem:
            raise HTTPException(
                status_code=403,
                detail=f"Access Denied: This curriculum document belongs to Semester {mat.semester}. You are enrolled in Semester {stud_sem}."
            )
    return mat

@portal_router.post("/materials")
def upload_material(payload: MaterialCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role not in ["teacher", "admin"]:
        raise HTTPException(status_code=403, detail="Only faculty can publish materials")

    mat = StudyMaterial(
        title=payload.title,
        subject=payload.subject,
        unit=payload.unit,
        pages=payload.pages,
        file_path=f"/docs/{payload.title.lower().replace(' ', '_')}.pdf",
        content_text=f"Official course notes extracted for {payload.title}.",
        semester=payload.semester if payload.semester is not None else 3,
        branch=payload.branch or "Computer Science & Engineering",
        teacher_id=user.id,
        is_published=True,
        is_ai_approved=True,
    )
    db.add(mat)
    db.commit()
    db.refresh(mat)
    return mat

@portal_router.post("/materials/upload-file")
async def upload_large_material(
    title: str = Form(...),
    subject: str = Form(...),
    unit: str = Form("Unit 1"),
    pages: int = Form(1),
    semester: Optional[int] = Form(3),
    branch: Optional[str] = Form("Computer Science & Engineering"),
    is_ai_approved: Optional[bool] = Form(True),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if user.role not in ["teacher", "admin"]:
        raise HTTPException(status_code=403, detail="Only faculty can publish materials")

    safe_filename = re.sub(r'[^a-zA-Z0-9_.-]', '_', file.filename or "uploaded_asset.bin")
    unique_filename = f"{uuid.uuid4().hex[:8]}_{safe_filename}"
    file_path = os.path.join(UPLOAD_DIR, unique_filename)

    total_bytes = 0
    try:
        with open(file_path, "wb") as buffer:
            while chunk := await file.read(CHUNK_SIZE_BYTES):
                total_bytes += len(chunk)
                if total_bytes > MAX_FILE_SIZE_BYTES:
                    buffer.close()
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    raise HTTPException(status_code=413, detail="File size exceeds the 1 GB maximum limit.")
                buffer.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=500, detail=f"Failed to stream and store file: {str(e)}")

    size_mb = round(total_bytes / (1024 * 1024), 2)
    display_path = f"/uploaded_materials/{unique_filename}"
    content_text = (
        f"Curriculum asset '{safe_filename}' ({size_mb} MB) uploaded for {title} in {subject} ({unit}). "
        "Streamed directly to disk and available for student download and grounded AI tutoring."
    )

    mat = StudyMaterial(
        title=title,
        subject=subject,
        unit=unit,
        pages=pages if pages > 0 else max(1, int(size_mb / 2)),
        file_path=display_path,
        content_text=content_text,
        semester=semester if semester is not None else 3,
        branch=branch or "Computer Science & Engineering",
        teacher_id=user.id,
        is_ai_approved=is_ai_approved if is_ai_approved is not None else True,
    )
    db.add(mat)
    db.commit()
    db.refresh(mat)

    return {
        "id": mat.id,
        "title": mat.title,
        "subject": mat.subject,
        "unit": mat.unit,
        "pages": mat.pages,
        "file_path": mat.file_path,
        "file_size_mb": size_mb,
        "content_text": mat.content_text,
        "message": f"Successfully uploaded and indexed '{safe_filename}' ({size_mb} MB)"
    }

@portal_router.post("/ai/chat")
def ai_tutor_chat(payload: AIChatPayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = payload.message.lower().strip()
    mat = db.query(StudyMaterial).filter(StudyMaterial.is_ai_approved == True).first()

    if any(kw in q for kw in ["normalize", "1nf", "2nf", "3nf", "bcnf"]):
        reply = (
            "According to the verified curriculum:\n"
            "• **1NF**: Enforces attribute atomicity and removes repeating groups.\n"
            "• **2NF**: Satisfies 1NF and removes partial dependencies on candidate keys.\n"
            "• **3NF**: Satisfies 2NF and removes transitive dependencies (X -> Y where Y is non-prime).\n"
            "• **BCNF**: For every functional dependency X -> Y, X must strictly be a super key."
        )
        src = mat.title if mat else "DBMS Curriculum"
    elif any(kw in q for kw in ["neural", "backprop", "gradient"]):
        reply = (
            "Based on the neural network curriculum:\n"
            "Backpropagation uses the calculus chain rule recursively backwards from the loss function "
            "to compute weight gradients, iteratively minimizing cost via gradient descent."
        )
        src = "Neural Networks & Backpropagation Blueprint"
    elif any(kw in q for kw in ["vector", "numpy"]):
        reply = (
            "Vectorization replaces interpreted Python loops with contiguous, SIMD C-level loops "
            "optimizing memory bandwidth and execution cycles."
        )
        src = "NumPy Array Broadcasting & Vectorization"
    else:
        reply = (
            f"Here is a syllabus-grounded summary regarding '{payload.message}': "
            "Focus on rigorous definitions, mechanical derivation steps, and clear diagrams "
            "for complete academic evaluations."
        )
        src = mat.title if mat else "EduNova Verified Curriculum"

    return {"reply": reply, "source": src, "verified": True}

@portal_router.get("/tests")
def list_tests(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    tests = db.query(TestAssessment).all()
    mock_questions = [
        {"id": 1, "prompt": "Which normal form requires that for every functional dependency X -> Y, X is a Super Key?",
         "options": ["1NF", "2NF", "3NF", "BCNF"], "correct": 3},
        {"id": 2, "prompt": "What mathematical principle underpins backpropagation in deep neural networks?",
         "options": ["Bayes' Theorem", "Calculus Chain Rule", "Gauss Elimination", "Markov Decision Process"], "correct": 1},
        {"id": 3, "prompt": "In relational algebra, which operator selects rows that satisfy a specified predicate?",
         "options": ["Projection (π)", "Selection (σ)", "Cartesian Product (×)", "Intersection (∩)"], "correct": 1},
        {"id": 4, "prompt": "Why does NumPy array vectorization execute drastically faster than standard Python loops?",
         "options": ["Threaded Coroutines", "SIMD contiguous memory execution", "No type checking", "Float conversion"], "correct": 1},
        {"id": 5, "prompt": "Transitive dependency is formally eliminated in which normal form?",
         "options": ["1NF", "2NF", "3NF", "4NF"], "correct": 2},
    ]
    return {"tests": tests, "mock_questions": mock_questions}

@portal_router.post("/tests/submit")
def submit_test(payload: TestSubmitPayload, user: User = Depends(get_current_user)):
    key = {0: 3, 1: 1, 2: 1, 3: 1, 4: 2}
    correct, breakdown = 0, []
    for i in range(5):
        sel = payload.answers.get(str(i))
        ok = sel == key[i]
        if ok:
            correct += 1
        breakdown.append({
            "question": f"Question {i + 1}",
            "selected": sel,
            "correct": key[i],
            "status": "Correct" if ok else ("Skipped" if sel is None else "Incorrect")
        })
    score = int((correct / 5) * 100)
    return {"score": score, "total": 100, "correct": correct, "incorrect": 5 - correct, "accuracy": score, "breakdown": breakdown}

# ------------------------------------------------------------------------------
# Coding Test, Multi-Language Runner, Aptitude & Teacher Question Control APIs
# ------------------------------------------------------------------------------

@portal_router.get("/tests/bank")
def get_test_bank(user: User = Depends(get_current_user)):
    return load_bank()

@portal_router.post("/tests/code/run")
def run_code_api(payload: CodeRunPayload, user: User = Depends(get_current_user)):
    bank = load_bank()
    tests = []
    if payload.question_id:
        q = next((item for item in bank.get("coding", []) if item["id"] == payload.question_id), None)
        if q:
            tests = q.get("tests", [])
            if not tests:
                tests = [{"input": q.get("input", ""), "output": q.get("expected_output", "")}]
    
    if not tests:
        tests = [{"input": payload.stdin_text or "", "output": ""}]

    passed = 0
    logs = []
    for i, test in enumerate(tests, 1):
        res = code_runner.run(payload.language, payload.code, test.get("input", ""))
        if not res["ok"]:
            logs.append(f"TEST CASE {i}: FAILED\n{res['error']}\n")
            return {
                "ok": False,
                "passed": passed,
                "total": len(tests),
                "summary": f"Failed at Test Case {i}",
                "logs": logs,
                "error": res["error"]
            }
        
        actual = normalize_output(res["output"])
        expected = normalize_output(test.get("output", ""))
        if expected and actual == expected:
            passed += 1
            logs.append(f"TEST CASE {i}: ✓ PASSED")
        elif expected:
            logs.append(f"TEST CASE {i}: ✗ FAILED\nExpected:\n{expected}\nActual:\n{actual}")
        else:
            logs.append(f"EXECUTION OUTPUT:\n{res['output']}")
            passed += 1

    all_passed = (passed == len(tests))
    summary = f"{'✓ ALL TESTS PASSED' if all_passed else '✗ SOME TESTS FAILED'}\nPassed: {passed}/{len(tests)}"
    return {
        "ok": all_passed,
        "passed": passed,
        "total": len(tests),
        "summary": summary,
        "logs": logs
    }

@portal_router.post("/tests/code/submit")
def submit_code_api(payload: CodeSubmitPayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bank = load_bank()
    q = next((item for item in bank.get("coding", []) if item["id"] == payload.question_id), None)
    if not q:
        raise HTTPException(status_code=404, detail="Coding question not found")

    tests = q.get("tests", [])
    if not tests:
        tests = [{"input": q.get("input", ""), "output": q.get("expected_output", "")}]

    passed = 0
    first_error = ""
    for i, test in enumerate(tests, 1):
        res = code_runner.run(payload.language, payload.code, test.get("input", ""))
        if not res["ok"]:
            first_error = f"Test case {i} failed with runtime error:\n{res['error']}"
            break
        
        if normalize_output(res["output"]) == normalize_output(test.get("output", "")):
            passed += 1
        else:
            first_error = f"Test case {i} failed.\nExpected:\n{test.get('output', '')}\nActual:\n{res.get('output', '')}"
            break

    total = len(tests)
    accepted = (passed == total)
    message = (
        f"✓ ACCEPTED\nLanguage: {payload.language}\nTest cases: {passed}/{total}\nQuestion: {q.get('title', '')}"
        if accepted else
        f"✗ REJECTED\nLanguage: {payload.language}\nPassed: {passed}/{total}\n\n{first_error}"
    )

    return {
        "accepted": accepted,
        "passed": passed,
        "total": total,
        "message": message,
        "question_id": payload.question_id
    }

@portal_router.get("/tests/aptitude")
def get_aptitude_questions_api(user: User = Depends(get_current_user)):
    bank = load_bank()
    available = list(bank.get("mcq", []))
    if len(available) >= APTITUDE_COUNT:
        selected = random.sample(available, APTITUDE_COUNT)
    else:
        selected = available[:]
    client_questions = []
    for q in selected:
        client_questions.append({
            "id": q.get("id"),
            "question": q.get("question"),
            "options": q.get("options", []),
            "topic": q.get("topic", "Problem Solving"),
            "answer": q.get("answer")
        })
    return {"count": len(client_questions), "questions": client_questions}

@portal_router.post("/tests/aptitude/submit")
def submit_aptitude_api(payload: AptitudeSubmitPayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bank = load_bank()
    mcq_map = {q["id"]: q for q in bank.get("mcq", [])}
    
    score = 0
    total = len(payload.answers)
    breakdown = []
    for qid, user_choice in payload.answers.items():
        q = mcq_map.get(str(qid))
        if q:
            correct_ans = safe_int(q.get("answer"), 0)
            is_correct = (user_choice == correct_ans)
            if is_correct:
                score += 1
            breakdown.append({
                "id": qid,
                "question": q.get("question"),
                "selected": user_choice,
                "correct": correct_ans,
                "status": "Correct" if is_correct else "Incorrect"
            })
    
    pct = round((score / max(1, total)) * 100, 1)
    
    try:
        attempt = UserTestAttempt(
            user_id=user.id,
            test_id=1,
            score=pct,
            total_questions=total,
            correct_count=score
        )
        db.add(attempt)
        db.commit()
    except Exception:
        db.rollback()

    return {
        "score": score,
        "total": total,
        "percentage": pct,
        "breakdown": breakdown
    }

@portal_router.post("/teacher/mcq")
def add_teacher_mcq(payload: TeacherMCQPayload, user: User = Depends(get_current_user)):
    if user.role not in ["teacher", "admin"]:
        raise HTTPException(status_code=403, detail="Forbidden: Teachers only")
    bank = load_bank()
    new_id = f"M{len(bank['mcq']) + 1:03d}"
    bank["mcq"].append({
        "id": new_id,
        "question": payload.question.strip(),
        "options": [opt.strip() for opt in payload.options],
        "answer": payload.answer,
        "topic": payload.topic.strip() or "Problem Solving"
    })
    save_bank(bank)
    return {"status": "success", "id": new_id, "total_mcq": len(bank["mcq"])}

@portal_router.post("/teacher/coding")
def add_teacher_coding(payload: TeacherCodingPayload, user: User = Depends(get_current_user)):
    if user.role not in ["teacher", "admin"]:
        raise HTTPException(status_code=403, detail="Forbidden: Teachers only")
    
    tests = []
    if payload.tests_raw:
        blocks = [b.strip() for b in payload.tests_raw.split("\n\n") if b.strip()]
        for block in blocks:
            if "=>" in block:
                first, rest = block.split("=>", 1)
                tests.append({"input": first.strip(), "output": rest.strip()})
    
    if not tests and payload.input and payload.expected_output:
        tests.append({"input": payload.input.strip(), "output": payload.expected_output.strip()})
        
    bank = load_bank()
    new_id = f"C{len(bank['coding']) + 1:03d}"
    bank["coding"].append({
        "id": new_id,
        "title": payload.title.strip(),
        "difficulty": payload.difficulty or "Medium",
        "description": payload.description.strip(),
        "input": payload.input.strip(),
        "expected_output": payload.expected_output.strip(),
        "tests": tests
    })
    save_bank(bank)
    return {"status": "success", "id": new_id, "total_coding": len(bank["coding"])}

@portal_router.post("/teacher/pdf-import")
async def import_teacher_pdf(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    if user.role not in ["teacher", "admin"]:
        raise HTTPException(status_code=403, detail="Forbidden: Teachers only")
    if PdfReader is None:
        raise HTTPException(status_code=500, detail="pypdf is not installed on the server. Install via: pip install pypdf")
    
    content = await file.read()
    import io
    reader = PdfReader(io.BytesIO(content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    draft = parse_teacher_pdf(text)
    return {
        "filename": file.filename,
        "mcq_count": len(draft["mcq"]),
        "coding_count": len(draft["coding"]),
        "draft": draft,
        "preview_text": text[:5000]
    }

@portal_router.post("/teacher/pdf-publish")
def publish_teacher_pdf(payload: TeacherPDFPublishPayload, user: User = Depends(get_current_user)):
    if user.role not in ["teacher", "admin"]:
        raise HTTPException(status_code=403, detail="Forbidden: Teachers only")
    bank = load_bank()
    bank["mcq"].extend(payload.mcq)
    bank["coding"].extend(payload.coding)
    save_bank(bank)
    return {
        "status": "success",
        "published_mcq": len(payload.mcq),
        "published_coding": len(payload.coding),
        "total_mcq": len(bank["mcq"]),
        "total_coding": len(bank["coding"])
    }


@portal_router.get("/attendance")
def get_attendance(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    students = [
        {"id": 1, "name": "Alex Mercer", "roll": "STU-2026-042", "attendance": 95, "present": 38, "late": 2, "absent": 0, "status": "Present"},
        {"id": 2, "name": "Devin Torres", "roll": "STU-2026-003", "attendance": 88, "present": 35, "late": 3, "absent": 2, "status": "Present"},
        {"id": 3, "name": "Maya Lin", "roll": "STU-2026-017", "attendance": 76, "present": 30, "late": 5, "absent": 4, "status": "Late"},
        {"id": 4, "name": "Farhan Siddiqui", "roll": "STU-2026-029", "attendance": 68, "present": 27, "late": 6, "absent": 7, "status": "Absent"},
        {"id": 5, "name": "Hannah Abbott", "roll": "STU-2026-051", "attendance": 93, "present": 37, "late": 2, "absent": 1, "status": "Present"},
    ]

    return {
        "period": {"start": "Apr 1, 2026", "end": "Apr 30, 2026"},
        "semester": "Semester 6",
        "class_name": "All Classes",
        "stats": {
            "overall": 92,
            "present": 1386,
            "late": 48,
            "absent": 72,
            "risk_students": 5,
            "last_month_change": 2
        },
        "trend": [80, 81, 82, 89, 85, 84, 86, 81, 85, 84, 84, 80, 84, 83, 87, 88, 86, 84, 80, 85, 84, 85, 88, 89],
        "students": students,
    }

@portal_router.get("/attendance/overview")
def get_attendance_overview(
    period: str = "30d",
    class_code: str = "all",
    semester: int = 3,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    days = 7 if period == "7d" else (90 if period == "90d" else 30)
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days)
    period_info = {"start": start_date.isoformat(), "end": end_date.isoformat()}

    classes = db.query(AcademicClass).filter(AcademicClass.semester == semester).all()
    if not classes:
        classes = db.query(AcademicClass).all()
    class_list = [{"code": c.code, "name": c.name} for c in classes]

    today_str = end_date.isoformat()
    today_records = {
        r.student_name: r.status
        for r in db.query(AttendanceRecord).filter(AttendanceRecord.date == today_str).all()
    }

    base_students = [
        {"id": 1, "name": "Alex Mercer", "roll": "STU-2026-042", "rate": 0.95, "today": "Present"},
        {"id": 2, "name": "Devin Torres", "roll": "STU-2026-003", "rate": 0.88, "today": "Present"},
        {"id": 3, "name": "Maya Lin", "roll": "STU-2026-017", "rate": 0.74, "today": "Late"},
        {"id": 4, "name": "Farhan Siddiqui", "roll": "STU-2026-029", "rate": 0.68, "today": "Absent"},
        {"id": 5, "name": "Hannah Abbott", "roll": "STU-2026-051", "rate": 0.93, "today": "Present"},
    ]

    total_sessions = max(5, int(days * 1.3))
    students_data = []
    for s in base_students:
        present_count = int(round(total_sessions * s["rate"]))
        rem = total_sessions - present_count
        late_count = max(0, int(round(rem * 0.4)))
        absent_count = max(0, rem - late_count)
        status = today_records.get(s["name"], s["today"])
        students_data.append({
            "id": s["id"],
            "name": s["name"],
            "roll": s["roll"],
            "present": present_count,
            "late": late_count,
            "absent": absent_count,
            "today_status": status,
        })

    num_points = min(days, 15 if days > 15 else days)
    step = max(1, days // num_points)
    trend_points = []
    base_trend_values = [89, 91, 90, 88, 92, 93, 91, 94, 92, 95, 93, 94, 96, 95, 94]
    for i in range(num_points):
        d = start_date + timedelta(days=i * step)
        val = base_trend_values[i % len(base_trend_values)]
        trend_points.append({"label": d.strftime("%b %d"), "value": val})

    delta = 1.8 if period == "30d" else (0.8 if period == "7d" else -0.5)

    return {
        "period": period_info,
        "classes": class_list,
        "students": students_data,
        "trend": trend_points,
        "delta": delta,
    }

@portal_router.post("/attendance/save")
def save_attendance(payload: AttendanceBatchPayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role not in ["teacher", "admin"]:
        raise HTTPException(status_code=403, detail="Only teachers can submit attendance")
    for item in payload.records:
        db.add(AttendanceRecord(student_name=item.student_name, class_name=item.class_name, date=item.date, status=item.status))
    db.commit()
    return {"status": "success", "message": f"Recorded attendance for {len(payload.records)} students."}

@portal_router.get("/notifications")
def get_notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Notification).order_by(Notification.id.desc()).all()

@portal_router.patch("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    notif = db.query(Notification).filter(Notification.id == notification_id).first()
    if notif:
        notif.is_read = True
        db.commit()
    return {"status": "success"}

@portal_router.post("/notifications/read-all")
def mark_all_notifications_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(Notification).update({Notification.is_read: True})
    db.commit()
    return {"status": "success"}

# ------------------------------------------------------------------------------
# Flashcards & Spaced Repetition Engine
# ------------------------------------------------------------------------------

@portal_router.get("/flashcards/{deck_id}")
def get_deck_flashcards(deck_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cards = db.query(Flashcard).filter(Flashcard.deck_id == deck_id).all()
    if not cards:
        cards = db.query(Flashcard).all()
    return cards

@portal_router.post("/flashcards/answer")
def record_flashcard_review(payload: FlashcardAnswerPayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    card = db.query(Flashcard).filter(Flashcard.id == payload.card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Flashcard not found")
    
    if payload.remembered:
        card.mastery_level = min(2, card.mastery_level + 1)
    else:
        card.mastery_level = max(0, card.mastery_level - 1)
    
    db.commit()
    return {"card_id": card.id, "new_level": card.mastery_level}


# ------------------------------------------------------------------------------
# Faculty Early Warning & Risk Analysis
# ------------------------------------------------------------------------------

@portal_router.get("/faculty/at-risk-students")
def get_at_risk_students(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role not in ["teacher", "admin"]:
        raise HTTPException(status_code=403, detail="Forbidden")

    students = (
        db.query(StudentProfile, User)
        .join(User, StudentProfile.user_id == User.id)
        .filter((StudentProfile.overall_attendance < 75.0) | (StudentProfile.average_score < 60.0))
        .all()
    )

    risk_list = []
    for profile, u in students:
        reasons = []
        if profile.overall_attendance < 75.0:
            reasons.append(f"Low Attendance ({profile.overall_attendance}%)")
        if profile.average_score < 60.0:
            reasons.append(f"Sub-par Average Score ({profile.average_score}%)")
        
        risk_list.append({
            "student_id": u.id,
            "name": u.full_name,
            "email": u.email,
            "enrollment_no": profile.enrollment_no,
            "attendance": profile.overall_attendance,
            "score": profile.average_score,
            "risk_factors": reasons
        })

    return {"count": len(risk_list), "at_risk_students": risk_list}


# ------------------------------------------------------------------------------
# In-Memory Semantic Text Search (Zero-Dependency TF-IDF RAG Engine)
# ------------------------------------------------------------------------------

def compute_similarity(query: str, document: str) -> float:
    def tokenize(text):
        return re.findall(r'\b[a-z0-9]{3,}\b', text.lower())

    q_words = set(tokenize(query))
    d_words = tokenize(document)
    if not q_words or not d_words:
        return 0.0

    match_count = sum(1 for w in d_words if w in q_words)
    return match_count / (math.sqrt(len(q_words)) * math.sqrt(len(d_words)) + 1e-5)

@portal_router.post("/ai/grounded-chat")
def rag_search_tutor(payload: AIChatPayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    materials = db.query(StudyMaterial).filter(StudyMaterial.is_ai_approved == True).all()
    best_material = None
    best_score = 0.0

    for m in materials:
        if m.content_text:
            score = compute_similarity(payload.message, m.content_text)
            if score > best_score:
                best_score = score
                best_material = m

    if best_score > 0.08 and best_material:
        answer = (
            f"**Verified Extract from {best_material.subject} ({best_material.unit}):**\n\n"
            f"> \"{best_material.content_text}\"\n\n"
            f"*Confidence Match: {min(99.0, round(best_score * 350, 1))}% | File: {best_material.file_path}*"
        )
        return {"reply": answer, "source": best_material.title, "verified": True}

    # Fallback to standard knowledge retrieval
    return {
        "reply": f"No high-confidence passage found in uploaded notes for '{payload.message}'. Consult official syllabus reference books.",
        "source": "General Academic Reference",
        "verified": False
    }

# ADMIN OVERSIGHT & PLATFORM CONTROL APIS
# ==============================================================================

def verify_admin(user: User):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator privileges required.")

@portal_router.get("/admin/overview")
def admin_overview(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    verify_admin(user)
    students_count = db.query(User).filter(User.role == "student").count()
    teachers_approved = db.query(User).filter(User.role == "teacher", User.approval_status == "approved").count()
    teachers_pending = db.query(User).filter(User.role == "teacher", User.approval_status == "pending").count()
    materials_count = db.query(StudyMaterial).count()
    audit_count = db.query(AdminAuditLog).count()

    sem_dist = {}
    for sem in range(1, 9):
        sem_dist[f"Sem {sem}"] = db.query(StudentProfile).filter(StudentProfile.current_semester == sem).count()

    return {
        "students_count": students_count,
        "teachers_approved": teachers_approved,
        "teachers_pending": teachers_pending,
        "materials_count": materials_count,
        "audit_count": audit_count,
        "semester_distribution": sem_dist
    }

@portal_router.get("/admin/teachers/pending")
def list_pending_teachers(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    verify_admin(user)
    records = (
        db.query(User, TeacherProfile)
        .outerjoin(TeacherProfile, User.id == TeacherProfile.user_id)
        .filter(User.role == "teacher", User.approval_status == "pending")
        .all()
    )
    res = []
    for u, tp in records:
        res.append({
            "user_id": u.id,
            "full_name": u.full_name,
            "email": u.email,
            "username": u.username,
            "phone_number": u.phone_number,
            "application_id": u.application_id,
            "college_name": tp.college_name if tp else "Metropolitan Institute",
            "department": tp.department if tp else "Engineering",
            "designation": tp.designation if tp else "Assistant Professor",
            "experience_years": tp.experience_years if tp else 5,
            "invitation_code": tp.invitation_code if tp else "",
            "created_at": u.created_at.isoformat() if u.created_at else "Recently"
        })
    return res

@portal_router.post("/admin/teachers/{user_id}/approve")
def approve_teacher_api(user_id: int, payload: TeacherApprovalPayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    verify_admin(user)
    target = db.query(User).filter(User.id == user_id, User.role == "teacher").first()
    if not target:
        raise HTTPException(status_code=404, detail="Teacher not found.")

    target.approval_status = "approved"
    target.is_active = True
    tp = target.teacher_profile
    if not tp:
        tp = TeacherProfile(user_id=target.id)
        db.add(tp)

    assigned_code = payload.teacher_id_code or f"TEA-2026-{target.id:03d}"
    tp.teacher_id_code = assigned_code
    if payload.assigned_semesters:
        tp.assigned_semesters = payload.assigned_semesters
    if payload.assigned_subjects:
        tp.assigned_subjects = payload.assigned_subjects

    db.add(AdminAuditLog(
        admin_id=user.id,
        action="APPROVE_TEACHER",
        target_user=target.email,
        details=f"Approved faculty {target.full_name} and assigned Teacher ID {assigned_code}."
    ))
    db.commit()
    return {"status": "success", "message": f"Approved {target.full_name} with Teacher ID {assigned_code}."}

@portal_router.post("/admin/teachers/{user_id}/reject")
def reject_teacher_api(user_id: int, payload: TeacherRejectionPayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    verify_admin(user)
    target = db.query(User).filter(User.id == user_id, User.role == "teacher").first()
    if not target:
        raise HTTPException(status_code=404, detail="Teacher not found.")

    target.approval_status = "rejected"
    target.rejection_reason = payload.reason.strip()

    db.add(AdminAuditLog(
        admin_id=user.id,
        action="REJECT_TEACHER",
        target_user=target.email,
        details=f"Rejected faculty application for {target.full_name}. Reason: {payload.reason}"
    ))
    db.commit()
    return {"status": "success", "message": f"Application for {target.full_name} marked as rejected."}

@portal_router.get("/admin/teachers")
def list_all_teachers(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    verify_admin(user)
    records = (
        db.query(User, TeacherProfile)
        .outerjoin(TeacherProfile, User.id == TeacherProfile.user_id)
        .filter(User.role == "teacher")
        .all()
    )
    res = []
    for u, tp in records:
        res.append({
            "id": u.id,
            "full_name": u.full_name,
            "email": u.email,
            "teacher_id_code": tp.teacher_id_code if tp else "-",
            "department": tp.department if tp else "Computer Science",
            "designation": tp.designation if tp else "Faculty",
            "approval_status": u.approval_status,
            "is_active": bool(u.is_active),
            "assigned_semesters": tp.assigned_semesters if tp else "All"
        })
    return res

@portal_router.get("/admin/students")
def list_all_students(
    q: Optional[str] = None,
    semester: Optional[int] = None,
    branch: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    verify_admin(user)
    query = (
        db.query(User, StudentProfile)
        .outerjoin(StudentProfile, User.id == StudentProfile.user_id)
        .filter(User.role == "student")
    )
    if q:
        kw = f"%{q.strip()}%"
        query = query.filter((User.full_name.ilike(kw)) | (User.email.ilike(kw)) | (User.username.ilike(kw)) | (StudentProfile.usn.ilike(kw)))
    if semester and semester > 0:
        query = query.filter(StudentProfile.current_semester == semester)
    if branch and branch != "all":
        query = query.filter(StudentProfile.branch == branch)

    records = query.all()
    res = []
    for u, sp in records:
        res.append({
            "id": u.id,
            "full_name": u.full_name,
            "email": u.email,
            "username": u.username,
            "usn": sp.usn if sp else u.email.split("@")[0],
            "semester": sp.current_semester if sp else 1,
            "branch": sp.branch if sp else "CSE",
            "college_name": sp.college_name if sp else "UIT",
            "is_profile_complete": bool(u.is_profile_complete),
            "completion_percentage": sp.completion_percentage if sp else 0,
            "is_active": bool(u.is_active)
        })
    return res

@portal_router.patch("/admin/students/{user_id}/semester")
def update_student_semester_api(user_id: int, payload: StudentSemesterUpdatePayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    verify_admin(user)
    target = db.query(User).filter(User.id == user_id, User.role == "student").first()
    if not target or not target.student_profile:
        raise HTTPException(status_code=404, detail="Student record not found.")

    old_sem = target.student_profile.current_semester
    target.student_profile.current_semester = payload.semester
    if payload.branch:
        target.student_profile.branch = payload.branch

    db.add(AdminAuditLog(
        admin_id=user.id,
        action="UPDATE_STUDENT_SEMESTER",
        target_user=target.email,
        details=f"Updated student {target.full_name} semester from Sem {old_sem} to Sem {payload.semester}."
    ))
    db.commit()
    return {"status": "success", "message": f"Updated {target.full_name} to Semester {payload.semester}."}

@portal_router.patch("/admin/users/{user_id}/toggle-status")
def toggle_user_status_api(user_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    verify_admin(user)
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")

    target.is_active = not target.is_active
    db.add(AdminAuditLog(
        admin_id=user.id,
        action="TOGGLE_USER_STATUS",
        target_user=target.email,
        details=f"User {target.email} active status toggled to {target.is_active}."
    ))
    db.commit()
    return {"status": "success", "is_active": target.is_active}

@portal_router.get("/admin/academic/meta")
def get_academic_meta(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    branches = db.query(AcademicBranch).all()
    subjects = db.query(AcademicSubject).all()
    return {
        "branches": [{"id": b.id, "name": b.name, "code": b.code} for b in branches],
        "subjects": [{"id": s.id, "name": s.name, "code": s.code, "semester": s.semester, "branch": s.branch} for s in subjects]
    }

@portal_router.post("/admin/academic/subject")
def add_academic_subject(payload: SubjectCreatePayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    verify_admin(user)
    sub = AcademicSubject(name=payload.name, code=payload.code, semester=payload.semester, branch=payload.branch)
    db.add(sub)
    db.add(AdminAuditLog(
        admin_id=user.id,
        action="ADD_SUBJECT",
        target_user="academic_catalog",
        details=f"Added course {payload.name} ({payload.code}) to Semester {payload.semester} ({payload.branch})."
    ))
    db.commit()
    return {"status": "success", "id": sub.id}

@portal_router.get("/admin/audit-logs")
def get_admin_audit_logs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    verify_admin(user)
    logs = db.query(AdminAuditLog).order_by(AdminAuditLog.id.desc()).limit(50).all()
    return [
        {
            "id": l.id,
            "action": l.action,
            "target_user": l.target_user,
            "details": l.details,
            "timestamp": l.timestamp.strftime("%b %d, %Y - %I:%M %p") if l.timestamp else "Recently"
        }
        for l in logs
    ]

# ==============================================================================
# 7. APP LIFESPAN & FASTAPI MOUNT
# ==============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    seed_demo_database()
    yield

app = FastAPI(title="EduNova Enterprise Platform", version="3.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(auth_router)
app.include_router(portal_router)
app.mount("/uploaded_materials", StaticFiles(directory=UPLOAD_DIR), name="uploaded_materials")

# ==============================================================================
# 8. FRONTEND SPA (WITH MODERN LOGIN UI & AMBIENT DOODLES)
# ==============================================================================

FRONTEND_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>EduNova — Modern Academic Platform</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script crossorigin src="https://unpkg.com/react@18/umd/react.development.js"></script>
  <script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>
  <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    /* =========================================
       CSS Variables & Custom Styles
       ========================================= */
    :root {
      --bg-gradient: linear-gradient(135deg, #e9fbf3 0%, #edf6ff 50%, #e0f2fe 100%);
      --orb-mint: rgba(134, 239, 172, 0.45);
      --orb-cyan: rgba(125, 211, 252, 0.45);
      --card-bg: #ffffff;
      --card-shadow: 0 20px 50px -12px rgba(14, 165, 233, 0.12),
                     0 10px 25px -5px rgba(16, 185, 129, 0.08);
      --primary-gradient: linear-gradient(90deg, #10b981 0%, #0ea5e9 100%);
      --text-dark: #0f172a;
      --text-muted: #64748b;
      --input-border: #e2e8f0;
      --accent-yellow: #fbbf24;
      --doodle-cyan: #0284c7;
      --doodle-green: #10b981;

      /* Workspace Variables */
      --bg-page: #f2fbf7;
      --bg-surface: #ffffff;
      --bg-muted: #e6f7ef;
      --border: #cbeee0;
      --text-primary: #0f241d;
      --text-secondary: #2f5647;
    }
    .dark {
      --bg-page: #091410;
      --bg-surface: #0f201a;
      --bg-muted: #152b23;
      --border: #1e3e33;
      --text-primary: #f0fdf4;
      --text-secondary: #a7d9c6;
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      font-family: 'DM Sans', 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
    }

    /* =========================================
       Background Ambient Glowing Orbs
       ========================================= */
    .ambient-orb {
      position: absolute;
      border-radius: 50%;
      filter: blur(80px);
      pointer-events: none;
      z-index: 0;
    }

    .orb-top-left {
      width: 450px;
      height: 450px;
      background: var(--orb-mint);
      top: -60px;
      left: -80px;
    }

    .orb-right {
      width: 500px;
      height: 500px;
      background: var(--orb-cyan);
      top: 10%;
      right: -100px;
    }

    .orb-bottom-left {
      width: 350px;
      height: 350px;
      background: rgba(167, 243, 208, 0.35);
      bottom: -60px;
      left: 5%;
    }

    /* =========================================
       Background SVG Doodles & Floating Elements
       ========================================= */
    .bg-elements {
      position: absolute;
      inset: 0;
      pointer-events: none;
      z-index: 1;
      overflow: hidden;
    }

    .doodle {
      position: absolute;
      opacity: 0.85;
      transition: transform 0.3s ease;
    }

    .paper-plane { top: 8%; left: 14%; width: 140px; }
    .bulb-left { top: 31%; left: 19%; width: 44px; }
    .book-left { top: 44%; left: 9%; width: 90px; }
    .bulb-right { top: 49%; right: 18%; width: 48px; }
    .book-right { top: 25%; right: 8%; width: 130px; }

    .triangle-top { top: 12%; right: 24%; width: 22px; }
    .triangle-bot { bottom: 14%; right: 17%; width: 20px; }
    .dot-yellow-1 { top: 56%; left: 26%; width: 16px; }
    .dot-yellow-2 { bottom: 31%; right: 11%; width: 20px; }
    .dot-cyan-1 { top: 7%; right: 28%; width: 10px; height: 10px; border-radius: 50%; background: #0284c7; }
    .dot-cyan-2 { bottom: 25%; right: 25%; width: 8px; height: 8px; border-radius: 50%; background: #0284c7; }

    /* Main Center Card */
    .login-card {
      position: relative;
      z-index: 2;
      width: 100%;
      max-width: 450px;
      background: var(--card-bg);
      border-radius: 28px;
      padding: 40px 36px 36px;
      box-shadow: var(--card-shadow);
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
    }

    .brand-title {
      font-size: 32px;
      font-weight: 800;
      letter-spacing: -0.5px;
      background: var(--primary-gradient);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      line-height: 1.1;
    }

    .btn-submit {
      margin-top: 6px;
      width: 100%;
      padding: 14px;
      border: none;
      border-radius: 14px;
      background: var(--primary-gradient);
      color: #ffffff;
      font-size: 16px;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 8px;
      box-shadow: 0 8px 20px -4px rgba(14, 165, 233, 0.4);
      transition: transform 0.15s ease, box-shadow 0.15s ease;
    }
    .btn-submit:hover {
      transform: translateY(-1px);
      box-shadow: 0 12px 24px -4px rgba(14, 165, 233, 0.5);
    }

    .btn-google {
      width: 100%;
      padding: 13px;
      border: 1.5px solid var(--input-border);
      border-radius: 14px;
      background: #ffffff;
      font-size: 14px;
      font-weight: 600;
      color: var(--text-dark);
      cursor: pointer;
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 10px;
      transition: background-color 0.15s ease;
    }
    .btn-google:hover {
      background-color: #f8fafc;
    }

    .focus-ring:focus-visible { outline: 2px solid #059669; outline-offset: 2px; }
    .skeleton-pulse { animation: skeleton-fade 1.5s ease-in-out infinite; }
    @keyframes skeleton-fade { 0%, 100% { opacity: 0.95; } 50% { opacity: 0.4; } }
  
    /* =========================================================
       EDUNOVA HUMAN REALITY — MASTER VISUAL THEME
       Warm, tactile, human-centered university workspace.
       ========================================================= */
    :root {
      --hr-ivory: #f7f3ea;
      --hr-paper: #fffdf8;
      --hr-paper-2: #f2eee4;
      --hr-sand: #e8dfcf;
      --hr-border: #ddd5c6;
      --hr-border-soft: #e9e2d6;
      --hr-green: #356f5b;
      --hr-green-dark: #285744;
      --hr-green-soft: #e7efe9;
      --hr-olive: #6f7f62;
      --hr-terracotta: #c47b5a;
      --hr-terracotta-soft: #f3e4dc;
      --hr-sky: #7fa9b8;
      --hr-sky-soft: #e6eef1;
      --hr-amber: #c58b3c;
      --hr-charcoal: #26312c;
      --hr-muted: #6f756f;
      --hr-shadow: 0 10px 28px rgba(63, 55, 42, 0.09), 0 2px 7px rgba(63, 55, 42, 0.05);
      --hr-shadow-hover: 0 16px 34px rgba(63, 55, 42, 0.13), 0 4px 10px rgba(63, 55, 42, 0.06);
      --bg-page: var(--hr-ivory) !important;
      --bg-surface: var(--hr-paper) !important;
      --bg-muted: var(--hr-paper-2) !important;
      --border: var(--hr-border) !important;
      --text-primary: var(--hr-charcoal) !important;
      --text-secondary: var(--hr-muted) !important;
      --card-bg: var(--hr-paper) !important;
      --input-border: var(--hr-border) !important;
      --text-dark: var(--hr-charcoal) !important;
      --text-muted: var(--hr-muted) !important;
      --primary-gradient: var(--hr-green) !important;
    }

    html, body {
      background: var(--hr-ivory) !important;
      color: var(--hr-charcoal);
    }

    body {
      background-image:
        radial-gradient(circle at 8% 8%, rgba(111,127,98,.08), transparent 24%),
        radial-gradient(circle at 92% 16%, rgba(196,123,90,.06), transparent 23%),
        linear-gradient(180deg, #faf7f0 0%, #f4efe5 100%) !important;
    }

    * {
      scrollbar-color: #b9c6bc #eee9df;
      scrollbar-width: thin;
    }

    /* Remove the old futuristic/glowing visual language. */
    .ambient-orb,
    .bg-elements {
      display: none !important;
    }
    [class*="backdrop-blur"] {
      backdrop-filter: none !important;
      -webkit-backdrop-filter: none !important;
    }

    /* Surfaces */
    [class~="bg-white"],
    .login-card {
      background: var(--hr-paper) !important;
    }
    [class*="bg-slate-50"] {
      background: #f2eee6 !important;
    }
    [class*="bg-slate-100"] {
      background: #ebe6dc !important;
    }
    [class*="bg-emerald-50"] {
      background: var(--hr-green-soft) !important;
    }
    [class*="bg-emerald-600"] {
      background: var(--hr-green) !important;
    }
    [class*="bg-teal-600"] {
      background: var(--hr-green) !important;
    }
    [class*="bg-blue-600"] {
      background: var(--hr-sky) !important;
    }

    /* Typography */
    [class*="text-slate-900"],
    [class*="text-slate-800"],
    [class*="text-slate-700"] {
      color: var(--hr-charcoal) !important;
    }
    [class*="text-slate-600"],
    [class*="text-slate-500"],
    [class*="text-slate-400"] {
      color: var(--hr-muted) !important;
    }
    [class*="text-emerald-600"],
    [class*="text-emerald-700"],
    [class*="text-emerald-800"] {
      color: var(--hr-green) !important;
    }
    [class*="text-blue-600"],
    [class*="text-cyan-600"] {
      color: var(--hr-sky) !important;
    }

    /* Borders */
    [class*="border-emerald-100"],
    [class*="border-emerald-200"],
    [class*="border-slate-100"],
    [class*="border-slate-200"] {
      border-color: var(--hr-border-soft) !important;
    }
    [class*="border-slate-700"],
    [class*="border-slate-800"] {
      border-color: #3b4941 !important;
    }

    /* Buttons and interactive controls */
    .btn-submit,
    [class*="bg-gradient-to-r"][class*="from-emerald-600"],
    [class*="bg-emerald-600"] {
      background: var(--hr-green) !important;
      color: #fffdf8 !important;
      box-shadow: 0 5px 12px rgba(53,111,91,.18) !important;
    }
    .btn-submit:hover,
    [class*="bg-emerald-600"]:hover {
      background: var(--hr-green-dark) !important;
      box-shadow: 0 8px 16px rgba(53,111,91,.22) !important;
    }
    .btn-google {
      background: var(--hr-paper) !important;
      border-color: var(--hr-border) !important;
      color: var(--hr-charcoal) !important;
    }
    .btn-google:hover {
      background: #f5f0e7 !important;
    }
    button {
      transition: background-color .18s ease, color .18s ease, border-color .18s ease,
                  box-shadow .18s ease, transform .18s ease !important;
    }
    button:active {
      transform: translateY(1px);
    }

    /* Cards: tactile paper, not glass. */
    [class*="rounded-3xl"],
    [class*="rounded-2xl"] {
      border-radius: 18px !important;
    }
    [class*="shadow-sm"] {
      box-shadow: var(--hr-shadow) !important;
    }
    [class*="shadow-xl"],
    [class*="shadow-2xl"] {
      box-shadow: var(--hr-shadow-hover) !important;
    }

    /* Header and navigation */
    header {
      background: rgba(255,253,248,.98) !important;
      border-color: var(--hr-border-soft) !important;
      box-shadow: 0 1px 0 rgba(63,55,42,.04), 0 4px 16px rgba(63,55,42,.035);
    }
    aside {
      background: #fbf8f1 !important;
      border-color: var(--hr-border) !important;
    }
    aside nav button {
      color: #58645d !important;
    }
    aside nav button:hover {
      background: #e9f0ea !important;
      color: var(--hr-green) !important;
    }
    aside nav button[class*="bg-gradient"],
    aside nav button[class*="bg-emerald-600"] {
      background: var(--hr-green) !important;
      color: #fff !important;
      box-shadow: 0 5px 12px rgba(53,111,91,.14) !important;
    }

    /* Login experience */
    .login-card {
      border: 1px solid var(--hr-border) !important;
      box-shadow: 0 22px 55px rgba(63,55,42,.13), 0 4px 14px rgba(63,55,42,.05) !important;
      max-width: 430px !important;
    }
    .brand-title {
      background: none !important;
      -webkit-text-fill-color: var(--hr-green) !important;
      color: var(--hr-green) !important;
      font-size: 30px !important;
      letter-spacing: -.7px;
    }
    .login-card input {
      background: #fffdf9 !important;
      border-color: var(--hr-border) !important;
      color: var(--hr-charcoal) !important;
    }
    .login-card input:focus {
      border-color: var(--hr-green) !important;
      box-shadow: 0 0 0 3px rgba(53,111,91,.11) !important;
    }
    .login-card .role-switcher {
      background: #eee9df !important;
    }

    /* Friendly AI — no robot/glow styling. */
    [class*="fa-graduation-cap"] {
      color: inherit;
    }
    [class*="bg-gradient-to-r"][class*="to-blue-600"] {
      background: var(--hr-green) !important;
    }

    /* Progress bars and data visuals */
    [class*="bg-emerald-500"],
    [class*="bg-emerald-400"] {
      background: var(--hr-green) !important;
    }
    [class*="bg-amber-500"],
    [class*="bg-yellow-500"] {
      background: var(--hr-amber) !important;
    }
    [class*="bg-red-500"] {
      background: var(--hr-terracotta) !important;
    }

    /* Search, profile and notification controls */
    header button[class*="bg-slate-100"] {
      background: #eee9df !important;
      color: #59635c !important;
    }
    header button[class*="bg-slate-100"]:hover {
      background: #e4ddd0 !important;
    }

    /* Tables */
    table thead tr {
      border-color: var(--hr-border) !important;
    }
    table tbody tr {
      border-color: #eee8dd !important;
    }
    table tbody tr:hover {
      background: #f0f4ef !important;
    }

    /* Form controls */
    input, select, textarea {
      accent-color: var(--hr-green);
    }
    input::placeholder, textarea::placeholder {
      color: #9a9b94 !important;
    }

    /* Human Reality status colors */
    .status-present { color: var(--hr-green) !important; background: var(--hr-green-soft) !important; }
    .status-late { color: var(--hr-amber) !important; background: #f5ecd9 !important; }
    .status-absent { color: #a95f49 !important; background: var(--hr-terracotta-soft) !important; }

    /* Mobile navigation */
    @media (max-width: 1023px) {
      main[role="main"] {
        padding: 18px !important;
      }
    }

    /* Dark mode: warm charcoal + forest green, not sci-fi black. */
    html.dark {
      --hr-ivory: #1e2521;
      --hr-paper: #28322c;
      --hr-paper-2: #222b26;
      --hr-sand: #3a4039;
      --hr-border: #435047;
      --hr-border-soft: #37443d;
      --hr-charcoal: #f1eee5;
      --hr-muted: #b7beb6;
      --hr-green-soft: #30463a;
      background: #1e2521 !important;
    }
    html.dark body {
      background:
        radial-gradient(circle at 10% 10%, rgba(111,127,98,.08), transparent 24%),
        linear-gradient(180deg, #202822 0%, #1b211e 100%) !important;
      color: var(--hr-charcoal) !important;
    }
    html.dark [class~="bg-white"],
    html.dark .login-card,
    html.dark header,
    html.dark aside {
      background: #28322c !important;
      color: #f1eee5 !important;
    }
    html.dark [class*="bg-slate-900"],
    html.dark [class*="bg-slate-800"] {
      background: #222b26 !important;
    }
    html.dark [class*="bg-slate-700"] {
      background: #354039 !important;
    }
    html.dark [class*="bg-slate-50"],
    html.dark [class*="bg-slate-100"] {
      background: #303932 !important;
    }
    html.dark [class*="text-slate-900"],
    html.dark [class*="text-slate-800"],
    html.dark [class*="text-slate-700"] {
      color: #f1eee5 !important;
    }
    html.dark [class*="text-slate-600"],
    html.dark [class*="text-slate-500"],
    html.dark [class*="text-slate-400"] {
      color: #b7beb6 !important;
    }
    html.dark [class*="border-slate-700"],
    html.dark [class*="border-slate-800"],
    html.dark [class*="border-emerald-100"],
    html.dark [class*="border-slate-200"] {
      border-color: #3d4942 !important;
    }
    html.dark input,
    html.dark select,
    html.dark textarea {
      background: #222b26 !important;
      border-color: #46534b !important;
      color: #f1eee5 !important;
    }
    html.dark .btn-google {
      background: #28322c !important;
      color: #f1eee5 !important;
      border-color: #46534b !important;
    }
    html.dark table tbody tr:hover {
      background: #303a33 !important;
    }

    /* Small paper grain for tactile depth without heavy imagery. */
    .login-card::after,
    main[role="main"]::after {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      opacity: .025;
      z-index: 999;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140' viewBox='0 0 140 140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.15'/%3E%3C/svg%3E");
      mix-blend-mode: multiply;
    }
    html.dark .login-card::after,
    html.dark main[role="main"]::after {
      mix-blend-mode: screen;
      opacity: .018;
    }


    /* =========================================================
       EDUNOVA HUMAN REALITY PRO — UI REFINEMENT LAYER
       Goal: premium university product, tactile paper surfaces,
       stronger hierarchy, calmer motion, and better responsive UX.
       ========================================================= */

    :root {
      --hr-bg: #f5f1e8;
      --hr-paper: #fffdf8;
      --hr-paper-warm: #fbf7ef;
      --hr-ink: #24312a;
      --hr-muted: #73786f;
      --hr-line: #ddd5c6;
      --hr-line-soft: #ece5d9;
      --hr-green: #356f5b;
      --hr-green-deep: #2a5a48;
      --hr-green-pale: #e8f0ea;
      --hr-terracotta: #bc7658;
      --hr-terracotta-pale: #f3e4dc;
      --hr-sage: #7d8c70;
      --hr-sky: #789eac;
      --hr-gold: #b98439;
      --hr-danger: #ad624d;
      --hr-shadow-1: 0 1px 2px rgba(58,48,33,.05), 0 7px 20px rgba(58,48,33,.06);
      --hr-shadow-2: 0 14px 34px rgba(58,48,33,.10), 0 4px 10px rgba(58,48,33,.05);
      --hr-radius: 18px;
      --hr-radius-sm: 12px;
    }

    /* Page texture + typography */
    html, body {
      min-height: 100%;
      background: var(--hr-bg) !important;
      color: var(--hr-ink);
    }

    body {
      background:
        radial-gradient(circle at 10% 6%, rgba(125,140,112,.075), transparent 25%),
        radial-gradient(circle at 90% 8%, rgba(188,118,88,.055), transparent 22%),
        linear-gradient(180deg, #faf7f0 0%, #f3eee4 100%) !important;
      font-feature-settings: "cv02", "cv03", "cv04", "cv11";
    }

    ::selection {
      background: rgba(53,111,91,.16);
      color: var(--hr-ink);
    }

    /* Remove remaining futuristic effects */
    .ambient-orb, .bg-elements {
      display: none !important;
    }

    [class*="backdrop-blur"] {
      backdrop-filter: none !important;
      -webkit-backdrop-filter: none !important;
    }

    /* Global focus */
    button:focus-visible,
    input:focus-visible,
    select:focus-visible,
    textarea:focus-visible {
      outline: 3px solid rgba(53,111,91,.16) !important;
      outline-offset: 2px !important;
    }

    /* Login — editorial split feel while staying inside current markup */
    .edunova-login-page {
      background:
        linear-gradient(90deg, rgba(255,253,248,.97) 0%, rgba(255,253,248,.92) 48%, rgba(247,242,231,.70) 100%),
        radial-gradient(circle at 72% 40%, rgba(125,140,112,.13), transparent 31%),
        radial-gradient(circle at 91% 18%, rgba(188,118,88,.09), transparent 25%) !important;
    }

    .login-card {
      border: 1px solid rgba(221,213,198,.95) !important;
      border-radius: 24px !important;
      background: rgba(255,253,248,.97) !important;
      box-shadow: 0 30px 70px rgba(53,44,30,.12), 0 8px 18px rgba(53,44,30,.05) !important;
      position: relative;
      overflow: hidden;
    }

    .login-card::before {
      content: "";
      position: absolute;
      top: 0; left: 0; right: 0;
      height: 5px;
      background: linear-gradient(90deg, var(--hr-green), var(--hr-sage), var(--hr-terracotta));
    }

    .login-card .brand-title {
      color: var(--hr-green) !important;
      font-weight: 800 !important;
      letter-spacing: -.8px !important;
      background: none !important;
      -webkit-text-fill-color: var(--hr-green) !important;
    }

    .login-card input,
    .login-card select {
      min-height: 46px;
      background: #fffefb !important;
      border: 1px solid var(--hr-line) !important;
      border-radius: 13px !important;
      box-shadow: inset 0 1px 0 rgba(255,255,255,.7);
    }

    .login-card input:hover {
      border-color: #c9c0b0 !important;
    }

    .login-card input:focus {
      border-color: var(--hr-green) !important;
      box-shadow: 0 0 0 4px rgba(53,111,91,.09) !important;
    }

    .login-card .btn-submit {
      min-height: 46px;
      border-radius: 13px !important;
      background: var(--hr-green) !important;
      box-shadow: 0 8px 16px rgba(53,111,91,.18) !important;
    }

    .login-card .btn-submit:hover {
      background: var(--hr-green-deep) !important;
      transform: translateY(-1px);
    }

    .login-card .btn-google {
      min-height: 46px;
      border-radius: 13px !important;
      background: #fffefb !important;
      border-color: var(--hr-line) !important;
    }

    /* App shell */
    .edunova-workspace {
      max-width: 1440px !important;
      width: 100%;
    }

    .edunova-header {
      background: rgba(255,253,248,.96) !important;
      border-color: var(--hr-line-soft) !important;
      box-shadow: 0 1px 0 rgba(58,48,33,.04), 0 5px 18px rgba(58,48,33,.035);
      backdrop-filter: none !important;
    }

    aside {
      background: #fcf9f2 !important;
      border-color: var(--hr-line) !important;
      box-shadow: 8px 0 30px rgba(58,48,33,.035);
    }

    aside nav button {
      min-height: 42px;
      border-radius: 12px !important;
      color: #59655d !important;
    }

    aside nav button:hover {
      background: #eef3ed !important;
      color: var(--hr-green) !important;
      transform: translateX(1px);
    }

    aside nav button[class*="bg-gradient"],
    aside nav button[class*="bg-emerald-600"],
    aside nav button[class*="bg-[#356f5b]"] {
      background: var(--hr-green) !important;
      box-shadow: 0 7px 15px rgba(53,111,91,.13) !important;
    }

    /* Header controls */
    header button {
      transition: transform .18s ease, background-color .18s ease, box-shadow .18s ease;
    }

    header button:hover {
      transform: translateY(-1px);
    }

    header button[aria-label="Notifications"],
    header button[aria-label="Toggle theme"],
    header button[aria-label="Open menu"] {
      background: #f0ece3 !important;
      color: #59635c !important;
      border: 1px solid #e5ddd0 !important;
    }

    /* Search trigger */
    header button:has(kbd) {
      min-height: 38px;
      background: #f2eee6 !important;
      border: 1px solid #e5ddd0 !important;
      border-radius: 12px !important;
    }

    /* Universal cards */
    [class*="rounded-3xl"],
    [class*="rounded-2xl"] {
      border-radius: var(--hr-radius) !important;
    }

    [class*="shadow-sm"] {
      box-shadow: var(--hr-shadow-1) !important;
    }

    [class*="shadow-xl"],
    [class*="shadow-2xl"] {
      box-shadow: var(--hr-shadow-2) !important;
    }

    /* Common card surfaces */
    [class~="bg-white"],
    [class*="bg-white/"],
    .edunova-card {
      background: var(--hr-paper) !important;
    }

    [class*="border-emerald-100"],
    [class*="border-emerald-200"],
    [class*="border-slate-100"],
    [class*="border-slate-200"] {
      border-color: var(--hr-line-soft) !important;
    }

    /* Better hierarchy inside cards */
    main h1, main h2, main h3, main h4 {
      color: var(--hr-ink);
      letter-spacing: -.22px;
    }

    main p[class*="text-[11px]"],
    main p[class*="text-xs"] {
      line-height: 1.55;
    }

    /* Stat cards: subtle top accent + number hierarchy */
    main .grid > div[class*="rounded-3xl"] {
      transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
    }

    main .grid > div[class*="rounded-3xl"]:hover {
      transform: translateY(-2px);
      box-shadow: var(--hr-shadow-2) !important;
      border-color: #d1c7b6 !important;
    }

    /* Hero banner */
    .edunova-hero {
      background:
        linear-gradient(135deg, #315f4f 0%, #356f5b 56%, #56725f 100%) !important;
      border: 1px solid rgba(255,255,255,.13);
      position: relative;
      overflow: hidden;
      box-shadow: 0 18px 34px rgba(53,111,91,.18) !important;
    }

    .edunova-hero::after {
      content: "";
      position: absolute;
      width: 260px;
      height: 260px;
      right: -90px;
      top: -120px;
      border-radius: 50%;
      background: rgba(255,250,240,.08);
    }

    .edunova-hero > * {
      position: relative;
      z-index: 2;
    }

    /* Progress visuals */
    [class*="bg-gradient-to-r"][class*="from-emerald-500"] {
      background: var(--hr-green) !important;
    }

    [class*="bg-emerald-500"],
    [class*="bg-emerald-400"],
    [class*="bg-emerald-600"] {
      background-color: var(--hr-green) !important;
    }

    [class*="text-emerald-500"],
    [class*="text-emerald-600"],
    [class*="text-emerald-700"] {
      color: var(--hr-green) !important;
    }

    [class*="bg-emerald-50"] {
      background-color: var(--hr-green-pale) !important;
    }

    [class*="text-blue-600"],
    [class*="text-cyan-600"] {
      color: var(--hr-sky) !important;
    }

    [class*="bg-blue-600"] {
      background-color: var(--hr-sky) !important;
    }

    [class*="bg-amber-500"],
    [class*="bg-yellow-500"] {
      background-color: var(--hr-gold) !important;
    }

    [class*="bg-red-500"] {
      background-color: var(--hr-danger) !important;
    }

    /* Badges */
    span[class*="rounded-full"],
    span[class*="rounded-lg"] {
      letter-spacing: .01em;
    }

    /* Tables */
    table {
      border-collapse: separate !important;
      border-spacing: 0 !important;
    }

    table thead tr {
      background: #f4efe6 !important;
    }

    table th {
      color: #687169 !important;
      font-size: 10px !important;
      font-weight: 800 !important;
      text-transform: uppercase;
      letter-spacing: .06em;
    }

    table tbody tr {
      border-color: var(--hr-line-soft) !important;
      transition: background-color .16s ease;
    }

    table tbody tr:hover {
      background: #f4f1ea !important;
    }

    /* Modals */
    [role="dialog"] > div[class*="bg-white"],
    [role="alertdialog"] {
      border: 1px solid var(--hr-line) !important;
      border-radius: 20px !important;
      background: var(--hr-paper) !important;
    }

    /* Toasts */
    #toast-root > div > div {
      border-radius: 14px !important;
      border-color: var(--hr-line-soft) !important;
      box-shadow: var(--hr-shadow-2) !important;
      background: rgba(255,253,248,.98) !important;
    }

    /* Inputs across the app */
    input, select, textarea {
      background: #fffefb !important;
      border-color: var(--hr-line) !important;
      border-radius: 12px !important;
      color: var(--hr-ink) !important;
      transition: border-color .16s ease, box-shadow .16s ease, background-color .16s ease;
    }

    input:hover, select:hover, textarea:hover {
      border-color: #c9c0b1 !important;
    }

    input:focus, select:focus, textarea:focus {
      border-color: var(--hr-green) !important;
      box-shadow: 0 0 0 4px rgba(53,111,91,.08) !important;
      outline: none !important;
    }

    input::placeholder, textarea::placeholder {
      color: #9a9a92 !important;
    }

    /* Friendly AI tutor */
    .ai-tutor-panel,
    [data-ai-panel="true"] {
      background:
        linear-gradient(180deg, #fffdf8 0%, #f7f2e9 100%) !important;
      border-color: var(--hr-line) !important;
    }

    /* Empty/loading/error states */
    .skeleton-pulse {
      background: linear-gradient(90deg, #eee9df 0%, #f7f3eb 50%, #eee9df 100%) !important;
      background-size: 200% 100%;
      animation: edunova-shimmer 1.6s ease-in-out infinite;
    }

    @keyframes edunova-shimmer {
      0% { background-position: 200% 0; }
      100% { background-position: -200% 0; }
    }

    /* Status chips */
    .status-present {
      color: var(--hr-green) !important;
      background: var(--hr-green-pale) !important;
    }

    .status-late {
      color: var(--hr-gold) !important;
      background: #f5ecd9 !important;
    }

    .status-absent {
      color: var(--hr-danger) !important;
      background: var(--hr-terracotta-pale) !important;
    }

    /* Better mobile treatment */
    @media (max-width: 1023px) {
      .edunova-workspace {
        padding: 16px !important;
      }

      .edunova-hero {
        border-radius: 22px !important;
        padding: 20px !important;
      }

      .edunova-header {
        padding-left: 14px !important;
        padding-right: 14px !important;
      }
    }

    @media (max-width: 640px) {
      .login-card {
        max-width: 100% !important;
        border-radius: 20px !important;
        padding: 28px 20px 24px !important;
      }

      .edunova-hero h2 {
        font-size: 18px !important;
      }

      main .grid {
        gap: 12px !important;
      }
    }

    /* Respect reduced motion */
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after {
        animation-duration: .01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: .01ms !important;
        scroll-behavior: auto !important;
      }
    }

    /* Warm dark mode */
    html.dark {
      --hr-bg: #202722;
      --hr-paper: #2a332e;
      --hr-paper-warm: #26302a;
      --hr-ink: #f1eee6;
      --hr-muted: #b6beb5;
      --hr-line: #465248;
      --hr-line-soft: #39453e;
      --hr-green: #6f9d87;
      --hr-green-deep: #5e8a74;
      --hr-green-pale: #30463b;
      --hr-terracotta: #c78a6b;
      --hr-sage: #98a88a;
      --hr-sky: #8bb0bb;
    }

    html.dark body {
      background:
        radial-gradient(circle at 12% 8%, rgba(111,157,135,.08), transparent 25%),
        linear-gradient(180deg, #232a25 0%, #1d241f 100%) !important;
    }

    html.dark aside,
    html.dark .edunova-header,
    html.dark .login-card,
    html.dark [class~="bg-white"],
    html.dark [class*="bg-white/"] {
      background: #2a332e !important;
      color: var(--hr-ink) !important;
      border-color: var(--hr-line-soft) !important;
    }

    html.dark input,
    html.dark select,
    html.dark textarea {
      background: #222a25 !important;
      color: #f1eee6 !important;
      border-color: #435047 !important;
    }

    html.dark input::placeholder,
    html.dark textarea::placeholder {
      color: #8f9a92 !important;
    }

    html.dark table thead tr {
      background: #303a34 !important;
    }

    html.dark table tbody tr:hover {
      background: #303a34 !important;
    }

    /* =========================================================
       EDUNOVA HUMAN REALITY STUDIO
       Desktop-first premium visual layer:
       larger typography, tactile graphics, book stacks,
       foreground stones, mouse-parallax depth and calmer motion.
       ========================================================= */

    :root {
      --studio-ivory: #f5f0e6;
      --studio-paper: #fffdf8;
      --studio-paper-2: #f7f3ea;
      --studio-ink: #26332d;
      --studio-muted: #747a72;
      --studio-line: #ddd4c4;
      --studio-line-soft: #ebe3d6;
      --studio-green: #356f5b;
      --studio-green-deep: #295846;
      --studio-green-pale: #e7efe9;
      --studio-sage: #7f8e73;
      --studio-terracotta: #be7859;
      --studio-terracotta-pale: #f3e3da;
      --studio-sky: #7fa8b6;
      --studio-gold: #bd8940;
      --studio-shadow: 0 10px 28px rgba(65,55,40,.08), 0 2px 7px rgba(65,55,40,.05);
      --studio-shadow-deep: 0 18px 42px rgba(65,55,40,.12), 0 5px 14px rgba(65,55,40,.06);
    }

    html, body {
      background: var(--studio-ivory) !important;
      color: var(--studio-ink);
    }

    body {
      min-height: 100vh;
      overflow-x: hidden;
      background:
        radial-gradient(circle at 10% 9%, rgba(127,142,115,.09), transparent 25%),
        radial-gradient(circle at 91% 14%, rgba(190,120,89,.07), transparent 22%),
        linear-gradient(180deg, #faf7f0 0%, #f1ece2 100%) !important;
    }

    /* Scene layer */
    #edunova-scenery {
      position: fixed;
      inset: 0;
      z-index: 0;
      pointer-events: none;
      overflow: hidden;
    }

    #root {
      position: relative;
      z-index: 2;
    }

    .scene-book-stack,
    .scene-rocks,
    .scene-leaf {
      position: absolute;
      will-change: transform;
      transition: transform .28s cubic-bezier(.2,.7,.2,1);
    }

    .scene-book-stack-left {
      left: -28px;
      top: 112px;
      width: 245px;
      height: 170px;
      transform: rotate(-6deg);
      opacity: .45;
    }

    .scene-book-stack-right {
      right: -30px;
      bottom: 150px;
      width: 230px;
      height: 165px;
      transform: rotate(7deg);
      opacity: .38;
    }

    .scene-book {
      position: absolute;
      left: 0;
      width: 205px;
      height: 33px;
      border-radius: 8px 10px 7px 7px;
      border: 1px solid rgba(73,61,47,.14);
      box-shadow: 0 8px 13px rgba(69,56,40,.10);
      background:
        linear-gradient(90deg, rgba(255,255,255,.42), transparent 12%, transparent 88%, rgba(0,0,0,.045)),
        linear-gradient(180deg, #fdf9ee, #ded2bd);
    }

    .scene-book::after {
      content: "";
      position: absolute;
      right: 18px;
      top: 10px;
      width: 72px;
      height: 2px;
      border-radius: 999px;
      background: rgba(100,92,79,.18);
      box-shadow: 0 7px 0 rgba(100,92,79,.12), 0 14px 0 rgba(100,92,79,.10);
    }

    .scene-book-1 { bottom: 0; width: 214px; background: linear-gradient(180deg,#7b8b72,#5d705e); }
    .scene-book-2 { bottom: 29px; left: 14px; width: 190px; background: linear-gradient(180deg,#d2a57f,#bc7a58); transform: rotate(2deg); }
    .scene-book-3 { bottom: 59px; left: -2px; width: 222px; background: linear-gradient(180deg,#f6f0df,#d8cbb5); transform: rotate(-1deg); }
    .scene-book-4 { bottom: 91px; left: 20px; width: 177px; background: linear-gradient(180deg,#7fa9b8,#678f9d); transform: rotate(2deg); }

    .scene-book-5 { bottom: 0; left: 18px; width: 208px; background: linear-gradient(180deg,#78886d,#596958); }
    .scene-book-6 { bottom: 31px; left: 0; width: 184px; background: linear-gradient(180deg,#d3a181,#bd7959); transform: rotate(-2deg); }
    .scene-book-7 { bottom: 61px; left: 25px; width: 173px; background: linear-gradient(180deg,#f5eddd,#d8ceb9); transform: rotate(1deg); }

    .scene-rocks-left {
      left: -18px;
      bottom: 32px;
      width: 230px;
      height: 105px;
      opacity: .58;
    }

    .scene-rocks-right {
      right: -10px;
      bottom: 12px;
      width: 260px;
      height: 125px;
      opacity: .60;
    }

    .scene-rock {
      position: absolute;
      display: block;
      border-radius: 48% 52% 45% 55%;
      box-shadow:
        inset -8px -9px 13px rgba(52,44,35,.18),
        inset 8px 8px 12px rgba(255,255,255,.22),
        0 8px 14px rgba(52,44,35,.13);
      border: 1px solid rgba(63,55,45,.12);
    }

    .rock-a { width: 62px; height: 39px; left: 8px; bottom: 7px; background: #aaa394; transform: rotate(-10deg); }
    .rock-b { width: 88px; height: 48px; left: 53px; bottom: 1px; background: #8f8b80; transform: rotate(6deg); }
    .rock-c { width: 54px; height: 33px; left: 130px; bottom: 10px; background: #b7b0a1; transform: rotate(-5deg); }

    .rock-d { width: 72px; height: 42px; left: 5px; bottom: 4px; background: #8b877a; transform: rotate(8deg); }
    .rock-e { width: 97px; height: 52px; left: 73px; bottom: 0; background: #aaa393; transform: rotate(-4deg); }
    .rock-f { width: 45px; height: 31px; left: 179px; bottom: 8px; background: #c0b9aa; transform: rotate(9deg); }

    .scene-leaf {
      width: 28px;
      height: 48px;
      border-radius: 100% 0 100% 0;
      background: linear-gradient(155deg, #83936f, #526a54);
      opacity: .17;
      filter: blur(.1px);
    }

    .scene-leaf-one { left: 16%; top: 16%; transform: rotate(-28deg); }
    .scene-leaf-two { right: 12%; top: 27%; transform: rotate(42deg) scale(.82); }

    /* Typography hierarchy — larger where it matters */
    main[role="main"] {
      max-width: 1460px !important;
      padding-left: 32px !important;
      padding-right: 32px !important;
    }

    main[role="main"] h1,
    main[role="main"] h2 {
      font-size: 1.55rem !important;
      line-height: 1.16 !important;
      font-weight: 800 !important;
      letter-spacing: -.035em !important;
      color: var(--studio-ink) !important;
    }

    main[role="main"] h3 {
      font-size: 1.08rem !important;
      line-height: 1.22 !important;
      font-weight: 800 !important;
      color: var(--studio-ink) !important;
    }

    main[role="main"] p,
    main[role="main"] label {
      font-size: .86rem !important;
      line-height: 1.55 !important;
    }

    main[role="main"] .text-xs { font-size: .78rem !important; }
    main[role="main"] [class*="text-[11px]"] { font-size: .73rem !important; }
    main[role="main"] [class*="text-[10px]"] { font-size: .68rem !important; }

    /* Sidebar typography and width */
    aside[class*="w-64"] {
      width: 17rem !important;
      min-width: 17rem !important;
    }

    aside[class*="w-20"] {
      width: 5rem !important;
      min-width: 5rem !important;
    }

    aside h1 {
      font-size: 1rem !important;
      letter-spacing: -.02em;
    }

    aside nav button {
      min-height: 44px !important;
      font-size: .79rem !important;
      border-radius: 12px !important;
    }

    /* Stronger header */
    header {
      background: rgba(255,253,248,.96) !important;
      border-color: var(--studio-line-soft) !important;
      box-shadow: 0 3px 18px rgba(65,55,40,.035);
    }

    header .text-xs {
      font-size: .74rem !important;
    }

    /* Cards: real paper depth */
    main[role="main"] [class*="bg-white"],
    main[role="main"] [class*="rounded-3xl"],
    main[role="main"] [class*="rounded-2xl"] {
      border-color: var(--studio-line-soft) !important;
    }

    main[role="main"] [class*="shadow-sm"] {
      box-shadow: var(--studio-shadow) !important;
    }

    main[role="main"] [class*="shadow-xl"],
    main[role="main"] [class*="shadow-2xl"] {
      box-shadow: var(--studio-shadow-deep) !important;
    }

    main[role="main"] .grid > div {
      transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
    }

    main[role="main"] .grid > div:hover {
      transform: translateY(-2px);
      box-shadow: var(--studio-shadow-deep) !important;
      border-color: #d0c6b5 !important;
    }

    /* Tactile buttons */
    button,
    a {
      transition: transform .16s ease, background-color .16s ease, box-shadow .16s ease, border-color .16s ease !important;
    }

    button:hover {
      transform: translateY(-1px);
    }

    button:active {
      transform: translateY(1px) scale(.995);
    }

    /* Green interaction system */
    [class*="bg-emerald-600"],
    [class*="bg-gradient-to-r"][class*="from-emerald-600"] {
      background: var(--studio-green) !important;
      box-shadow: 0 7px 15px rgba(53,111,91,.16) !important;
    }

    [class*="bg-emerald-600"]:hover {
      background: var(--studio-green-deep) !important;
    }

    [class*="text-emerald-600"],
    [class*="text-emerald-700"] {
      color: var(--studio-green) !important;
    }

    [class*="bg-emerald-50"] {
      background: var(--studio-green-pale) !important;
    }

    [class*="bg-blue-600"],
    [class*="to-blue-600"] {
      background: var(--studio-sky) !important;
    }

    [class*="bg-amber-500"],
    [class*="bg-yellow-500"] {
      background: var(--studio-gold) !important;
    }

    [class*="bg-red-500"] {
      background: var(--studio-terracotta) !important;
    }

    /* Forms */
    input, select, textarea {
      min-height: 42px;
      background: #fffdf9 !important;
      color: var(--studio-ink) !important;
      border-color: var(--studio-line) !important;
      border-radius: 12px !important;
    }

    input:focus, select:focus, textarea:focus {
      border-color: var(--studio-green) !important;
      box-shadow: 0 0 0 4px rgba(53,111,91,.08) !important;
      outline: none !important;
    }

    /* AI Tutor: warmer, friendlier */
    [class*="fa-robot"] {
      opacity: .82;
    }

    /* Increase test question readability */
    label[class*="text-xs"] {
      font-size: .83rem !important;
      line-height: 1.6 !important;
    }

    /* Dashboard hero if present */
    main[role="main"] .bg-gradient-to-r,
    main[role="main"] .bg-gradient-to-br {
      background: linear-gradient(135deg, #315f4f 0%, #356f5b 58%, #60755d 100%) !important;
    }

    /* Scrollbars */
    * {
      scrollbar-width: thin;
      scrollbar-color: #b9c2b9 #ece7dd;
    }

    /* Motion */
    .scene-book-stack,
    .scene-rocks,
    .scene-leaf {
      animation: studio-breathe 7s ease-in-out infinite;
    }

    @keyframes studio-breathe {
      0%, 100% { filter: saturate(.96); }
      50% { filter: saturate(1.03); }
    }

    @media (max-width: 1279px) {
      main[role="main"] {
        padding-left: 24px !important;
        padding-right: 24px !important;
      }
      .scene-book-stack-left,
      .scene-book-stack-right,
      .scene-rocks-left,
      .scene-rocks-right {
        opacity: .20;
      }
    }

    @media (max-width: 1023px) {
      main[role="main"] {
        padding: 20px !important;
      }
      .scene-book-stack-left,
      .scene-book-stack-right,
      .scene-rocks-left,
      .scene-rocks-right,
      .scene-leaf {
        display: none !important;
      }
    }

    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after {
        animation-duration: .01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: .01ms !important;
        scroll-behavior: auto !important;
      }
    }

    html.dark {
      --studio-ivory: #202822;
      --studio-paper: #2a342e;
      --studio-ink: #f2eee5;
      --studio-muted: #b7bdb6;
      --studio-line: #46534a;
      --studio-line-soft: #39463e;
      --studio-green: #709c87;
      --studio-green-deep: #5e896f;
      --studio-green-pale: #30453a;
    }

    html.dark body {
      background:
        radial-gradient(circle at 12% 10%, rgba(112,156,135,.08), transparent 25%),
        linear-gradient(180deg, #252c27 0%, #1d241f 100%) !important;
    }

    html.dark header,
    html.dark aside,
    html.dark main[role="main"] [class*="bg-white"],
    html.dark .login-card {
      background: #2a342e !important;
      color: #f2eee5 !important;
      border-color: var(--studio-line-soft) !important;
    }

    html.dark input,
    html.dark select,
    html.dark textarea {
      background: #222b26 !important;
      color: #f2eee5 !important;
      border-color: var(--studio-line) !important;
    }

    html.dark main[role="main"] h1,
    html.dark main[role="main"] h2,
    html.dark main[role="main"] h3 {
      color: #f2eee5 !important;
    }

  </style>
</head>
<body>
  <div id="root"></div>

    <div id="edunova-scenery" aria-hidden="true">
      <div className="scene-book-stack scene-book-stack-left" data-depth="0.32">
        <div className="scene-book scene-book-1"></div>
        <div className="scene-book scene-book-2"></div>
        <div className="scene-book scene-book-3"></div>
        <div className="scene-book scene-book-4"></div>
      </div>

      <div className="scene-book-stack scene-book-stack-right" data-depth="0.22">
        <div className="scene-book scene-book-5"></div>
        <div className="scene-book scene-book-6"></div>
        <div className="scene-book scene-book-7"></div>
      </div>

      <div className="scene-rocks scene-rocks-left" data-depth="0.42">
        <span className="scene-rock rock-a"></span>
        <span className="scene-rock rock-b"></span>
        <span className="scene-rock rock-c"></span>
      </div>

      <div className="scene-rocks scene-rocks-right" data-depth="0.55">
        <span className="scene-rock rock-d"></span>
        <span className="scene-rock rock-e"></span>
        <span className="scene-rock rock-f"></span>
      </div>

      <div className="scene-leaf scene-leaf-one" data-depth="0.18"></div>
      <div className="scene-leaf scene-leaf-two" data-depth="0.28"></div>
    </div>

  <div id="modal-root"></div>
  <div id="toast-root"></div>

  <script type="text/babel">
    const { useState, useEffect, useRef, useCallback, useMemo } = React;

    // --- Toast System ---
    let toastListeners = new Set();
    let toastState = [];
    const toast = (msg, type = "info") => {
      const id = Date.now() + Math.random();
      toastState.push({ id, msg, type });
      toastListeners.forEach(fn => fn([...toastState]));
      setTimeout(() => {
        toastState = toastState.filter(t => t.id !== id);
        toastListeners.forEach(fn => fn([...toastState]));
      }, 4000);
    };
    toast.success = m => toast(m, "success");
    toast.error = m => toast(m, "error");
    toast.warning = m => toast(m, "warning");

    function ToastContainer() {
      const [toasts, setToasts] = useState([]);
      useEffect(() => {
        toastListeners.add(setToasts);
        return () => toastListeners.delete(setToasts);
      }, []);

      const borderColors = {
        success: "border-emerald-500 text-emerald-700",
        error: "border-red-500 text-red-600",
        warning: "border-amber-500 text-amber-600",
        info: "border-teal-500 text-teal-600"
      };

      const icons = {
        success: "fa-circle-check",
        error: "fa-triangle-exclamation",
        warning: "fa-circle-exclamation",
        info: "fa-circle-info"
      };

      return ReactDOM.createPortal(
        <div className="fixed bottom-5 right-5 z-[9999] flex flex-col gap-2 pointer-events-none max-w-sm w-full">
          {toasts.map(t => (
            <div key={t.id} role="status" aria-live="polite"
                 className={`pointer-events-auto flex items-center gap-3 p-4 rounded-2xl shadow-xl border-l-4 bg-white/95 text-slate-800 border border-slate-200/80 ${borderColors[t.type] || borderColors.info}`}>
              <i className={`fas ${icons[t.type] || icons.info} text-base`}></i>
              <span className="text-xs font-semibold flex-1 leading-snug">{t.msg}</span>
            </div>
          ))}
        </div>,
        document.getElementById("toast-root")
      );
    }

    // --- API Client ---
    const api = {
      async request(endpoint, options = {}, token = null) {
        const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
        if (token) headers["Authorization"] = `Bearer ${token}`;
        const res = await fetch(endpoint, { ...options, headers });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Operation failed");
        return data;
      },
      login: (email, password, role) => api.request("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password, role }) }),
      register: (email, password, full_name, role) => api.request("/api/auth/register", { method: "POST", body: JSON.stringify({ email, password, full_name, role }) }),
      registerStudent: (payload) => api.request("/api/auth/register/student", { method: "POST", body: JSON.stringify(payload) }),
      registerTeacher: (payload) => api.request("/api/auth/register/teacher", { method: "POST", body: JSON.stringify(payload) }),
      verifyEmail: (token, code) => api.request("/api/auth/verify-email", { method: "POST", body: JSON.stringify({ code }) }, token),
      getQuickToken: (role = "student") => api.request(`/api/auth/quick-token?role=${role}`),
      getMyProfile: (token) => api.request("/api/profile/me", {}, token),
      updatePersonalProfile: (token, payload) => api.request("/api/profile/personal", { method: "PUT", body: JSON.stringify(payload) }, token),
      updateAcademicProfile: (token, payload) => api.request("/api/profile/academic", { method: "PUT", body: JSON.stringify(payload) }, token),
      updateAvatar: (token, avatar_url) => api.request("/api/profile/avatar", { method: "POST", body: JSON.stringify({ avatar_url }) }, token),
      getMaterials: (token, semester = null, branch = null) => {
        let url = "/api/materials";
        const params = [];
        if (semester) params.push(`semester=${semester}`);
        if (branch) params.push(`branch=${encodeURIComponent(branch)}`);
        if (params.length) url += `?${params.join("&")}`;
        return api.request(url, {}, token);
      },
      getAdminOverview: (token) => api.request("/api/admin/overview", {}, token),
      getPendingTeachers: (token) => api.request("/api/admin/teachers/pending", {}, token),
      approveTeacher: (token, userId, payload) => api.request(`/api/admin/teachers/${userId}/approve`, { method: "POST", body: JSON.stringify(payload) }, token),
      rejectTeacher: (token, userId, reason) => api.request(`/api/admin/teachers/${userId}/reject`, { method: "POST", body: JSON.stringify({ reason }) }, token),
      getAdminTeachers: (token) => api.request("/api/admin/teachers", {}, token),
      getAdminStudents: (token, q = "", sem = 0, br = "all") => {
        let url = `/api/admin/students?q=${encodeURIComponent(q)}`;
        if (sem) url += `&semester=${sem}`;
        if (br && br !== "all") url += `&branch=${encodeURIComponent(br)}`;
        return api.request(url, {}, token);
      },
      updateStudentSemester: (token, userId, semester, branch = null) => api.request(`/api/admin/students/${userId}/semester`, { method: "PATCH", body: JSON.stringify({ semester, branch }) }, token),
      toggleUserStatus: (token, userId) => api.request(`/api/admin/users/${userId}/toggle-status`, { method: "PATCH" }, token),
      getAcademicMeta: (token) => api.request("/api/admin/academic/meta", {}, token),
      addAcademicSubject: (token, payload) => api.request("/api/admin/academic/subject", { method: "POST", body: JSON.stringify(payload) }, token),
      getAdminAuditLogs: (token) => api.request("/api/admin/audit-logs", {}, token),

      register: (email, password, full_name, role) => api.request("/api/auth/register", { method: "POST", body: JSON.stringify({ email, password, full_name, role }) }),
      getQuickToken: (role = "student") => api.request(`/api/auth/quick-token?role=${role}`),
      getDashboard: (token, role) => api.request(`/api/dashboard/${role}`, {}, token),
      getMaterials: (token) => api.request("/api/materials", {}, token),
      uploadMaterial: (token, payload) => api.request("/api/materials", { method: "POST", body: JSON.stringify(payload) }, token),
      askAI: (token, message) => api.request("/api/ai/chat", { method: "POST", body: JSON.stringify({ message }) }, token),
      getTests: (token) => api.request("/api/tests", {}, token),
      submitTest: (token, payload) => api.request("/api/tests/submit", { method: "POST", body: JSON.stringify(payload) }, token),
      getQuestionBank: (token) => api.request("/api/tests/bank", {}, token),
      runCode: (token, payload) => api.request("/api/tests/code/run", { method: "POST", body: JSON.stringify(payload) }, token),
      submitCode: (token, payload) => api.request("/api/tests/code/submit", { method: "POST", body: JSON.stringify(payload) }, token),
      getAptitudeQuestions: (token) => api.request("/api/tests/aptitude", {}, token),
      submitAptitude: (token, payload) => api.request("/api/tests/aptitude/submit", { method: "POST", body: JSON.stringify(payload) }, token),
      addTeacherMCQ: (token, payload) => api.request("/api/teacher/mcq", { method: "POST", body: JSON.stringify(payload) }, token),
      addTeacherCoding: (token, payload) => api.request("/api/teacher/coding", { method: "POST", body: JSON.stringify(payload) }, token),
      importTeacherPDF: (token, formData) => {
        return fetch("/api/teacher/pdf-import", {
          method: "POST",
          headers: token ? { "Authorization": `Bearer ${token}` } : {},
          body: formData,
        }).then(res => {
          if (!res.ok) throw new Error("Failed to parse PDF");
          return res.json();
        });
      },
      publishTeacherPDF: (token, payload) => api.request("/api/teacher/pdf-publish", { method: "POST", body: JSON.stringify(payload) }, token),
      getAttendance: (token) => api.request("/api/attendance", {}, token),
      getAttendanceOverview: (token, p) => api.request(
        `/api/attendance/overview?period=${p.period}&class_code=${encodeURIComponent(p.classCode)}&semester=${p.semester}`,
        {}, token),
      saveAttendance: (token, records) => api.request("/api/attendance/save", { method: "POST", body: JSON.stringify({ records }) }, token),
      getNotifications: (token) => api.request("/api/notifications", {}, token),
      markRead: (token, id) => api.request(`/api/notifications/${id}/read`, { method: "PATCH" }, token),
      markAllRead: (token) => api.request("/api/notifications/read-all", { method: "POST" }, token),
      getFlashcards: (token, deckId = 1) => api.request(`/api/flashcards/${deckId}`, {}, token),
      answerFlashcard: (token, card_id, remembered) => api.request("/api/flashcards/answer", { method: "POST", body: JSON.stringify({ card_id, remembered }) }, token),
      getAtRiskStudents: (token) => api.request("/api/faculty/at-risk-students", {}, token),
      askGroundedAI: (token, message) => api.request("/api/ai/grounded-chat", { method: "POST", body: JSON.stringify({ message }) }, token),
      uploadLargeMaterial: (token, formData, onProgress) => {
        return new Promise((resolve, reject) => {
          const xhr = new XMLHttpRequest();
          xhr.open("POST", "/api/materials/upload-file");
          if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
          xhr.upload.onprogress = (event) => {
            if (event.lengthComputable && onProgress) {
              const percent = Math.round((event.loaded / event.total) * 100);
              onProgress(percent);
            }
          };
          xhr.onload = () => {
            if (xhr.status >= 200 && xhr.status < 300) {
              try {
                const res = JSON.parse(xhr.responseText);
                resolve(res);
              } catch (e) {
                resolve({ message: "Upload complete" });
              }
            } else {
              let msg = "Upload failed";
              try {
                const err = JSON.parse(xhr.responseText);
                msg = err.detail || msg;
              } catch (_) {}
              reject(new Error(msg));
            }
          };
          xhr.onerror = () => reject(new Error("Network connection error during streaming upload"));
          xhr.send(formData);
        });
      },
    };

    // --- Modern Dual-Panel EduNova Authentication Component ---
    // --- Modern Dual-Panel EduNova Authentication Component (Upgraded 3-Role) ---
    function ModernLoginScreen({ onLoginSuccess }) {
      const [authMode, setAuthMode] = useState('signin'); // 'signin', 'student_signup', 'teacher_signup'

      // Form values
      const [name, setName] = useState('');
      const [username, setUsername] = useState('');
      const [email, setEmail] = useState('');
      const [password, setPassword] = useState('');
      const [confirmPassword, setConfirmPassword] = useState('');
      const [contactNumber, setContactNumber] = useState('');
      const [collegeName, setCollegeName] = useState('University Institute of Technology');
      const [department, setDepartment] = useState('Computer Science & Engineering');
      const [designation, setDesignation] = useState('Assistant Professor');
      const [experienceYears, setExperienceYears] = useState(5);
      const [invitationCode, setInvitationCode] = useState('EDUNOVA-FACULTY-2026');
      const [rememberMe, setRememberMe] = useState(true);
      const [agreeTerms, setAgreeTerms] = useState(false);

      // UI states
      const [showPassword, setShowPassword] = useState(false);
      const [isLoading, setIsLoading] = useState(false);
      const [toastMessage, setToastMessage] = useState(null);
      const [showForgotModal, setShowForgotModal] = useState(false);
      const [forgotEmail, setForgotEmail] = useState('');
      const [forgotSent, setForgotSent] = useState(false);

      // Error states
      const [emailError, setEmailError] = useState('');
      const [passwordError, setPasswordError] = useState('');
      const [usernameError, setUsernameError] = useState('');

      const showToast = (message, type = 'success') => {
        setToastMessage({ message, type });
        setTimeout(() => setToastMessage(null), 4000);
      };

      const validateEmail = (val) => {
        const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!val) return 'Email is required';
        if (!re.test(val)) return 'Please enter a valid institutional email';
        return '';
      };

      const validatePassword = (val) => {
        if (!val) return 'Password is required';
        if (val.length < 5) return 'Password must be at least 5 characters';
        return '';
      };

      const fillDemoAccount = (roleKey) => {
        setEmailError('');
        setPasswordError('');
        setUsernameError('');
        if (roleKey === 'student_ready') {
          setEmail('alex.student@edunova.edu');
          setPassword('12345');
          setAuthMode('signin');
          showToast('Loaded Alex Rivera (Semester 3 Student - Complete Profile)');
        } else if (roleKey === 'student_new') {
          setEmail('jordan.new@university.edu');
          setPassword('12345');
          setAuthMode('signin');
          showToast('Loaded Jordan Reed (Student - Incomplete Profile for Onboarding Test)');
        } else if (roleKey === 'teacher_approved') {
          setEmail('sarah.mentor@edunova.edu');
          setPassword('12345');
          setAuthMode('signin');
          showToast('Loaded Prof. Sarah Chen (Approved Faculty - Teacher ID TEA-2026-001)');
        } else if (roleKey === 'teacher_pending') {
          setEmail('marcus.pending@university.edu');
          setPassword('12345');
          setAuthMode('signin');
          showToast('Loaded Prof. Marcus Vance (Pending Teacher Applicant)');
        } else if (roleKey === 'admin') {
          setEmail('admin@edunova.edu');
          setPassword('12345');
          setAuthMode('signin');
          showToast('Loaded System Administrator (Full Platform Oversight)');
        }
      };

      const handleSubmit = async (e) => {
        e.preventDefault();
        setEmailError('');
        setPasswordError('');
        setUsernameError('');

        if (authMode === 'signin') {
          if (!email.trim()) { setEmailError('Please enter your email or username'); return; }
          const pErr = validatePassword(password);
          if (pErr) { setPasswordError(pErr); return; }

          setIsLoading(true);
          try {
            const res = await api.login(email.trim(), password);
            localStorage.setItem("edunova_token", res.access_token);
            localStorage.setItem("edunova_user", JSON.stringify(res));
            showToast(`Welcome, ${res.full_name}! Redirecting...`, 'success');
            setTimeout(() => onLoginSuccess(res), 400);
          } catch (err) {
            showToast(err.message, 'error');
            setEmailError(err.message);
          } finally {
            setIsLoading(false);
          }
        } else if (authMode === 'student_signup') {
          const eErr = validateEmail(email);
          const pErr = validatePassword(password);
          if (!name.trim()) { setEmailError('Please enter your full name'); return; }
          if (!username.trim() || username.length < 3) { setUsernameError('Username must be at least 3 characters'); return; }
          if (eErr) { setEmailError(eErr); return; }
          if (pErr) { setPasswordError(pErr); return; }
          if (password !== confirmPassword) { setPasswordError('Passwords do not match'); return; }
          if (!agreeTerms) { showToast('Please accept the Institutional Terms & Privacy Policy', 'error'); return; }

          setIsLoading(true);
          try {
            const res = await api.registerStudent({
              full_name: name.trim(),
              username: username.toLowerCase().trim(),
              email: email.trim(),
              password: password
            });
            localStorage.setItem("edunova_token", res.access_token);
            localStorage.setItem("edunova_user", JSON.stringify(res));
            showToast(`Account registered! Please complete your academic profile.`, 'success');
            setTimeout(() => onLoginSuccess(res), 500);
          } catch (err) {
            showToast(err.message, 'error');
            setEmailError(err.message);
          } finally {
            setIsLoading(false);
          }
        } else if (authMode === 'teacher_signup') {
          const eErr = validateEmail(email);
          const pErr = validatePassword(password);
          if (!name.trim()) { setEmailError('Please enter your faculty full name'); return; }
          if (!username.trim()) { setUsernameError('Username is required'); return; }
          if (eErr) { setEmailError(eErr); return; }
          if (pErr) { setPasswordError(pErr); return; }
          if (password !== confirmPassword) { setPasswordError('Passwords do not match'); return; }
          if (invitationCode.trim() !== 'EDUNOVA-FACULTY-2026') {
            showToast('Invalid institutional registration code. Use EDUNOVA-FACULTY-2026 for demo registration.', 'error');
            return;
          }

          setIsLoading(true);
          try {
            const res = await api.registerTeacher({
              full_name: name.trim(),
              username: username.toLowerCase().trim(),
              email: email.trim(),
              password: password,
              contact_number: contactNumber.trim(),
              college_name: collegeName.trim(),
              department: department,
              designation: designation,
              experience_years: parseInt(experienceYears, 10) || 5,
              invitation_code: invitationCode.trim()
            });
            localStorage.setItem("edunova_token", res.access_token);
            localStorage.setItem("edunova_user", JSON.stringify(res));
            showToast(`Faculty application submitted! Status: Pending Approval.`, 'success');
            setTimeout(() => onLoginSuccess(res), 500);
          } catch (err) {
            showToast(err.message, 'error');
            setEmailError(err.message);
          } finally {
            setIsLoading(false);
          }
        }
      };

      const handleForgotSubmit = (e) => {
        e.preventDefault();
        if (!validateEmail(forgotEmail)) {
          setForgotSent(true);
          setTimeout(() => {
            setForgotSent(false);
            setShowForgotModal(false);
            showToast(`Password recovery link dispatched to ${forgotEmail}`, 'success');
            setForgotEmail('');
          }, 1200);
        } else {
          showToast('Please enter a valid email address', 'error');
        }
      };

      return (
        <div className="min-h-screen w-full bg-[#F4FBF8] text-[#174D3D] flex items-center justify-center p-3 sm:p-6 md:p-8 font-sans antialiased">
          {/* Toast Notification */}
          {toastMessage && (
            <div className={`fixed top-6 right-6 z-50 flex items-center gap-3 px-4 py-3 rounded-2xl border shadow-xl backdrop-blur-md transition-all ${
              toastMessage.type === 'error' ? 'bg-rose-50 border-rose-200 text-rose-800' : 'bg-[#E8F7F1] border-[#159B73]/30 text-[#174D3D]'
            }`}>
              <i className={`fas ${toastMessage.type === 'error' ? 'fa-circle-exclamation text-rose-600' : 'fa-circle-check text-[#159B73]'} text-base`}></i>
              <span className="text-xs font-semibold">{toastMessage.message}</span>
            </div>
          )}

          {/* Forgot Password Modal */}
          {showForgotModal && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
              <div className="bg-white rounded-3xl border border-[#D5EADB] shadow-2xl max-w-md w-full p-6 relative">
                <button onClick={() => setShowForgotModal(false)} className="absolute top-4 right-4 text-slate-400 hover:text-slate-600 p-1">
                  <i className="fas fa-xmark text-lg"></i>
                </button>
                <div className="w-12 h-12 rounded-2xl bg-[#E8F7F1] text-[#159B73] flex items-center justify-center mb-4">
                  <i className="fas fa-key text-xl"></i>
                </div>
                <h3 className="text-lg font-bold text-[#174D3D]">Reset Your Credentials</h3>
                <p className="text-xs text-[#52796F] mt-1 mb-4 leading-relaxed">
                  Enter your registered institutional email address. An encrypted one-time reset link will be dispatched immediately.
                </p>
                <form onSubmit={handleForgotSubmit} className="space-y-4">
                  <div>
                    <label className="block text-[11px] font-bold text-[#174D3D] uppercase tracking-wider mb-1">Email address</label>
                    <input
                      type="email"
                      value={forgotEmail}
                      onChange={e => setForgotEmail(e.target.value)}
                      placeholder="student@institution.edu"
                      required
                      className="w-full px-3.5 py-2.5 text-xs bg-[#F4FBF8] border border-[#D4E8DC] rounded-xl outline-none focus:ring-2 focus:ring-[#159B73]"
                    />
                  </div>
                  <div className="flex justify-end gap-2 pt-2">
                    <button type="button" onClick={() => setShowForgotModal(false)} className="px-4 py-2 text-xs font-semibold text-slate-500 rounded-xl hover:bg-slate-100">Cancel</button>
                    <button type="submit" disabled={forgotSent} className="px-5 py-2 bg-[#159B73] text-white text-xs font-bold rounded-xl shadow-md">
                      {forgotSent ? 'Sending...' : 'Send Reset Link'}
                    </button>
                  </div>
                </form>
              </div>
            </div>
          )}

          {/* Main Card */}
          <div className="w-full max-w-5xl bg-white rounded-3xl shadow-[0_12px_45px_rgba(21,155,115,0.08)] border border-[#DFEEE5] overflow-hidden grid grid-cols-1 lg:grid-cols-12 min-h-[640px]">
            
            {/* Left Hero Panel */}
            <div className="lg:col-span-5 bg-[#E8F7F1]/80 p-6 sm:p-8 flex flex-col justify-between border-b lg:border-b-0 lg:border-r border-[#D9ECE1] relative">
              <div>
                <div className="flex items-center gap-2 mb-3">
                  <span className="px-2.5 py-0.5 rounded-full text-[10px] font-extrabold uppercase tracking-wider bg-[#D3EFE4] text-[#159B73] border border-[#BCE5D5]">
                    HUMAN REALITY • V3.2
                  </span>
                  <span className="text-[11px] text-[#52796F] font-bold">Role-Based Ecosystem</span>
                </div>

                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-2xl bg-gradient-to-tr from-[#159B73] to-[#1AC492] text-white flex items-center justify-center shadow-md shadow-[#159B73]/25">
                    <i className="fas fa-graduation-cap text-lg"></i>
                  </div>
                  <h1 className="text-2xl font-black tracking-tight text-[#174D3D]">
                    Edu<span className="text-[#159B73]">Nova</span>
                  </h1>
                </div>

                <h2 className="text-xl sm:text-2xl font-black text-[#174D3D] mt-4 leading-tight">
                  Academic Platform & <br />
                  <span className="text-[#159B73]">Semester Access Control</span>
                </h2>
                <p className="mt-2 text-xs text-[#486D61] leading-relaxed">
                  Strict semester partitioning, faculty verification, and AI-grounded academic mentorship.
                </p>

                {/* Role Feature Cards */}
                <div className="mt-5 space-y-2.5">
                  <div className="p-3 bg-white/90 rounded-2xl border border-[#D5EADB] shadow-xs flex items-center gap-3">
                    <div className="w-8 h-8 rounded-xl bg-emerald-100 text-[#159B73] flex items-center justify-center text-xs shrink-0">
                      <i className="fas fa-user-graduate"></i>
                    </div>
                    <div>
                      <h4 className="text-xs font-bold text-[#174D3D]">Students</h4>
                      <p className="text-[10px] text-[#63877C]">Semester-partitioned study PDFs, AI tutor & active recall decks.</p>
                    </div>
                  </div>

                  <div className="p-3 bg-white/90 rounded-2xl border border-[#D5EADB] shadow-xs flex items-center gap-3">
                    <div className="w-8 h-8 rounded-xl bg-teal-100 text-teal-700 flex items-center justify-center text-xs shrink-0">
                      <i className="fas fa-chalkboard-user"></i>
                    </div>
                    <div>
                      <h4 className="text-xs font-bold text-[#174D3D]">Teachers</h4>
                      <p className="text-[10px] text-[#63877C]">Institutional verification, curriculum publishing & evaluation bank.</p>
                    </div>
                  </div>

                  <div className="p-3 bg-white/90 rounded-2xl border border-[#D5EADB] shadow-xs flex items-center gap-3">
                    <div className="w-8 h-8 rounded-xl bg-blue-100 text-blue-700 flex items-center justify-center text-xs shrink-0">
                      <i className="fas fa-shield-halved"></i>
                    </div>
                    <div>
                      <h4 className="text-xs font-bold text-[#174D3D]">Administrators</h4>
                      <p className="text-[10px] text-[#63877C]">Faculty approval workflow, student registry & platform audit logs.</p>
                    </div>
                  </div>
                </div>
              </div>

              {/* Demo Account Switcher */}
              <div className="mt-6 pt-4 border-t border-[#D5EADB]">
                <p className="text-[10px] font-bold uppercase tracking-wider text-[#52796F] mb-2 flex items-center gap-1.5">
                  <i className="fas fa-wand-magic-sparkles text-[#159B73]"></i> Quick Demo Personas:
                </p>
                <div className="flex flex-wrap gap-1.5">
                  <button type="button" onClick={() => fillDemoAccount('student_ready')} className="px-2.5 py-1 text-[11px] font-semibold bg-white text-[#174D3D] border border-[#CDE5D9] rounded-lg hover:bg-emerald-50">
                    Alex (Student Sem 3)
                  </button>
                  <button type="button" onClick={() => fillDemoAccount('student_new')} className="px-2.5 py-1 text-[11px] font-semibold bg-white text-amber-800 border border-amber-200 rounded-lg hover:bg-amber-50">
                    Jordan (New Student)
                  </button>
                  <button type="button" onClick={() => fillDemoAccount('teacher_approved')} className="px-2.5 py-1 text-[11px] font-semibold bg-white text-[#159B73] border border-[#CDE5D9] rounded-lg hover:bg-emerald-50">
                    Prof. Chen (Faculty)
                  </button>
                  <button type="button" onClick={() => fillDemoAccount('teacher_pending')} className="px-2.5 py-1 text-[11px] font-semibold bg-white text-slate-700 border border-slate-200 rounded-lg hover:bg-slate-50">
                    Prof. Vance (Pending)
                  </button>
                  <button type="button" onClick={() => fillDemoAccount('admin')} className="px-2.5 py-1 text-[11px] font-bold bg-[#174D3D] text-white rounded-lg hover:bg-[#123e31]">
                    Admin
                  </button>
                </div>
              </div>
            </div>

            {/* Right Form Panel */}
            <div className="lg:col-span-7 bg-white p-6 sm:p-8 flex flex-col justify-between">
              <div>
                {/* Mode Selector Tabs */}
                <div className="flex items-center justify-between border-b border-slate-100 pb-3 mb-5">
                  <div className="flex bg-[#F4FBF8] p-1 rounded-2xl border border-[#D5EADB]">
                    <button
                      type="button"
                      onClick={() => { setAuthMode('signin'); setEmailError(''); setPasswordError(''); }}
                      className={`px-3 py-1.5 text-xs font-bold rounded-xl transition ${authMode === 'signin' ? 'bg-white text-[#174D3D] shadow-sm' : 'text-[#5C8275] hover:text-[#174D3D]'}`}
                    >
                      Sign In
                    </button>
                    <button
                      type="button"
                      onClick={() => { setAuthMode('student_signup'); setEmailError(''); setPasswordError(''); }}
                      className={`px-3 py-1.5 text-xs font-bold rounded-xl transition ${authMode === 'student_signup' ? 'bg-white text-[#174D3D] shadow-sm' : 'text-[#5C8275] hover:text-[#174D3D]'}`}
                    >
                      Student Register
                    </button>
                    <button
                      type="button"
                      onClick={() => { setAuthMode('teacher_signup'); setEmailError(''); setPasswordError(''); }}
                      className={`px-3 py-1.5 text-xs font-bold rounded-xl transition ${authMode === 'teacher_signup' ? 'bg-white text-[#174D3D] shadow-sm' : 'text-[#5C8275] hover:text-[#174D3D]'}`}
                    >
                      Faculty Apply
                    </button>
                  </div>
                </div>

                {/* Form Titles */}
                <div className="mb-4">
                  <h3 className="text-xl font-bold text-[#174D3D]">
                    {authMode === 'signin' && 'Access Your Workspace'}
                    {authMode === 'student_signup' && 'Create Student Account'}
                    {authMode === 'teacher_signup' && 'Institutional Faculty Application'}
                  </h3>
                  <p className="text-xs text-[#52796F] mt-0.5">
                    {authMode === 'signin' && 'Sign in with your email or username to access your dashboard.'}
                    {authMode === 'student_signup' && 'Register your student credentials to begin guided profile setup.'}
                    {authMode === 'teacher_signup' && 'Submit academic credentials for institutional review and Teacher ID issuance.'}
                  </p>
                </div>

                <form onSubmit={handleSubmit} noValidate className="space-y-3">
                  
                  {/* Student Sign Up Fields */}
                  {authMode === 'student_signup' && (
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      <div>
                        <label className="block text-[11px] font-bold text-[#174D3D] uppercase tracking-wider mb-1">Full Name</label>
                        <input
                          type="text"
                          value={name}
                          onChange={e => setName(e.target.value)}
                          placeholder="Alex Rivera"
                          className="w-full px-3.5 py-2 text-xs bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                        />
                      </div>
                      <div>
                        <label className="block text-[11px] font-bold text-[#174D3D] uppercase tracking-wider mb-1">Username (@)</label>
                        <input
                          type="text"
                          value={username}
                          onChange={e => setUsername(e.target.value)}
                          placeholder="alexrivera"
                          className="w-full px-3.5 py-2 text-xs bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                        />
                      </div>
                    </div>
                  )}

                  {/* Teacher Sign Up Fields */}
                  {authMode === 'teacher_signup' && (
                    <div className="space-y-3">
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div>
                          <label className="block text-[11px] font-bold text-[#174D3D] uppercase tracking-wider mb-1">Full Name with Title</label>
                          <input
                            type="text"
                            value={name}
                            onChange={e => setName(e.target.value)}
                            placeholder="Dr. Elena Vance"
                            className="w-full px-3.5 py-2 text-xs bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                          />
                        </div>
                        <div>
                          <label className="block text-[11px] font-bold text-[#174D3D] uppercase tracking-wider mb-1">Username (@)</label>
                          <input
                            type="text"
                            value={username}
                            onChange={e => setUsername(e.target.value)}
                            placeholder="elenavance"
                            className="w-full px-3.5 py-2 text-xs bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                          />
                        </div>
                      </div>

                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div>
                          <label className="block text-[11px] font-bold text-[#174D3D] uppercase tracking-wider mb-1">Department / Branch</label>
                          <select
                            value={department}
                            onChange={e => setDepartment(e.target.value)}
                            className="w-full px-3 py-2 text-xs bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                          >
                            <option value="Computer Science & Engineering">Computer Science & Engineering</option>
                            <option value="Information Science & Engineering">Information Science & Engineering</option>
                            <option value="Artificial Intelligence & Machine Learning">AI & Machine Learning</option>
                            <option value="Electronics & Communication">Electronics & Communication</option>
                          </select>
                        </div>
                        <div>
                          <label className="block text-[11px] font-bold text-[#174D3D] uppercase tracking-wider mb-1">Designation</label>
                          <input
                            type="text"
                            value={designation}
                            onChange={e => setDesignation(e.target.value)}
                            placeholder="Associate Professor"
                            className="w-full px-3.5 py-2 text-xs bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                          />
                        </div>
                      </div>

                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div>
                          <label className="block text-[11px] font-bold text-[#174D3D] uppercase tracking-wider mb-1">Contact Phone</label>
                          <input
                            type="text"
                            value={contactNumber}
                            onChange={e => setContactNumber(e.target.value)}
                            placeholder="+1 (555) 019-2834"
                            className="w-full px-3.5 py-2 text-xs bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                          />
                        </div>
                        <div>
                          <label className="block text-[11px] font-bold text-[#174D3D] uppercase tracking-wider mb-1">Institution Code</label>
                          <input
                            type="text"
                            value={invitationCode}
                            onChange={e => setInvitationCode(e.target.value)}
                            placeholder="EDUNOVA-FACULTY-2026"
                            className="w-full px-3.5 py-2 text-xs bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                          />
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Email Field */}
                  <div>
                    <label className="block text-[11px] font-bold text-[#174D3D] uppercase tracking-wider mb-1">
                      {authMode === 'signin' ? 'Email Address or Username' : 'Institutional Email Address'}
                    </label>
                    <div className="relative">
                      <span className="absolute inset-y-0 left-0 pl-3 flex items-center text-slate-400">
                        <i className="fas fa-envelope text-xs"></i>
                      </span>
                      <input
                        type={authMode === 'signin' ? 'text' : 'email'}
                        value={email}
                        onChange={e => { setEmail(e.target.value); setEmailError(''); }}
                        placeholder={authMode === 'signin' ? 'student@university.edu or alexrivera' : 'faculty@institution.edu'}
                        className={`w-full pl-9 pr-4 py-2.5 text-xs bg-[#F4FBF8] rounded-xl border ${emailError ? 'border-rose-400' : 'border-[#D2E7DA]'} outline-none focus:ring-2 focus:ring-[#159B73]`}
                      />
                    </div>
                    {emailError && <p className="text-[10px] text-rose-600 mt-0.5 font-medium">{emailError}</p>}
                  </div>

                  {/* Password Field */}
                  <div>
                    <div className="flex justify-between items-center mb-1">
                      <label className="text-[11px] font-bold text-[#174D3D] uppercase tracking-wider">Password</label>
                      {authMode === 'signin' && (
                        <button type="button" onClick={() => setShowForgotModal(true)} className="text-[11px] text-[#159B73] hover:underline font-semibold">
                          Forgot password?
                        </button>
                      )}
                    </div>
                    <div className="relative">
                      <span className="absolute inset-y-0 left-0 pl-3 flex items-center text-slate-400">
                        <i className="fas fa-lock text-xs"></i>
                      </span>
                      <input
                        type={showPassword ? 'text' : 'password'}
                        value={password}
                        onChange={e => { setPassword(e.target.value); setPasswordError(''); }}
                        placeholder="••••••••••••"
                        className={`w-full pl-9 pr-10 py-2.5 text-xs bg-[#F4FBF8] rounded-xl border ${passwordError ? 'border-rose-400' : 'border-[#D2E7DA]'} outline-none focus:ring-2 focus:ring-[#159B73]`}
                      />
                      <button
                        type="button"
                        onClick={() => setShowPassword(!showPassword)}
                        className="absolute inset-y-0 right-0 pr-3 flex items-center text-slate-400 hover:text-slate-600"
                      >
                        <i className={`fas ${showPassword ? 'fa-eye-slash' : 'fa-eye'} text-xs`}></i>
                      </button>
                    </div>
                    {passwordError && <p className="text-[10px] text-rose-600 mt-0.5 font-medium">{passwordError}</p>}
                  </div>

                  {/* Confirm Password on Sign Up */}
                  {authMode !== 'signin' && (
                    <div>
                      <label className="block text-[11px] font-bold text-[#174D3D] uppercase tracking-wider mb-1">Confirm Password</label>
                      <input
                        type="password"
                        value={confirmPassword}
                        onChange={e => setConfirmPassword(e.target.value)}
                        placeholder="••••••••••••"
                        className="w-full px-3.5 py-2 text-xs bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                      />
                    </div>
                  )}

                  {/* Terms & Conditions */}
                  {authMode !== 'signin' && (
                    <label className="flex items-start gap-2 pt-1 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={agreeTerms}
                        onChange={e => setAgreeTerms(e.target.checked)}
                        className="w-3.5 h-3.5 mt-0.5 rounded border-[#C4E1D1] text-[#159B73] accent-[#159B73]"
                      />
                      <span className="text-[10px] text-[#4E7366]">
                        I certify this account represents accredited university enrollment / appointment.
                      </span>
                    </label>
                  )}

                  {/* Submit Button */}
                  <button
                    type="submit"
                    disabled={isLoading}
                    className="w-full mt-2 py-2.5 px-4 bg-[#159B73] hover:bg-[#128864] text-white font-bold text-xs rounded-xl shadow-md transition-all flex items-center justify-center gap-2"
                  >
                    {isLoading ? (
                      <i className="fas fa-circle-notch fa-spin"></i>
                    ) : (
                      <>
                        <span>
                          {authMode === 'signin' && 'Sign In to Workspace'}
                          {authMode === 'student_signup' && 'Create Account & Begin Profile'}
                          {authMode === 'teacher_signup' && 'Submit Application for Review'}
                        </span>
                        <i className="fas fa-arrow-right text-xs"></i>
                      </>
                    )}
                  </button>
                </form>
              </div>

              {/* Security Badge */}
              <div className="pt-4 border-t border-slate-100 flex items-center justify-between text-[10px] text-slate-400">
                <span className="flex items-center gap-1.5"><i className="fas fa-lock text-emerald-600"></i> Role Verified by Backend</span>
                <span>EduNova Security Protocol</span>
              </div>
            </div>

          </div>
        </div>
      );
    }

    function Tooltip({ text, children }) {
      const [show, setShow] = useState(false);
      return (
        <div className="relative flex items-center" onMouseEnter={() => setShow(true)} onMouseLeave={() => setShow(false)} onFocus={() => setShow(true)} onBlur={() => setShow(false)}>
          {children}
          {show && (
            <div role="tooltip" className="absolute left-full ml-3 px-2.5 py-1 z-50 text-[11px] font-semibold tracking-wide text-white bg-slate-900 dark:bg-slate-800 border border-slate-700 rounded-md shadow-md whitespace-nowrap pointer-events-none">
              {text}
            </div>
          )}
        </div>
      );
    }

    function Skeleton({ className = "" }) {
      return <div className={`bg-emerald-100/60 dark:bg-slate-800 rounded-xl skeleton-pulse ${className}`} aria-hidden="true" />;
    }

    function ConfirmationDialog({ open, title, description, confirmText = "Confirm", confirmVariant = "primary", onConfirm, onCancel, loading }) {
      const cancelRef = useRef(null);
      useEffect(() => {
        if (open) {
          cancelRef.current?.focus();
          const handleKey = e => { if (e.key === "Escape") onCancel(); };
          window.addEventListener("keydown", handleKey);
          return () => window.removeEventListener("keydown", handleKey);
        }
      }, [open]);

      if (!open) return null;
      return ReactDOM.createPortal(
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div role="alertdialog" aria-modal="true" aria-labelledby="dialog-title" aria-describedby="dialog-desc"
               className="bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-3xl p-6 max-w-md w-full shadow-2xl space-y-4">
            <h3 id="dialog-title" className="text-base font-bold text-slate-900 dark:text-slate-100">{title}</h3>
            <p id="dialog-desc" className="text-xs text-slate-600 dark:text-slate-400 leading-relaxed">{description}</p>
            <div className="flex justify-end gap-3 pt-2">
              <button ref={cancelRef} onClick={onCancel} disabled={loading}
                      className="px-4 py-2 text-xs font-semibold rounded-xl bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-200 focus-ring">
                Cancel
              </button>
              <button onClick={onConfirm} disabled={loading}
                      className={`px-4 py-2 text-xs font-semibold rounded-xl text-white focus-ring flex items-center gap-2 ${confirmVariant === "danger" ? "bg-red-600 hover:bg-red-700" : "bg-[#356f5b] hover:opacity-95"}`}>
                {loading && <i className="fas fa-circle-notch fa-spin"></i>}
                {confirmText}
              </button>
            </div>
          </div>
        </div>,
        document.getElementById("modal-root")
      );
    }

    function EmptyState({ icon = "fa-folder-open", title, description, actionText, onAction }) {
      return (
        <div className="p-12 text-center flex flex-col items-center justify-center rounded-3xl border-2 border-dashed border-emerald-200/70 dark:border-slate-800 bg-white/50 dark:bg-slate-900/40">
          <div className="w-12 h-12 rounded-2xl bg-emerald-50 dark:bg-emerald-950/40 text-emerald-600 dark:text-emerald-400 flex items-center justify-center mb-3">
            <i className={`fas ${icon} text-xl`}></i>
          </div>
          <h4 className="text-sm font-bold text-slate-800 dark:text-slate-200">{title}</h4>
          <p className="text-xs text-slate-500 dark:text-slate-400 max-w-xs mt-1 mb-4 leading-relaxed">{description}</p>
          {actionText && (
            <button onClick={onAction} className="px-4 py-2 bg-[#356f5b] hover:opacity-95 text-white rounded-xl text-xs font-bold focus-ring">
              {actionText}
            </button>
          )}
        </div>
      );
    }

    function ErrorState({ title = "Sync Failure", message, onRetry }) {
      return (
        <div className="p-8 text-center flex flex-col items-center bg-red-50/60 dark:bg-red-950/20 border border-red-200 dark:border-red-900/50 rounded-2xl">
          <i className="fas fa-circle-exclamation text-red-600 dark:text-red-400 text-2xl mb-2"></i>
          <h4 className="text-sm font-bold text-red-900 dark:text-red-300">{title}</h4>
          <p className="text-xs text-red-700 dark:text-red-400 mt-1 mb-4 max-w-md">{message}</p>
          {onRetry && (
            <button onClick={onRetry} className="px-4 py-2 bg-red-600 text-white rounded-xl text-xs font-semibold focus-ring">
              <i className="fas fa-rotate-right mr-1.5"></i>Retry
            </button>
          )}
        </div>
      );
    }

    // --- Global Command Palette (Ctrl+K) ---
    function GlobalSearchDialog({ open, onClose, onSelect, dataIndex }) {
      const [query, setQuery] = useState("");
      const inputRef = useRef(null);

      useEffect(() => {
        if (open) {
          setQuery("");
          setTimeout(() => inputRef.current?.focus(), 50);
          const handleKey = e => { if (e.key === "Escape") onClose(); };
          window.addEventListener("keydown", handleKey);
          return () => window.removeEventListener("keydown", handleKey);
        }
      }, [open]);

      const filtered = useMemo(() => {
        if (!query.trim()) return [];
        const q = query.toLowerCase();
        return dataIndex.filter(item => item.label.toLowerCase().includes(q) || item.category.toLowerCase().includes(q));
      }, [query, dataIndex]);

      if (!open) return null;
      return ReactDOM.createPortal(
        <div className="fixed inset-0 z-50 flex items-start justify-center pt-20 p-4 bg-black/60 backdrop-blur-sm" onClick={onClose}>
          <div className="bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-3xl w-full max-w-xl shadow-2xl overflow-hidden" onClick={e => e.stopPropagation()}>
            <div className="flex items-center px-4 py-3.5 border-b border-slate-100 dark:border-slate-800">
              <i className="fas fa-magnifying-glass text-emerald-600 mr-3 text-xs"></i>
              <input ref={inputRef} value={query} onChange={e => setQuery(e.target.value)}
                     placeholder="Search subjects, resources, exams, actions..."
                     className="w-full bg-transparent text-xs text-slate-900 dark:text-slate-100 placeholder-slate-400 outline-none" />
              <kbd className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-50 dark:bg-slate-800 text-emerald-700 dark:text-emerald-400">ESC</kbd>
            </div>
            <div className="max-h-80 overflow-y-auto p-2 divide-y divide-slate-100 dark:divide-slate-800">
              {query.trim() && filtered.length === 0 && (
                <div className="py-8 text-center text-xs text-slate-400">No results found for "{query}"</div>
              )}
              {filtered.map((item, idx) => (
                <button key={idx} onClick={() => { onSelect(item); onClose(); }}
                        className="w-full text-left p-2.5 rounded-2xl hover:bg-emerald-50/60 dark:hover:bg-slate-800 flex items-center justify-between group focus-ring">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-xl bg-emerald-50 dark:bg-emerald-950/40 text-emerald-600 flex items-center justify-center text-xs">
                      <i className={`fas ${item.icon}`}></i>
                    </div>
                    <div>
                      <p className="text-xs font-semibold text-slate-800 dark:text-slate-200">{item.label}</p>
                      <p className="text-[10px] text-slate-400">{item.detail}</p>
                    </div>
                  </div>
                  <span className="text-[10px] font-semibold text-emerald-600 dark:text-emerald-400 uppercase tracking-wider">{item.category}</span>
                </button>
              ))}
              {!query.trim() && (
                <div className="p-3 text-[11px] text-slate-400 flex justify-between">
                  <span>Quick Jumps</span>
                  <span>Use keywords: "DBMS", "Attendance", "Test"</span>
                </div>
              )}
            </div>
          </div>
        </div>,
        document.getElementById("modal-root")
      );
    }

    // --- Header Notifications Dropdown ---
    function NotificationPanel({ token, open, onClose }) {
      const [notifications, setNotifications] = useState([]);
      const [loading, setLoading] = useState(true);

      const refresh = useCallback(() => {
        api.getNotifications(token)
          .then(setNotifications)
          .catch(() => toast.error("Notification synchronization error"))
          .finally(() => setLoading(false));
      }, [token]);

      useEffect(() => { if (open) refresh(); }, [open, refresh]);

      const markAsRead = async (id) => {
        await api.markRead(token, id);
        setNotifications(prev => prev.map(n => n.id === id ? { ...n, is_read: true } : n));
      };

      const markAllAsRead = async () => {
        await api.markAllRead(token);
        setNotifications(prev => prev.map(n => ({ ...n, is_read: true })));
        toast.success("All notifications marked as read");
      };

      if (!open) return null;
      return (
        <div className="absolute right-0 top-full mt-2 w-80 sm:w-96 bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-3xl shadow-xl z-50 overflow-hidden">
          <div className="p-3.5 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between bg-emerald-50/40 dark:bg-slate-800/50">
            <h4 className="text-xs font-bold text-slate-800 dark:text-slate-200 flex items-center gap-1.5">
              <i className="fas fa-bell text-emerald-600"></i>Alert Center
            </h4>
            <button onClick={markAllAsRead} className="text-[11px] font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">
              Mark all read
            </button>
          </div>
          <div className="max-h-80 overflow-y-auto divide-y divide-slate-100 dark:divide-slate-800">
            {loading && <div className="p-4 space-y-2"><Skeleton className="h-4 w-3/4"/><Skeleton className="h-4 w-1/2"/></div>}
            {!loading && notifications.length === 0 && (
              <div className="py-8 text-center text-xs text-slate-400">No active alerts recorded.</div>
            )}
            {!loading && notifications.map(n => (
              <div key={n.id} onClick={() => !n.is_read && markAsRead(n.id)}
                   className={`p-3.5 text-left transition flex gap-3 cursor-pointer ${n.is_read ? "opacity-60 bg-transparent" : "bg-emerald-50/30 dark:bg-emerald-950/20"}`}>
                <div className="w-2 h-2 rounded-full mt-1.5 shrink-0 bg-emerald-500" style={{ opacity: n.is_read ? 0 : 1 }} />
                <div className="space-y-0.5 flex-1">
                  <div className="flex justify-between items-baseline">
                    <p className="text-xs font-bold text-slate-800 dark:text-slate-200">{n.title}</p>
                    <span className="text-[10px] text-slate-400">{n.timestamp}</span>
                  </div>
                  <p className="text-[11px] text-slate-600 dark:text-slate-400 leading-normal">{n.message}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      );
    }

    // --- Charts ---
    function AnalyticsCharts({ data, role }) {
      const attendance = role === "student" ? data.attendance : data.average_attendance;
      const progress = role === "student" ? data.overall_progress : data.average_performance;

      const radius = 34;
      const circum = 2 * Math.PI * radius;
      const strokeDashoffset = circum - (progress / 100) * circum;

      return (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="p-6 bg-white dark:bg-slate-900 border border-emerald-100/80 dark:border-slate-800 rounded-3xl shadow-sm space-y-4">
            <div className="flex justify-between items-center">
              <div>
                <h4 className="text-xs font-bold text-slate-800 dark:text-slate-200">Syllabus Completion Index</h4>
                <p className="text-[11px] text-slate-500">Verified progress against accredited benchmarks</p>
              </div>
              <span className="text-xs font-extrabold text-emerald-600 dark:text-emerald-400">{progress}%</span>
            </div>
            <div className="flex items-center justify-center py-2">
              <svg className="w-28 h-28 transform -rotate-90" aria-hidden="true">
                <circle cx="56" cy="56" r={radius} className="text-emerald-50 dark:text-slate-800 stroke-current" strokeWidth="8" fill="transparent" />
                <circle cx="56" cy="56" r={radius} className="text-emerald-500 stroke-current" strokeWidth="8" strokeLinecap="round" fill="transparent"
                        strokeDasharray={circum} strokeDashoffset={strokeDashoffset} style={{ transition: "stroke-dashoffset 0.6s ease" }} />
              </svg>
            </div>
            <div className="grid grid-cols-2 text-center text-xs border-t border-slate-100 dark:border-slate-800 pt-3">
              <div>
                <p className="text-[10px] text-slate-400">Attendance Baseline</p>
                <p className="font-bold text-slate-700 dark:text-slate-300 mt-0.5">{attendance}%</p>
              </div>
              <div>
                <p className="text-[10px] text-slate-400">Evaluations Recorded</p>
                <p className="font-bold text-slate-700 dark:text-slate-300 mt-0.5">{role === "student" ? data.tests_completed : data.total_classes}</p>
              </div>
            </div>
          </div>

          <div className="p-6 bg-white dark:bg-slate-900 border border-emerald-100/80 dark:border-slate-800 rounded-3xl shadow-sm space-y-3">
            <div className="flex justify-between items-center">
              <div>
                <h4 className="text-xs font-bold text-slate-800 dark:text-slate-200">Subject Distribution Matrix</h4>
                <p className="text-[11px] text-slate-500">Performance load across registered domains</p>
              </div>
              <i className="fas fa-chart-simple text-emerald-500 text-xs"></i>
            </div>
            <div className="space-y-3 pt-2">
              {(data.subjects || data.classes || []).map((s, idx) => {
                const label = s.name || s.title;
                const score = s.progress || 80;
                return (
                  <div key={idx} className="space-y-1">
                    <div className="flex justify-between text-xs">
                      <span className="font-semibold text-slate-700 dark:text-slate-300">{label}</span>
                      <span className="text-emerald-600 dark:text-emerald-400 font-mono text-[11px] font-bold">{score}%</span>
                    </div>
                    <div className="h-2 rounded-full bg-emerald-50 dark:bg-slate-800 overflow-hidden">
                      <div className="h-full bg-gradient-to-r from-emerald-500 to-blue-500 rounded-full" style={{ width: `${score}%`, transition: "width 0.5s ease" }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      );
    }

    // --- Dashboard View ---
    function DashboardView({ token, user, onNavigate }) {
      const [data, setData] = useState(null);
      const [loading, setLoading] = useState(true);
      const [error, setError] = useState(null);

      const fetchMetrics = useCallback(() => {
        setLoading(true);
        setError(null);
        api.getDashboard(token, user.role)
          .then(setData)
          .catch(e => setError(e.message))
          .finally(() => setLoading(false));
      }, [token, user.role]);

      useEffect(() => { fetchMetrics(); }, [fetchMetrics]);

      if (loading) return (
        <div className="space-y-6">
          <Skeleton className="h-28 w-full rounded-3xl" />
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {[1, 2, 3, 4].map(i => <Skeleton key={i} className="h-24 w-full rounded-2xl" />)}
          </div>
          <Skeleton className="h-64 w-full rounded-3xl" />
        </div>
      );

      if (error) return <ErrorState message={error} onRetry={fetchMetrics} />;

      return (
        <div className="space-y-6">
          <div className="edunova-hero p-7 text-white rounded-[28px] shadow-xl flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
            <div>
              <span className="text-[10px] font-extrabold uppercase tracking-widest bg-white/20 px-3 py-1 rounded-full">Academic Session Active</span>
              <h2 className="text-xl font-extrabold mt-2">Welcome back, {user.full_name}</h2>
              <p className="text-xs text-emerald-100 mt-1">Platform operational status optimal. Grounded syllabus engine synchronized.</p>
            </div>
            {user.role === "student" ? (
              <div className="flex flex-wrap items-center gap-2">
                <div className="bg-white/15 backdrop-blur-md border border-white/25 px-4 py-2 rounded-2xl text-xs font-bold flex items-center gap-2">
                  <i className="fas fa-fire text-amber-400"></i>
                  <span>{data.study_streak_days} Day Study Velocity</span>
                </div>
                <button onClick={() => onNavigate("flashcards")}
                        className="px-4 py-2 bg-white text-emerald-800 rounded-xl text-xs font-bold hover:bg-emerald-50 focus-ring shadow flex items-center gap-1.5">
                  <i className="fas fa-layer-group text-emerald-600"></i>Recall Deck
                </button>
              </div>
            ) : (
              <div className="flex flex-wrap gap-2">
                <button onClick={() => onNavigate("intervention")}
                        className="px-4 py-2 bg-red-500/30 hover:bg-red-500/40 border border-red-300/50 text-white rounded-xl text-xs font-bold focus-ring shadow flex items-center gap-1.5">
                  <i className="fas fa-triangle-exclamation text-amber-300"></i>Early Warning
                </button>
                <button onClick={() => onNavigate("attendance")}
                        className="px-5 py-2.5 bg-white text-emerald-800 rounded-xl text-xs font-bold hover:bg-emerald-50 focus-ring shadow">
                  Record Roll Call
                </button>
              </div>
            )}
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {user.role === "student" ? (
              <>
                <div className="p-5 bg-white dark:bg-slate-900 border border-emerald-100/80 dark:border-slate-800 rounded-3xl shadow-sm">
                  <p className="text-[11px] font-semibold text-slate-500">Curriculum Progress</p>
                  <p className="text-2xl font-extrabold text-emerald-600 mt-1">{data.overall_progress}%</p>
                </div>
                <div className="p-5 bg-white dark:bg-slate-900 border border-emerald-100/80 dark:border-slate-800 rounded-3xl shadow-sm">
                  <p className="text-[11px] font-semibold text-slate-500">Attendance Index</p>
                  <p className="text-2xl font-extrabold text-teal-600 mt-1">{data.attendance}%</p>
                </div>
                <div className="p-5 bg-white dark:bg-slate-900 border border-emerald-100/80 dark:border-slate-800 rounded-3xl shadow-sm">
                  <p className="text-[11px] font-semibold text-slate-500">Completed Assessments</p>
                  <p className="text-2xl font-extrabold text-slate-800 dark:text-slate-100 mt-1">{data.tests_completed}</p>
                </div>
                <div className="p-5 bg-white dark:bg-slate-900 border border-emerald-100/80 dark:border-slate-800 rounded-3xl shadow-sm">
                  <p className="text-[11px] font-semibold text-slate-500">Due Tasks</p>
                  <p className="text-2xl font-extrabold text-amber-600 mt-1">{data.pending_assignments}</p>
                </div>
              </>
            ) : (
              <>
                <div className="p-5 bg-white dark:bg-slate-900 border border-emerald-100/80 dark:border-slate-800 rounded-3xl shadow-sm">
                  <p className="text-[11px] font-semibold text-slate-500">Students Enrolled</p>
                  <p className="text-2xl font-extrabold text-emerald-600 mt-1">{data.total_students}</p>
                </div>
                <div className="p-5 bg-white dark:bg-slate-900 border border-emerald-100/80 dark:border-slate-800 rounded-3xl shadow-sm">
                  <p className="text-[11px] font-semibold text-slate-500">Active Classes</p>
                  <p className="text-2xl font-extrabold text-slate-800 dark:text-slate-100 mt-1">{data.total_classes}</p>
                </div>
                <div className="p-5 bg-white dark:bg-slate-900 border border-emerald-100/80 dark:border-slate-800 rounded-3xl shadow-sm">
                  <p className="text-[11px] font-semibold text-slate-500">Avg Attendance</p>
                  <p className="text-2xl font-extrabold text-teal-600 mt-1">{data.average_attendance}%</p>
                </div>
                <div className="p-5 bg-white dark:bg-slate-900 border border-emerald-100/80 dark:border-slate-800 rounded-3xl shadow-sm">
                  <p className="text-[11px] font-semibold text-slate-500">Pending Test Reviews</p>
                  <p className="text-2xl font-extrabold text-amber-600 mt-1">{data.pending_tests}</p>
                </div>
              </>
            )}
          </div>

          <AnalyticsCharts data={data} role={user.role} />
        </div>
      );
    }

    // --- Study Materials View (Upgraded with 1 GB Streaming Upload & FAB) ---
    function ProfileSetupView({ token, user, onProfileUpdated, onNavigateDashboard }) {
      const [profileTab, setProfileTab] = useState("personal"); // "personal" or "academic"
      const [loading, setLoading] = useState(true);
      const [saving, setSaving] = useState(false);
      const [showAvatarModal, setShowAvatarModal] = useState(false);
      const [showEmailVerifyModal, setShowEmailVerifyModal] = useState(false);
      const [verifyCode, setVerifyCode] = useState("");
      const [verifyingEmail, setVerifyingEmail] = useState(false);

      // Form state
      const [fullName, setFullName] = useState("");
      const [username, setUsername] = useState("");
      const [bio, setBio] = useState("");
      const [city, setCity] = useState("");
      const [gender, setGender] = useState("");
      const [dateOfBirth, setDateOfBirth] = useState("");
      const [avatarUrl, setAvatarUrl] = useState("");
      const [emailVerified, setEmailVerified] = useState(false);
      const [completionPercentage, setCompletionPercentage] = useState(0);

      // Academic fields
      const [collegeName, setCollegeName] = useState("University Institute of Technology");
      const [branch, setBranch] = useState("Computer Science & Engineering");
      const [usn, setUsn] = useState("");
      const [currentSemester, setCurrentSemester] = useState(3);
      const [yearOfCompletion, setYearOfCompletion] = useState(2026);
      const [academicInterests, setAcademicInterests] = useState("");
      const [technicalSkills, setTechnicalSkills] = useState("");
      const [careerInterests, setCareerInterests] = useState("");

      const [formErrors, setFormErrors] = useState({});

      const loadProfile = useCallback(() => {
        setLoading(true);
        api.getMyProfile(token)
          .then(data => {
            setFullName(data.full_name || "");
            setUsername(data.username || "");
            setBio(data.bio || "");
            setCity(data.city || "");
            setGender(data.gender || "");
            setDateOfBirth(data.date_of_birth || "");
            setAvatarUrl(data.avatar_url || "");
            setEmailVerified(Boolean(data.email_verified));

            if (data.academic) {
              setCollegeName(data.academic.college_name || "University Institute of Technology");
              setBranch(data.academic.branch || "Computer Science & Engineering");
              setUsn(data.academic.usn || "");
              setCurrentSemester(data.academic.current_semester || 3);
              setYearOfCompletion(data.academic.year_of_completion || 2026);
              setAcademicInterests(data.academic.academic_interests || "");
              setTechnicalSkills(data.academic.technical_skills || "");
              setCareerInterests(data.academic.career_interests || "");
              setCompletionPercentage(data.academic.completion_percentage || 0);
            }
          })
          .catch(() => toast.error("Failed to load profile details"))
          .finally(() => setLoading(false));
      }, [token]);

      useEffect(() => { loadProfile(); }, [loadProfile]);

      // Calculate dynamic client completion
      const dynamicPercentage = useMemo(() => {
        const fields = [
          Boolean(fullName.trim()),
          Boolean(username.trim()),
          Boolean(gender),
          Boolean(city.trim()),
          Boolean(dateOfBirth),
          Boolean(collegeName.trim()),
          Boolean(branch.trim()),
          Boolean(usn.trim()),
          Boolean(currentSemester),
          Boolean(yearOfCompletion)
        ];
        const done = fields.filter(Boolean).length;
        return Math.round((done / fields.length) * 100);
      }, [fullName, username, gender, city, dateOfBirth, collegeName, branch, usn, currentSemester, yearOfCompletion]);

      const handleSavePersonal = async () => {
        const errors = {};
        if (!fullName.trim()) errors.fullName = "Full name is required";
        if (!username.trim() || username.length < 3) errors.username = "Valid username required (min 3 chars)";
        if (!gender) errors.gender = "Please select your gender";
        setFormErrors(errors);
        if (Object.keys(errors).length > 0) {
          toast.error("Please fill in required personal fields");
          return false;
        }

        setSaving(true);
        try {
          await api.updatePersonalProfile(token, {
            full_name: fullName.trim(),
            username: username.toLowerCase().trim(),
            bio: bio.trim(),
            city: city.trim(),
            gender: gender,
            date_of_birth: dateOfBirth,
            avatar_url: avatarUrl
          });
          toast.success("Personal details updated");
          return true;
        } catch (err) {
          toast.error(err.message);
          return false;
        } finally {
          setSaving(false);
        }
      };

      const handleSaveAcademic = async () => {
        const errors = {};
        if (!collegeName.trim()) errors.collegeName = "College Name is compulsory";
        if (!branch.trim()) errors.branch = "Branch / Department is compulsory";
        if (!usn.trim()) errors.usn = "USN / Student ID is compulsory";
        if (!currentSemester) errors.currentSemester = "Current Semester is compulsory";
        if (!yearOfCompletion) errors.yearOfCompletion = "Year of Completion is compulsory";
        setFormErrors(errors);

        if (Object.keys(errors).length > 0) {
          toast.error("All compulsory academic fields must be filled to complete registration");
          return false;
        }

        setSaving(true);
        try {
          const res = await api.updateAcademicProfile(token, {
            college_name: collegeName.trim(),
            branch: branch.trim(),
            usn: usn.trim(),
            current_semester: parseInt(currentSemester, 10),
            year_of_completion: parseInt(yearOfCompletion, 10),
            academic_interests: academicInterests.trim(),
            technical_skills: technicalSkills.trim(),
            career_interests: careerInterests.trim()
          });

          toast.success("Academic details verified and saved!");
          if (res.is_profile_complete) {
            toast.success("🎉 Profile setup complete! Semester dashboard unlocked.");
            if (onProfileUpdated) onProfileUpdated({ ...user, is_profile_complete: true, semester: parseInt(currentSemester, 10), branch });
          }
          return true;
        } catch (err) {
          toast.error(err.message);
          return false;
        } finally {
          setSaving(false);
        }
      };

      const handleSaveAll = async () => {
        const pOk = await handleSavePersonal();
        if (pOk) {
          const aOk = await handleSaveAcademic();
          if (aOk && onNavigateDashboard) {
            setTimeout(() => onNavigateDashboard(), 600);
          }
        }
      };

      const handleEmailVerification = async () => {
        if (!verifyCode.trim()) { toast.error("Please enter verification code (demo: 2026)"); return; }
        setVerifyingEmail(true);
        try {
          await api.verifyEmail(token, verifyCode);
          setEmailVerified(true);
          setShowEmailVerifyModal(false);
          toast.success("Email successfully verified!");
        } catch (err) {
          toast.error(err.message);
        } finally {
          setVerifyingEmail(false);
        }
      };

      const presetAvatars = [
        "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150&auto=format&fit=crop&q=80",
        "https://images.unsplash.com/photo-1539571696357-5a69c17a67c6?w=150&auto=format&fit=crop&q=80",
        "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=150&auto=format&fit=crop&q=80",
        "https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=150&auto=format&fit=crop&q=80",
        "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?w=150&auto=format&fit=crop&q=80",
        "https://images.unsplash.com/photo-1438761681033-6461ffad8d80?w=150&auto=format&fit=crop&q=80",
      ];

      if (loading) {
        return (
          <div className="space-y-4 max-w-4xl mx-auto p-4">
            <Skeleton className="h-12 w-1/3 rounded-2xl" />
            <Skeleton className="h-44 w-full rounded-3xl" />
            <Skeleton className="h-96 w-full rounded-3xl" />
          </div>
        );
      }

      return (
        <div className="max-w-4xl mx-auto space-y-6 pb-24">
          
          {/* Incomplete Banner Notification if Profile Incomplete */}
          {!user.is_profile_complete && (
            <div className="p-4 rounded-2xl bg-amber-50 border border-amber-200 text-amber-900 flex items-center justify-between gap-3 shadow-xs">
              <div className="flex items-center gap-3">
                <div className="w-9 h-9 rounded-xl bg-amber-100 text-amber-700 flex items-center justify-center shrink-0">
                  <i className="fas fa-id-card-clip text-base"></i>
                </div>
                <div>
                  <h4 className="text-xs font-bold">Mandatory Student Profile Setup</h4>
                  <p className="text-[11px] text-amber-700">Please complete all compulsory academic fields below to unlock your Semester {currentSemester} study repository.</p>
                </div>
              </div>
              <span className="text-xs font-extrabold bg-amber-200/80 px-2.5 py-1 rounded-lg text-amber-800">{dynamicPercentage}% Complete</span>
            </div>
          )}

          {/* Top Header */}
          <div className="flex items-center justify-between">
            <div>
              <div className="flex items-center gap-2 text-xs text-slate-400 mb-1">
                <span>Account</span>
                <i className="fas fa-chevron-right text-[10px]"></i>
                <span className="text-emerald-700 font-semibold">Edit Profile</span>
              </div>
              <h2 className="text-2xl font-black text-slate-900 dark:text-slate-100">Edit Student Profile</h2>
              <p className="text-xs text-slate-500">Manage institutional identity, academic semester, and credentials.</p>
            </div>
            {user.is_profile_complete && onNavigateDashboard && (
              <button onClick={onNavigateDashboard} className="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-xl text-xs font-bold transition flex items-center gap-2">
                <i className="fas fa-arrow-left"></i>Back to Dashboard
              </button>
            )}
          </div>

          {/* Profile Overview Card (Matching User Screenshot Layout) */}
          <div className="p-6 bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-3xl shadow-sm flex flex-col sm:flex-row items-center gap-6 justify-between">
            <div className="flex items-center gap-5 w-full sm:w-auto">
              <div className="relative group">
                <div className="w-20 h-20 rounded-2xl overflow-hidden bg-emerald-100 border-2 border-emerald-500/30 flex items-center justify-center shadow-md">
                  {avatarUrl ? (
                    <img src={avatarUrl} alt="Avatar" className="w-full h-full object-cover" />
                  ) : (
                    <span className="text-2xl font-black text-emerald-800">{fullName ? fullName.charAt(0) : "S"}</span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => setShowAvatarModal(true)}
                  className="absolute -bottom-2 -right-2 w-8 h-8 rounded-full bg-[#159B73] text-white flex items-center justify-center shadow hover:bg-[#118360] transition"
                  title="Change Avatar"
                >
                  <i className="fas fa-camera text-xs"></i>
                </button>
              </div>

              <div>
                <div className="flex items-center gap-2">
                  <h3 className="text-base font-bold text-slate-900 dark:text-slate-100">{fullName || "Student User"}</h3>
                  <span className="px-2 py-0.5 rounded-md text-[10px] font-extrabold bg-emerald-100 text-emerald-800">
                    Semester {currentSemester}
                  </span>
                </div>
                <p className="text-xs text-slate-400 font-mono">@{username || "username"}</p>
                <div className="flex flex-wrap items-center gap-2 mt-1.5">
                  <span className="text-[11px] font-semibold text-slate-600 dark:text-slate-300">
                    <i className="fas fa-id-badge text-emerald-600 mr-1"></i>{usn || "USN Pending"}
                  </span>
                  <span className="text-slate-300">•</span>
                  <span className="text-[11px] text-slate-500 truncate max-w-[200px]">{branch}</span>
                </div>
              </div>
            </div>

            {/* Profile Completion Meter & Email Badge */}
            <div className="flex flex-col sm:items-end w-full sm:w-64 gap-2 pt-4 sm:pt-0 border-t sm:border-t-0 border-slate-100 dark:border-slate-800">
              <div className="w-full">
                <div className="flex justify-between items-center text-xs font-bold mb-1">
                  <span className="text-slate-500">Profile Completion</span>
                  <span className={`${dynamicPercentage === 100 ? 'text-emerald-600' : 'text-amber-600'}`}>{dynamicPercentage}%</span>
                </div>
                <div className="h-2 w-full bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
                  <div
                    className={`h-full transition-all duration-500 rounded-full ${dynamicPercentage === 100 ? 'bg-emerald-500' : 'bg-gradient-to-r from-amber-400 to-emerald-500'}`}
                    style={{ width: `${dynamicPercentage}%` }}
                  />
                </div>
              </div>

              <div className="flex items-center gap-2">
                {emailVerified ? (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-bold bg-emerald-50 text-emerald-700 border border-emerald-200">
                    <i className="fas fa-circle-check"></i> Email Verified
                  </span>
                ) : (
                  <button
                    onClick={() => setShowEmailVerifyModal(true)}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-bold bg-amber-50 text-amber-700 border border-amber-200 hover:bg-amber-100 transition"
                  >
                    <i className="fas fa-triangle-exclamation"></i> Verify Email Now
                  </button>
                )}
              </div>
            </div>
          </div>

          {/* Navigation Tabs (Personal vs Professional) */}
          <div className="flex border-b border-slate-200 dark:border-slate-800 gap-4">
            <button
              onClick={() => setProfileTab("personal")}
              className={`pb-3 text-xs font-bold border-b-2 transition flex items-center gap-2 ${
                profileTab === "personal"
                  ? "border-[#159B73] text-[#159B73]"
                  : "border-transparent text-slate-500 hover:text-slate-800"
              }`}
            >
              <i className="fas fa-user"></i>Personal Information
            </button>
            <button
              onClick={() => setProfileTab("academic")}
              className={`pb-3 text-xs font-bold border-b-2 transition flex items-center gap-2 ${
                profileTab === "academic"
                  ? "border-[#159B73] text-[#159B73]"
                  : "border-transparent text-slate-500 hover:text-slate-800"
              }`}
            >
              <i className="fas fa-graduation-cap"></i>Professional & Academic Details
              {!user.is_profile_complete && <span className="w-2 h-2 rounded-full bg-amber-500"></span>}
            </button>
          </div>

          {/* TAB 1: PERSONAL INFORMATION */}
          {profileTab === "personal" && (
            <div className="p-6 bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-3xl shadow-sm space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {/* Full Name */}
                <div>
                  <div className="flex justify-between items-center mb-1">
                    <label className="text-[11px] font-bold text-slate-700 dark:text-slate-300 uppercase tracking-wider">Full Name *</label>
                    <span className="text-[10px] text-slate-400 font-mono">{fullName.length}/50</span>
                  </div>
                  <input
                    type="text"
                    maxLength={50}
                    value={fullName}
                    onChange={e => setFullName(e.target.value)}
                    placeholder="Alex Rivera"
                    className="w-full px-3.5 py-2.5 text-xs bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                  />
                  {formErrors.fullName && <p className="text-[10px] text-rose-600 mt-1">{formErrors.fullName}</p>}
                </div>

                {/* Username */}
                <div>
                  <label className="block text-[11px] font-bold text-slate-700 dark:text-slate-300 uppercase tracking-wider mb-1">Username (@) *</label>
                  <div className="relative">
                    <span className="absolute inset-y-0 left-0 pl-3 flex items-center text-slate-400 text-xs">@</span>
                    <input
                      type="text"
                      value={username}
                      onChange={e => setUsername(e.target.value)}
                      placeholder="alexrivera"
                      className="w-full pl-7 pr-3.5 py-2.5 text-xs bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                    />
                  </div>
                  {formErrors.username && <p className="text-[10px] text-rose-600 mt-1">{formErrors.username}</p>}
                </div>
              </div>

              {/* Bio */}
              <div>
                <div className="flex justify-between items-center mb-1">
                  <label className="text-[11px] font-bold text-slate-700 dark:text-slate-300 uppercase tracking-wider">Bio & Academic Interests</label>
                  <span className="text-[10px] text-slate-400 font-mono">{bio.length}/250</span>
                </div>
                <textarea
                  rows={3}
                  maxLength={250}
                  value={bio}
                  onChange={e => setBio(e.target.value)}
                  placeholder="Share your technical interests, research projects, or career aspirations..."
                  className="w-full px-3.5 py-2.5 text-xs bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {/* Email Address */}
                <div>
                  <label className="block text-[11px] font-bold text-slate-700 dark:text-slate-300 uppercase tracking-wider mb-1">Institutional Email</label>
                  <input
                    type="email"
                    value={user.email}
                    disabled
                    className="w-full px-3.5 py-2.5 text-xs bg-slate-100 text-slate-500 rounded-xl border border-slate-200 cursor-not-allowed"
                  />
                </div>

                {/* City */}
                <div>
                  <label className="block text-[11px] font-bold text-slate-700 dark:text-slate-300 uppercase tracking-wider mb-1">City / Region *</label>
                  <input
                    type="text"
                    value={city}
                    onChange={e => setCity(e.target.value)}
                    placeholder="Bengaluru"
                    className="w-full px-3.5 py-2.5 text-xs bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {/* Gender Cards (Reference from User Screenshot) */}
                <div>
                  <label className="block text-[11px] font-bold text-slate-700 dark:text-slate-300 uppercase tracking-wider mb-2">Gender *</label>
                  <div className="grid grid-cols-2 gap-2">
                    {["Male", "Female", "Other", "Prefer not to say"].map(g => (
                      <button
                        key={g}
                        type="button"
                        onClick={() => setGender(g)}
                        className={`p-2 rounded-xl text-xs font-semibold border transition text-center ${
                          gender === g
                            ? "bg-[#159B73] text-white border-[#159B73] shadow-xs"
                            : "bg-[#F4FBF8] text-slate-700 border-[#D2E7DA] hover:bg-slate-100"
                        }`}
                      >
                        {g}
                      </button>
                    ))}
                  </div>
                  {formErrors.gender && <p className="text-[10px] text-rose-600 mt-1">{formErrors.gender}</p>}
                </div>

                {/* Date of Birth */}
                <div>
                  <label className="block text-[11px] font-bold text-slate-700 dark:text-slate-300 uppercase tracking-wider mb-2">Date of Birth *</label>
                  <input
                    type="date"
                    value={dateOfBirth}
                    onChange={e => setDateOfBirth(e.target.value)}
                    className="w-full px-3.5 py-2.5 text-xs bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                  />
                </div>
              </div>

              <div className="flex justify-end pt-3">
                <button
                  type="button"
                  disabled={saving}
                  onClick={handleSavePersonal}
                  className="px-5 py-2.5 bg-[#159B73] hover:bg-[#128864] text-white font-bold text-xs rounded-xl shadow-md transition"
                >
                  {saving ? "Saving..." : "Save Personal Details"}
                </button>
              </div>
            </div>
          )}

          {/* TAB 2: PROFESSIONAL & ACADEMIC DETAILS */}
          {profileTab === "academic" && (
            <div className="p-6 bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-3xl shadow-sm space-y-4">
              <div className="p-3 bg-emerald-50 border border-emerald-200 rounded-2xl text-[11px] text-emerald-800">
                <i className="fas fa-shield-halved mr-1.5 text-emerald-600"></i>
                <strong>Semester Access Enforcement:</strong> Curriculum notes, evaluations and verified PDF materials are strictly filtered according to your enrolled semester and department.
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {/* College / Institution Name */}
                <div>
                  <label className="block text-[11px] font-bold text-slate-700 dark:text-slate-300 uppercase tracking-wider mb-1">
                    College / Institution Name *
                  </label>
                  <input
                    type="text"
                    value={collegeName}
                    onChange={e => setCollegeName(e.target.value)}
                    placeholder="University Institute of Technology"
                    className="w-full px-3.5 py-2.5 text-xs bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                  />
                  {formErrors.collegeName && <p className="text-[10px] text-rose-600 mt-1">{formErrors.collegeName}</p>}
                </div>

                {/* Branch / Department */}
                <div>
                  <label className="block text-[11px] font-bold text-slate-700 dark:text-slate-300 uppercase tracking-wider mb-1">
                    Branch / Department *
                  </label>
                  <select
                    value={branch}
                    onChange={e => setBranch(e.target.value)}
                    className="w-full px-3 py-2.5 text-xs bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                  >
                    <option value="Computer Science & Engineering">Computer Science & Engineering</option>
                    <option value="Information Science & Engineering">Information Science & Engineering</option>
                    <option value="Artificial Intelligence & Machine Learning">AI & Machine Learning</option>
                    <option value="Data Science & Engineering">Data Science & Engineering</option>
                    <option value="Electronics & Communication">Electronics & Communication</option>
                    <option value="Mechanical Engineering">Mechanical Engineering</option>
                    <option value="Civil Engineering">Civil Engineering</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                {/* USN */}
                <div>
                  <label className="block text-[11px] font-bold text-slate-700 dark:text-slate-300 uppercase tracking-wider mb-1">
                    USN / University ID *
                  </label>
                  <input
                    type="text"
                    value={usn}
                    onChange={e => setUsn(e.target.value.toUpperCase())}
                    placeholder="1MS22CS042"
                    className="w-full px-3.5 py-2.5 text-xs font-mono font-bold bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                  />
                  {formErrors.usn && <p className="text-[10px] text-rose-600 mt-1">{formErrors.usn}</p>}
                </div>

                {/* Current Semester (1 to 8) */}
                <div>
                  <label className="block text-[11px] font-bold text-slate-700 dark:text-slate-300 uppercase tracking-wider mb-1">
                    Current Semester *
                  </label>
                  <select
                    value={currentSemester}
                    onChange={e => setCurrentSemester(parseInt(e.target.value, 10))}
                    className="w-full px-3 py-2.5 text-xs font-bold bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                  >
                    {[1, 2, 3, 4, 5, 6, 7, 8].map(s => (
                      <option key={s} value={s}>Semester {s}</option>
                    ))}
                  </select>
                </div>

                {/* Year of Completion */}
                <div>
                  <label className="block text-[11px] font-bold text-slate-700 dark:text-slate-300 uppercase tracking-wider mb-1">
                    Graduation Year *
                  </label>
                  <select
                    value={yearOfCompletion}
                    onChange={e => setYearOfCompletion(parseInt(e.target.value, 10))}
                    className="w-full px-3 py-2.5 text-xs font-bold bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                  >
                    {[2024, 2025, 2026, 2027, 2028, 2029, 2030].map(y => (
                      <option key={y} value={y}>{y}</option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Technical Skills */}
              <div>
                <label className="block text-[11px] font-bold text-slate-700 dark:text-slate-300 uppercase tracking-wider mb-1">
                  Technical Skills & Frameworks
                </label>
                <input
                  type="text"
                  value={technicalSkills}
                  onChange={e => setTechnicalSkills(e.target.value)}
                  placeholder="e.g. Python, SQL, C++, React, Docker, Machine Learning"
                  className="w-full px-3.5 py-2.5 text-xs bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-[11px] font-bold text-slate-700 dark:text-slate-300 uppercase tracking-wider mb-1">Academic Interests</label>
                  <input
                    type="text"
                    value={academicInterests}
                    onChange={e => setAcademicInterests(e.target.value)}
                    placeholder="Distributed Systems, Neural Networks, Database Optimization"
                    className="w-full px-3.5 py-2.5 text-xs bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-bold text-slate-700 dark:text-slate-300 uppercase tracking-wider mb-1">Career Interests</label>
                  <input
                    type="text"
                    value={careerInterests}
                    onChange={e => setCareerInterests(e.target.value)}
                    placeholder="Software Development Engineer, Cloud Architect, AI Researcher"
                    className="w-full px-3.5 py-2.5 text-xs bg-[#F4FBF8] rounded-xl border border-[#D2E7DA] outline-none focus:ring-2 focus:ring-[#159B73]"
                  />
                </div>
              </div>

              <div className="flex justify-end pt-3">
                <button
                  type="button"
                  disabled={saving}
                  onClick={handleSaveAcademic}
                  className="px-5 py-2.5 bg-[#159B73] hover:bg-[#128864] text-white font-bold text-xs rounded-xl shadow-md transition"
                >
                  {saving ? "Saving..." : "Verify & Save Academic Details"}
                </button>
              </div>
            </div>
          )}

          {/* Sticky Bottom Save Profile Container (Specifically requested for Mobile / Responsive UX) */}
          <div className="fixed bottom-0 left-0 right-0 p-3 bg-white/95 dark:bg-slate-900/95 border-t border-slate-200 dark:border-slate-800 z-40 backdrop-blur-md flex items-center justify-between sm:hidden shadow-lg">
            <span className="text-xs font-bold text-emerald-800 dark:text-emerald-300">
              {dynamicPercentage}% Completed
            </span>
            <button
              onClick={handleSaveAll}
              disabled={saving}
              className="px-5 py-2 bg-[#159B73] text-white text-xs font-bold rounded-xl shadow-md"
            >
              {saving ? "Saving..." : "Save Profile & Finish"}
            </button>
          </div>

          {/* Avatar Selection Modal */}
          {showAvatarModal && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
              <div className="bg-white rounded-3xl border border-[#D5EADB] shadow-2xl max-w-md w-full p-6">
                <div className="flex justify-between items-center mb-4">
                  <h3 className="text-base font-bold text-[#174D3D]">Choose Profile Photo</h3>
                  <button onClick={() => setShowAvatarModal(false)} className="text-slate-400 hover:text-slate-600">
                    <i className="fas fa-xmark text-lg"></i>
                  </button>
                </div>
                <div className="grid grid-cols-3 gap-3 mb-5">
                  {presetAvatars.map((url, idx) => (
                    <button
                      key={idx}
                      onClick={() => { setAvatarUrl(url); setShowAvatarModal(false); toast.success("Avatar selected"); }}
                      className="w-20 h-20 rounded-2xl overflow-hidden border-2 border-slate-200 hover:border-emerald-500 transition shadow-xs"
                    >
                      <img src={url} alt="Preset" className="w-full h-full object-cover" />
                    </button>
                  ))}
                </div>
                <div className="border-t border-slate-100 pt-3">
                  <label className="block text-[11px] font-bold text-[#174D3D] uppercase tracking-wider mb-1">Or Paste Image URL</label>
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={avatarUrl}
                      onChange={e => setAvatarUrl(e.target.value)}
                      placeholder="https://example.com/avatar.jpg"
                      className="w-full px-3 py-2 text-xs bg-[#F4FBF8] border border-[#D2E7DA] rounded-xl outline-none"
                    />
                    <button
                      onClick={() => setShowAvatarModal(false)}
                      className="px-4 py-2 bg-[#159B73] text-white text-xs font-bold rounded-xl shrink-0"
                    >
                      Apply
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Email Verification Modal */}
          {showEmailVerifyModal && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
              <div className="bg-white rounded-3xl border border-[#D5EADB] shadow-2xl max-w-sm w-full p-6 text-center">
                <div className="w-12 h-12 rounded-2xl bg-emerald-100 text-[#159B73] flex items-center justify-center mx-auto mb-3">
                  <i className="fas fa-envelope-circle-check text-xl"></i>
                </div>
                <h3 className="text-base font-bold text-[#174D3D]">Verify Institutional Email</h3>
                <p className="text-xs text-slate-500 mt-1 mb-4">
                  Enter the 4-digit code dispatched to <strong>{user.email}</strong>. (For demo verification, enter: <code>2026</code>)
                </p>
                <input
                  type="text"
                  maxLength={6}
                  value={verifyCode}
                  onChange={e => setVerifyCode(e.target.value)}
                  placeholder="2026"
                  className="w-36 mx-auto px-4 py-2.5 text-center text-lg font-mono font-bold tracking-widest bg-[#F4FBF8] border border-[#D2E7DA] rounded-xl outline-none focus:ring-2 focus:ring-[#159B73] mb-4"
                />
                <div className="flex gap-2 justify-center">
                  <button onClick={() => setShowEmailVerifyModal(false)} className="px-4 py-2 text-xs font-bold text-slate-500 rounded-xl hover:bg-slate-100">Cancel</button>
                  <button onClick={handleEmailVerification} disabled={verifyingEmail} className="px-5 py-2 bg-[#159B73] text-white text-xs font-bold rounded-xl shadow-md">
                    {verifyingEmail ? "Verifying..." : "Confirm Code"}
                  </button>
                </div>
              </div>
            </div>
          )}

        </div>
      );
    }

    function TeacherPendingStatusView({ user, token, onLogout }) {
      const [checking, setChecking] = useState(false);
      const isRejected = user.approval_status === "rejected";

      const handleRefresh = async () => {
        setChecking(true);
        try {
          const profile = await api.getMyProfile(token);
          if (profile.approval_status === "approved") {
            toast.success("Congratulations! Your faculty account has been approved.");
            window.location.reload();
          } else if (profile.approval_status === "rejected") {
            toast.error("Application has been reviewed and rejected.");
          } else {
            toast.info("Application is still pending institutional administrative review.");
          }
        } catch (_) {
          toast.info("Checked status: Still pending verification.");
        } finally {
          setChecking(false);
        }
      };

      return (
        <div className="min-h-screen bg-[#F4FBF8] flex items-center justify-center p-4">
          <div className="max-w-lg w-full bg-white rounded-3xl border border-[#D5EADB] shadow-xl p-8 text-center space-y-6">
            <div className={`w-16 h-16 rounded-3xl mx-auto flex items-center justify-center text-2xl ${
              isRejected ? "bg-rose-100 text-rose-600" : "bg-amber-100 text-amber-700"
            }`}>
              <i className={`fas ${isRejected ? "fa-circle-xmark" : "fa-clock"}`}></i>
            </div>

            <div>
              <span className={`px-3 py-1 rounded-full text-[10px] font-extrabold uppercase tracking-wider ${
                isRejected ? "bg-rose-100 text-rose-800 border border-rose-200" : "bg-amber-100 text-amber-800 border border-amber-200"
              }`}>
                {isRejected ? "Application Rejected" : "Institutional Verification Pending"}
              </span>
              <h2 className="text-xl font-black text-[#174D3D] mt-3">
                {isRejected ? "Faculty Application Status" : "Account Awaiting Admin Approval"}
              </h2>
              <p className="text-xs text-[#52796F] mt-1 leading-relaxed max-w-sm mx-auto">
                {isRejected
                  ? "Your faculty registration request was reviewed and declined by the institutional administrator."
                  : "Thank you for applying to the EduNova faculty cohort. For security and syllabus integrity, teacher accounts require administrator verification."}
              </p>
            </div>

            <div className="p-4 bg-[#F4FBF8] border border-[#D5EADB] rounded-2xl text-left space-y-2 text-xs">
              <div className="flex justify-between">
                <span className="text-slate-400">Applicant:</span>
                <span className="font-bold text-[#174D3D]">{user.full_name}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Institutional Email:</span>
                <span className="font-mono text-slate-700">{user.email}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Tracking Application ID:</span>
                <span className="font-mono font-bold text-[#159B73]">{user.application_id || "APP-2026-PENDING"}</span>
              </div>
              {isRejected && user.rejection_reason && (
                <div className="pt-2 border-t border-slate-200 text-rose-700 font-medium">
                  <strong>Rejection Reason:</strong> {user.rejection_reason}
                </div>
              )}
            </div>

            <div className="flex gap-3 justify-center pt-2">
              <button
                onClick={handleRefresh}
                disabled={checking}
                className="px-5 py-2.5 bg-[#159B73] hover:bg-[#128864] text-white text-xs font-bold rounded-xl shadow-md flex items-center gap-2"
              >
                {checking ? <i className="fas fa-circle-notch fa-spin"></i> : <i className="fas fa-rotate"></i>}
                Check Status
              </button>
              <button
                onClick={onLogout}
                className="px-5 py-2.5 bg-slate-100 hover:bg-slate-200 text-slate-600 text-xs font-bold rounded-xl"
              >
                Sign Out
              </button>
            </div>
          </div>
        </div>
      );
    }

    function AdminDashboardView({ token, user }) {
      const [adminTab, setAdminTab] = useState("overview"); // overview, pending_teachers, teachers, students, academic, audit
      const [loading, setLoading] = useState(true);
      const [overviewData, setOverviewData] = useState(null);
      const [pendingTeachers, setPendingTeachers] = useState([]);
      const [teachersList, setTeachersList] = useState([]);
      const [studentsList, setStudentsList] = useState([]);
      const [auditLogs, setAuditLogs] = useState([]);
      const [academicMeta, setAcademicMeta] = useState({ branches: [], subjects: [] });

      // Student search & filter
      const [studentQuery, setStudentQuery] = useState("");
      const [studentSemester, setStudentSemester] = useState(0);
      const [studentBranch, setStudentBranch] = useState("all");

      // Modals
      const [approveModal, setApproveModal] = useState({ open: false, teacher: null, idCode: "" });
      const [rejectModal, setRejectModal] = useState({ open: false, teacher: null, reason: "" });
      const [editStudentSemModal, setEditStudentSemModal] = useState({ open: false, student: null, newSem: 3 });
      const [viewProfileModal, setViewProfileModal] = useState({ open: false, student: null });
      const [addSubjectModal, setAddSubjectModal] = useState({ open: false, name: "", code: "", semester: 3, branch: "Computer Science & Engineering" });

      const refreshAll = useCallback(() => {
        setLoading(true);
        Promise.all([
          api.getAdminOverview(token).then(setOverviewData).catch(() => {}),
          api.getPendingTeachers(token).then(setPendingTeachers).catch(() => {}),
          api.getAdminTeachers(token).then(setTeachersList).catch(() => {}),
          api.getAdminStudents(token, studentQuery, studentSemester, studentBranch).then(setStudentsList).catch(() => {}),
          api.getAcademicMeta(token).then(setAcademicMeta).catch(() => {}),
          api.getAdminAuditLogs(token).then(setAuditLogs).catch(() => {})
        ]).finally(() => setLoading(false));
      }, [token, studentQuery, studentSemester, studentBranch]);

      useEffect(() => { refreshAll(); }, [refreshAll]);

      const handleApproveTeacher = async () => {
        if (!approveModal.teacher) return;
        try {
          const res = await api.approveTeacher(token, approveModal.teacher.user_id, {
            teacher_id_code: approveModal.idCode.trim() || `TEA-2026-${approveModal.teacher.user_id:03d}`,
            assigned_semesters: "1,2,3,4,5,6,7,8"
          });
          toast.success(res.message);
          setApproveModal({ open: false, teacher: null, idCode: "" });
          refreshAll();
        } catch (err) {
          toast.error(err.message);
        }
      };

      const handleRejectTeacher = async () => {
        if (!rejectModal.teacher || !rejectModal.reason.trim()) {
          toast.error("Please state the rejection reason");
          return;
        }
        try {
          await api.rejectTeacher(token, rejectModal.teacher.user_id, rejectModal.reason.trim());
          toast.warning(`Application for ${rejectModal.teacher.full_name} rejected.`);
          setRejectModal({ open: false, teacher: null, reason: "" });
          refreshAll();
        } catch (err) {
          toast.error(err.message);
        }
      };

      const handleUpdateSemester = async () => {
        if (!editStudentSemModal.student) return;
        try {
          await api.updateStudentSemester(token, editStudentSemModal.student.id, editStudentSemModal.newSem);
          toast.success(`Promoted ${editStudentSemModal.student.full_name} to Semester ${editStudentSemModal.newSem}`);
          setEditStudentSemModal({ open: false, student: null, newSem: 3 });
          refreshAll();
        } catch (err) {
          toast.error(err.message);
        }
      };

      const handleToggleStatus = async (userId, userName) => {
        try {
          const res = await api.toggleUserStatus(token, userId);
          toast.info(`Account status for ${userName} set to: ${res.is_active ? 'Active' : 'Deactivated'}`);
          refreshAll();
        } catch (err) {
          toast.error(err.message);
        }
      };

      const handleAddSubject = async (e) => {
        e.preventDefault();
        try {
          await api.addAcademicSubject(token, addSubjectModal);
          toast.success(`Added ${addSubjectModal.name} (${addSubjectModal.code}) to Semester ${addSubjectModal.semester}`);
          setAddSubjectModal({ open: false, name: "", code: "", semester: 3, branch: "Computer Science & Engineering" });
          refreshAll();
        } catch (err) {
          toast.error(err.message);
        }
      };

      return (
        <div className="space-y-6">
          {/* Top Admin Header */}
          <div className="edunova-hero p-6 text-white rounded-3xl shadow-xl flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
            <div>
              <span className="px-3 py-1 rounded-full text-[10px] font-extrabold uppercase tracking-wider bg-white/20">
                Institutional Authority Level
              </span>
              <h2 className="text-xl font-extrabold mt-2 flex items-center gap-2">
                <i className="fas fa-shield-halved"></i>EduNova Platform Administration & Governance
              </h2>
              <p className="text-xs text-emerald-100 mt-1">
                Faculty onboarding, semester access enforcement, student registry & institutional audit logs.
              </p>
            </div>
            <div className="flex gap-2">
              <span className="bg-white/20 px-3 py-1.5 rounded-xl text-xs font-bold flex items-center gap-2">
                <i className="fas fa-user-clock text-amber-300"></i>
                {pendingTeachers.length} Pending Review{pendingTeachers.length !== 1 ? 's' : ''}
              </span>
            </div>
          </div>

          {/* Navigation Sub-Tabs */}
          <div className="flex flex-wrap gap-2 border-b border-slate-200 dark:border-slate-800 pb-3">
            {[
              { id: "overview", label: "Platform Overview", icon: "fa-chart-pie" },
              { id: "pending_teachers", label: `Teacher Approvals (${pendingTeachers.length})`, icon: "fa-user-check", badge: pendingTeachers.length },
              { id: "teachers", label: "Faculty Directory", icon: "fa-chalkboard-user" },
              { id: "students", label: "Student Registry", icon: "fa-user-graduate" },
              { id: "academic", label: "Academic Mapping", icon: "fa-sitemap" },
              { id: "audit", label: "Audit Logs", icon: "fa-clock-rotate-left" }
            ].map(tab => (
              <button
                key={tab.id}
                onClick={() => setAdminTab(tab.id)}
                className={`px-3.5 py-2 rounded-xl text-xs font-bold transition flex items-center gap-2 ${
                  adminTab === tab.id
                    ? "bg-[#159B73] text-white shadow-md shadow-emerald-700/20"
                    : "bg-white text-slate-600 hover:bg-slate-100 border border-slate-200/60"
                }`}
              >
                <i className={`fas ${tab.icon}`}></i>
                <span>{tab.label}</span>
              </button>
            ))}
          </div>

          {/* TAB 1: PLATFORM OVERVIEW */}
          {adminTab === "overview" && overviewData && (
            <div className="space-y-6">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="p-5 bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-3xl shadow-sm">
                  <p className="text-[11px] font-semibold text-slate-500">Enrolled Students</p>
                  <p className="text-2xl font-extrabold text-[#159B73] mt-1">{overviewData.students_count}</p>
                </div>
                <div className="p-5 bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-3xl shadow-sm">
                  <p className="text-[11px] font-semibold text-slate-500">Verified Faculty</p>
                  <p className="text-2xl font-extrabold text-teal-700 mt-1">{overviewData.teachers_approved}</p>
                </div>
                <div className="p-5 bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-3xl shadow-sm">
                  <p className="text-[11px] font-semibold text-slate-500">Pending Applications</p>
                  <p className="text-2xl font-extrabold text-amber-600 mt-1">{overviewData.teachers_pending}</p>
                </div>
                <div className="p-5 bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-3xl shadow-sm">
                  <p className="text-[11px] font-semibold text-slate-500">Curriculum Materials</p>
                  <p className="text-2xl font-extrabold text-slate-800 dark:text-slate-100 mt-1">{overviewData.materials_count}</p>
                </div>
              </div>

              {/* Semester Distribution Visual */}
              <div className="p-6 bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-3xl shadow-sm">
                <h3 className="text-sm font-bold text-[#174D3D] mb-4 flex items-center gap-2">
                  <i className="fas fa-layer-group text-emerald-600"></i>Student Enrollment Across Semesters (1 to 8)
                </h3>
                <div className="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-8 gap-3">
                  {Object.entries(overviewData.semester_distribution || {}).map(([sem, count]) => (
                    <div key={sem} className="p-3 bg-[#F4FBF8] border border-[#D5EADB] rounded-2xl text-center">
                      <p className="text-[10px] font-extrabold uppercase text-slate-400">{sem}</p>
                      <p className="text-xl font-extrabold text-[#174D3D] mt-1">{count}</p>
                      <p className="text-[9px] text-slate-400">Students</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* TAB 2: PENDING TEACHER VERIFICATION */}
          {adminTab === "pending_teachers" && (
            <div className="space-y-4">
              <div className="flex justify-between items-center">
                <div>
                  <h3 className="text-base font-bold text-slate-900">Faculty Review Queue</h3>
                  <p className="text-xs text-slate-500">Review submitted institution codes and issue official Teacher IDs.</p>
                </div>
                <button onClick={refreshAll} className="px-3 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-600 rounded-xl text-xs font-semibold">
                  <i className="fas fa-rotate mr-1"></i>Refresh
                </button>
              </div>

              {pendingTeachers.length === 0 ? (
                <EmptyState
                  icon="fa-circle-check"
                  title="No Pending Teacher Applications"
                  description="All submitted faculty credentials have been processed and verified."
                />
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {pendingTeachers.map(t => (
                    <div key={t.user_id} className="p-5 bg-white border border-amber-200 rounded-3xl shadow-sm space-y-3">
                      <div className="flex justify-between items-start">
                        <div>
                          <span className="px-2 py-0.5 rounded text-[10px] font-extrabold bg-amber-100 text-amber-800">
                            {t.application_id}
                          </span>
                          <h4 className="text-base font-bold text-slate-900 mt-1">{t.full_name}</h4>
                          <p className="text-xs text-slate-500 font-mono">{t.email}</p>
                        </div>
                        <span className="text-[10px] text-slate-400">{t.created_at}</span>
                      </div>

                      <div className="grid grid-cols-2 gap-2 text-[11px] p-3 bg-slate-50 rounded-2xl border border-slate-100">
                        <div>
                          <span className="text-slate-400 block">Department:</span>
                          <span className="font-semibold text-slate-800">{t.department}</span>
                        </div>
                        <div>
                          <span className="text-slate-400 block">Designation:</span>
                          <span className="font-semibold text-slate-800">{t.designation}</span>
                        </div>
                        <div>
                          <span className="text-slate-400 block">Experience:</span>
                          <span className="font-semibold text-slate-800">{t.experience_years} Years</span>
                        </div>
                        <div>
                          <span className="text-slate-400 block">Code Verified:</span>
                          <span className="font-mono text-emerald-700 font-bold">{t.invitation_code}</span>
                        </div>
                      </div>

                      <div className="flex justify-end gap-2 pt-1">
                        <button
                          onClick={() => setRejectModal({ open: true, teacher: t, reason: "" })}
                          className="px-3.5 py-2 bg-rose-50 text-rose-700 hover:bg-rose-100 rounded-xl text-xs font-bold transition"
                        >
                          Reject
                        </button>
                        <button
                          onClick={() => setApproveModal({ open: true, teacher: t, idCode: `TEA-2026-${t.user_id:03d}` })}
                          className="px-4 py-2 bg-[#159B73] hover:bg-[#128864] text-white rounded-xl text-xs font-bold transition shadow-sm"
                        >
                          Approve & Issue ID
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* TAB 3: FACULTY DIRECTORY */}
          {adminTab === "teachers" && (
            <div className="space-y-4">
              <h3 className="text-base font-bold text-slate-900">Approved Institutional Faculty</h3>
              <div className="overflow-x-auto bg-white rounded-3xl border border-slate-200 shadow-sm">
                <table className="w-full text-left text-xs">
                  <thead className="bg-slate-50 border-b border-slate-200">
                    <tr>
                      <th className="p-4 font-bold text-slate-600">Teacher ID</th>
                      <th className="p-4 font-bold text-slate-600">Faculty Member</th>
                      <th className="p-4 font-bold text-slate-600">Department</th>
                      <th className="p-4 font-bold text-slate-600">Designation</th>
                      <th className="p-4 font-bold text-slate-600">Status</th>
                      <th className="p-4 font-bold text-slate-600 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {teachersList.map(t => (
                      <tr key={t.id} className="hover:bg-slate-50/50">
                        <td className="p-4 font-mono font-bold text-[#159B73]">{t.teacher_id_code || `TEA-2026-${t.id:03d}`}</td>
                        <td className="p-4">
                          <p className="font-bold text-slate-900">{t.full_name}</p>
                          <p className="text-[11px] text-slate-400 font-mono">{t.email}</p>
                        </td>
                        <td className="p-4 text-slate-700">{t.department}</td>
                        <td className="p-4 text-slate-700">{t.designation}</td>
                        <td className="p-4">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-extrabold ${
                            t.is_active ? "bg-emerald-100 text-emerald-800" : "bg-rose-100 text-rose-800"
                          }`}>
                            {t.is_active ? "Active" : "Deactivated"}
                          </span>
                        </td>
                        <td className="p-4 text-right">
                          <button
                            onClick={() => handleToggleStatus(t.id, t.full_name)}
                            className={`px-3 py-1.5 rounded-xl text-xs font-bold transition ${
                              t.is_active ? "bg-slate-100 hover:bg-slate-200 text-slate-600" : "bg-emerald-100 text-emerald-800"
                            }`}
                          >
                            {t.is_active ? "Deactivate" : "Activate"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* TAB 4: STUDENT REGISTRY */}
          {adminTab === "students" && (
            <div className="space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-white p-4 rounded-3xl border border-slate-200">
                <div className="relative flex-1">
                  <span className="absolute inset-y-0 left-0 pl-3 flex items-center text-slate-400">
                    <i className="fas fa-magnifying-glass text-xs"></i>
                  </span>
                  <input
                    type="text"
                    value={studentQuery}
                    onChange={e => setStudentQuery(e.target.value)}
                    placeholder="Search by student name, USN, or email..."
                    className="w-full pl-9 pr-4 py-2 text-xs bg-slate-50 border border-slate-200 rounded-xl outline-none focus:ring-2 focus:ring-[#159B73]"
                  />
                </div>
                <div className="flex gap-2">
                  <select
                    value={studentSemester}
                    onChange={e => setStudentSemester(parseInt(e.target.value, 10))}
                    className="px-3 py-2 text-xs bg-slate-50 border border-slate-200 rounded-xl font-bold"
                  >
                    <option value={0}>All Semesters</option>
                    {[1, 2, 3, 4, 5, 6, 7, 8].map(s => (
                      <option key={s} value={s}>Semester {s}</option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="overflow-x-auto bg-white rounded-3xl border border-slate-200 shadow-sm">
                <table className="w-full text-left text-xs">
                  <thead className="bg-slate-50 border-b border-slate-200">
                    <tr>
                      <th className="p-4 font-bold text-slate-600">USN</th>
                      <th className="p-4 font-bold text-slate-600">Student Name</th>
                      <th className="p-4 font-bold text-slate-600">Enrolled Semester</th>
                      <th className="p-4 font-bold text-slate-600">Branch</th>
                      <th className="p-4 font-bold text-slate-600">Profile Status</th>
                      <th className="p-4 font-bold text-slate-600 text-right">Admin Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {studentsList.map(s => (
                      <tr key={s.id} className="hover:bg-slate-50/50">
                        <td className="p-4 font-mono font-bold text-slate-900">{s.usn}</td>
                        <td className="p-4">
                          <p className="font-bold text-slate-900">{s.full_name}</p>
                          <p className="text-[11px] text-slate-400 font-mono">{s.email}</p>
                        </td>
                        <td className="p-4">
                          <span className="px-2.5 py-1 rounded-lg text-xs font-extrabold bg-emerald-50 text-emerald-800 border border-emerald-200">
                            Semester {s.semester}
                          </span>
                        </td>
                        <td className="p-4 text-slate-600 truncate max-w-[150px]">{s.branch}</td>
                        <td className="p-4">
                          {s.is_profile_complete ? (
                            <span className="px-2 py-0.5 rounded text-[10px] font-extrabold bg-emerald-100 text-emerald-800">
                              Complete (100%)
                            </span>
                          ) : (
                            <span className="px-2 py-0.5 rounded text-[10px] font-extrabold bg-amber-100 text-amber-800">
                              Incomplete ({s.completion_percentage}%)
                            </span>
                          )}
                        </td>
                        <td className="p-4 text-right space-x-1.5">
                          <button
                            onClick={() => setEditStudentSemModal({ open: true, student: s, newSem: s.semester })}
                            className="px-2.5 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-xl text-xs font-semibold"
                            title="Promote or Change Semester"
                          >
                            <i className="fas fa-arrow-up-right-from-square mr-1"></i>Edit Sem
                          </button>
                          <button
                            onClick={() => handleToggleStatus(s.id, s.full_name)}
                            className={`px-2.5 py-1.5 rounded-xl text-xs font-semibold ${s.is_active ? "text-red-600 hover:bg-red-50" : "text-emerald-700 bg-emerald-50"}`}
                          >
                            {s.is_active ? "Deactivate" : "Activate"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* TAB 5: ACADEMIC MAPPING */}
          {adminTab === "academic" && (
            <div className="space-y-6">
              <div className="flex justify-between items-center">
                <div>
                  <h3 className="text-base font-bold text-slate-900">Academic Structure & Courses</h3>
                  <p className="text-xs text-slate-500">Departments and curriculum subjects categorized by semester.</p>
                </div>
                <button
                  onClick={() => setAddSubjectModal({ open: true, name: "", code: "", semester: 3, branch: "Computer Science & Engineering" })}
                  className="px-4 py-2 bg-[#159B73] hover:bg-[#128864] text-white text-xs font-bold rounded-xl shadow-sm flex items-center gap-1.5"
                >
                  <i className="fas fa-plus"></i>Add Subject
                </button>
              </div>

              {/* Branches */}
              <div className="p-5 bg-white rounded-3xl border border-slate-200 shadow-sm space-y-3">
                <h4 className="text-xs font-bold uppercase tracking-wider text-slate-500">Accredited Engineering Branches</h4>
                <div className="flex flex-wrap gap-2">
                  {academicMeta.branches.map(b => (
                    <span key={b.id} className="px-3 py-1.5 rounded-xl bg-slate-100 text-slate-800 text-xs font-semibold flex items-center gap-2">
                      <span className="font-mono text-emerald-700 font-bold">{b.code}</span>
                      <span>{b.name}</span>
                    </span>
                  ))}
                </div>
              </div>

              {/* Subjects Grouped by Semester */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {[1, 2, 3, 4, 5, 6, 7, 8].map(sem => {
                  const subs = academicMeta.subjects.filter(s => s.semester === sem);
                  return (
                    <div key={sem} className="p-5 bg-white rounded-3xl border border-slate-200 shadow-sm space-y-3">
                      <div className="flex justify-between items-center border-b border-slate-100 pb-2">
                        <span className="font-extrabold text-[#174D3D] text-sm">Semester {sem}</span>
                        <span className="text-[10px] font-bold text-slate-400">{subs.length} Registered Courses</span>
                      </div>
                      <div className="space-y-2">
                        {subs.length === 0 ? (
                          <p className="text-[11px] text-slate-400 italic">No courses mapped yet.</p>
                        ) : (
                          subs.map(s => (
                            <div key={s.id} className="p-2.5 bg-slate-50 rounded-xl flex justify-between items-center">
                              <div>
                                <p className="font-bold text-slate-800 text-xs">{s.name}</p>
                                <p className="text-[10px] text-slate-400">{s.branch}</p>
                              </div>
                              <span className="font-mono text-xs font-bold text-[#159B73] bg-emerald-50 px-2 py-0.5 rounded">
                                {s.code}
                              </span>
                            </div>
                          ))
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* TAB 6: AUDIT LOGS */}
          {adminTab === "audit" && (
            <div className="space-y-4">
              <h3 className="text-base font-bold text-slate-900">Platform Security & Audit Trail</h3>
              <div className="overflow-x-auto bg-white rounded-3xl border border-slate-200 shadow-sm">
                <table className="w-full text-left text-xs">
                  <thead className="bg-slate-50 border-b border-slate-200">
                    <tr>
                      <th className="p-4 font-bold text-slate-600">Timestamp</th>
                      <th className="p-4 font-bold text-slate-600">Action</th>
                      <th className="p-4 font-bold text-slate-600">Target User / Entity</th>
                      <th className="p-4 font-bold text-slate-600">Audit Details</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {auditLogs.map(l => (
                      <tr key={l.id} className="hover:bg-slate-50/50">
                        <td className="p-4 text-slate-400 font-mono text-[11px] whitespace-nowrap">{l.timestamp}</td>
                        <td className="p-4 font-bold text-[#174D3D]">{l.action}</td>
                        <td className="p-4 font-mono text-slate-600">{l.target_user}</td>
                        <td className="p-4 text-slate-700 max-w-md">{l.details}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Approve Teacher Modal */}
          {approveModal.open && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
              <div className="bg-white rounded-3xl border border-slate-200 shadow-2xl max-w-md w-full p-6 space-y-4">
                <h3 className="text-base font-bold text-slate-900">Approve Faculty Application</h3>
                <p className="text-xs text-slate-500">
                  Assign a unique official Teacher ID to <strong>{approveModal.teacher?.full_name}</strong>.
                </p>
                <div>
                  <label className="block text-[11px] font-bold text-slate-700 uppercase tracking-wider mb-1">Assigned Teacher ID</label>
                  <input
                    type="text"
                    value={approveModal.idCode}
                    onChange={e => setApproveModal({ ...approveModal, idCode: e.target.value.toUpperCase() })}
                    placeholder="TEA-2026-002"
                    className="w-full px-3.5 py-2.5 text-xs font-mono font-bold bg-slate-50 border border-slate-200 rounded-xl outline-none focus:ring-2 focus:ring-[#159B73]"
                  />
                </div>
                <div className="flex justify-end gap-2 pt-2">
                  <button onClick={() => setApproveModal({ open: false, teacher: null, idCode: "" })} className="px-4 py-2 text-xs font-bold text-slate-500 rounded-xl hover:bg-slate-100">Cancel</button>
                  <button onClick={handleApproveTeacher} className="px-5 py-2 bg-[#159B73] text-white text-xs font-bold rounded-xl shadow-md">
                    Confirm Approval
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Reject Teacher Modal */}
          {rejectModal.open && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
              <div className="bg-white rounded-3xl border border-slate-200 shadow-2xl max-w-md w-full p-6 space-y-4">
                <h3 className="text-base font-bold text-rose-700">Decline Faculty Application</h3>
                <p className="text-xs text-slate-500">
                  Provide an official reason for declining <strong>{rejectModal.teacher?.full_name}</strong>'s application.
                </p>
                <div>
                  <label className="block text-[11px] font-bold text-slate-700 uppercase tracking-wider mb-1">Reason for Rejection *</label>
                  <textarea
                    rows={3}
                    value={rejectModal.reason}
                    onChange={e => setRejectModal({ ...rejectModal, reason: e.target.value })}
                    placeholder="Institutional appointment credentials could not be verified."
                    className="w-full px-3.5 py-2.5 text-xs bg-slate-50 border border-slate-200 rounded-xl outline-none focus:ring-2 focus:ring-rose-500"
                  />
                </div>
                <div className="flex justify-end gap-2 pt-2">
                  <button onClick={() => setRejectModal({ open: false, teacher: null, reason: "" })} className="px-4 py-2 text-xs font-bold text-slate-500 rounded-xl hover:bg-slate-100">Cancel</button>
                  <button onClick={handleRejectTeacher} className="px-5 py-2 bg-rose-600 text-white text-xs font-bold rounded-xl shadow-md">
                    Confirm Rejection
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Edit Student Semester Modal */}
          {editStudentSemModal.open && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
              <div className="bg-white rounded-3xl border border-slate-200 shadow-2xl max-w-sm w-full p-6 space-y-4">
                <h3 className="text-base font-bold text-slate-900">Promote / Change Semester</h3>
                <p className="text-xs text-slate-500">
                  Updating enrolled semester for <strong>{editStudentSemModal.student?.full_name}</strong>. Their accessible curriculum materials will adjust automatically.
                </p>
                <div>
                  <label className="block text-[11px] font-bold text-slate-700 uppercase tracking-wider mb-1">Academic Semester</label>
                  <select
                    value={editStudentSemModal.newSem}
                    onChange={e => setEditStudentSemModal({ ...editStudentSemModal, newSem: parseInt(e.target.value, 10) })}
                    className="w-full px-3 py-2.5 text-xs font-bold bg-slate-50 border border-slate-200 rounded-xl outline-none"
                  >
                    {[1, 2, 3, 4, 5, 6, 7, 8].map(s => (
                      <option key={s} value={s}>Semester {s}</option>
                    ))}
                  </select>
                </div>
                <div className="flex justify-end gap-2 pt-2">
                  <button onClick={() => setEditStudentSemModal({ open: false, student: null, newSem: 3 })} className="px-4 py-2 text-xs font-bold text-slate-500 rounded-xl hover:bg-slate-100">Cancel</button>
                  <button onClick={handleUpdateSemester} className="px-5 py-2 bg-[#159B73] text-white text-xs font-bold rounded-xl shadow-md">
                    Save Semester
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Add Subject Modal */}
          {addSubjectModal.open && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
              <div className="bg-white rounded-3xl border border-slate-200 shadow-2xl max-w-md w-full p-6 space-y-4">
                <h3 className="text-base font-bold text-slate-900">Add Academic Course / Subject</h3>
                <form onSubmit={handleAddSubject} className="space-y-3">
                  <div>
                    <label className="block text-[11px] font-bold text-slate-700 uppercase tracking-wider mb-1">Course Title</label>
                    <input
                      type="text"
                      required
                      value={addSubjectModal.name}
                      onChange={e => setAddSubjectModal({ ...addSubjectModal, name: e.target.value })}
                      placeholder="e.g. Compiler Design & Optimization"
                      className="w-full px-3 py-2 text-xs bg-slate-50 border border-slate-200 rounded-xl outline-none"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-[11px] font-bold text-slate-700 uppercase tracking-wider mb-1">Course Code</label>
                      <input
                        type="text"
                        required
                        value={addSubjectModal.code}
                        onChange={e => setAddSubjectModal({ ...addSubjectModal, code: e.target.value.toUpperCase() })}
                        placeholder="CD-603"
                        className="w-full px-3 py-2 text-xs font-mono font-bold bg-slate-50 border border-slate-200 rounded-xl outline-none"
                      />
                    </div>
                    <div>
                      <label className="block text-[11px] font-bold text-slate-700 uppercase tracking-wider mb-1">Semester</label>
                      <select
                        value={addSubjectModal.semester}
                        onChange={e => setAddSubjectModal({ ...addSubjectModal, semester: parseInt(e.target.value, 10) })}
                        className="w-full px-3 py-2 text-xs font-bold bg-slate-50 border border-slate-200 rounded-xl outline-none"
                      >
                        {[1, 2, 3, 4, 5, 6, 7, 8].map(s => (
                          <option key={s} value={s}>Semester {s}</option>
                        ))}
                      </select>
                    </div>
                  </div>
                  <div>
                    <label className="block text-[11px] font-bold text-slate-700 uppercase tracking-wider mb-1">Branch</label>
                    <select
                      value={addSubjectModal.branch}
                      onChange={e => setAddSubjectModal({ ...addSubjectModal, branch: e.target.value })}
                      className="w-full px-3 py-2 text-xs bg-slate-50 border border-slate-200 rounded-xl outline-none"
                    >
                      <option value="Computer Science & Engineering">Computer Science & Engineering</option>
                      <option value="Information Science & Engineering">Information Science & Engineering</option>
                      <option value="Artificial Intelligence & Machine Learning">AI & Machine Learning</option>
                      <option value="All Branches">All Branches</option>
                    </select>
                  </div>
                  <div className="flex justify-end gap-2 pt-2">
                    <button type="button" onClick={() => setAddSubjectModal({ ...addSubjectModal, open: false })} className="px-4 py-2 text-xs font-bold text-slate-500 rounded-xl hover:bg-slate-100">Cancel</button>
                    <button type="submit" className="px-5 py-2 bg-[#159B73] text-white text-xs font-bold rounded-xl shadow-md">
                      Register Course
                    </button>
                  </div>
                </form>
              </div>
            </div>
          )}

        </div>
      );
    }

    function MaterialsView({ token, user, onAskAI }) {
      const [materials, setMaterials] = useState([]);
      const [loading, setLoading] = useState(true);
      const [error, setError] = useState(null);
      const [activePdf, setActivePdf] = useState(null);

      // Semester & Branch filters for Faculty / Admin
      const [filterSemester, setFilterSemester] = useState(user.role === "student" ? (user.semester || 3) : 0);
      const [filterBranch, setFilterBranch] = useState("all");

      // Quick note modal state
      const [showQuickModal, setShowQuickModal] = useState(false);
      const [quickForm, setQuickForm] = useState({
        title: "",
        subject: "Artificial Intelligence",
        unit: "Unit 1",
        pages: 12,
        semester: user.semester || 3,
        branch: user.branch || "Computer Science & Engineering"
      });

      // 1 GB Large file upload state
      const [showUploadModal, setShowUploadModal] = useState(false);
      const [selectedFile, setSelectedFile] = useState(null);
      const [fileForm, setFileForm] = useState({
        title: "",
        subject: "Database Management Systems",
        unit: "Unit 1",
        pages: 10,
        semester: user.semester || 3,
        branch: user.branch || "Computer Science & Engineering"
      });
      const [uploadProgress, setUploadProgress] = useState(0);
      const [isUploading, setIsUploading] = useState(false);
      const [uploadError, setUploadError] = useState("");
      const [uploadStage, setUploadStage] = useState("idle");
      const [aiApproved, setAiApproved] = useState(true);

      const load = useCallback(() => {
        setLoading(true);
        setError(null);
        api.getMaterials(token, user.role === "student" ? null : filterSemester, filterBranch)
          .then(setMaterials)
          .catch(e => setError(e.message))
          .finally(() => setLoading(false));
      }, [token, filterSemester, filterBranch, user.role]);

      useEffect(() => { load(); }, [load]);

      const handleQuickUpload = async (e) => {
        e.preventDefault();
        try {
          await api.uploadMaterial(token, quickForm);
          toast.success(`Study note cataloged for Semester ${quickForm.semester}`);
          setShowQuickModal(false);
          setQuickForm({ title: "", subject: "Artificial Intelligence", unit: "Unit 1", pages: 12, semester: 3, branch: "Computer Science & Engineering" });
          load();
        } catch (err) {
          toast.error(err.message);
        }
      };

      const handleLargeFileUpload = async (e) => {
        if (e) e.preventDefault();
        if (!selectedFile) {
          setUploadError("Please select a file to upload");
          return;
        }

        setIsUploading(true);
        setUploadStage("uploading");
        setUploadProgress(0);
        setUploadError("");

        const formData = new FormData();
        formData.append("title", fileForm.title);
        formData.append("subject", fileForm.subject);
        formData.append("unit", fileForm.unit);
        formData.append("pages", fileForm.pages || 1);
        formData.append("file", selectedFile);
        formData.append("is_ai_approved", aiApproved);
        formData.append("semester", fileForm.semester);
        formData.append("branch", fileForm.branch);

        try {
          const res = await api.uploadLargeMaterial(token, formData, (percent) => {
            setUploadProgress(percent);
            if (percent >= 100) setUploadStage("processing");
          });
          toast.success(res.message || "File uploaded and partitioned by semester!");
          setShowUploadModal(false);
          setSelectedFile(null);
          setUploadProgress(0);
          setUploadStage("idle");
          load();
        } catch (err) {
          setUploadError(err.message);
          setUploadStage("idle");
          toast.error(err.message);
        } finally {
          setIsUploading(false);
        }
      };

      return (
        <div className="space-y-6 relative pb-16">
          {/* Header Bar */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-base font-bold text-slate-900 dark:text-slate-100 flex items-center gap-2">
                  <i className="fas fa-book-bookmark text-emerald-600"></i>Curriculum Repository & Course Notes
                </h3>
                {user.role === "student" && (
                  <span className="px-2.5 py-0.5 rounded-full text-[10px] font-extrabold bg-emerald-100 text-emerald-800 border border-emerald-200">
                    Semester {user.semester || 3}
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-500 mt-0.5">
                {user.role === "student"
                  ? `Strict semester access control: You are viewing authorized resources for Semester ${user.semester || 3} (${user.branch || 'CSE'}).`
                  : "Institutional curriculum documents partitioned by academic semester."}
              </p>
            </div>

            {/* Teacher / Admin Controls */}
            {user.role !== "student" && (
              <div className="flex items-center gap-2">
                <select
                  value={filterSemester}
                  onChange={e => setFilterSemester(parseInt(e.target.value, 10))}
                  className="px-3 py-1.5 text-xs bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl font-bold"
                >
                  <option value={0}>All Semesters</option>
                  {[1, 2, 3, 4, 5, 6, 7, 8].map(s => (
                    <option key={s} value={s}>Semester {s}</option>
                  ))}
                </select>
                <button
                  onClick={() => setShowQuickModal(true)}
                  className="px-3 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-xl text-xs font-semibold"
                >
                  <i className="fas fa-file-pen mr-1"></i>Quick Note
                </button>
                <button
                  onClick={() => { setShowUploadModal(true); setSelectedFile(null); setUploadProgress(0); setUploadError(""); }}
                  className="px-4 py-2 bg-[#159B73] hover:bg-[#128864] text-white rounded-xl text-xs font-bold shadow-sm"
                >
                  <i className="fas fa-cloud-arrow-up mr-1.5"></i>Upload PDF (1 GB)
                </button>
              </div>
            )}
          </div>

          {/* Student Access Notice */}
          {user.role === "student" && (
            <div className="p-3.5 bg-emerald-50/70 border border-emerald-200/80 rounded-2xl flex items-center justify-between text-xs text-emerald-900">
              <div className="flex items-center gap-2.5">
                <i className="fas fa-shield-halved text-emerald-700 text-sm"></i>
                <span>Enrolled Department: <strong>{user.branch || 'Computer Science & Engineering'}</strong> • Enrolled Semester: <strong>Semester {user.semester || 3}</strong></span>
              </div>
              <span className="text-[10px] bg-emerald-200/80 px-2 py-0.5 rounded text-emerald-800 font-bold">Partition Locked</span>
            </div>
          )}

          {/* Grid of Materials */}
          {loading ? (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {[1, 2, 3].map(i => <Skeleton key={i} className="h-44 rounded-3xl" />)}
            </div>
          ) : error ? (
            <ErrorState message={error} onRetry={load} />
          ) : materials.length === 0 ? (
            <EmptyState
              icon="fa-folder-open"
              title="No Materials for Selected Semester"
              description={`No syllabus notes have been uploaded for Semester ${filterSemester || user.semester || 3} yet.`}
              actionText={user.role !== "student" ? "Upload First Material" : null}
              onAction={() => setShowUploadModal(true)}
            />
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {materials.map(m => (
                <div key={m.id} className="p-5 bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-3xl shadow-sm flex flex-col justify-between space-y-4 hover:border-emerald-300 transition">
                  <div>
                    <div className="flex justify-between items-start mb-2">
                      <span className="text-[10px] font-extrabold uppercase tracking-wider px-2.5 py-1 rounded-lg bg-emerald-50 text-emerald-700 border border-emerald-100">
                        {m.subject}
                      </span>
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-slate-100 text-slate-700">
                        Semester {m.semester || 3}
                      </span>
                    </div>
                    <h4 className="text-sm font-bold text-slate-900 dark:text-slate-100 mt-2 leading-snug">{m.title}</h4>
                    <p className="text-[11px] text-slate-500 mt-1 line-clamp-3 leading-relaxed">{m.content_text}</p>
                  </div>

                  <div className="pt-3 border-t border-slate-100 dark:border-slate-800 flex items-center justify-between">
                    <span className="text-[10px] text-slate-400 font-mono">
                      <i className="fas fa-file-pdf text-red-500 mr-1"></i>{m.pages} Pages
                    </span>
                    <button
                      onClick={() => onAskAI(m.title)}
                      className="px-3 py-1.5 bg-emerald-50 hover:bg-emerald-100 text-emerald-800 text-[11px] font-bold rounded-xl transition flex items-center gap-1.5"
                    >
                      <i className="fas fa-graduation-cap text-emerald-600"></i>Explain with AI
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Quick Note Modal */}
          {showQuickModal && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
              <div className="bg-white rounded-3xl border border-slate-200 shadow-2xl max-w-md w-full p-6 space-y-4">
                <h3 className="text-base font-bold text-slate-900">Publish Quick Course Note</h3>
                <form onSubmit={handleQuickUpload} className="space-y-3">
                  <div>
                    <label className="block text-[11px] font-bold text-slate-700 uppercase tracking-wider mb-1">Title</label>
                    <input
                      type="text"
                      required
                      value={quickForm.title}
                      onChange={e => setQuickForm({ ...quickForm, title: e.target.value })}
                      placeholder="e.g. Unit 4 - Graph Search & Greedy Heuristics"
                      className="w-full px-3 py-2 text-xs bg-slate-50 border border-slate-200 rounded-xl outline-none"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-[11px] font-bold text-slate-700 uppercase tracking-wider mb-1">Target Semester</label>
                      <select
                        value={quickForm.semester}
                        onChange={e => setQuickForm({ ...quickForm, semester: parseInt(e.target.value, 10) })}
                        className="w-full px-3 py-2 text-xs bg-slate-50 border border-slate-200 rounded-xl font-bold"
                      >
                        {[1, 2, 3, 4, 5, 6, 7, 8].map(s => (
                          <option key={s} value={s}>Semester {s}</option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="block text-[11px] font-bold text-slate-700 uppercase tracking-wider mb-1">Subject</label>
                      <input
                        type="text"
                        required
                        value={quickForm.subject}
                        onChange={e => setQuickForm({ ...quickForm, subject: e.target.value })}
                        placeholder="Artificial Intelligence"
                        className="w-full px-3 py-2 text-xs bg-slate-50 border border-slate-200 rounded-xl"
                      />
                    </div>
                  </div>
                  <div className="flex justify-end gap-2 pt-2">
                    <button type="button" onClick={() => setShowQuickModal(false)} className="px-4 py-2 text-xs font-bold text-slate-500 rounded-xl hover:bg-slate-100">Cancel</button>
                    <button type="submit" className="px-5 py-2 bg-[#159B73] text-white text-xs font-bold rounded-xl shadow-md">
                      Publish Note
                    </button>
                  </div>
                </form>
              </div>
            </div>
          )}

          {/* Large PDF Upload Modal (1 GB) */}
          {showUploadModal && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
              <div className="bg-white rounded-3xl border border-slate-200 shadow-2xl max-w-lg w-full p-6 space-y-4">
                <div className="flex justify-between items-center">
                  <h3 className="text-base font-bold text-slate-900">Upload Certified Syllabus Asset (Up to 1 GB)</h3>
                  <button onClick={() => setShowUploadModal(false)} className="text-slate-400 hover:text-slate-600">
                    <i className="fas fa-xmark text-lg"></i>
                  </button>
                </div>
                <form onSubmit={handleLargeFileUpload} className="space-y-3">
                  <div>
                    <label className="block text-[11px] font-bold text-slate-700 uppercase tracking-wider mb-1">Material Title</label>
                    <input
                      type="text"
                      required
                      value={fileForm.title}
                      onChange={e => setFileForm({ ...fileForm, title: e.target.value })}
                      placeholder="e.g. Complete Relational Database Systems Textbook"
                      className="w-full px-3 py-2 text-xs bg-slate-50 border border-slate-200 rounded-xl outline-none"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-[11px] font-bold text-slate-700 uppercase tracking-wider mb-1">Target Semester</label>
                      <select
                        value={fileForm.semester}
                        onChange={e => setFileForm({ ...fileForm, semester: parseInt(e.target.value, 10) })}
                        className="w-full px-3 py-2 text-xs bg-slate-50 border border-slate-200 rounded-xl font-bold"
                      >
                        {[1, 2, 3, 4, 5, 6, 7, 8].map(s => (
                          <option key={s} value={s}>Semester {s}</option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="block text-[11px] font-bold text-slate-700 uppercase tracking-wider mb-1">Subject</label>
                      <input
                        type="text"
                        required
                        value={fileForm.subject}
                        onChange={e => setFileForm({ ...fileForm, subject: e.target.value })}
                        placeholder="Database Management Systems"
                        className="w-full px-3 py-2 text-xs bg-slate-50 border border-slate-200 rounded-xl"
                      />
                    </div>
                  </div>

                  {/* File Dropzone */}
                  <div className="p-5 border-2 border-dashed border-[#D5EADB] rounded-2xl bg-[#F4FBF8] text-center">
                    <input
                      type="file"
                      id="large-pdf-input"
                      onChange={e => setSelectedFile(e.target.files[0])}
                      className="hidden"
                    />
                    <label htmlFor="large-pdf-input" className="cursor-pointer block">
                      <i className="fas fa-cloud-arrow-up text-2xl text-[#159B73] mb-2"></i>
                      <p className="text-xs font-bold text-slate-800">
                        {selectedFile ? selectedFile.name : "Click to select curriculum file or textbook (up to 1 GB)"}
                      </p>
                      {selectedFile && (
                        <p className="text-[11px] text-slate-400 mt-1">{(selectedFile.size / (1024 * 1024)).toFixed(2)} MB</p>
                      )}
                    </label>
                  </div>

                  {isUploading && (
                    <div className="space-y-1">
                      <div className="flex justify-between text-xs font-bold text-emerald-800">
                        <span>Streaming to Storage...</span>
                        <span>{uploadProgress}%</span>
                      </div>
                      <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                        <div className="h-full bg-emerald-500 transition-all duration-200" style={{ width: `${uploadProgress}%` }}></div>
                      </div>
                    </div>
                  )}

                  <div className="flex justify-end gap-2 pt-2">
                    <button type="button" onClick={() => setShowUploadModal(false)} className="px-4 py-2 text-xs font-bold text-slate-500 rounded-xl hover:bg-slate-100">Cancel</button>
                    <button type="submit" disabled={isUploading} className="px-5 py-2 bg-[#159B73] text-white text-xs font-bold rounded-xl shadow-md">
                      {isUploading ? "Streaming..." : "Upload & Partition"}
                    </button>
                  </div>
                </form>
              </div>
            </div>
          )}
        </div>
      );
    }

    function AITutorView({ token, initialQuery }) {
      const [messages, setMessages] = useState([
        { sender: "ai", text: "EduNova Learning Companion is ready. Ask any conceptual or derivation question based on verified curricula.", source: "Core Curriculum" }
      ]);
      const [input, setInput] = useState(initialQuery || "");
      const [loading, setLoading] = useState(false);
      const [useRAG, setUseRAG] = useState(true);
      const chatEndRef = useRef(null);

      useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

      const handleSend = async q => {
        const text = q || input;
        if (!text.trim() || loading) return;
        setMessages(prev => [...prev, { sender: "user", text }]);
        setInput("");
        setLoading(true);
        try {
          const res = useRAG ? await api.askGroundedAI(token, text) : await api.askAI(token, text);
          setMessages(prev => [...prev, { sender: "ai", text: res.reply, source: res.source }]);
        } catch {
          setMessages(prev => [...prev, { sender: "ai", text: "I couldn't reach the learning service right now. Please try again.", source: "System Error" }]);
        } finally {
          setLoading(false);
        }
      };

      return (
        <div className="bg-white dark:bg-slate-900 border border-emerald-100/80 dark:border-slate-800 rounded-3xl shadow-sm h-[75vh] flex flex-col justify-between overflow-hidden">
          <div className="p-4 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between bg-emerald-50/40 dark:bg-slate-800/40">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-xl bg-[#356f5b] text-white flex items-center justify-center text-xs shadow-sm">
                <i className="fas fa-graduation-cap"></i>
              </div>
              <div>
                <h4 className="text-xs font-bold text-slate-800 dark:text-slate-200">EduNova Learning Companion</h4>
                <p className="text-[10px] text-emerald-600 dark:text-emerald-400 font-semibold">Learning from your course materials</p>
              </div>
            </div>
            <button type="button" onClick={() => setUseRAG(!useRAG)}
                    className={`px-3 py-1 rounded-xl text-[10px] font-bold transition flex items-center gap-1.5 focus-ring ${useRAG ? "bg-emerald-600 text-white shadow-sm" : "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300"}`}>
              <i className={`fas ${useRAG ? "fa-brain" : "fa-book"}`}></i>
              <span>{useRAG ? "Course notes: ON" : "General learning"}</span>
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {messages.map((m, idx) => (
              <div key={idx} className={`flex ${m.sender === "user" ? "justify-end" : "justify-start"}`}>
                <div className={`p-4 rounded-2xl max-w-lg text-xs leading-relaxed ${m.sender === "user" ? "bg-[#356f5b] text-white rounded-br-none" : "bg-emerald-50/50 dark:bg-slate-800 text-slate-800 dark:text-slate-100 rounded-bl-none border border-emerald-100/50 dark:border-slate-700"}`}>
                  <p className="whitespace-pre-line">{m.text}</p>
                  {m.source && (
                    <p className="text-[10px] text-emerald-600 dark:text-emerald-400 font-bold mt-2 pt-2 border-t border-slate-200 dark:border-slate-700">
                      <i className="fas fa-bookmark mr-1"></i>Source: {m.source}
                    </p>
                  )}
                </div>
              </div>
            ))}
            {loading && <div className="text-xs text-slate-400 italic"><i className="fas fa-circle-notch fa-spin mr-2"></i>Synthesizing syllabus-verified response...</div>}
            <div ref={chatEndRef} />
          </div>

          <form onSubmit={e => { e.preventDefault(); handleSend(); }} className="p-3.5 border-t border-slate-200 dark:border-slate-800 flex gap-2 bg-white dark:bg-slate-900">
            <input value={input} onChange={e => setInput(e.target.value)} placeholder="Ask about Relational Algebra, BCNF, Backpropagation..."
                   className="flex-1 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-2.5 text-xs outline-none focus-ring text-slate-800 dark:text-slate-100" />
            <button type="submit" disabled={loading} className="px-5 bg-[#356f5b] hover:opacity-95 text-white rounded-xl text-xs font-bold focus-ring">
              Send
            </button>
          </form>
        </div>
      );
    }

    // --- EduNova Coding Test, 30-min Timer, Software Aptitude (50 MCQs) & Teacher Control View ---
    const DEFAULT_TEMPLATES = {
      Python: "def main():\\n    # Write your solution here\\n    pass\\n\\nif __name__ == '__main__':\\n    main()\\n",
      C: "#include <stdio.h>\\n\\nint main(void) {\\n    // Write your solution here\\n    return 0;\\n}\\n",
      "C++": "#include <iostream>\\nusing namespace std;\\n\\nint main() {\\n    // Write your solution here\\n    return 0;\\n}\\n",
      Java: "import java.util.*;\\n\\npublic class Main {\\n    public static void main(String[] args) {\\n        // Write your solution here\\n    }\\n}\\n",
    };

    function TestsView({ token, user }) {
      const [testMode, setTestMode] = useState("coding"); // "coding" | "aptitude" | "teacher"
      const [bank, setBank] = useState({ coding: [], mcq: [] });
      const [loading, setLoading] = useState(true);

      // Coding stage state
      const [currentCodingIndex, setCurrentCodingIndex] = useState(0);
      const [selectedLanguage, setSelectedLanguage] = useState("Python");
      const [codingAnswers, setCodingAnswers] = useState({});
      const [currentCode, setCurrentCode] = useState(DEFAULT_TEMPLATES.Python);
      const [resultsText, setResultsText] = useState("Ready. Select a language, write your code, and press Run.");
      const [isRunning, setIsRunning] = useState(false);
      const [isSubmitting, setIsSubmitting] = useState(false);
      const [savedLabel, setSavedLabel] = useState("● Ready");

      // 30-minute countdown timer
      const [codingSecondsRemaining, setCodingSecondsRemaining] = useState(30 * 60);
      const [timerRunning, setTimerRunning] = useState(true);

      // Aptitude stage state
      const [aptitudeQuestions, setAptitudeQuestions] = useState([]);
      const [currentAptitudeIndex, setCurrentAptitudeIndex] = useState(0);
      const [mcqAnswers, setMcqAnswers] = useState({});
      const [aptitudeSubmitted, setAptitudeSubmitted] = useState(false);
      const [aptitudeResult, setAptitudeResult] = useState(null);

      // Teacher control state
      const [teacherTab, setTeacherTab] = useState("mcq"); // "mcq" | "coding" | "pdf"
      const [mcqForm, setMcqForm] = useState({
        question: "",
        options: ["", "", "", ""],
        answer: 0,
        topic: "Problem Solving"
      });
      const [codingForm, setCodingForm] = useState({
        title: "",
        difficulty: "Easy",
        description: "",
        input: "",
        expected_output: "",
        tests_raw: "3 => 01 02 03\\n04 05 06\\n07 08 09\\n\\n2 => 01 02\\n03 04"
      });
      const [pdfDraft, setPdfDraft] = useState({ mcq: [], coding: [] });
      const [pdfPreviewText, setPdfPreviewText] = useState("");
      const [pdfFileName, setPdfFileName] = useState("");
      const [pdfUploading, setPdfUploading] = useState(false);

      // Confirmation dialogs
      const [confirmSubmitCodingOpen, setConfirmSubmitCodingOpen] = useState(false);
      const [confirmSubmitAptitudeOpen, setConfirmSubmitAptitudeOpen] = useState(false);

      // Fetch question bank on mount
      const fetchBank = useCallback(async () => {
        try {
          setLoading(true);
          const res = await api.getQuestionBank(token);
          setBank(res);
          if (res.coding && res.coding.length > 0) {
            setCurrentCode(DEFAULT_TEMPLATES.Python);
          }
        } catch (err) {
          toast.error("Failed to load question bank: " + err.message);
        } finally {
          setLoading(false);
        }
      }, [token]);

      useEffect(() => {
        fetchBank();
      }, [fetchBank]);

      // Timer effect
      useEffect(() => {
        if (!timerRunning || testMode !== "coding") return;
        const interval = setInterval(() => {
          setCodingSecondsRemaining(prev => {
            if (prev <= 1) {
              clearInterval(interval);
              setTimerRunning(false);
              toast.warning("Coding test time expired! Unlocking Software Aptitude test...");
              unlockAptitude(true);
              return 0;
            }
            return prev - 1;
          });
        }, 1000);
        return () => clearInterval(interval);
      }, [timerRunning, testMode]);

      // Format time MM:SS
      const formatTime = (secs) => {
        const m = Math.floor(secs / 60);
        const s = secs % 60;
        return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
      };

      // Change language
      const handleLanguageChange = (lang) => {
        setCodingAnswers(prev => ({
          ...prev,
          [currentCodingIndex]: {
            language: selectedLanguage,
            code: currentCode
          }
        }));
        setSelectedLanguage(lang);
        const existing = codingAnswers[currentCodingIndex];
        if (existing && existing.language === lang) {
          setCurrentCode(existing.code);
        } else {
          setCurrentCode(DEFAULT_TEMPLATES[lang] || "");
        }
        setSavedLabel(`● ${lang} selected`);
      };

      // Select coding question
      const selectCodingQuestion = (idx) => {
        setCodingAnswers(prev => ({
          ...prev,
          [currentCodingIndex]: {
            language: selectedLanguage,
            code: currentCode
          }
        }));
        setCurrentCodingIndex(idx);
        const saved = codingAnswers[idx];
        if (saved && saved.code !== undefined) {
          setSelectedLanguage(saved.language || "Python");
          setCurrentCode(saved.code);
        } else {
          setSelectedLanguage("Python");
          setCurrentCode(DEFAULT_TEMPLATES.Python);
        }
        setSavedLabel("● Ready");
      };

      // Save current answer
      const saveCurrentCode = () => {
        setCodingAnswers(prev => ({
          ...prev,
          [currentCodingIndex]: {
            language: selectedLanguage,
            code: currentCode
          }
        }));
        setSavedLabel("● Saved");
        toast.success("Code saved for Question " + (currentCodingIndex + 1));
      };

      // Run code locally
      const handleRunCode = async () => {
        saveCurrentCode();
        const currentQ = bank.coding[currentCodingIndex];
        if (!currentCode.trim()) {
          setResultsText("Error: Code editor is empty.");
          return;
        }

        setIsRunning(true);
        setResultsText(`Running ${selectedLanguage} against sample test cases for:\\n${currentQ?.title || "Problem"}...\\n`);
        try {
          const res = await api.runCode(token, {
            language: selectedLanguage,
            code: currentCode,
            question_id: currentQ?.id
          });
          const logText = (res.logs || []).join("\\n\\n");
          setResultsText(`${res.summary}\\n\\nLanguage: ${selectedLanguage}\\n\\n${logText}`);
          if (res.ok) {
            toast.success("All sample test cases passed!");
          } else {
            toast.error("Some test cases failed or encountered runtime errors.");
          }
        } catch (err) {
          setResultsText("Execution Error:\\n" + err.message);
          toast.error("Execution failed: " + err.message);
        } finally {
          setIsRunning(false);
        }
      };

      // Submit coding answer
      const handleSubmitCoding = async () => {
        saveCurrentCode();
        const currentQ = bank.coding[currentCodingIndex];
        if (!currentCode.trim()) {
          setResultsText("Error: Cannot submit empty code.");
          return;
        }

        setIsSubmitting(true);
        setResultsText("Submitting solution against all hidden test cases...");
        try {
          const res = await api.submitCode(token, {
            language: selectedLanguage,
            code: currentCode,
            question_id: currentQ?.id
          });
          setResultsText(res.message);
          if (res.accepted) {
            setCodingAnswers(prev => ({
              ...prev,
              [currentCodingIndex]: {
                language: selectedLanguage,
                code: currentCode,
                accepted: true
              }
            }));
            toast.success(`✓ Question ${currentCodingIndex + 1} Accepted!`);
          } else {
            toast.error(`✗ Question ${currentCodingIndex + 1} Rejected. Check test results.`);
          }
        } catch (err) {
          setResultsText("Submission Error:\\n" + err.message);
          toast.error("Submission failed: " + err.message);
        } finally {
          setIsSubmitting(false);
        }
      };

      // Unlock Aptitude Stage
      const unlockAptitude = async (auto = false) => {
        saveCurrentCode();
        setTimerRunning(false);
        try {
          setLoading(true);
          const res = await api.getAptitudeQuestions(token);
          if (!res.questions || res.questions.length === 0) {
            toast.warning("No MCQ questions found in the teacher bank. Faculty must add or import MCQs.");
            return;
          }
          setAptitudeQuestions(res.questions);
          setCurrentAptitudeIndex(0);
          setMcqAnswers({});
          setTestMode("aptitude");
          toast.success(`Coding stage complete! Unlocked Software Aptitude with ${res.questions.length} questions.`);
        } catch (err) {
          toast.error("Failed to load aptitude questions: " + err.message);
        } finally {
          setLoading(false);
        }
      };

      // Aptitude option select
      const handleSelectOption = (choiceIdx) => {
        setMcqAnswers(prev => ({
          ...prev,
          [currentAptitudeIndex]: choiceIdx
        }));
      };

      // Submit Aptitude
      const handleFinishAptitude = async () => {
        setConfirmSubmitAptitudeOpen(false);
        try {
          setLoading(true);
          const payloadAnswers = {};
          aptitudeQuestions.forEach((q, idx) => {
            if (mcqAnswers[idx] !== undefined) {
              payloadAnswers[q.id] = mcqAnswers[idx];
            }
          });
          const res = await api.submitAptitude(token, { answers: payloadAnswers });
          setAptitudeResult(res);
          setAptitudeSubmitted(true);
          toast.success(`Assessment Complete! Software Aptitude Score: ${res.score}/${res.total} (${res.percentage}%)`);
        } catch (err) {
          toast.error("Failed to submit aptitude evaluation: " + err.message);
        } finally {
          setLoading(false);
        }
      };

      // Teacher Actions
      const handleAddTeacherMCQ = async (e) => {
        e.preventDefault();
        if (!mcqForm.question.trim() || mcqForm.options.some(o => !o.trim())) {
          toast.error("Enter question and all 4 options.");
          return;
        }
        try {
          await api.addTeacherMCQ(token, mcqForm);
          toast.success("MCQ added to teacher question bank.");
          setMcqForm({ question: "", options: ["", "", "", ""], answer: 0, topic: "Problem Solving" });
          fetchBank();
        } catch (err) {
          toast.error("Failed to add MCQ: " + err.message);
        }
      };

      const handleAddTeacherCoding = async (e) => {
        e.preventDefault();
        if (!codingForm.title.trim() || !codingForm.description.trim()) {
          toast.error("Title and description are required.");
          return;
        }
        try {
          await api.addTeacherCoding(token, codingForm);
          toast.success("Coding question and test cases added to bank.");
          setCodingForm({
            title: "",
            difficulty: "Easy",
            description: "",
            input: "",
            expected_output: "",
            tests_raw: "3 => 01 02 03\\n04 05 06\\n07 08 09\\n\\n2 => 01 02\\n03 04"
          });
          fetchBank();
        } catch (err) {
          toast.error("Failed to add coding question: " + err.message);
        }
      };

      const handleUploadPDF = async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        setPdfFileName(file.name);
        setPdfUploading(true);
        try {
          const formData = new FormData();
          formData.append("file", file);
          const res = await api.importTeacherPDF(token, formData);
          setPdfDraft(res.draft || { mcq: [], coding: [] });
          setPdfPreviewText(
            `PDF: ${res.filename}\\nMCQ drafts: ${res.mcq_count}\\nCoding drafts: ${res.coding_count}\\n\\n` +
            `----- EXTRACTED PREVIEW -----\\n\\n${res.preview_text}`
          );
          toast.success(`PDF parsed! Detected ${res.mcq_count} MCQs and ${res.coding_count} Coding questions.`);
        } catch (err) {
          toast.error("PDF upload/parse error: " + err.message);
        } finally {
          setPdfUploading(false);
        }
      };

      const handlePublishPDFDraft = async () => {
        if (!pdfDraft.mcq.length && !pdfDraft.coding.length) {
          toast.warning("No structured questions detected in draft. Use standard teacher markers.");
          return;
        }
        try {
          const res = await api.publishTeacherPDF(token, pdfDraft);
          toast.success(`Published ${res.published_mcq} MCQs and ${res.published_coding} coding questions to question bank!`);
          setPdfDraft({ mcq: [], coding: [] });
          setPdfPreviewText("");
          setPdfFileName("");
          fetchBank();
        } catch (err) {
          toast.error("Failed to publish draft: " + err.message);
        }
      };

      if (loading && !bank.coding.length) {
        return (
          <div className="p-12 text-center text-slate-500 space-y-3">
            <i className="fas fa-spinner fa-spin text-3xl text-emerald-600"></i>
            <p className="text-sm font-semibold">Loading EduNova Assessment Suite...</p>
          </div>
        );
      }

      const currentQ = bank.coding[currentCodingIndex] || {};
      const currentMCQ = aptitudeQuestions[currentAptitudeIndex] || {};

      return (
        <div className="space-y-6">
          {/* Top Assessment Header */}
          <div className="bg-[#004D43] text-white rounded-2xl p-4 sm:p-5 flex flex-wrap items-center justify-between gap-4 shadow-md border border-[#003F39]">
            {/* Left: Brand & Stage Info */}
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-[#003F39] text-[#19D99C] flex items-center justify-center font-bold text-xl border border-[#07584D]">
                ◇
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="text-base sm:text-lg font-bold text-white leading-tight">
                    {testMode === "coding" ? "Fresher Mock Test 02" : (testMode === "aptitude" ? "Software Aptitude Test" : "Teacher Question Control")}
                  </h3>
                  <span className="px-2 py-0.5 rounded-full text-[10px] font-extrabold uppercase bg-[#07584D] text-[#19D99C] border border-[#28756A]">
                    {testMode === "coding" ? "Coding Assessment" : (testMode === "aptitude" ? "Stage 2" : "Faculty Control")}
                  </span>
                </div>
                <p className="text-xs text-[#B8D4CF] mt-0.5">
                  {testMode === "coding" ? "30 Minutes • Multi-Language Local Execution" : (testMode === "aptitude" ? "Problem Solving • 50 Random Questions" : "Manage Question Bank & PDF Importer")}
                </p>
              </div>
            </div>

            {/* Right: Timer & Top Action Controls */}
            <div className="flex items-center gap-2.5 sm:gap-3 ml-auto">
              {/* Timer Box */}
              <div className="bg-[#07584D] border border-[#28756A] rounded-xl px-3.5 py-1.5 text-center min-w-[110px]">
                <p className={`text-base font-extrabold tracking-wider ${codingSecondsRemaining <= 300 ? "text-rose-400 animate-pulse" : "text-[#F6C84A]"}`}>
                  {testMode === "coding" ? formatTime(codingSecondsRemaining) : (testMode === "aptitude" ? "UNLOCKED" : "CONTROL")}
                </p>
                <p className="text-[9px] text-[#C6DCD8] uppercase font-bold tracking-tight">
                  {testMode === "coding" ? "Coding Time" : (testMode === "aptitude" ? "Aptitude Stage" : "Bank Active")}
                </p>
              </div>

              {testMode === "coding" && (
                <>
                  <button
                    onClick={saveCurrentCode}
                    className="px-3.5 py-2 bg-transparent hover:bg-[#07584D] text-white border border-[#3D8579] rounded-xl text-xs font-bold transition flex items-center gap-1.5"
                  >
                    <i className="fas fa-floppy-disk text-xs"></i>
                    <span>Save</span>
                  </button>
                  <button
                    onClick={() => setConfirmSubmitCodingOpen(true)}
                    className="px-4 py-2 bg-[#009B72] hover:bg-[#007D5C] text-white rounded-xl text-xs font-bold transition flex items-center gap-1.5 shadow-sm"
                  >
                    <span>Finish Coding</span>
                    <i className="fas fa-arrow-right text-xs"></i>
                  </button>
                </>
              )}

              {testMode === "aptitude" && !aptitudeSubmitted && (
                <button
                  onClick={() => setConfirmSubmitAptitudeOpen(true)}
                  className="px-4 py-2 bg-[#009B72] hover:bg-[#007D5C] text-white rounded-xl text-xs font-bold transition flex items-center gap-1.5 shadow-sm"
                >
                  <i className="fas fa-check-circle text-xs"></i>
                  <span>Submit Aptitude</span>
                </button>
              )}

              {/* Teacher Toggle */}
              {(user?.role === "teacher" || user?.role === "admin") && (
                <button
                  onClick={() => setTestMode(prev => prev === "teacher" ? "coding" : "teacher")}
                  className="px-3.5 py-2 bg-[#07584D] hover:bg-[#003F39] text-[#19D99C] border border-[#28756A] rounded-xl text-xs font-bold transition flex items-center gap-1.5"
                  title="Toggle Teacher Question Control"
                >
                  <i className="fas fa-gear text-xs"></i>
                  <span className="hidden md:inline">{testMode === "teacher" ? "Student View" : "Teacher Control"}</span>
                </button>
              )}
            </div>
          </div>

          {/* ================================================================ */}
          {/* MODE 1: CODING ASSESSMENT VIEW                                   */}
          {/* ================================================================ */}
          {testMode === "coding" && (
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
              {/* Column 1: Problem Description Panel (3 cols) */}
              <div className="lg:col-span-4 bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-2xl p-5 shadow-sm space-y-4 max-h-[850px] overflow-y-auto">
                <div className="flex items-center justify-between pb-2 border-b border-slate-100 dark:border-slate-800">
                  <span className="text-xs font-bold text-emerald-700 dark:text-emerald-400">
                    ● {currentQ.difficulty || "Medium"} Difficulty
                  </span>
                  <span className="text-xs text-slate-500 font-semibold">
                    Question {currentCodingIndex + 1} / {bank.coding.length}
                  </span>
                </div>

                <div>
                  <h3 className="text-lg font-bold text-slate-900 dark:text-slate-100 leading-snug">
                    {currentQ.title || "Coding Problem"}
                  </h3>
                  <p className="text-xs text-slate-600 dark:text-slate-300 mt-2 leading-relaxed whitespace-pre-wrap">
                    {currentQ.description}
                  </p>
                </div>

                {/* Example Card */}
                <div className="bg-[#F0F8F9] dark:bg-slate-800/60 border border-[#D9E9EB] dark:border-slate-700/60 rounded-xl p-3.5 space-y-2">
                  <p className="text-xs font-bold text-slate-800 dark:text-slate-200">Example</p>
                  <div className="text-[11px] font-mono text-slate-800 dark:text-slate-300 space-y-1.5">
                    <div>
                      <span className="text-slate-500 font-semibold">Input:</span>
                      <pre className="bg-white dark:bg-slate-900 p-2 rounded-lg border border-slate-200 dark:border-slate-700 mt-1 overflow-x-auto">{currentQ.input || "(No input required)"}</pre>
                    </div>
                    <div>
                      <span className="text-slate-500 font-semibold">Expected Output:</span>
                      <pre className="bg-white dark:bg-slate-900 p-2 rounded-lg border border-slate-200 dark:border-slate-700 mt-1 overflow-x-auto">{currentQ.expected_output}</pre>
                    </div>
                  </div>
                </div>

                {/* Hidden Test Cases Card */}
                {currentQ.tests && currentQ.tests.length > 0 && (
                  <div className="bg-[#F6FBF9] dark:bg-slate-800/40 border border-[#DCEEE7] dark:border-slate-700/40 rounded-xl p-3.5">
                    <p className="text-xs font-bold text-emerald-800 dark:text-emerald-400 flex items-center gap-1.5">
                      <i className="fas fa-shield-check text-xs"></i>
                      <span>Hidden Test Cases</span>
                    </p>
                    <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">
                      {currentQ.tests.length} teacher-configured test cases will be validated upon submission.
                    </p>
                  </div>
                )}

                {/* Constraints & Notes */}
                <div className="border-t border-slate-100 dark:border-slate-800 pt-3 text-[11px] text-slate-500 dark:text-slate-400 space-y-1">
                  <p className="font-bold text-slate-700 dark:text-slate-300">Constraints & Notes:</p>
                  <ul className="list-disc pl-4 space-y-0.5">
                    <li>Output is compared after safe whitespace normalization.</li>
                    <li>Time limit is strictly 5s per test case.</li>
                    <li>Supported: Python 3, GCC (C), G++ (C++), JDK (Java).</li>
                  </ul>
                </div>
              </div>

              {/* Column 2: Editor & Results Terminal (5 cols) */}
              <div className="lg:col-span-5 space-y-4">
                {/* Editor Container */}
                <div className="bg-[#13232C] rounded-2xl overflow-hidden shadow-sm border border-slate-700/60">
                  {/* Editor Toolbar */}
                  <div className="bg-[#1B303A] px-4 py-2.5 flex items-center justify-between border-b border-slate-700/60">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-slate-300 font-bold">Language:</span>
                      <select
                        value={selectedLanguage}
                        onChange={(e) => handleLanguageChange(e.target.value)}
                        className="bg-[#13232C] text-emerald-400 text-xs font-bold px-2.5 py-1 rounded-lg border border-slate-600 focus:outline-none focus:ring-1 focus:ring-emerald-500"
                      >
                        <option value="Python">Python</option>
                        <option value="Java">Java</option>
                        <option value="C">C</option>
                        <option value="C++">C++</option>
                      </select>
                      <span className="text-[11px] font-bold text-emerald-400 ml-2">{savedLabel}</span>
                    </div>

                    <div className="flex items-center gap-2">
                      <button
                        onClick={handleRunCode}
                        disabled={isRunning}
                        className="px-3 py-1 bg-[#E8F7F3] hover:bg-[#D5EFE8] text-[#007D5C] rounded-lg text-xs font-bold transition flex items-center gap-1.5 disabled:opacity-50"
                      >
                        {isRunning ? <i className="fas fa-spinner fa-spin text-xs"></i> : <i className="fas fa-play text-xs"></i>}
                        <span>Run</span>
                      </button>
                      <button
                        onClick={handleSubmitCoding}
                        disabled={isSubmitting}
                        className="px-3.5 py-1 bg-[#007D5C] hover:bg-[#009B72] text-white rounded-lg text-xs font-bold transition flex items-center gap-1.5 disabled:opacity-50"
                      >
                        {isSubmitting ? <i className="fas fa-spinner fa-spin text-xs"></i> : <i className="fas fa-paper-plane text-xs"></i>}
                        <span>Submit</span>
                      </button>
                    </div>
                  </div>

                  {/* Editor Filename Header */}
                  <div className="bg-[#172B34] px-4 py-1.5 flex items-center justify-between border-b border-slate-700/40 text-[11px] font-mono text-slate-400">
                    <span>
                      {selectedLanguage === "Python" ? "main.py" : (selectedLanguage === "Java" ? "Main.java" : (selectedLanguage === "C" ? "main.c" : "main.cpp"))}
                    </span>
                    <span className="text-[10px] text-slate-500">Local Multi-Language Runner</span>
                  </div>

                  {/* Code Textarea */}
                  <textarea
                    value={currentCode}
                    onChange={(e) => {
                      setCurrentCode(e.target.value);
                      setSavedLabel("● Unsaved changes");
                    }}
                    rows={16}
                    className="w-full bg-[#13232C] text-[#DCE8EA] font-mono text-xs p-4 focus:outline-none resize-y border-none leading-relaxed"
                    spellCheck="false"
                  />
                </div>

                {/* Test Results Box */}
                <div className="bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-2xl p-4 shadow-sm space-y-2">
                  <div className="flex items-center justify-between">
                    <h4 className="text-xs font-bold text-slate-800 dark:text-slate-200 flex items-center gap-1.5">
                      <i className="fas fa-terminal text-emerald-600"></i>
                      <span>Test Results & Execution Logs</span>
                    </h4>
                    {codingAnswers[currentCodingIndex]?.accepted && (
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-100 text-emerald-800">
                        ✓ Accepted
                      </span>
                    )}
                  </div>
                  <pre className="bg-[#13232C] text-[#DCE8EA] font-mono text-[11px] p-3.5 rounded-xl max-h-48 overflow-y-auto whitespace-pre-wrap leading-relaxed">
                    {resultsText}
                  </pre>
                </div>
              </div>

              {/* Column 3: Navigation Panel (3 cols) */}
              <div className="lg:col-span-3 bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-2xl p-5 shadow-sm space-y-5">
                <div>
                  <h4 className="text-sm font-bold text-slate-900 dark:text-slate-100">Coding Test</h4>
                  <div className="flex items-center justify-between text-xs text-slate-500 mt-1 font-semibold">
                    <span>Progress</span>
                    <span>Question {currentCodingIndex + 1} / {bank.coding.length}</span>
                  </div>
                  <div className="w-full h-2 bg-slate-100 dark:bg-slate-800 rounded-full mt-2 overflow-hidden">
                    <div
                      className="h-full bg-[#009B72] rounded-full transition-all duration-300"
                      style={{ width: `${((currentCodingIndex + 1) / Math.max(1, bank.coding.length)) * 100}%` }}
                    />
                  </div>
                </div>

                {/* Prev & Next Buttons */}
                <div className="flex gap-2">
                  <button
                    disabled={currentCodingIndex === 0}
                    onClick={() => selectCodingQuestion(currentCodingIndex - 1)}
                    className="flex-1 py-2 bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 text-slate-700 dark:text-slate-300 rounded-xl text-xs font-bold transition disabled:opacity-40"
                  >
                    ← Prev
                  </button>
                  <button
                    onClick={() => {
                      if (currentCodingIndex < bank.coding.length - 1) {
                        selectCodingQuestion(currentCodingIndex + 1);
                      } else {
                        setConfirmSubmitCodingOpen(true);
                      }
                    }}
                    className="flex-1 py-2 bg-[#007D5C] hover:bg-[#009B72] text-white rounded-xl text-xs font-bold transition"
                  >
                    {currentCodingIndex < bank.coding.length - 1 ? "Next →" : "Finish →"}
                  </button>
                </div>

                {/* Question Grid */}
                <div>
                  <p className="text-xs font-bold text-slate-800 dark:text-slate-200 mb-2.5">Question Navigation</p>
                  <div className="grid grid-cols-4 gap-2">
                    {bank.coding.map((q, idx) => {
                      const isActive = idx === currentCodingIndex;
                      const isAnswered = codingAnswers[idx] !== undefined;
                      const isAccepted = codingAnswers[idx]?.accepted;
                      return (
                        <button
                          key={q.id || idx}
                          onClick={() => selectCodingQuestion(idx)}
                          className={`h-10 rounded-xl text-xs font-bold transition flex items-center justify-center border ${
                            isActive
                              ? "bg-[#007D5C] text-white border-[#007D5C] shadow-sm"
                              : isAccepted
                              ? "bg-emerald-100 text-emerald-800 border-emerald-300 dark:bg-emerald-950/60 dark:text-emerald-300"
                              : isAnswered
                              ? "bg-[#DDF4EE] text-[#007D5C] border-[#BCE5D5]"
                              : "bg-[#F3F6F7] dark:bg-slate-800 text-slate-600 dark:text-slate-400 border-transparent hover:border-slate-300"
                          }`}
                        >
                          {idx + 1}
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Status Box */}
                <div className="bg-[#EFFAF7] dark:bg-slate-800/50 rounded-xl p-3.5 border border-[#DCE9E5] dark:border-slate-700/50 text-xs text-slate-600 dark:text-slate-300 space-y-1">
                  <p className="font-bold text-slate-800 dark:text-slate-200">Status:</p>
                  <p>Answered: {Object.keys(codingAnswers).length} / {bank.coding.length}</p>
                  <p>Remaining: {Math.max(0, bank.coding.length - Object.keys(codingAnswers).length)}</p>
                </div>

                {/* Aptitude Stage Unlock Card */}
                <div className="bg-[#EEF7FF] dark:bg-slate-800/60 border border-[#C9DDF2] dark:border-slate-700/60 rounded-xl p-4 space-y-1.5">
                  <p className="text-xs font-bold text-blue-700 dark:text-blue-400 flex items-center gap-1.5">
                    <i className="fas fa-lock text-xs"></i>
                    <span>Software Aptitude Stage</span>
                  </p>
                  <p className="text-[11px] text-slate-600 dark:text-slate-300 leading-relaxed">
                    Complete all coding questions or finish early to unlock the 50 random problem-solving MCQ test.
                  </p>
                  <button
                    onClick={() => setConfirmSubmitCodingOpen(true)}
                    className="w-full mt-2 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-bold transition shadow-sm"
                  >
                    Unlock Aptitude Stage →
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* ================================================================ */}
          {/* MODE 2: SOFTWARE APTITUDE (50 MCQS) VIEW                         */}
          {/* ================================================================ */}
          {testMode === "aptitude" && (
            <div className="space-y-6">
              {aptitudeSubmitted && aptitudeResult ? (
                /* Assessment Finished Summary Card */
                <div className="max-w-2xl mx-auto bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-3xl p-8 shadow-sm text-center space-y-5">
                  <div className="w-16 h-16 rounded-3xl bg-emerald-50 dark:bg-emerald-950/40 text-emerald-600 flex items-center justify-center mx-auto text-2xl shadow-inner">
                    <i className="fas fa-award"></i>
                  </div>
                  <div>
                    <h3 className="text-2xl font-extrabold text-slate-900 dark:text-slate-100">
                      Software Aptitude Completed!
                    </h3>
                    <p className="text-xs text-slate-500 mt-1">
                      Your evaluation has been automatically scored and recorded in your student profile.
                    </p>
                  </div>

                  <div className="grid grid-cols-3 gap-3 py-4 border-y border-slate-100 dark:border-slate-800">
                    <div className="p-3 bg-slate-50 dark:bg-slate-800/60 rounded-2xl">
                      <p className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Score</p>
                      <p className="text-2xl font-black text-emerald-600 mt-1">{aptitudeResult.score} / {aptitudeResult.total}</p>
                    </div>
                    <div className="p-3 bg-slate-50 dark:bg-slate-800/60 rounded-2xl">
                      <p className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Percentage</p>
                      <p className="text-2xl font-black text-emerald-600 mt-1">{aptitudeResult.percentage}%</p>
                    </div>
                    <div className="p-3 bg-slate-50 dark:bg-slate-800/60 rounded-2xl">
                      <p className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Status</p>
                      <p className="text-2xl font-black text-emerald-600 mt-1">{aptitudeResult.percentage >= 60 ? "Passed" : "Needs Review"}</p>
                    </div>
                  </div>

                  <div className="flex justify-center gap-3 pt-2">
                    <button
                      onClick={() => setTestMode("coding")}
                      className="px-5 py-2.5 bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 text-slate-700 dark:text-slate-300 rounded-xl text-xs font-bold transition"
                    >
                      Review Coding Test
                    </button>
                    <button
                      onClick={() => {
                        setAptitudeSubmitted(false);
                        setTestMode("coding");
                        toast.success("Ready for next evaluation cycle!");
                      }}
                      className="px-6 py-2.5 bg-[#007D5C] hover:bg-[#009B72] text-white rounded-xl text-xs font-bold transition shadow-sm"
                    >
                      Finish & Exit
                    </button>
                  </div>
                </div>
              ) : (
                /* Active 50-MCQ Aptitude Interface */
                <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
                  {/* Left: MCQ Problem & Option Cards (8 cols) */}
                  <div className="lg:col-span-8 bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-2xl p-6 shadow-sm space-y-6">
                    <div className="flex items-center justify-between pb-3 border-b border-slate-100 dark:border-slate-800">
                      <span className="text-xs font-bold text-emerald-700 dark:text-emerald-400 uppercase tracking-wider">
                        ● {currentMCQ.topic || "Problem Solving"} MCQ
                      </span>
                      <span className="text-xs text-slate-500 font-semibold">
                        Question {currentAptitudeIndex + 1} of {aptitudeQuestions.length}
                      </span>
                    </div>

                    <div>
                      <h3 className="text-base sm:text-lg font-bold text-slate-900 dark:text-slate-100 leading-relaxed">
                        {currentMCQ.question}
                      </h3>
                      <p className="text-xs text-slate-500 mt-1">Select the single correct option below:</p>
                    </div>

                    {/* Options Grid */}
                    <div className="space-y-3">
                      {(currentMCQ.options || []).map((opt, optIdx) => {
                        const isSelected = mcqAnswers[currentAptitudeIndex] === optIdx;
                        return (
                          <button
                            key={optIdx}
                            onClick={() => handleSelectOption(optIdx)}
                            className={`w-full p-4 rounded-xl text-left text-xs font-medium transition flex items-center justify-between border ${
                              isSelected
                                ? "bg-[#DDF4EE] border-[#009B72] text-emerald-900 dark:bg-emerald-950/60 dark:text-emerald-200 font-bold shadow-sm"
                                : "bg-[#F6F8F8] dark:bg-slate-800 border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300 hover:border-emerald-300"
                            }`}
                          >
                            <div className="flex items-center gap-3">
                              <span className={`w-7 h-7 rounded-lg flex items-center justify-center font-bold text-xs ${
                                isSelected ? "bg-[#007D5C] text-white" : "bg-white dark:bg-slate-700 text-slate-600 dark:text-slate-300 border border-slate-300 dark:border-slate-600"
                              }`}>
                                {String.fromCharCode(65 + optIdx)}
                              </span>
                              <span>{opt}</span>
                            </div>
                            {isSelected && (
                              <i className="fas fa-check-circle text-emerald-600 text-sm"></i>
                            )}
                          </button>
                        );
                      })}
                    </div>

                    {/* Prev / Next Controls */}
                    <div className="flex items-center justify-between pt-4 border-t border-slate-100 dark:border-slate-800">
                      <button
                        disabled={currentAptitudeIndex === 0}
                        onClick={() => setCurrentAptitudeIndex(prev => Math.max(0, prev - 1))}
                        className="px-4 py-2 bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 text-slate-700 dark:text-slate-300 rounded-xl text-xs font-bold transition disabled:opacity-40"
                      >
                        ← Previous
                      </button>

                      {currentAptitudeIndex < aptitudeQuestions.length - 1 ? (
                        <button
                          onClick={() => setCurrentAptitudeIndex(prev => prev + 1)}
                          className="px-5 py-2 bg-[#007D5C] hover:bg-[#009B72] text-white rounded-xl text-xs font-bold transition"
                        >
                          Next Question →
                        </button>
                      ) : (
                        <button
                          onClick={() => setConfirmSubmitAptitudeOpen(true)}
                          className="px-5 py-2 bg-[#009B72] hover:bg-[#007D5C] text-white rounded-xl text-xs font-bold transition shadow-sm"
                        >
                          Finish Aptitude Test ✓
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Right: 50-Item Grid Navigation (4 cols) */}
                  <div className="lg:col-span-4 bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-2xl p-5 shadow-sm space-y-4">
                    <div>
                      <h4 className="text-sm font-bold text-slate-900 dark:text-slate-100">50 Random MCQs</h4>
                      <div className="flex items-center justify-between text-xs text-slate-500 mt-1 font-semibold">
                        <span>Answered</span>
                        <span>{Object.keys(mcqAnswers).length} / {aptitudeQuestions.length}</span>
                      </div>
                      <div className="w-full h-2 bg-slate-100 dark:bg-slate-800 rounded-full mt-2 overflow-hidden">
                        <div
                          className="h-full bg-[#009B72] rounded-full transition-all duration-300"
                          style={{ width: `${(Object.keys(mcqAnswers).length / Math.max(1, aptitudeQuestions.length)) * 100}%` }}
                        />
                      </div>
                    </div>

                    {/* Question Grid */}
                    <div className="max-h-72 overflow-y-auto pr-1">
                      <div className="grid grid-cols-5 gap-1.5">
                        {aptitudeQuestions.map((q, idx) => {
                          const isActive = idx === currentAptitudeIndex;
                          const isAnswered = mcqAnswers[idx] !== undefined;
                          return (
                            <button
                              key={q.id || idx}
                              onClick={() => setCurrentAptitudeIndex(idx)}
                              className={`h-8 rounded-lg text-[11px] font-bold transition flex items-center justify-center border ${
                                isActive
                                  ? "bg-[#007D5C] text-white border-[#007D5C] shadow-sm"
                                  : isAnswered
                                  ? "bg-[#DDF4EE] text-[#007D5C] border-[#BCE5D5]"
                                  : "bg-[#F3F6F7] dark:bg-slate-800 text-slate-600 dark:text-slate-400 border-transparent hover:border-slate-300"
                              }`}
                            >
                              {idx + 1}
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    <div className="bg-[#EFFAF7] dark:bg-slate-800/50 rounded-xl p-3 border border-[#DCE9E5] dark:border-slate-700/50 text-[11px] text-slate-600 dark:text-slate-300 space-y-1">
                      <p className="font-bold text-slate-800 dark:text-slate-200">Stage Information:</p>
                      <p>Questions sampled from the teacher's problem-solving bank.</p>
                      <p>Your answers are saved automatically as you make choices.</p>
                    </div>

                    <button
                      onClick={() => setConfirmSubmitAptitudeOpen(true)}
                      className="w-full py-2.5 bg-[#009B72] hover:bg-[#007D5C] text-white rounded-xl text-xs font-bold transition shadow-sm"
                    >
                      Submit Entire Test
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ================================================================ */}
          {/* MODE 3: TEACHER QUESTION CONTROL (FACULTY ONLY)                  */}
          {/* ================================================================ */}
          {testMode === "teacher" && (
            <div className="bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-3xl p-6 shadow-sm space-y-6">
              {/* Tab Selector */}
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 dark:border-slate-800 pb-4">
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setTeacherTab("mcq")}
                    className={`px-4 py-2 rounded-xl text-xs font-bold transition ${
                      teacherTab === "mcq" ? "bg-[#007D5C] text-white shadow-sm" : "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300"
                    }`}
                  >
                    MCQ Question Bank ({bank.mcq.length})
                  </button>
                  <button
                    onClick={() => setTeacherTab("coding")}
                    className={`px-4 py-2 rounded-xl text-xs font-bold transition ${
                      teacherTab === "coding" ? "bg-[#007D5C] text-white shadow-sm" : "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300"
                    }`}
                  >
                    Coding Question Bank ({bank.coding.length})
                  </button>
                  <button
                    onClick={() => setTeacherTab("pdf")}
                    className={`px-4 py-2 rounded-xl text-xs font-bold transition ${
                      teacherTab === "pdf" ? "bg-[#007D5C] text-white shadow-sm" : "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300"
                    }`}
                  >
                    Teacher PDF Import
                  </button>
                </div>

                <button
                  onClick={() => setTestMode("coding")}
                  className="px-4 py-2 bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 text-slate-700 dark:text-slate-300 rounded-xl text-xs font-bold transition flex items-center gap-1.5"
                >
                  <i className="fas fa-arrow-left text-xs"></i>
                  <span>Back to Student Test</span>
                </button>
              </div>

              {/* TAB 1: MCQ Bank */}
              {teacherTab === "mcq" && (
                <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
                  {/* Form (6 cols) */}
                  <form onSubmit={handleAddTeacherMCQ} className="lg:col-span-6 bg-[#F7FAF9] dark:bg-slate-800/40 p-5 rounded-2xl border border-slate-200 dark:border-slate-700 space-y-4">
                    <h4 className="text-sm font-bold text-slate-800 dark:text-slate-200">Add Problem-Solving MCQ</h4>

                    <div>
                      <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">Question Prompt</label>
                      <textarea
                        value={mcqForm.question}
                        onChange={(e) => setMcqForm({ ...mcqForm, question: e.target.value })}
                        rows={3}
                        placeholder="e.g. Which data structure follows FIFO order?"
                        className="w-full p-2.5 text-xs rounded-xl bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-600 focus:outline-none"
                      />
                    </div>

                    <div className="space-y-2">
                      <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300">Four Options</label>
                      {mcqForm.options.map((opt, optIdx) => (
                        <input
                          key={optIdx}
                          type="text"
                          value={opt}
                          onChange={(e) => {
                            const newOpts = [...mcqForm.options];
                            newOpts[optIdx] = e.target.value;
                            setMcqForm({ ...mcqForm, options: newOpts });
                          }}
                          placeholder={`Option ${String.fromCharCode(65 + optIdx)}`}
                          className="w-full p-2.5 text-xs rounded-xl bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-600 focus:outline-none"
                        />
                      ))}
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">Correct Answer</label>
                        <select
                          value={mcqForm.answer}
                          onChange={(e) => setMcqForm({ ...mcqForm, answer: parseInt(e.target.value, 10) })}
                          className="w-full p-2 text-xs rounded-xl bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-600 focus:outline-none"
                        >
                          <option value={0}>Option A</option>
                          <option value={1}>Option B</option>
                          <option value={2}>Option C</option>
                          <option value={3}>Option D</option>
                        </select>
                      </div>
                      <div>
                        <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">Topic</label>
                        <input
                          type="text"
                          value={mcqForm.topic}
                          onChange={(e) => setMcqForm({ ...mcqForm, topic: e.target.value })}
                          placeholder="e.g. Data Structures"
                          className="w-full p-2 text-xs rounded-xl bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-600 focus:outline-none"
                        />
                      </div>
                    </div>

                    <button
                      type="submit"
                      className="w-full py-2.5 bg-[#007D5C] hover:bg-[#009B72] text-white rounded-xl text-xs font-bold transition shadow-sm"
                    >
                      Add to Question Bank
                    </button>
                  </form>

                  {/* List (6 cols) */}
                  <div className="lg:col-span-6 bg-[#13232C] text-[#DCE8EA] p-4 rounded-2xl font-mono text-xs max-h-[550px] overflow-y-auto space-y-3">
                    <p className="text-emerald-400 font-bold">Published MCQs ({bank.mcq.length}):</p>
                    {bank.mcq.map((q, idx) => (
                      <div key={q.id || idx} className="pb-3 border-b border-slate-700/50 space-y-1">
                        <p className="text-white font-bold">{idx + 1}. [{q.topic || "General"}] {q.question}</p>
                        <p className="text-slate-400 pl-3">A) {q.options?.[0]}</p>
                        <p className="text-slate-400 pl-3">B) {q.options?.[1]}</p>
                        <p className="text-slate-400 pl-3">C) {q.options?.[2]}</p>
                        <p className="text-slate-400 pl-3">D) {q.options?.[3]}</p>
                        <p className="text-emerald-300 pl-3 font-semibold">Answer: {String.fromCharCode(65 + (q.answer || 0))}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* TAB 2: Coding Bank */}
              {teacherTab === "coding" && (
                <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
                  {/* Form (6 cols) */}
                  <form onSubmit={handleAddTeacherCoding} className="lg:col-span-6 bg-[#F7FAF9] dark:bg-slate-800/40 p-5 rounded-2xl border border-slate-200 dark:border-slate-700 space-y-4">
                    <h4 className="text-sm font-bold text-slate-800 dark:text-slate-200">Add Coding Problem & Test Cases</h4>

                    <div className="grid grid-cols-3 gap-3">
                      <div className="col-span-2">
                        <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">Title</label>
                        <input
                          type="text"
                          value={codingForm.title}
                          onChange={(e) => setCodingForm({ ...codingForm, title: e.target.value })}
                          placeholder="e.g. Reverse an Array"
                          className="w-full p-2.5 text-xs rounded-xl bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-600 focus:outline-none"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">Difficulty</label>
                        <select
                          value={codingForm.difficulty}
                          onChange={(e) => setCodingForm({ ...codingForm, difficulty: e.target.value })}
                          className="w-full p-2.5 text-xs rounded-xl bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-600 focus:outline-none"
                        >
                          <option value="Easy">Easy</option>
                          <option value="Medium">Medium</option>
                          <option value="Hard">Hard</option>
                        </select>
                      </div>
                    </div>

                    <div>
                      <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">Description</label>
                      <textarea
                        value={codingForm.description}
                        onChange={(e) => setCodingForm({ ...codingForm, description: e.target.value })}
                        rows={3}
                        placeholder="Detailed problem statement..."
                        className="w-full p-2.5 text-xs rounded-xl bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-600 focus:outline-none"
                      />
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">Example Input</label>
                        <textarea
                          value={codingForm.input}
                          onChange={(e) => setCodingForm({ ...codingForm, input: e.target.value })}
                          rows={2}
                          className="w-full p-2 text-xs rounded-xl bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-600 focus:outline-none font-mono"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">Expected Output</label>
                        <textarea
                          value={codingForm.expected_output}
                          onChange={(e) => setCodingForm({ ...codingForm, expected_output: e.target.value })}
                          rows={2}
                          className="w-full p-2 text-xs rounded-xl bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-600 focus:outline-none font-mono"
                        />
                      </div>
                    </div>

                    <div>
                      <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                        Hidden Test Cases (Format: INPUT => OUTPUT separated by blank lines)
                      </label>
                      <textarea
                        value={codingForm.tests_raw}
                        onChange={(e) => setCodingForm({ ...codingForm, tests_raw: e.target.value })}
                        rows={3}
                        className="w-full p-2 text-xs rounded-xl bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-600 focus:outline-none font-mono"
                      />
                    </div>

                    <button
                      type="submit"
                      className="w-full py-2.5 bg-[#007D5C] hover:bg-[#009B72] text-white rounded-xl text-xs font-bold transition shadow-sm"
                    >
                      Add Coding Question
                    </button>
                  </form>

                  {/* List (6 cols) */}
                  <div className="lg:col-span-6 bg-[#13232C] text-[#DCE8EA] p-4 rounded-2xl font-mono text-xs max-h-[550px] overflow-y-auto space-y-3">
                    <p className="text-emerald-400 font-bold">Published Coding Problems ({bank.coding.length}):</p>
                    {bank.coding.map((q, idx) => (
                      <div key={q.id || idx} className="pb-3 border-b border-slate-700/50 space-y-1">
                        <p className="text-white font-bold">{idx + 1}. [{q.difficulty || "Medium"}] {q.title}</p>
                        <p className="text-slate-400 text-[11px]">{q.description}</p>
                        <p className="text-emerald-300 text-[11px]">Hidden Test Cases: {q.tests?.length || 1}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* TAB 3: PDF Import */}
              {teacherTab === "pdf" && (
                <div className="space-y-4">
                  <div className="bg-[#F7FAF9] dark:bg-slate-800/40 p-6 rounded-2xl border border-slate-200 dark:border-slate-700 space-y-3">
                    <h4 className="text-sm font-bold text-slate-800 dark:text-slate-200">Upload Teacher PDF to Question Bank</h4>
                    <p className="text-xs text-slate-500 leading-relaxed max-w-2xl">
                      Upload a syllabus or test PDF containing standard teacher markers like <code className="bg-slate-200 dark:bg-slate-700 px-1 py-0.5 rounded font-mono">Question: ... A) ... Answer: B</code> and <code className="bg-slate-200 dark:bg-slate-700 px-1 py-0.5 rounded font-mono">CODING: ... Title: ... Description: ... Input: ... Output: ...</code>.
                    </p>

                    <div className="flex flex-wrap items-center gap-3 pt-2">
                      <label className="px-4 py-2.5 bg-[#007D5C] hover:bg-[#009B72] text-white rounded-xl text-xs font-bold transition cursor-pointer flex items-center gap-2 shadow-sm">
                        <i className="fas fa-file-pdf"></i>
                        <span>{pdfUploading ? "Parsing PDF..." : "Choose Teacher PDF"}</span>
                        <input type="file" accept=".pdf" onChange={handleUploadPDF} className="hidden" />
                      </label>

                      {pdfFileName && (
                        <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">
                          {pdfFileName}
                        </span>
                      )}

                      {(pdfDraft.mcq.length > 0 || pdfDraft.coding.length > 0) && (
                        <button
                          onClick={handlePublishPDFDraft}
                          className="px-4 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-xs font-bold transition flex items-center gap-2 shadow-sm ml-auto"
                        >
                          <i className="fas fa-upload"></i>
                          <span>Publish Draft ({pdfDraft.mcq.length} MCQs, {pdfDraft.coding.length} Coding)</span>
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Preview Text */}
                  {pdfPreviewText && (
                    <div className="bg-[#13232C] text-[#DCE8EA] p-4 rounded-2xl font-mono text-xs max-h-72 overflow-y-auto whitespace-pre-wrap leading-relaxed">
                      {pdfPreviewText}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Confirmation Modals */}
          <ConfirmationDialog
            open={confirmSubmitCodingOpen}
            title="Finish Coding Test"
            description={`You have answered ${Object.keys(codingAnswers).length} of ${bank.coding.length} coding questions. Are you ready to lock the coding stage and unlock the 50-MCQ Software Aptitude Test?`}
            confirmText="Finish & Unlock Aptitude"
            onConfirm={() => {
              setConfirmSubmitCodingOpen(false);
              unlockAptitude();
            }}
            onCancel={() => setConfirmSubmitCodingOpen(false)}
          />

          <ConfirmationDialog
            open={confirmSubmitAptitudeOpen}
            title="Submit Aptitude Test"
            description={`You have answered ${Object.keys(mcqAnswers).length} of ${aptitudeQuestions.length} aptitude questions. Do you want to submit and view your final score?`}
            confirmText="Confirm & Submit"
            onConfirm={handleFinishAptitude}
            onCancel={() => setConfirmSubmitAptitudeOpen(false)}
          />
        </div>
      );
    }

    // --- Attendance View ---
const ATT_CSS = `
.att-root{
  --att-card:#fffdf8; --att-soft:#f7f3ea; --att-line:#e7dfd0; --att-ink:#24312a; --att-muted:#77807a;
  --att-green:#2c6a54; --att-green-deep:#235643; --att-green-pale:#e6f0ea; --att-on:#ffffff;
  --att-present:#2f8f6b; --att-late:#e9a93b; --att-absent:#ea5a72;
  --att-shadow:0 1px 2px rgba(58,48,33,.05),0 8px 22px rgba(58,48,33,.06);
  display:flex; flex-direction:column; gap:18px; color:var(--att-ink);
}
html.dark .att-root{
  --att-card:#2a342e; --att-soft:#303a34; --att-line:#3f4c44; --att-ink:#f2eee5; --att-muted:#b5bdb5;
  --att-green:#6fae92; --att-green-deep:#5c9a80; --att-green-pale:#30453a; --att-on:#14231c;
  --att-shadow:0 8px 22px rgba(0,0,0,.28);
}
.att-root *{box-sizing:border-box}
.att-card{background:var(--att-card); border:1px solid var(--att-line); border-radius:16px; box-shadow:var(--att-shadow)}
.att-busy{opacity:.65; transition:opacity .2s}

/* header */
.att-head{display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:16px}
.att-title-wrap{display:flex; align-items:center; gap:14px}
.att-title-icon{width:52px; height:52px; border-radius:14px; background:var(--att-green-pale); color:var(--att-green);
  display:flex; align-items:center; justify-content:center; font-size:20px; border:1px solid var(--att-line); flex:none}
.att-root h2.att-title{font-size:28px !important; line-height:1.1 !important; margin:0; font-weight:800 !important; letter-spacing:-.03em !important}
.att-sub{display:block; margin-top:3px; font-size:13px; color:var(--att-muted)}
.att-controls{display:flex; flex-wrap:wrap; gap:10px; align-items:center}

.att-btn{height:40px; padding:0 14px; border-radius:11px; border:1px solid var(--att-line); background:var(--att-card);
  color:var(--att-ink); font-size:13px; font-weight:600; display:inline-flex; align-items:center; gap:8px; cursor:pointer; white-space:nowrap}
.att-btn:hover{background:var(--att-soft)}
.att-btn.is-active{border-color:var(--att-green); background:var(--att-green-pale)}
.att-btn.sm{height:34px; padding:0 12px; font-size:12px; border-radius:10px}
.att-btn.att-primary{background:var(--att-green); border-color:var(--att-green); color:var(--att-on)}
.att-btn.att-primary:hover{background:var(--att-green-deep)}
.att-btn:disabled{opacity:.5; cursor:not-allowed}
.att-caret{font-size:10px; opacity:.6}

.att-select{position:relative}
.att-menu{position:absolute; top:calc(100% + 6px); left:0; min-width:100%; z-index:30; background:var(--att-card);
  border:1px solid var(--att-line); border-radius:12px; box-shadow:0 16px 34px rgba(58,48,33,.18); padding:6px; max-height:290px; overflow:auto}
.att-menu.right{left:auto; right:0}
.att-menu button{display:flex; width:100%; align-items:center; justify-content:space-between; gap:14px; padding:8px 10px; border:0;
  background:transparent; border-radius:8px; font-size:13px; color:var(--att-ink); cursor:pointer; text-align:left; white-space:nowrap}
.att-menu button:hover,.att-menu button[aria-selected=true]{background:var(--att-green-pale)}
.att-menu i{color:var(--att-green); font-size:11px}

/* stat cards */
.att-stats{display:grid; grid-template-columns:1.35fr 1fr 1fr 1fr 1.2fr; gap:14px}
.att-stat{padding:16px 18px; display:flex; align-items:center; gap:14px; min-height:98px; text-align:left; color:inherit; font:inherit}
.att-stat-icon{width:44px; height:44px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:18px; flex:none}
.att-stat-label{display:block; font-size:13px; font-weight:700}
.att-stat-num{display:block; font-size:26px; font-weight:800; line-height:1.15; letter-spacing:-.02em}
.att-stat-sub{display:block; font-size:12px; color:var(--att-muted)}
.att-stat.absent{background:#fdf1f2}
.att-stat.risk{background:var(--att-green-pale); cursor:pointer; width:100%}
html.dark .att-stat.absent{background:rgba(234,90,114,.12)}
.att-delta{font-size:12px; font-weight:600}
.att-delta.up{color:var(--att-present)} .att-delta.down{color:var(--att-absent)}
.att-donut{position:relative; flex:none}
.att-donut-center{position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center}
.att-donut-num{font-size:22px; font-weight:800; letter-spacing:-.02em; line-height:1}
.att-donut-cap{font-size:11px; color:var(--att-muted); margin-top:3px}

/* trend row */
.att-trend{display:grid; grid-template-columns:minmax(0,1.9fr) minmax(0,1.05fr) minmax(0,.95fr)}
.att-trend > div{padding:18px 20px}
.att-trend > div + div{border-left:1px solid var(--att-line)}
.att-root h3.att-h3{font-size:15px !important; font-weight:800 !important; margin:0 0 10px; letter-spacing:-.01em !important}
.att-chart{position:relative; width:100%}
.att-chart svg{display:block; width:100%; height:auto; overflow:visible}
.att-axis{fill:var(--att-muted); font-size:11px}
.att-grid{stroke:var(--att-line); stroke-width:1}
.att-tip{position:absolute; transform:translate(-50%,-135%); background:var(--att-ink); color:var(--att-card); font-size:11px;
  font-weight:600; padding:4px 8px; border-radius:8px; pointer-events:none; white-space:nowrap}
.att-legend-list{display:flex; flex-direction:column; gap:12px; font-size:13px; flex:1}
.att-legend-row{display:flex; align-items:center; gap:9px}
.att-legend-row b{margin-left:auto; font-weight:700}
.att-legend-row span.n{color:var(--att-muted); min-width:64px; text-align:right}
.att-dot{width:10px; height:10px; border-radius:50%; flex:none; display:inline-block}
.att-insight{display:flex; flex-direction:column; justify-content:center; gap:10px; background:var(--att-green-pale);
  border-radius:14px; margin:14px; padding:16px !important; position:relative}
.att-insight-icon{width:34px; height:34px; border-radius:10px; background:var(--att-card); color:var(--att-green);
  display:flex; align-items:center; justify-content:center}
.att-insight-title{font-size:14px; font-weight:800; display:block}
.att-insight-text{font-size:12.5px; color:var(--att-muted); line-height:1.5; display:block; max-width:170px}
.att-plant{position:absolute; right:12px; bottom:10px}

/* table card */
.att-table-head{display:flex; flex-wrap:wrap; gap:12px; align-items:center; justify-content:space-between; padding:16px 20px}
.att-tools{display:flex; flex-wrap:wrap; gap:10px; align-items:center}
.att-search{position:relative}
.att-search i{position:absolute; left:12px; top:50%; transform:translateY(-50%); font-size:12px; color:var(--att-muted)}
.att-root .att-search input{width:230px; height:34px !important; min-height:0 !important; padding:0 12px 0 32px !important;
  font-size:12px !important; border-radius:10px !important; background:var(--att-card) !important;
  border:1px solid var(--att-line) !important; color:var(--att-ink) !important}
.att-selected{display:inline-flex; align-items:center; gap:8px; font-size:12px; font-weight:600; padding:0 6px; color:var(--att-green)}
.att-banner{display:flex; flex-wrap:wrap; gap:12px; align-items:center; justify-content:space-between; margin:0 20px 14px;
  padding:12px 14px; border-radius:12px; background:var(--att-green-pale); border:1px solid var(--att-line)}
.att-banner b{display:block; font-size:13px}
.att-banner span{font-size:12px; color:var(--att-muted)}
.att-scroll{overflow-x:auto}
.att-root table{width:100%; min-width:880px; font-size:13px}
.att-root table thead tr{background:var(--att-soft) !important}
.att-root table th{text-transform:none !important; font-size:12px !important; font-weight:700 !important; letter-spacing:0 !important;
  color:var(--att-ink) !important; text-align:left; padding:11px 12px; white-space:nowrap}
.att-root table td{padding:11px 12px; border-top:1px solid var(--att-line); vertical-align:middle}
.att-root table tbody tr:hover{background:var(--att-soft) !important}
.att-root table tbody tr.is-selected{background:var(--att-green-pale) !important}
.att-th-btn{background:none; border:0; padding:0; font:inherit; color:inherit; cursor:pointer; display:inline-flex; gap:6px; align-items:center}
.att-th-btn i{font-size:10px; opacity:.55}
.att-name{font-weight:700}
.att-roll{font-family:ui-monospace,Menlo,Consolas,monospace; font-size:11.5px; color:var(--att-muted)}
.att-pctcell{display:flex; align-items:center; gap:10px}
.att-pct{font-weight:700; min-width:38px}
.att-bar{width:150px; height:7px; border-radius:99px; background:var(--att-line); overflow:hidden; flex:none}
.att-bar i{display:block; height:100%; border-radius:99px}
.att-bar i.excellent,.att-bar i.good{background:var(--att-present)}
.att-bar i.risk{background:var(--att-late)}
.att-bar i.critical{background:var(--att-absent)}
.att-pill{display:inline-block; font-size:11.5px; font-weight:700; padding:3px 11px; border-radius:99px; white-space:nowrap}
.att-pill.excellent{color:#23795a; background:rgba(47,143,107,.15)}
.att-pill.good{color:#4d8a2f; background:rgba(140,192,99,.22)}
.att-pill.risk{color:#b0740c; background:rgba(233,169,59,.2)}
.att-pill.critical{color:#c93b57; background:rgba(234,90,114,.16)}
html.dark .att-pill.excellent{color:#7fd1ad} html.dark .att-pill.good{color:#b4de8f}
html.dark .att-pill.risk{color:#f2c56e} html.dark .att-pill.critical{color:#ff8fa3}
.att-check{width:18px; height:18px; border-radius:5px; border:1.5px solid var(--att-line); background:var(--att-card);
  display:inline-flex; align-items:center; justify-content:center; cursor:pointer; padding:0; color:var(--att-on); font-size:10px}
.att-check.on{background:var(--att-green); border-color:var(--att-green)}
.att-check:hover,.att-kebab:hover{transform:none !important}
.att-kebab{width:30px; height:30px; border-radius:8px; border:0; background:transparent; color:var(--att-muted); cursor:pointer}
.att-kebab:hover{background:var(--att-soft); color:var(--att-ink)}
.att-kebab-menu{position:fixed; z-index:70; width:190px; background:var(--att-card); border:1px solid var(--att-line);
  border-radius:12px; box-shadow:0 16px 34px rgba(58,48,33,.2); padding:6px}
.att-kebab-menu button{display:flex; align-items:center; gap:10px; width:100%; padding:8px 10px; border:0; background:transparent;
  border-radius:8px; font-size:13px; color:var(--att-ink); cursor:pointer; text-align:left}
.att-kebab-menu button:hover{background:var(--att-green-pale)}
.att-seg{display:inline-flex; border:1px solid var(--att-line); border-radius:9px; overflow:hidden}
.att-seg button{border:0; background:transparent; padding:5px 9px; font-size:11px; font-weight:700; color:var(--att-muted); cursor:pointer}
.att-seg button:hover{transform:none !important}
.att-seg button.on.Present{background:var(--att-present); color:#fff}
.att-seg button.on.Late{background:var(--att-late); color:#fff}
.att-seg button.on.Absent{background:var(--att-absent); color:#fff}
.att-foot{display:flex; flex-wrap:wrap; gap:12px; align-items:center; justify-content:space-between; padding:14px 20px; border-top:1px solid var(--att-line);
  font-size:12px; color:var(--att-muted)}
.att-key{display:flex; flex-wrap:wrap; gap:16px}
.att-key span{display:inline-flex; align-items:center; gap:6px}
.att-empty{padding:34px 20px; text-align:center; color:var(--att-muted); font-size:13px}

@media (max-width:1180px){
  .att-stats{grid-template-columns:repeat(3,1fr)}
  .att-trend{grid-template-columns:1fr}
  .att-trend > div + div{border-left:0; border-top:1px solid var(--att-line)}
  .att-insight{margin:0 14px 14px}
}
@media (max-width:700px){
  .att-stats{grid-template-columns:1fr 1fr}
  .att-root .att-search input{width:100%}
  .att-search{flex:1; min-width:150px}
}
@media (max-width:460px){ .att-stats{grid-template-columns:1fr} }
`;

// ---------- helpers ---------------------------------------------------------
const ATT_BANDS = [
  { key: "excellent", label: "Excellent", min: 90, range: "90–100%", color: "#2f8f6b" },
  { key: "good", label: "Good", min: 80, range: "80–89%", color: "#8cc063" },
  { key: "risk", label: "At Risk", min: 70, range: "70–79%", color: "#e9a93b" },
  { key: "critical", label: "Needs Attention", min: 0, range: "below 70%", color: "#ea5a72" },
];
const attBand = pct => ATT_BANDS.find(b => pct >= b.min) || ATT_BANDS[ATT_BANDS.length - 1];
const attPct = s => { const t = s.present + s.late + s.absent; return t ? (s.present / t) * 100 : 0; };
const attFmt = n => Number(n).toLocaleString("en-US");
const attDate = iso => new Date(iso + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
const ATT_STANDING = { excellent: "Excellent standing", good: "Good standing", risk: "At risk", critical: "Needs attention" };
const ATT_PERIODS = [
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "90d", label: "Last 90 days" },
];

function attDownloadCsv(rows) {
  const NL = String.fromCharCode(10);
  const esc = v => '"' + String(v).split('"').join('""') + '"';
  const head = ["Student", "Enrollment", "Attendance %", "Present", "Late", "Absent", "Status"];
  const body = rows.map(s => [s.name, s.roll, Math.round(s.pct), s.present, s.late, s.absent, s.band.label].map(esc).join(","));
  const blob = new Blob([[head.map(esc).join(",")].concat(body).join(NL)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "attendance-report.csv";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---------- small building blocks ------------------------------------------
function AttSelect({ icon, label, options, value, onChange, align = "left", ariaLabel, active, small }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return;
    const close = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const esc = e => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  return (
    <div className="att-select" ref={ref}>
      <button type="button" className={`att-btn ${small ? "sm" : ""} ${active ? "is-active" : ""}`} aria-haspopup="listbox"
              aria-expanded={open} aria-label={ariaLabel} onClick={() => setOpen(o => !o)}>
        {icon && <i className={`fas ${icon}`}></i>}
        <span>{label}</span>
        <i className="fas fa-chevron-down att-caret"></i>
      </button>
      {open && (
        <div className={`att-menu ${align === "right" ? "right" : ""}`} role="listbox">
          {options.map(o => (
            <button key={o.value} type="button" role="option" aria-selected={o.value === value}
                    onClick={() => { onChange(o.value); setOpen(false); }}>
              <span>{o.label}</span>
              {o.value === value && <i className="fas fa-check"></i>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function AttCheck({ checked, onChange, label }) {
  return (
    <button type="button" role="checkbox" aria-checked={checked} aria-label={label}
            className={`att-check ${checked ? "on" : ""}`} onClick={onChange}>
      {checked && <i className="fas fa-check"></i>}
    </button>
  );
}

function AttDonut({ segments, total, size = 96, stroke = 12, children }) {
  const r = (size - stroke) / 2;
  const C = 2 * Math.PI * r;
  const sum = total || segments.reduce((a, s) => a + s.value, 0) || 1;
  let acc = 0;
  return (
    <div className="att-donut" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ transform: "rotate(-90deg)" }} aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--att-line)" strokeWidth={stroke} />
        {segments.map((s, i) => {
          const len = (s.value / sum) * C;
          const el = (
            <circle key={i} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={s.color} strokeWidth={stroke}
                    strokeDasharray={`${len} ${C - len}`} strokeDashoffset={-acc}
                    style={{ transition: "stroke-dasharray .5s ease" }} />
          );
          acc += len;
          return el;
        })}
      </svg>
      <div className="att-donut-center">{children}</div>
    </div>
  );
}

function AttTrendChart({ points }) {
  const W = 640, H = 230, L = 44, R = 14, T = 12, B = 30;
  const [hover, setHover] = useState(null);
  const ref = useRef(null);
  if (!points.length) return <div className="att-empty">No attendance recorded in this period.</div>;

  const x = i => L + (points.length === 1 ? (W - L - R) / 2 : (i / (points.length - 1)) * (W - L - R));
  const y = v => T + (1 - v / 100) * (H - T - B);
  const pts = points.map((p, i) => ({ x: x(i), y: y(p.value) }));
  let line = `M${pts[0].x},${pts[0].y}`;
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i], cx = (a.x + b.x) / 2;
    line += ` C${cx},${a.y} ${cx},${b.y} ${b.x},${b.y}`;
  }
  const area = `${line} L${pts[pts.length - 1].x},${y(0)} L${pts[0].x},${y(0)} Z`;
  const labelIdx = Array.from(new Set([0, 0.25, 0.5, 0.75, 1].map(f => Math.round(f * (points.length - 1)))));

  const onMove = e => {
    const rect = ref.current.getBoundingClientRect();
    const rel = ((e.clientX - rect.left) / rect.width) * W;
    const idx = Math.round(((rel - L) / (W - L - R)) * (points.length - 1));
    setHover(Math.max(0, Math.min(points.length - 1, idx)));
  };

  return (
    <div className="att-chart" ref={ref} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Attendance trend over the selected period">
        <defs>
          <linearGradient id="attArea" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#2f8f6b" stopOpacity="0.22" />
            <stop offset="100%" stopColor="#2f8f6b" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {[0, 20, 40, 60, 80, 100].map(t => (
          <g key={t}>
            <line className="att-grid" x1={L} x2={W - R} y1={y(t)} y2={y(t)} strokeDasharray={t === 0 ? "0" : "3 4"} />
            <text className="att-axis" x={L - 8} y={y(t) + 4} textAnchor="end">{t}%</text>
          </g>
        ))}
        <path d={area} fill="url(#attArea)" />
        <path d={line} fill="none" stroke="#2f8f6b" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
        {points.length <= 45 && pts.map((p, i) => <circle key={i} cx={p.x} cy={p.y} r="3" fill="#2f8f6b" />)}
        {hover !== null && (
          <g>
            <line x1={pts[hover].x} x2={pts[hover].x} y1={T} y2={y(0)} stroke="#2f8f6b" strokeOpacity=".35" strokeDasharray="3 3" />
            <circle cx={pts[hover].x} cy={pts[hover].y} r="5" fill="#fff" stroke="#2f8f6b" strokeWidth="2.5" />
          </g>
        )}
        {labelIdx.map(i => (
          <text key={i} className="att-axis" x={pts[i].x} y={H - 8} textAnchor="middle">{points[i].label}</text>
        ))}
      </svg>
      {hover !== null && (
        <div className="att-tip" style={{ left: `${(pts[hover].x / W) * 100}%`, top: `${(pts[hover].y / H) * 100}%` }}>
          {points[hover].label}: {points[hover].value}%
        </div>
      )}
    </div>
  );
}

// ---------- the page --------------------------------------------------------
function AttendanceView({ token, user }) {
  const isStaff = user.role === "teacher" || user.role === "admin";

  const [period, setPeriod] = useState("30d");
  const [classCode, setClassCode] = useState("all");
  const [semester, setSemester] = useState(3);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [sort, setSort] = useState({ key: "name", dir: "asc" });
  const [selected, setSelected] = useState(new Set());
  const [menu, setMenu] = useState(null);

  const [rollCall, setRollCall] = useState(false);
  const [marks, setMarks] = useState({});
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api.getAttendanceOverview(token, { period, classCode, semester })
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [token, period, classCode, semester]);
  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    document.addEventListener("mousedown", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [menu]);

  const students = data ? data.students : [];
  const enriched = useMemo(() => students.map(s => {
    const pct = attPct(s);
    return { ...s, pct, band: attBand(pct) };
  }), [students]);

  const totals = useMemo(() => {
    const present = enriched.reduce((a, s) => a + s.present, 0);
    const late = enriched.reduce((a, s) => a + s.late, 0);
    const absent = enriched.reduce((a, s) => a + s.absent, 0);
    const all = present + late + absent;
    return { present, late, absent, all, rate: all ? (present / all) * 100 : 0 };
  }, [enriched]);
  const share = n => (totals.all ? Math.round((n / totals.all) * 100) : 0);
  const riskCount = enriched.filter(s => s.band.key === "risk" || s.band.key === "critical").length;

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = enriched.filter(s => {
      if (q && !(s.name.toLowerCase().includes(q) || s.roll.toLowerCase().includes(q))) return false;
      if (statusFilter === "all") return true;
      if (statusFilter === "attention") return s.band.key === "risk" || s.band.key === "critical";
      return s.band.key === statusFilter;
    });
    const dir = sort.dir === "asc" ? 1 : -1;
    return list.sort((a, b) => {
      if (sort.key === "name") return a.name.localeCompare(b.name) * dir;
      return (a[sort.key] - b[sort.key]) * dir;
    });
  }, [enriched, query, statusFilter, sort]);

  const toggleSort = key => setSort(s => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: key === "name" ? "asc" : "desc" }));
  const sortIcon = key => (sort.key === key ? (sort.dir === "asc" ? "fa-arrow-up" : "fa-arrow-down") : "fa-arrows-up-down");

  const allSelected = visible.length > 0 && visible.every(s => selected.has(s.id));
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(visible.map(s => s.id)));
  const toggleOne = id => setSelected(prev => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  const classes = data ? data.classes : [];
  const rollClass = classCode === "all" ? (classes[0] ? classes[0].code : "AIML-301") : classCode;

  const startRollCall = () => {
    const m = {};
    enriched.forEach(s => { m[s.id] = s.today_status || "Present"; });
    setMarks(m);
    setRollCall(true);
  };
  const commit = async () => {
    setConfirmOpen(false);
    setSaving(true);
    const today = new Date().toISOString().split("T")[0];
    const batch = enriched.map(s => ({ student_name: s.name, class_name: rollClass, date: today, status: marks[s.id] || "Present" }));
    try {
      await api.saveAttendance(token, batch);
      toast.success("Attendance committed for " + batch.length + " students.");
      setRollCall(false);
      load();
    } catch (e) {
      toast.error(e.message);
    } finally {
      setSaving(false);
    }
  };

  const remind = (s) => { toast.success("Attendance reminder sent to " + s.name); setMenu(null); };
  const copyRoll = (s) => {
    if (navigator.clipboard) navigator.clipboard.writeText(s.roll).then(() => toast.success("Copied " + s.roll));
    setMenu(null);
  };
  const remindSelected = () => {
    toast.success("Reminder sent to " + selected.size + " students");
    setSelected(new Set());
  };

  const classOptions = [{ value: "all", label: "All Classes" }].concat(classes.map(c => ({ value: c.code, label: c.name })));
  const semOptions = [1, 2, 3, 4, 5, 6, 7, 8].map(n => ({ value: n, label: "Semester " + n }));
  const filterOptions = [
    { value: "all", label: "All statuses" },
    { value: "attention", label: "At risk and needs attention" },
    ...ATT_BANDS.map(b => ({ value: b.key, label: b.label })),
  ];
  const activeClassLabel = (classOptions.find(o => o.value === classCode) || classOptions[0]).label;
  const periodLabel = data ? attDate(data.period.start) + " – " + attDate(data.period.end) : (ATT_PERIODS.find(p => p.value === period) || {}).label;

  // ----- loading / error -----
  if (loading && !data) return (
    <div className="att-root">
      <style>{ATT_CSS}</style>
      <Skeleton className="h-14 w-full" />
      <div className="att-stats">{[1, 2, 3, 4, 5].map(i => <Skeleton key={i} className="h-24 w-full" />)}</div>
      <Skeleton className="h-60 w-full" />
      <Skeleton className="h-72 w-full" />
    </div>
  );
  if (error && !data) return <ErrorState title="Attendance unavailable" message={error} onRetry={load} />;

  const delta = data.delta || 0;
  const insight = Math.abs(delta) < 0.5
    ? { title: "Attendance is consistent!", text: "Your class attendance is holding steady compared with the earlier part of this period." }
    : delta > 0
      ? { title: "Attendance is improving!", text: "Your class attendance has improved by " + delta.toFixed(1) + "% compared with the earlier part of this period." }
      : { title: "Attendance is slipping", text: "Your class attendance has dropped by " + Math.abs(delta).toFixed(1) + "% compared with the earlier part of this period." };
  const overallBand = attBand(totals.rate);

  return (
    <div className={`att-root ${loading ? "att-busy" : ""}`}>
      <style>{ATT_CSS}</style>

      {/* Header */}
      <div className="att-head">
        <div className="att-title-wrap">
          <div className="att-title-icon"><i className="fas fa-calendar-check"></i></div>
          <div>
            <h2 className="att-title">Attendance</h2>
            <span className="att-sub">Track and manage student attendance with ease</span>
          </div>
        </div>
        <div className="att-controls">
          <AttSelect icon="fa-calendar-days" label={periodLabel} options={ATT_PERIODS} value={period} onChange={setPeriod} ariaLabel="Date range" align="right" />
          <AttSelect label={"Semester " + semester} options={semOptions} value={semester} onChange={setSemester} ariaLabel="Semester" align="right" />
          <AttSelect label={activeClassLabel} options={classOptions} value={classCode} onChange={setClassCode} ariaLabel="Class" align="right" />
          {isStaff && !rollCall && (
            <button type="button" className="att-btn" onClick={startRollCall}><i className="fas fa-clipboard-user"></i>Take Roll Call</button>
          )}
          <button type="button" className="att-btn att-primary" onClick={() => attDownloadCsv(enriched)}>
            <i className="fas fa-download"></i>Export Report
          </button>
        </div>
      </div>

      {/* Stat cards */}
      <div className="att-stats">
        <div className="att-card att-stat">
          <AttDonut size={84} stroke={10} total={100}
                    segments={[{ value: totals.rate, color: overallBand.key === "critical" ? "#ea5a72" : overallBand.key === "risk" ? "#e9a93b" : "#2f8f6b" }]}>
            <span className="att-donut-num">{Math.round(totals.rate)}%</span>
          </AttDonut>
          <div>
            <span className="att-stat-label">Overall Attendance</span>
            <span className="att-stat-sub">{ATT_STANDING[overallBand.key]}</span>
            <span className={`att-delta ${delta >= 0 ? "up" : "down"}`}>
              <i className={`fas ${delta >= 0 ? "fa-arrow-up" : "fa-arrow-down"}`}></i> {Math.abs(delta).toFixed(1)}% vs earlier
            </span>
          </div>
        </div>

        <div className="att-card att-stat">
          <div className="att-stat-icon" style={{ background: "rgba(47,143,107,.14)", color: "#2f8f6b" }}><i className="fas fa-user"></i></div>
          <div>
            <span className="att-stat-label" style={{ color: "#2f8f6b" }}>Present</span>
            <span className="att-stat-num">{attFmt(totals.present)}</span>
            <span className="att-stat-sub">{share(totals.present)}%</span>
          </div>
        </div>

        <div className="att-card att-stat">
          <div className="att-stat-icon" style={{ background: "rgba(233,169,59,.18)", color: "#d48f16" }}><i className="fas fa-clock"></i></div>
          <div>
            <span className="att-stat-label" style={{ color: "#c98510" }}>Late</span>
            <span className="att-stat-num">{attFmt(totals.late)}</span>
            <span className="att-stat-sub">{share(totals.late)}%</span>
          </div>
        </div>

        <div className="att-card att-stat absent">
          <div className="att-stat-icon" style={{ background: "rgba(234,90,114,.14)", color: "#ea5a72" }}><i className="fas fa-circle-xmark"></i></div>
          <div>
            <span className="att-stat-label" style={{ color: "#dc4a63" }}>Absent</span>
            <span className="att-stat-num">{attFmt(totals.absent)}</span>
            <span className="att-stat-sub">{share(totals.absent)}%</span>
          </div>
        </div>

        <button type="button" className="att-card att-stat risk" onClick={() => { setStatusFilter("attention"); }} aria-label="Show students at attendance risk">
          <div className="att-stat-icon" style={{ background: "var(--att-card)", color: "var(--att-green)" }}><i className="fas fa-triangle-exclamation"></i></div>
          <div style={{ flex: 1 }}>
            <span className="att-stat-label" style={{ color: "var(--att-green)" }}>Attendance Risk</span>
            <span className="att-stat-num" style={{ fontSize: 20 }}>{riskCount} {riskCount === 1 ? "Student" : "Students"}</span>
            <span className="att-stat-sub">{riskCount ? "Needs attention" : "All on track"}</span>
          </div>
          <i className="fas fa-chevron-right" style={{ color: "var(--att-muted)", fontSize: 12 }}></i>
        </button>
      </div>

      {/* Trend + breakdown + insight */}
      <div className="att-card att-trend">
        <div>
          <h3 className="att-h3">Attendance Trend</h3>
          <AttTrendChart points={data.trend} />
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
          <AttDonut size={104} stroke={13}
                    segments={[
                      { value: totals.present, color: "#2f8f6b" },
                      { value: totals.late, color: "#e9a93b" },
                      { value: totals.absent, color: "#ea5a72" },
                    ]}>
            <span className="att-donut-num" style={{ fontSize: 20 }}>{Math.round(totals.rate)}%</span>
            <span className="att-donut-cap">Overall</span>
          </AttDonut>
          <div className="att-legend-list">
            {[["Present", totals.present, "#2f8f6b"], ["Late", totals.late, "#e9a93b"], ["Absent", totals.absent, "#ea5a72"]].map(([name, n, color]) => (
              <div key={name} className="att-legend-row">
                <span className="att-dot" style={{ background: color }}></span>{name}
                <span className="n">{attFmt(n)}</span><b>({share(n)}%)</b>
              </div>
            ))}
          </div>
        </div>
        <div className="att-insight">
          <div className="att-insight-icon"><i className="fas fa-arrow-trend-up"></i></div>
          <span className="att-insight-title">{insight.title}</span>
          <span className="att-insight-text">{insight.text}</span>
          <svg className="att-plant" width="54" height="58" viewBox="0 0 54 58" fill="none" aria-hidden="true">
            <path d="M27 48 C27 38 27 30 27 22" stroke="#2f8f6b" strokeWidth="2.5" strokeLinecap="round" />
            <path d="M27 36 C18 36 12 30 12 22 C21 22 27 28 27 36Z" fill="#5fae8a" />
            <path d="M27 30 C35 30 41 24 41 16 C32 16 27 22 27 30Z" fill="#2f8f6b" />
            <rect x="17" y="47" width="20" height="9" rx="3" fill="#e2dac9" />
          </svg>
        </div>
      </div>

      {/* Student table */}
      <div className="att-card">
        <div className="att-table-head">
          <h3 className="att-h3" style={{ margin: 0 }}>Student Attendance</h3>
          <div className="att-tools">
            {selected.size > 0 && isStaff && (
              <span className="att-selected">
                {selected.size} selected
                <button type="button" className="att-btn sm" onClick={remindSelected}><i className="fas fa-bell"></i>Send reminder</button>
              </span>
            )}
            <div className="att-search">
              <i className="fas fa-magnifying-glass"></i>
              <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search student..." aria-label="Search students" />
            </div>
            <AttSelect small icon="fa-filter" label={statusFilter === "all" ? "Filter" : (filterOptions.find(o => o.value === statusFilter) || {}).label}
                       options={filterOptions} value={statusFilter} onChange={setStatusFilter} ariaLabel="Filter by status" align="right" active={statusFilter !== "all"} />
            <button type="button" className="att-btn sm" onClick={() => attDownloadCsv(selected.size ? visible.filter(s => selected.has(s.id)) : visible)}>
              <i className="fas fa-download"></i>Export
            </button>
          </div>
        </div>

        {rollCall && (
          <div className="att-banner">
            <div>
              <b>Roll call for {new Date().toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" })}</b>
              <span>Class {rollClass}. Mark each student, then commit the records.</span>
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button type="button" className="att-btn sm" onClick={() => { const m = {}; enriched.forEach(s => { m[s.id] = "Present"; }); setMarks(m); }}>Mark all present</button>
              <button type="button" className="att-btn sm" onClick={() => setRollCall(false)} disabled={saving}>Cancel</button>
              <button type="button" className="att-btn sm att-primary" onClick={() => setConfirmOpen(true)} disabled={saving}>
                {saving ? <i className="fas fa-circle-notch fa-spin"></i> : <i className="fas fa-check"></i>}Commit Records
              </button>
            </div>
          </div>
        )}

        <div className="att-scroll">
          <table>
            <thead>
              <tr>
                <th style={{ width: 44 }}><AttCheck checked={allSelected} onChange={toggleAll} label="Select all students" /></th>
                <th><button type="button" className="att-th-btn" onClick={() => toggleSort("name")}>Student Name <i className={`fas ${sortIcon("name")}`}></i></button></th>
                <th>Enrollment</th>
                <th><button type="button" className="att-th-btn" onClick={() => toggleSort("pct")}>Attendance % <i className={`fas ${sortIcon("pct")}`}></i></button></th>
                <th>Present</th>
                <th>Late</th>
                <th>Absent</th>
                {rollCall && <th>Mark today</th>}
                <th>Status</th>
                <th style={{ width: 48 }}></th>
              </tr>
            </thead>
            <tbody>
              {visible.map(s => (
                <tr key={s.id} className={selected.has(s.id) ? "is-selected" : ""}>
                  <td><AttCheck checked={selected.has(s.id)} onChange={() => toggleOne(s.id)} label={"Select " + s.name} /></td>
                  <td className="att-name">{s.name}</td>
                  <td className="att-roll">{s.roll}</td>
                  <td>
                    <div className="att-pctcell">
                      <span className="att-pct">{Math.round(s.pct)}%</span>
                      <div className="att-bar" role="img" aria-label={Math.round(s.pct) + " percent attendance"}>
                        <i className={s.band.key} style={{ width: s.pct + "%" }}></i>
                      </div>
                    </div>
                  </td>
                  <td>{s.present}</td>
                  <td>{s.late}</td>
                  <td>{s.absent}</td>
                  {rollCall && (
                    <td>
                      <div className="att-seg" role="group" aria-label={"Mark " + s.name}>
                        {["Present", "Late", "Absent"].map(st => (
                          <button key={st} type="button" className={(marks[s.id] === st ? "on " : "") + st}
                                  onClick={() => setMarks(m => ({ ...m, [s.id]: st }))}>{st}</button>
                        ))}
                      </div>
                    </td>
                  )}
                  <td><span className={`att-pill ${s.band.key}`}>{s.band.label}</span></td>
                  <td>
                    <button type="button" className="att-kebab" aria-label={"Actions for " + s.name}
                            onMouseDown={e => e.stopPropagation()}
                            onClick={e => {
                              const r = e.currentTarget.getBoundingClientRect();
                              setMenu(m => (m && m.id === s.id ? null : { id: s.id, top: r.bottom + 6, left: Math.max(8, r.right - 190) }));
                            }}>
                      <i className="fas fa-ellipsis-vertical"></i>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {visible.length === 0 && <div className="att-empty">No students match these filters. Clear the search or filter to see everyone.</div>}
        </div>

        <div className="att-foot">
          <span>Showing {visible.length} of {enriched.length} students</span>
          <div className="att-key">
            {ATT_BANDS.map(b => (
              <span key={b.key}><i className="att-dot" style={{ background: b.color }}></i>{b.label} ({b.range})</span>
            ))}
          </div>
        </div>
      </div>

      {menu && (() => {
        const s = enriched.find(x => x.id === menu.id);
        if (!s) return null;
        return (
          <div className="att-kebab-menu" style={{ top: menu.top, left: menu.left }} onMouseDown={e => e.stopPropagation()} role="menu">
            {isStaff && <button type="button" role="menuitem" onClick={() => remind(s)}><i className="fas fa-bell"></i>Send reminder</button>}
            <button type="button" role="menuitem" onClick={() => copyRoll(s)}><i className="fas fa-copy"></i>Copy enrollment no.</button>
          </div>
        );
      })()}

      <ConfirmationDialog open={confirmOpen} title="Commit attendance"
                          description={"Save today's attendance for " + enriched.length + " students in " + rollClass + "? Committing again today will update these records."}
                          confirmText="Commit records" onConfirm={commit} onCancel={() => setConfirmOpen(false)} loading={saving} />
    </div>
  );
}

// --- Active Recall & Flashcards View ---
    function FlashcardsView({ token }) {
      const catalog = [
        { id: 1, subject: "DSA", difficulty: "Easy", front: "What is a stack?", back: "A stack is a LIFO data structure where the last inserted element is removed first." },
        { id: 2, subject: "Java", difficulty: "Medium", front: "What is polymorphism in Java?", back: "Polymorphism lets one interface represent different implementations, commonly through overloading and overriding." },
        { id: 3, subject: "Python", difficulty: "Easy", front: "What is a dictionary in Python?", back: "A dictionary is a mutable mapping that stores data as key-value pairs." },
        { id: 4, subject: "C", difficulty: "Medium", front: "What is a pointer in C?", back: "A pointer is a variable that stores the memory address of another object or variable." },
        { id: 5, subject: "DBMS", difficulty: "Medium", front: "What is normalization?", back: "Normalization organizes relational data to reduce redundancy and update anomalies." },
        { id: 6, subject: "Aptitude", difficulty: "Easy", front: "What is 20% of 200?", back: "40." },
        { id: 7, subject: "C++", difficulty: "Medium", front: "What is inheritance in C++?", back: "Inheritance lets a derived class reuse and extend members of a base class." },
        { id: 8, subject: "DSA", difficulty: "Easy", front: "What is a queue?", back: "A queue is a FIFO data structure where the first inserted element is removed first." },
        { id: 9, subject: "Java", difficulty: "Easy", front: "What is the JVM?", back: "The JVM executes Java bytecode and provides the runtime environment for Java programs." },
        { id: 10, subject: "Python", difficulty: "Medium", front: "What is a Python list?", back: "A list is an ordered, mutable collection that can contain values of different types." },
        { id: 11, subject: "C", difficulty: "Easy", front: "What does malloc() do?", back: "malloc() dynamically allocates a block of memory and returns a pointer to it." },
        { id: 12, subject: "DBMS", difficulty: "Medium", front: "What is a primary key?", back: "A primary key uniquely identifies each row in a relational table." },
        { id: 13, subject: "Aptitude", difficulty: "Easy", front: "What is 15% of 300?", back: "45." },
        { id: 14, subject: "C++", difficulty: "Hard", front: "What is a virtual function?", back: "A virtual function enables runtime polymorphism through dynamic dispatch in an inheritance hierarchy." },
        { id: 15, subject: "DSA", difficulty: "Medium", front: "What is binary search?", back: "Binary search repeatedly halves a sorted search space, giving O(log n) time complexity." },
        { id: 16, subject: "Java", difficulty: "Medium", front: "What is method overriding?", back: "Overriding occurs when a subclass provides a new implementation of an inherited method." },
        { id: 17, subject: "Python", difficulty: "Medium", front: "What is a Python tuple?", back: "A tuple is an ordered, immutable collection of values." },
        { id: 18, subject: "C", difficulty: "Hard", front: "What is pointer arithmetic?", back: "Pointer arithmetic moves an address by multiples of the pointed-to type's size." },
        { id: 19, subject: "DBMS", difficulty: "Easy", front: "What is a foreign key?", back: "A foreign key references a key in another table and helps maintain referential integrity." },
        { id: 20, subject: "Aptitude", difficulty: "Medium", front: "What is the average of 10, 20 and 30?", back: "20." },
        { id: 21, subject: "C++", difficulty: "Easy", front: "What is encapsulation?", back: "Encapsulation bundles data with methods and controls direct access to an object's internal state." }
      ];

      const [cards, setCards] = useState(catalog);
      const [currentIndex, setCurrentIndex] = useState(0);
      const [flipped, setFlipped] = useState(false);
      const [loading, setLoading] = useState(true);
      const [search, setSearch] = useState("");
      const [subject, setSubject] = useState("All");
      const [difficulty, setDifficulty] = useState("All");
      const [favorites, setFavorites] = useState(() => {
        try { return JSON.parse(localStorage.getItem("edunova_flashcard_favorites") || "[]"); }
        catch (e) { return []; }
      });
      const [favoritesMode, setFavoritesMode] = useState(false);
      const [shuffleMode, setShuffleMode] = useState(false);
      const [sessionResults, setSessionResults] = useState({});
      const [sessionStarted] = useState(() => Date.now());
      const [elapsed, setElapsed] = useState(0);
      const [summaryOpen, setSummaryOpen] = useState(false);
      const [progressOpen, setProgressOpen] = useState(false);

      useEffect(() => {
        api.getFlashcards(token, 1)
          .then(data => {
            if (Array.isArray(data) && data.length) {
              const apiById = new Map(data.map(c => [Number(c.id), c]));
              setCards(catalog.map(local => {
                const remote = apiById.get(local.id);
                return remote ? { ...local, ...remote } : local;
              }));
            }
          })
          .catch(() => {})
          .finally(() => setLoading(false));
      }, [token]);

      useEffect(() => {
        const timer = setInterval(() => setElapsed(Math.floor((Date.now() - sessionStarted) / 1000)), 1000);
        return () => clearInterval(timer);
      }, [sessionStarted]);

      useEffect(() => {
        try { localStorage.setItem("edunova_flashcard_favorites", JSON.stringify(favorites)); }
        catch (e) {}
      }, [favorites]);

      const filteredCards = useMemo(() => {
        const q = search.trim().toLowerCase();
        return cards.filter(card => {
          const matchesSearch = !q || `${card.front} ${card.back} ${card.subject}`.toLowerCase().includes(q);
          const matchesSubject = subject === "All" || card.subject === subject;
          const matchesDifficulty = difficulty === "All" || card.difficulty === difficulty;
          const matchesFavorites = !favoritesMode || favorites.includes(card.id);
          return matchesSearch && matchesSubject && matchesDifficulty && matchesFavorites;
        });
      }, [cards, search, subject, difficulty, favoritesMode, favorites]);

      useEffect(() => {
        if (currentIndex >= filteredCards.length) setCurrentIndex(0);
        setFlipped(false);
      }, [search, subject, difficulty, favoritesMode, filteredCards.length]);

      const activeCard = filteredCards[currentIndex] || filteredCards[0] || cards[0];
      const knownCount = Object.values(sessionResults).filter(v => v === "known").length;
      const reviewCount = Object.values(sessionResults).filter(v => v === "review").length;
      const studiedCount = Object.keys(sessionResults).length;
      const accuracy = studiedCount ? Math.round((knownCount / studiedCount) * 100) : 0;
      const progress = filteredCards.length ? Math.round(((currentIndex + 1) / filteredCards.length) * 100) : 0;
      const mm = String(Math.floor(elapsed / 60)).padStart(2, "0");
      const ss = String(elapsed % 60).padStart(2, "0");

      const toggleFavorite = (cardId) => {
        setFavorites(prev => prev.includes(cardId) ? prev.filter(id => id !== cardId) : [...prev, cardId]);
      };

      const goTo = (index) => {
        if (!filteredCards.length) return;
        setCurrentIndex((index + filteredCards.length) % filteredCards.length);
        setFlipped(false);
      };

      const handleResult = async (remembered) => {
        if (!activeCard) return;
        setSessionResults(prev => ({ ...prev, [activeCard.id]: remembered ? "known" : "review" }));
        setFlipped(false);
        try { await api.answerFlashcard(token, activeCard.id, remembered); } catch (e) {}
        goTo(currentIndex + 1);
      };

      const shuffleCards = () => {
        setShuffleMode(true);
        setCards(prev => [...prev].sort(() => Math.random() - 0.5));
        setCurrentIndex(0);
        setFlipped(false);
        toast.success("Flashcards shuffled");
      };

      const clearFilters = () => {
        setSearch(""); setSubject("All"); setDifficulty("All"); setFavoritesMode(false);
        setCurrentIndex(0); setFlipped(false);
      };

      useEffect(() => {
        const onKey = (event) => {
          if (summaryOpen || progressOpen) return;
          if (event.code === "Space") { event.preventDefault(); setFlipped(v => !v); }
          if (event.key === "ArrowRight") goTo(currentIndex + 1);
          if (event.key === "ArrowLeft") goTo(currentIndex - 1);
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
      }, [currentIndex, filteredCards.length, summaryOpen, progressOpen]);

      const subjects = [...new Set(cards.map(c => c.subject))];
      const difficulties = ["Easy", "Medium", "Hard"];

      return (
        <div className="max-w-6xl mx-auto space-y-4">
          <section className="bg-white/95 dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-3xl shadow-sm overflow-hidden">
            <div className="px-5 py-4 flex items-center justify-between gap-4 border-b border-slate-100 dark:border-slate-800">
              <div className="flex items-center gap-3">
                <div className="w-11 h-11 rounded-2xl bg-pink-50 dark:bg-pink-950/30 flex items-center justify-center text-pink-400 text-xl"><i className="fas fa-brain"></i></div>
                <div><h2 className="text-xl font-extrabold text-[#174D3D] dark:text-slate-100">Flashcards</h2><p className="text-xs text-slate-500">Active Recall • Spaced Revision</p></div>
              </div>
              <div className="flex items-center gap-2 text-xs font-semibold text-emerald-700 dark:text-emerald-400"><i className="far fa-clock"></i>{mm}:{ss}</div>
            </div>
            <div className="p-4 grid grid-cols-1 md:grid-cols-[1.5fr_1fr_1fr_auto_auto] gap-2">
              <div className="relative"><i className="fas fa-search absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-xs"></i><input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search flashcards..." className="w-full pl-9 pr-3 py-2 text-xs" /></div>
              <select value={subject} onChange={e => setSubject(e.target.value)} className="px-3 py-2 text-xs"><option value="All">Subject: All</option>{subjects.map(s => <option key={s} value={s}>{s}</option>)}</select>
              <select value={difficulty} onChange={e => setDifficulty(e.target.value)} className="px-3 py-2 text-xs"><option value="All">Difficulty: All</option>{difficulties.map(d => <option key={d} value={d}>{d}</option>)}</select>
              <button onClick={shuffleCards} className={`px-3 py-2 rounded-xl border text-xs font-bold flex items-center justify-center gap-1.5 ${shuffleMode ? "bg-emerald-600 text-white border-emerald-600" : "bg-white text-emerald-700 border-emerald-200"}`}><i className="fas fa-shuffle"></i>Shuffle</button>
              <button onClick={() => { setFavoritesMode(v => !v); setCurrentIndex(0); setFlipped(false); }} className={`px-3 py-2 rounded-xl text-xs font-bold flex items-center justify-center gap-1.5 ${favoritesMode ? "bg-amber-500 text-white" : "bg-emerald-600 text-white"}`}><i className="fas fa-star"></i>Favorites</button>
            </div>
            {favoritesMode && <div className="mx-4 mb-3 px-3 py-2 rounded-xl bg-emerald-50 dark:bg-emerald-950/30 flex items-center justify-between text-xs"><span className="font-bold text-emerald-700 dark:text-emerald-300"><i className="fas fa-star mr-2"></i>Favorites Mode • {filteredCards.length} cards</span><button onClick={() => { setFavoritesMode(false); setCurrentIndex(0); }} className="font-bold text-emerald-700">Clear</button></div>}
          </section>

          <section className="bg-white/95 dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-3xl p-4 shadow-sm">
            <div className="flex items-center justify-between mb-2"><div className="text-xs font-extrabold text-[#174D3D] dark:text-slate-100">Card {filteredCards.length ? currentIndex + 1 : 0} of {filteredCards.length}</div><div className="flex items-center gap-4 text-[11px] text-slate-500"><span><i className="fas fa-check text-emerald-500 mr-1"></i>Known: {knownCount}</span><span><i className="fas fa-rotate text-teal-500 mr-1"></i>Review: {reviewCount}</span><span><i className="fas fa-star text-amber-400 mr-1"></i>Favorites: {favorites.length}</span></div></div>
            <div className="h-2 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden"><div className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-teal-500 transition-all" style={{width: `${progress}%`}}></div></div>
          </section>

          {activeCard ? <section className="bg-white/95 dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-3xl p-4 md:p-6 shadow-sm">
            <div className="min-h-[330px] rounded-2xl border border-slate-100 dark:border-slate-800 bg-gradient-to-br from-white to-emerald-50/40 dark:from-slate-900 dark:to-slate-800/80 p-5 md:p-8 flex flex-col">
              <div className="flex items-center justify-between gap-3"><span className="px-3 py-1 rounded-full text-[11px] font-extrabold bg-violet-50 text-violet-700 dark:bg-violet-950/40 dark:text-violet-300">{activeCard.subject}</span><div className="flex items-center gap-3"><span className={`px-3 py-1 rounded-full text-[11px] font-extrabold ${activeCard.difficulty === "Easy" ? "bg-emerald-100 text-emerald-700" : activeCard.difficulty === "Medium" ? "bg-amber-100 text-amber-700" : "bg-red-100 text-red-700"}`}>{activeCard.difficulty}</span><button onClick={() => toggleFavorite(activeCard.id)} aria-label="Toggle favorite" className={`w-9 h-9 rounded-xl border flex items-center justify-center ${favorites.includes(activeCard.id) ? "text-amber-500 bg-amber-50 border-amber-200" : "text-slate-400 bg-white border-slate-200"}`}><i className={favorites.includes(activeCard.id) ? "fas fa-star" : "far fa-star"}></i></button></div></div>
              <div className="flex-1 flex items-center justify-center text-center px-4 py-8">{!flipped ? <div><h3 className="text-2xl md:text-3xl font-extrabold text-[#174D3D] dark:text-slate-100 leading-tight">{activeCard.front}</h3><p className="mt-5 text-sm text-slate-500">Click “Show Answer” or press SPACE</p></div> : <div className="w-full max-w-2xl rounded-2xl bg-cyan-50/70 dark:bg-cyan-950/20 border border-cyan-100 dark:border-cyan-900/50 p-6"><p className="text-sm md:text-base font-semibold text-slate-700 dark:text-slate-200 leading-relaxed">{activeCard.back}</p></div>}</div>
              <div className="flex justify-center"><button onClick={() => toggleFavorite(activeCard.id)} className="px-4 py-2 rounded-xl border border-slate-200 dark:border-slate-700 text-xs font-bold text-slate-600 dark:text-slate-300"><i className={favorites.includes(activeCard.id) ? "fas fa-star text-amber-500 mr-2" : "far fa-star mr-2"}></i>{favorites.includes(activeCard.id) ? "Remove from Favorites" : "Add to Favorites"}</button></div>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mt-4">
              <button onClick={() => goTo(currentIndex - 1)} className="py-2.5 rounded-xl border border-slate-200 dark:border-slate-700 text-xs font-bold text-slate-600 dark:text-slate-300"><i className="fas fa-arrow-left mr-2"></i>Previous</button>
              <button onClick={() => setFlipped(v => !v)} className="py-2.5 rounded-xl bg-emerald-600 text-white text-xs font-bold shadow-sm"><i className="fas fa-eye mr-2"></i>{flipped ? "Hide Answer" : "Show Answer"}</button>
              <button onClick={() => handleResult(false)} className="py-2.5 rounded-xl border border-cyan-200 bg-cyan-50 text-cyan-700 text-xs font-bold"><i className="fas fa-rotate mr-2"></i>Review Again</button>
              <button onClick={() => handleResult(true)} className="py-2.5 rounded-xl bg-emerald-600 text-white text-xs font-bold"><i className="fas fa-check mr-2"></i>I Know This</button>
              <button onClick={() => goTo(currentIndex + 1)} className="py-2.5 rounded-xl border border-slate-200 dark:border-slate-700 text-xs font-bold text-slate-600 dark:text-slate-300">Next <i className="fas fa-arrow-right ml-2"></i></button>
            </div>
            <div className="flex flex-wrap justify-center gap-2 mt-4"><button onClick={() => setProgressOpen(true)} className="px-5 py-2.5 rounded-xl border border-emerald-200 bg-emerald-50 text-emerald-700 text-xs font-bold"><i className="fas fa-chart-column mr-2"></i>Study Progress</button><button onClick={() => setSummaryOpen(true)} className="px-5 py-2.5 rounded-xl border border-emerald-200 bg-emerald-50 text-emerald-700 text-xs font-bold"><i className="fas fa-chart-simple mr-2"></i>End Study Session</button></div>
          </section> : <section className="bg-white dark:bg-slate-900 border border-slate-200 rounded-3xl p-10 text-center"><i className="fas fa-filter text-3xl text-slate-300"></i><p className="mt-3 text-sm font-bold text-slate-600">No flashcards match these filters.</p><button onClick={clearFilters} className="mt-3 text-xs font-bold text-emerald-700">Clear filters</button></section>}

          <section className="bg-white/95 dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-3xl p-4 shadow-sm"><div className="flex items-center justify-between mb-3"><h3 className="text-sm font-extrabold text-[#174D3D] dark:text-slate-100">Matching Cards</h3><span className="text-[11px] text-slate-400">{filteredCards.length} results</span></div><div className="grid md:grid-cols-2 gap-2 max-h-64 overflow-y-auto pr-1">{filteredCards.map((card, idx) => <button key={card.id} onClick={() => goTo(idx)} className={`text-left p-3 rounded-2xl border ${idx === currentIndex ? "border-emerald-400 bg-emerald-50/70 dark:bg-emerald-950/20" : "border-slate-100 dark:border-slate-800 bg-white dark:bg-slate-900"}`}><div className="flex items-center justify-between gap-2"><span className="px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-[9px] font-bold text-slate-600 dark:text-slate-300">{card.subject}</span><span className="text-[9px] font-bold text-slate-400">{card.difficulty}</span></div><p className="mt-2 text-xs font-semibold text-slate-700 dark:text-slate-200 truncate">{card.front}</p></button>)}</div></section>

          {progressOpen && <div className="fixed inset-0 z-[100] bg-slate-900/45 backdrop-blur-sm flex items-center justify-center p-4" onClick={() => setProgressOpen(false)}><div className="w-full max-w-2xl bg-white dark:bg-slate-900 rounded-3xl shadow-2xl p-5" onClick={e => e.stopPropagation()}><div className="flex items-center justify-between"><div><h3 className="text-lg font-extrabold text-[#174D3D] dark:text-slate-100">Progress Overview</h3><p className="text-xs text-slate-500">Current flashcard study session</p></div><button onClick={() => setProgressOpen(false)} className="text-slate-400"><i className="fas fa-xmark"></i></button></div><div className="grid md:grid-cols-2 gap-4 mt-5"><div className="rounded-2xl bg-emerald-50 dark:bg-emerald-950/20 p-5 flex items-center gap-5"><div className="w-24 h-24 rounded-full border-[10px] border-emerald-200 border-r-emerald-600 flex items-center justify-center text-xl font-extrabold text-emerald-700">{progress}%</div><div><p className="text-xs text-slate-500">Cards Studied</p><p className="text-2xl font-extrabold text-[#174D3D] dark:text-slate-100">{studiedCount} / {cards.length}</p></div></div><div className="space-y-2"><div className="flex justify-between text-xs"><span>Known</span><b>{knownCount}</b></div><div className="flex justify-between text-xs"><span>Review Again</span><b>{reviewCount}</b></div><div className="flex justify-between text-xs"><span>Favorites</span><b>{favorites.length}</b></div><div className="flex justify-between text-xs"><span>Session accuracy</span><b>{accuracy}%</b></div></div></div><div className="mt-5"><div className="flex justify-between text-xs font-bold mb-1"><span>Deck progress</span><span>{progress}%</span></div><div className="h-3 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden"><div className="h-full bg-emerald-500 rounded-full" style={{width: `${progress}%`}}></div></div></div><button onClick={() => setProgressOpen(false)} className="w-full mt-5 py-2.5 rounded-xl bg-emerald-600 text-white text-xs font-bold">Continue Studying</button></div></div>}

          {summaryOpen && <div className="fixed inset-0 z-[100] bg-slate-900/45 backdrop-blur-sm flex items-center justify-center p-4" onClick={() => setSummaryOpen(false)}><div className="w-full max-w-md bg-white dark:bg-slate-900 rounded-3xl shadow-2xl p-6" onClick={e => e.stopPropagation()}><div className="flex items-center justify-between"><div className="flex items-center gap-3"><div className="w-10 h-10 rounded-xl bg-emerald-50 text-emerald-600 flex items-center justify-center"><i className="fas fa-chart-column"></i></div><h3 className="text-lg font-extrabold text-[#174D3D] dark:text-slate-100">Study Session Summary</h3></div><button onClick={() => setSummaryOpen(false)} className="text-slate-400"><i className="fas fa-xmark"></i></button></div><p className="text-xs text-slate-500 mt-4">Study session completed!</p><div className="mt-4 space-y-3 text-sm"><div className="flex justify-between"><span><i className="far fa-clock text-emerald-500 mr-2"></i>Time</span><b>{mm}:{ss}</b></div><div className="flex justify-between"><span><i className="fas fa-layer-group text-emerald-500 mr-2"></i>Cards studied</span><b>{studiedCount}</b></div><div className="flex justify-between"><span><i className="fas fa-book-open text-emerald-500 mr-2"></i>Total cards</span><b>{cards.length}</b></div><div className="flex justify-between"><span><i className="fas fa-check text-emerald-500 mr-2"></i>Known</span><b>{knownCount}</b></div><div className="flex justify-between"><span><i className="fas fa-rotate text-teal-500 mr-2"></i>Review Again</span><b>{reviewCount}</b></div><div className="flex justify-between"><span><i className="fas fa-star text-amber-400 mr-2"></i>Favorites</span><b>{favorites.length}</b></div></div><div className="mt-5 p-4 rounded-2xl bg-emerald-50 dark:bg-emerald-950/20 flex items-center justify-between"><span className="text-xs font-bold">Session accuracy</span><b className="text-xl text-[#174D3D] dark:text-emerald-300">{accuracy}%</b></div><button onClick={() => setSummaryOpen(false)} className="w-full mt-5 py-3 rounded-xl bg-emerald-600 text-white text-xs font-bold">OK</button></div></div>}
        </div>
      );
    }

    // --- Faculty Early Warning & Risk Analysis View ---
    function FacultyInterventionView({ token }) {
      const defaultAtRisk = [
        { name: "Maya Lin", roll: "STU-2026-017", attendance: 68.4, score: 54.0, trigger: "Attendance & Assessment Deficit" },
        { name: "Farhan Siddiqui", roll: "STU-2026-029", attendance: 71.0, score: 78.5, trigger: "Sub-75% Attendance Lockout Risk" },
      ];
      const [atRisk, setAtRisk] = useState(defaultAtRisk);
      const [loading, setLoading] = useState(true);

      useEffect(() => {
        api.getAtRiskStudents(token)
          .then(res => {
            if (res && res.at_risk_students && res.at_risk_students.length > 0) {
              const formatted = res.at_risk_students.map(s => ({
                name: s.name,
                roll: s.enrollment_no,
                attendance: s.attendance,
                score: s.score,
                trigger: s.risk_factors.join(" & ") || "Academic Compliance Alert"
              }));
              setAtRisk(formatted);
            }
          })
          .catch(() => {})
          .finally(() => setLoading(false));
      }, [token]);

      const handleNotify = (studentName) => {
        toast.success(`Academic counseling notification dispatched to ${studentName}`);
      };

      return (
        <div className="bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-3xl p-6 space-y-4 shadow-sm">
          <div className="flex items-center justify-between">
            <div>
              <h4 className="text-xs font-bold text-red-600 dark:text-red-400 flex items-center gap-2">
                <i className="fas fa-triangle-exclamation"></i>Early Warning System (Academic Risk)
              </h4>
              <p className="text-[11px] text-slate-500">Students flagged below institutional compliance thresholds (Attendance &lt; 75% or Score &lt; 60%)</p>
            </div>
            <span className="text-xs font-bold px-2.5 py-1 bg-red-100 dark:bg-red-950/40 text-red-700 dark:text-red-300 rounded-xl">{atRisk.length} Action Items</span>
          </div>

          <div className="divide-y divide-slate-100 dark:divide-slate-800">
            {atRisk.map((item, idx) => (
              <div key={idx} className="py-3 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div>
                  <p className="text-xs font-bold text-slate-800 dark:text-slate-200">{item.name} <span className="font-mono text-slate-400 text-[10px]">({item.roll})</span></p>
                  <p className="text-[11px] text-red-600 dark:text-red-400 mt-0.5 font-medium">{item.trigger}</p>
                </div>
                <div className="flex items-center gap-3">
                  <div className="text-right">
                    <p className="text-xs font-bold text-slate-700 dark:text-slate-300">{item.attendance}% Attd</p>
                    <p className="text-[10px] text-slate-400">{item.score}% Avg Score</p>
                  </div>
                  <button onClick={() => handleNotify(item.name)}
                          className="px-3 py-1.5 bg-emerald-50 hover:bg-emerald-100 dark:bg-emerald-950/40 dark:hover:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 text-xs font-bold rounded-xl focus-ring">
                    Notify Counselor
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      );
    }

    // --- Master App Container with Navigation & Header ---
    function App() {
      const [user, setUser] = useState(() => JSON.parse(localStorage.getItem("edunova_user") || "null"));
      const [token, setToken] = useState(() => localStorage.getItem("edunova_token"));
      const [activeTab, setActiveTab] = useState("dashboard");
      const [sidebarCollapsed, setSidebarCollapsed] = useState(() => localStorage.getItem("edunova_sidebar") === "collapsed");
      const [mobileNavOpen, setMobileNavOpen] = useState(false);
      const [darkMode, setDarkMode] = useState(() => localStorage.getItem("edunova_theme") === "dark");
      const [searchOpen, setSearchOpen] = useState(false);
      const [notifOpen, setNotifOpen] = useState(false);
      const [profileMenuOpen, setProfileMenuOpen] = useState(false);
      const [aiPrefill, setAiPrefill] = useState("");

      useEffect(() => {
        if (darkMode) document.documentElement.classList.add("dark");
        else document.documentElement.classList.remove("dark");
        localStorage.setItem("edunova_theme", darkMode ? "dark" : "light");
      }, [darkMode]);

      useEffect(() => {
        localStorage.setItem("edunova_sidebar", sidebarCollapsed ? "collapsed" : "expanded");
      }, [sidebarCollapsed]);

      // Command+K listener
      useEffect(() => {
        const handleKeyDown = e => {
          if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
            e.preventDefault();
            setSearchOpen(prev => !prev);
          }
        };
        window.addEventListener("keydown", handleKeyDown);
        return () => window.removeEventListener("keydown", handleKeyDown);
      }, []);

      const searchIndex = useMemo(() => [
        { label: "Dashboard Overview", detail: "Return to analytics & benchmarks", category: "Navigation", icon: "fa-chart-pie", tab: "dashboard" },
        { label: "Study Materials", detail: "Browse certified course PDFs", category: "Navigation", icon: "fa-book", tab: "materials" },
        { label: "Learning Companion", detail: "Learn with a friendly academic companion", category: "Navigation", icon: "fa-graduation-cap", tab: "ai-tutor" },
        { label: "Active Recall Flashcards", detail: "Spaced repetition practice deck", category: "Navigation", icon: "fa-layer-group", tab: "flashcards" },
        { label: "Assessments & Tests", detail: "Launch self-guided examinations", category: "Navigation", icon: "fa-clipboard-check", tab: "tests" },
        { label: "Attendance Roll Call", detail: "Examine institutional presence records", category: "Navigation", icon: "fa-calendar-check", tab: "attendance" },
        { label: "Edit Student Profile", detail: "Manage personal & academic profile", category: "Account", icon: "fa-user-pen", tab: "profile" },
      ], []);

      const handleLogout = () => {
        localStorage.clear();
        setUser(null);
        setToken(null);
        setActiveTab("dashboard");
        toast.warning("Session closed");
      };

      // 1. Unauthenticated -> Login Screen
      if (!user || !token) {
        return (
          <>
            <ToastContainer />
            <ModernLoginScreen onLoginSuccess={res => {
              setUser(res);
              setToken(res.access_token);
              if (res.role === "student" && !res.is_profile_complete) {
                setActiveTab("profile");
              } else if (res.role === "admin") {
                setActiveTab("dashboard");
              } else {
                setActiveTab("dashboard");
              }
            }} />
          </>
        );
      }

      // 2. Teacher Pending Approval -> Status Screen
      if (user.role === "teacher" && (user.approval_status === "pending" || user.approval_status === "rejected")) {
        return (
          <>
            <ToastContainer />
            <TeacherPendingStatusView user={user} token={token} onLogout={handleLogout} />
          </>
        );
      }

      // 3. Mandatory Student Onboarding Check
      // If student profile is incomplete, force them to complete profile before opening dashboard
      const isStudentIncomplete = (user.role === "student" && !user.is_profile_complete);

      // Dynamic navigation items based on verified role
      const navItems = user.role === "admin" ? [
        { id: "dashboard", icon: "fa-shield-halved", label: "Admin Oversight" },
        { id: "materials", icon: "fa-book-open", label: "Curriculum Repository" },
        { id: "attendance", icon: "fa-calendar-check", label: "Attendance Records" },
        { id: "tests", icon: "fa-clipboard-check", label: "Test Bank & Runner" },
      ] : user.role === "teacher" ? [
        { id: "dashboard", icon: "fa-chart-pie", label: "Faculty Dashboard" },
        { id: "materials", icon: "fa-book-open", label: "Course Notes Authoring" },
        { id: "tests", icon: "fa-clipboard-check", label: "Evaluations & Coding" },
        { id: "attendance", icon: "fa-calendar-check", label: "Roll Call" },
        { id: "intervention", icon: "fa-triangle-exclamation", label: "Early Warning" },
      ] : [
        { id: "dashboard", icon: "fa-chart-pie", label: "Dashboard" },
        { id: "materials", icon: "fa-book-open", label: `Study Hub (Sem ${user.semester || 3})` },
        { id: "ai-tutor", icon: "fa-graduation-cap", label: "Learning Companion" },
        { id: "flashcards", icon: "fa-layer-group", label: "Flashcards" },
        { id: "tests", icon: "fa-clipboard-check", label: "Evaluations" },
        { id: "attendance", icon: "fa-calendar-check", label: "Attendance" },
        { id: "profile", icon: "fa-user-pen", label: "Edit Profile" },
      ];

      return (
        <div className="edunova-app min-h-screen flex flex-col bg-[var(--bg-page)] text-[var(--text-primary)]">
          <ToastContainer />
          <GlobalSearchDialog open={searchOpen} onClose={() => setSearchOpen(false)} dataIndex={searchIndex} onSelect={item => setActiveTab(item.tab)} />

          <div className="flex flex-1">
            {/* Desktop Sidebar */}
            <aside className={`hidden lg:flex flex-col justify-between border-r border-emerald-100 dark:border-slate-800 bg-white/90 dark:bg-slate-900 transition-all duration-300 p-4 ${sidebarCollapsed ? "w-20" : "w-64"}`}>
              <div className="space-y-6">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3 overflow-hidden">
                    <div className="w-9 h-9 rounded-2xl bg-gradient-to-tr from-[#159B73] to-[#1AC492] text-white flex items-center justify-center shadow-md">
                      <i className="fas fa-graduation-cap text-base"></i>
                    </div>
                    {!sidebarCollapsed && (
                      <div>
                        <h1 className="text-sm font-bold tracking-tight">EduNova</h1>
                        <p className="text-[10px] text-emerald-600 font-bold uppercase tracking-wider">Human Reality • v3.2</p>
                      </div>
                    )}
                  </div>
                  <button onClick={() => setSidebarCollapsed(!sidebarCollapsed)} aria-label="Toggle Navigation Width"
                          className="text-slate-400 hover:text-slate-600 text-xs p-1 focus-ring rounded">
                    <i className={`fas ${sidebarCollapsed ? "fa-angles-right" : "fa-angles-left"}`}></i>
                  </button>
                </div>

                <nav className="space-y-1">
                  {navItems.map(item => {
                    const disabled = isStudentIncomplete && item.id !== "profile";
                    const btn = (
                      <button
                        key={item.id}
                        disabled={disabled}
                        onClick={() => setActiveTab(item.id)}
                        className={`w-full flex items-center gap-3.5 px-3 py-2.5 rounded-xl text-xs font-semibold transition focus-ring ${
                          disabled ? "opacity-40 cursor-not-allowed text-slate-400" :
                          activeTab === item.id ? "bg-[#356f5b] text-white shadow-md shadow-emerald-600/15" :
                          "text-slate-600 dark:text-slate-400 hover:bg-emerald-50/70 dark:hover:bg-slate-800"
                        }`}
                      >
                        <i className={`fas ${item.icon} text-sm shrink-0 w-5 text-center`}></i>
                        {!sidebarCollapsed && <span>{item.label}</span>}
                      </button>
                    );
                    return sidebarCollapsed ? <Tooltip key={item.id} text={item.label}>{btn}</Tooltip> : btn;
                  })}
                </nav>
              </div>

              {/* Sidebar Footer User Card */}
              <div className="pt-4 border-t border-slate-100 dark:border-slate-800 flex items-center justify-between">
                <div className="flex items-center gap-2.5 overflow-hidden">
                  <div className="w-8 h-8 rounded-full bg-emerald-100 dark:bg-emerald-950 text-emerald-800 dark:text-emerald-300 flex items-center justify-center font-bold text-xs shrink-0">
                    {user.full_name ? user.full_name.charAt(0) : "U"}
                  </div>
                  {!sidebarCollapsed && (
                    <div className="overflow-hidden">
                      <p className="text-xs font-bold truncate">{user.full_name}</p>
                      <p className="text-[10px] text-emerald-700 dark:text-emerald-400 font-semibold uppercase">
                        {user.role === "admin" ? "Admin" : user.role === "teacher" ? `Faculty (${user.teacher_id_code || 'Verified'})` : `Student (Sem ${user.semester || 3})`}
                      </p>
                    </div>
                  )}
                </div>
                <button onClick={handleLogout} aria-label="Sign out" className="text-slate-400 hover:text-red-600 text-xs p-1.5 focus-ring rounded">
                  <i className="fas fa-arrow-right-from-bracket"></i>
                </button>
              </div>
            </aside>

            {/* Main Application Area */}
            <div className="flex-1 flex flex-col min-w-0">
              {/* Header */}
              <header className="edunova-header px-6 py-3.5 border-b border-emerald-100/60 dark:border-slate-800 flex items-center justify-between sticky top-0 z-30 bg-white/90 dark:bg-slate-900/90 backdrop-blur-md">
                <div className="flex items-center gap-3">
                  <button onClick={() => setMobileNavOpen(true)} className="lg:hidden text-slate-600 dark:text-slate-300 p-1.5">
                    <i className="fas fa-bars text-sm"></i>
                  </button>
                  <button onClick={() => setSearchOpen(true)} className="flex items-center gap-2 px-3 py-1.5 bg-slate-100 dark:bg-slate-800 rounded-xl text-xs text-slate-400 hover:bg-slate-200 transition">
                    <i className="fas fa-magnifying-glass text-slate-400"></i>
                    <span className="hidden sm:inline">Search platform...</span>
                    <kbd className="px-1.5 py-0.5 rounded bg-white dark:bg-slate-700 text-[10px] font-mono shadow-xs">Ctrl+K</kbd>
                  </button>
                </div>

                <div className="flex items-center gap-3">
                  {/* Role Badge in Header */}
                  <span className={`hidden sm:inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-[11px] font-bold ${
                    user.role === "admin" ? "bg-slate-900 text-white" :
                    user.role === "teacher" ? "bg-teal-50 text-teal-800 border border-teal-200" :
                    "bg-emerald-50 text-emerald-800 border border-emerald-200"
                  }`}>
                    <i className={`fas ${user.role === 'admin' ? 'fa-shield-halved' : user.role === 'teacher' ? 'fa-chalkboard-user' : 'fa-graduation-cap'}`}></i>
                    {user.role === "admin" ? "Platform Administrator" :
                     user.role === "teacher" ? `Faculty: ${user.teacher_id_code || 'Verified'}` :
                     `Student: Sem ${user.semester || 3} (${user.branch ? user.branch.split(' ')[0] : 'CSE'})`}
                  </span>

                  <button onClick={() => setDarkMode(!darkMode)} className="w-8 h-8 rounded-xl bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 flex items-center justify-center text-xs">
                    <i className={`fas ${darkMode ? "fa-sun" : "fa-moon"}`}></i>
                  </button>

                  <div className="relative">
                    <button onClick={() => setNotifOpen(!notifOpen)} className="w-8 h-8 rounded-xl bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 flex items-center justify-center text-xs relative">
                      <i className="fas fa-bell"></i>
                      <span className="absolute top-1 right-1 w-2 h-2 rounded-full bg-emerald-500"></span>
                    </button>
                    <NotificationPanel token={token} open={notifOpen} onClose={() => setNotifOpen(false)} />
                  </div>

                  {/* Profile Dropdown */}
                  <div className="relative">
                    <button onClick={() => setProfileMenuOpen(!profileMenuOpen)} className="flex items-center gap-2 p-1 rounded-xl hover:bg-slate-100 dark:hover:bg-slate-800 transition">
                      <div className="w-7 h-7 rounded-full bg-emerald-600 text-white flex items-center justify-center text-xs font-bold">
                        {user.full_name ? user.full_name.charAt(0) : "U"}
                      </div>
                      <i className="fas fa-chevron-down text-[10px] text-slate-400"></i>
                    </button>

                    {profileMenuOpen && (
                      <div className="absolute right-0 top-full mt-2 w-52 bg-white dark:bg-slate-900 border border-emerald-100 dark:border-slate-800 rounded-3xl shadow-xl p-2 z-50 space-y-1">
                        <div className="p-2 border-b border-slate-100 dark:border-slate-800">
                          <p className="text-xs font-bold text-slate-900 dark:text-slate-100">{user.full_name}</p>
                          <p className="text-[10px] text-slate-400 truncate">{user.email}</p>
                        </div>
                        {user.role === "student" && (
                          <button onClick={() => { setActiveTab("profile"); setProfileMenuOpen(false); }}
                                  className="w-full text-left px-2.5 py-1.5 text-xs text-slate-600 dark:text-slate-300 hover:bg-emerald-50 rounded-lg flex items-center gap-2">
                            <i className="fas fa-user-pen text-emerald-600"></i>Edit Profile
                          </button>
                        )}
                        <button onClick={handleLogout}
                                className="w-full text-left px-2.5 py-1.5 text-xs text-red-600 hover:bg-red-50 rounded-lg flex items-center gap-2">
                          <i className="fas fa-power-off"></i>Sign Out
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              </header>

              {/* View Workspace */}
              <main className="edunova-workspace flex-1 p-6 md:p-8 max-w-6xl w-full mx-auto" role="main">
                {/* Incomplete profile mandatory setup enforcement */}
                {isStudentIncomplete ? (
                  <ProfileSetupView
                    token={token}
                    user={user}
                    onProfileUpdated={updated => {
                      setUser(updated);
                      localStorage.setItem("edunova_user", JSON.stringify(updated));
                    }}
                    onNavigateDashboard={() => setActiveTab("dashboard")}
                  />
                ) : user.role === "admin" ? (
                  activeTab === "dashboard" ? <AdminDashboardView token={token} user={user} /> :
                  activeTab === "materials" ? <MaterialsView token={token} user={user} onAskAI={t => { setAiPrefill(`Explain concepts in: ${t}`); setActiveTab("ai-tutor"); }} /> :
                  activeTab === "attendance" ? <AttendanceView token={token} user={user} /> :
                  activeTab === "tests" ? <TestsView token={token} user={user} /> :
                  <AdminDashboardView token={token} user={user} />
                ) : (
                  <>
                    {activeTab === "dashboard" && <DashboardView token={token} user={user} onNavigate={setActiveTab} />}
                    {activeTab === "materials" && <MaterialsView token={token} user={user} onAskAI={t => { setAiPrefill(`Explain core exam concepts in: ${t}`); setActiveTab("ai-tutor"); }} />}
                    {activeTab === "ai-tutor" && <AITutorView token={token} initialQuery={aiPrefill} />}
                    {activeTab === "flashcards" && <FlashcardsView token={token} />}
                    {activeTab === "tests" && <TestsView token={token} user={user} />}
                    {activeTab === "attendance" && <AttendanceView token={token} user={user} />}
                    {activeTab === "intervention" && <FacultyInterventionView token={token} />}
                    {activeTab === "profile" && (
                      <ProfileSetupView
                        token={token}
                        user={user}
                        onProfileUpdated={updated => {
                          setUser(updated);
                          localStorage.setItem("edunova_user", JSON.stringify(updated));
                        }}
                        onNavigateDashboard={() => setActiveTab("dashboard")}
                      />
                    )}
                  </>
                )}
              </main>
            </div>
          </div>
        </div>
      );
    }

    ReactDOM.createRoot(document.getElementById("root")).render(<App />);
  </script>

  <script>
    (() => {
      const scene = document.getElementById("edunova-scenery");
      if (!scene) return;
      let raf = null;
      let tx = 0, ty = 0, cx = 0, cy = 0;

      const update = () => {
        cx += (tx - cx) * 0.08;
        cy += (ty - cy) * 0.08;
        document.querySelectorAll("#edunova-scenery [data-depth]").forEach(el => {
          const depth = parseFloat(el.dataset.depth || "0.2");
          const x = cx * depth;
          const y = cy * depth;
          el.style.transform = `translate3d(${x}px, ${y}px, 0)`;
        });
        raf = requestAnimationFrame(update);
      };

      window.addEventListener("pointermove", (event) => {
        const x = (event.clientX / window.innerWidth - 0.5) * 26;
        const y = (event.clientY / window.innerHeight - 0.5) * 20;
        tx = x;
        ty = y;
        if (!raf) raf = requestAnimationFrame(update);
      }, { passive: true });

      window.addEventListener("mouseleave", () => {
        tx = 0;
        ty = 0;
      });
    })();
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
@app.get("/student/dashboard", response_class=HTMLResponse)
@app.get("/teacher/dashboard", response_class=HTMLResponse)
def render_edunova_portal():
    return HTMLResponse(content=FRONTEND_HTML)

# ==============================================================================
# 9. SERVER ENTRYPOINT
# ==============================================================================

if __name__ == "__main__":
    print("=" * 65)
    print("🌿 EDUNOVA HUMAN REALITY SMART EDUCATION PLATFORM v3.2")
    print("👉 Live Web: http://127.0.0.1:8000")
    print("👉 Swagger:  http://127.0.0.1:8000/docs")
    print("🔑 Default login: student@university.edu / 12345")
    print("=" * 65)
    import webbrowser
    threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:8000")).start()
    uvicorn.run(app, host="127.0.0.1", port=8000)
    