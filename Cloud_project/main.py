from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc
from sqlalchemy.orm import Session

from config import settings
from database import SessionLocal, get_db, init_db
from models import (
    CreateSubjectRequest,
    Document,
    DocumentResponse,
    GenerateMindmapRequest,
    GenerateQuizRequest,
    GenerateTestRequest,
    GradeMockExamRequest,
    Mindmap,
    MockExam,
    Quiz,
    SubmitResultRequest,
    TestResult,
    User,
)
from services.ai_service import ai_service
from services.queue_service import (
    batch_generate_mock_exam_job,
    batch_generate_quiz_job,
    process_document_ingestion_job,
    queue_service,
)
from services.storage_service import storage_service

SAMPLE_SYLLABUS_TEXT = """# CS-402: Distributed Cloud Systems & AI Architecture

## Module 1: Cloud-Native Compute & Container Orchestration
Modern cloud applications decouple stateless web services from stateful backing stores.
Containers package application code, runtime dependencies, and system libraries into immutable artifacts.
Horizontal Pod Autoscaling adjusts replica counts dynamically based on CPU, memory, or custom queue depth metrics.

## Module 2: Distributed Storage & Object Stores (S3 / Cloudflare R2)
Object storage systems manage unstructured blobs (PDFs, media, backups) using flat namespaces and HTTP REST APIs.
Unlike POSIX block filesystems, S3-compatible stores provide high durability, eventual or strong read-after-write consistency, and presigned URLs for zero-trust client access.
Cloudflare R2 eliminates egress bandwidth fees while maintaining AWS S3 API compatibility.

## Module 3: Relational Persistence & Connection Pooling (PostgreSQL)
ACID transactions guarantee Atomicity, Consistency, Isolation, and Durability.
Multi-Version Concurrency Control (MVCC) allows readers and writers to operate without blocking each other.
Connection poolers (such as SQLAlchemy QueuePool or PgBouncer) prevent connection exhaustion under high concurrency.

## Module 4: Asynchronous Task Queues (Redis & RQ / Celery)
Long-running operations—such as parsing multi-megabyte PDF textbooks or running multi-step LLM reasoning pipelines—should be offloaded from synchronous HTTP request threads.
Producer-consumer queues backed by Redis persist job payloads in memory and distribute work across background worker processes with retry policies and timeouts.

## Module 5: Structured LLM Pipelines (Google Gemini API)
Production AI applications enforce deterministic output schemas using Pydantic models and `response_mime_type="application/json"`.
Controlled temperature settings, context truncation, and rubric-grounded evaluation reduce hallucination during automated assessment generation and grading.
"""


def seed_default_data() -> None:
    """Ensure a default learner profile and sample syllabus exist for immediate exploration."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == 1).first()
        if not user:
            user = User(id=1, email="student@studyflow.ai", full_name="Alex Rivera")
            db.add(user)
            db.commit()
            db.refresh(user)

        doc_count = db.query(Document).count()
        if doc_count == 0:
            s3_key, backend = storage_service.upload_file(
                SAMPLE_SYLLABUS_TEXT.encode("utf-8"),
                "CS402_Cloud_AI_Syllabus.md",
                "text/markdown",
            )
            metadata = ai_service._extract_fallback_topics(
                "CS-402: Distributed Cloud Systems & AI Architecture", SAMPLE_SYLLABUS_TEXT
            )
            sample_doc = Document(
                user_id=user.id,
                title="CS-402: Distributed Cloud Systems & AI Architecture",
                filename="CS402_Cloud_AI_Syllabus.md",
                content_type="text/markdown",
                file_size_bytes=len(SAMPLE_SYLLABUS_TEXT.encode("utf-8")),
                s3_key=s3_key,
                storage_backend=backend,
                extracted_text=SAMPLE_SYLLABUS_TEXT,
                summary_metadata={
                    "course_or_subject": "Distributed Cloud Systems & AI",
                    "executive_summary": (
                        "Covers cloud-native compute, S3/R2 object storage, PostgreSQL MVCC, "
                        "Redis asynchronous task queues, and structured Gemini LLM pipelines."
                    ),
                    "key_topics": metadata,
                    "estimated_study_hours": 6.5,
                    "difficulty_level": "Intermediate",
                },
            )
            db.add(sample_doc)
            db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_default_data()
    yield


app = FastAPI(
    title="StudyFlow AI API",
    description="Self-paced AI study companion with S3/R2 document ingestion, Gemini mock exams, interactive quizzes, and Mermaid mindmaps.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# =====================================================================
# Frontend Entrypoint & Cloud Health Check
# =====================================================================

@app.get("/", include_in_schema=False)
async def serve_dashboard():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "StudyFlow AI API is running. Visit /docs for OpenAPI documentation."}


@app.get("/health", tags=["System"])
def health_check(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Reports live connectivity across all 5 cloud services."""
    db_ok = True
    try:
        db.query(User).first()
    except Exception:
        db_ok = False

    return {
        "status": "healthy" if db_ok else "degraded",
        "services": {
            "render_web_service": "online",
            "postgresql_database": "connected" if db_ok else "error",
            "object_storage": "cloudflare_r2" if storage_service.is_cloud_storage_active else "local_fallback",
            "google_gemini_api": "live" if ai_service.is_live else "fallback_simulator",
            "redis_worker_queue": "connected" if queue_service.is_redis_connected else "sync_fallback",
        },
        "models": {
            "default": settings.GEMINI_MODEL,
            "pro": settings.GEMINI_PRO_MODEL,
        },
    }


# =====================================================================
# 1. Document & Syllabus Ingestion (/upload, /documents)
# =====================================================================

@app.post("/upload", response_model=DocumentResponse, tags=["Documents"])
async def upload_document(
    file: UploadFile = File(...),
    title: Optional[str] = Form(default=None),
    async_processing: bool = Form(default=False),
    db: Session = Depends(get_db),
):
    """
    Uploads a syllabus, lecture notes, PDF, or text file to S3/Cloudflare R2,
    extracts text content, and runs Gemini syllabus topic analysis.
    """
    raw_bytes = await file.read()
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(raw_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(raw_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum allowed size of {settings.MAX_UPLOAD_SIZE_MB} MB.",
        )

    filename = file.filename or "untitled_notes.txt"
    content_type = file.content_type or "text/plain"
    doc_title = (title or Path(filename).stem.replace("_", " ").replace("-", " ")).strip()

    try:
        extracted_text = storage_service.extract_text_from_bytes(raw_bytes, filename, content_type)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to parse document text: {exc}") from exc

    s3_key, storage_backend = storage_service.upload_file(raw_bytes, filename, content_type)

    doc = Document(
        user_id=1,
        title=doc_title,
        filename=filename,
        content_type=content_type,
        file_size_bytes=len(raw_bytes),
        s3_key=s3_key,
        storage_backend=storage_backend,
        extracted_text=extracted_text,
        summary_metadata=None,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    if async_processing and queue_service.is_redis_connected:
        queue_service.enqueue_or_run(process_document_ingestion_job, doc.id)
    else:
        doc.summary_metadata = ai_service.extract_document_metadata(doc.title, doc.extracted_text)
        db.commit()
        db.refresh(doc)

    return DocumentResponse(
        id=doc.id,
        title=doc.title,
        filename=doc.filename,
        content_type=doc.content_type,
        file_size_bytes=doc.file_size_bytes,
        s3_key=doc.s3_key,
        storage_backend=doc.storage_backend,
        summary_metadata=doc.summary_metadata,
        text_preview=doc.extracted_text[:600],
        created_at=doc.created_at,
    )


@app.get("/documents", response_model=List[DocumentResponse], tags=["Documents"])
def list_documents(db: Session = Depends(get_db)):
    docs = db.query(Document).order_by(desc(Document.created_at)).all()
    return [
        DocumentResponse(
            id=d.id,
            title=d.title,
            filename=d.filename,
            content_type=d.content_type,
            file_size_bytes=d.file_size_bytes,
            s3_key=d.s3_key,
            storage_backend=d.storage_backend,
            summary_metadata=d.summary_metadata,
            text_preview=(d.extracted_text or "")[:600],
            created_at=d.created_at,
        )
        for d in docs
    ]


@app.delete("/documents/{document_id}", tags=["Documents"])
def delete_document(document_id: int, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    db.delete(doc)
    db.commit()
    return {"status": "deleted", "document_id": document_id}


@app.post("/seed-subject", response_model=DocumentResponse, tags=["Documents"])
def create_or_replace_subject(req: CreateSubjectRequest, db: Session = Depends(get_db)):
    """
    Replaces the default pre-seeded syllabus (or creates a new subject syllabus) in real time
    from the user's preferred subject input on the website.
    """
    subject_title = req.subject.strip()
    syllabus_text = ai_service.generate_subject_syllabus(subject_title, req.custom_notes)
    safe_slug = "".join(c if c.isalnum() else "_" for c in subject_title)[:40].strip("_") or "subject"
    filename = f"{safe_slug}_Syllabus.md"

    s3_key, storage_backend = storage_service.upload_file(
        syllabus_text.encode("utf-8"),
        filename,
        "text/markdown",
    )
    metadata = ai_service.extract_document_metadata(subject_title, syllabus_text)

    target_doc = None
    if req.replace_existing_demo:
        # Find the initial seeded document (id=1 or oldest markdown syllabus) and replace it in place
        target_doc = db.query(Document).order_by(Document.id.asc()).first()

    if target_doc is not None and req.replace_existing_demo:
        target_doc.title = subject_title
        target_doc.filename = filename
        target_doc.content_type = "text/markdown"
        target_doc.file_size_bytes = len(syllabus_text.encode("utf-8"))
        target_doc.s3_key = s3_key
        target_doc.storage_backend = storage_backend
        target_doc.extracted_text = syllabus_text
        target_doc.summary_metadata = metadata
        db.commit()
        db.refresh(target_doc)
        doc = target_doc
    else:
        doc = Document(
            user_id=1,
            title=subject_title,
            filename=filename,
            content_type="text/markdown",
            file_size_bytes=len(syllabus_text.encode("utf-8")),
            s3_key=s3_key,
            storage_backend=storage_backend,
            extracted_text=syllabus_text,
            summary_metadata=metadata,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

    return DocumentResponse(
        id=doc.id,
        title=doc.title,
        filename=doc.filename,
        content_type=doc.content_type,
        file_size_bytes=doc.file_size_bytes,
        s3_key=doc.s3_key,
        storage_backend=doc.storage_backend,
        summary_metadata=doc.summary_metadata,
        text_preview=doc.extracted_text[:600],
        created_at=doc.created_at,
    )


def _resolve_or_create_document(
    db: Session,
    document_id: Optional[int],
    custom_subject: Optional[str],
) -> Document:
    """
    Resolves an uploaded Document by ID or dynamically creates/matches a subject document
    from free-typed user input so quizzes/exams/mindmaps work without uploading a file first.
    """
    if document_id is not None:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc:
            return doc

    typed = (custom_subject or "").strip()
    if typed:
        # 1. Check if the user typed the title or filename of an existing document
        existing = (
            db.query(Document)
            .filter(Document.title.ilike(typed))
            .order_by(desc(Document.created_at))
            .first()
        )
        if existing:
            return existing

        # 2. Otherwise dynamically synthesize a syllabus document for this custom subject
        syllabus_text = ai_service.generate_subject_syllabus(typed)
        safe_slug = "".join(c if c.isalnum() else "_" for c in typed)[:40].strip("_") or "custom_topic"
        filename = f"{safe_slug}_AutoSyllabus.md"
        s3_key, storage_backend = storage_service.upload_file(
            syllabus_text.encode("utf-8"),
            filename,
            "text/markdown",
        )
        metadata = ai_service.extract_document_metadata(typed, syllabus_text)
        new_doc = Document(
            user_id=1,
            title=typed,
            filename=filename,
            content_type="text/markdown",
            file_size_bytes=len(syllabus_text.encode("utf-8")),
            s3_key=s3_key,
            storage_backend=storage_backend,
            extracted_text=syllabus_text,
            summary_metadata=metadata,
        )
        db.add(new_doc)
        db.commit()
        db.refresh(new_doc)
        return new_doc

    raise HTTPException(
        status_code=400,
        detail="Please enter a subject/topic or select a study document.",
    )


# =====================================================================
# 2. Interactive Quiz Generation (/generate-quiz, /quizzes)
# =====================================================================

@app.post("/generate-quiz", tags=["AI Generation"])
def generate_quiz_endpoint(req: GenerateQuizRequest, db: Session = Depends(get_db)):
    """Generates an interactive Flash-Quiz from either a free-typed subject/topic or an uploaded document."""
    doc = _resolve_or_create_document(db, req.document_id, req.custom_subject)

    if req.async_mode and queue_service.is_redis_connected:
        dispatch = queue_service.enqueue_or_run(
            batch_generate_quiz_job,
            doc.id,
            req.num_questions,
            req.difficulty,
            req.focus_topic,
        )
        return dispatch

    generated = ai_service.generate_quiz(
        title=doc.title,
        text=doc.extracted_text,
        num_questions=req.num_questions,
        difficulty=req.difficulty,
        focus_topic=req.focus_topic,
    )

    quiz_record = Quiz(
        user_id=doc.user_id,
        document_id=doc.id,
        title=generated.title,
        difficulty=req.difficulty,
        num_questions=len(generated.questions),
        questions_json=[q.model_dump() for q in generated.questions],
    )
    db.add(quiz_record)
    db.commit()
    db.refresh(quiz_record)

    return {
        "id": quiz_record.id,
        "document_id": doc.id,
        "title": quiz_record.title,
        "difficulty": quiz_record.difficulty,
        "num_questions": quiz_record.num_questions,
        "key_topics_covered": generated.key_topics_covered,
        "questions": quiz_record.questions_json,
        "created_at": quiz_record.created_at,
    }


@app.get("/quizzes", tags=["AI Generation"])
def list_quizzes(document_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(Quiz).order_by(desc(Quiz.created_at))
    if document_id is not None:
        query = query.filter(Quiz.document_id == document_id)
    quizzes = query.all()
    return [
        {
            "id": q.id,
            "document_id": q.document_id,
            "title": q.title,
            "difficulty": q.difficulty,
            "num_questions": q.num_questions,
            "questions": q.questions_json,
            "created_at": q.created_at,
        }
        for q in quizzes
    ]


# =====================================================================
# 3. Timed Mock Exam Generation & AI Rubric Grading (/generate-test)
# =====================================================================

@app.post("/generate-test", tags=["AI Generation"])
def generate_mock_exam_endpoint(req: GenerateTestRequest, db: Session = Depends(get_db)):
    """
    Generates a timed Mock Exam Paper with Multiple Choice, Short Answer, and Long Essay
    sections plus detailed grading rubrics.
    """
    doc = _resolve_or_create_document(db, req.document_id, req.custom_subject)

    if req.async_mode and queue_service.is_redis_connected:
        dispatch = queue_service.enqueue_or_run(
            batch_generate_mock_exam_job,
            doc.id,
            req.duration_minutes,
            req.difficulty,
            req.mcq_count,
            req.short_answer_count,
            req.essay_count,
            req.focus_topic,
        )
        return dispatch

    generated = ai_service.generate_mock_exam(
        title=doc.title,
        text=doc.extracted_text,
        duration_minutes=req.duration_minutes,
        difficulty=req.difficulty,
        mcq_count=req.mcq_count,
        short_answer_count=req.short_answer_count,
        essay_count=req.essay_count,
        focus_topic=req.focus_topic,
    )

    exam_record = MockExam(
        user_id=doc.user_id,
        document_id=doc.id,
        title=generated.exam_title,
        duration_minutes=generated.duration_minutes,
        total_marks=generated.total_marks,
        difficulty=req.difficulty,
        exam_payload=generated.model_dump(),
    )
    db.add(exam_record)
    db.commit()
    db.refresh(exam_record)

    return {
        "id": exam_record.id,
        "document_id": doc.id,
        "title": exam_record.title,
        "duration_minutes": exam_record.duration_minutes,
        "total_marks": exam_record.total_marks,
        "difficulty": exam_record.difficulty,
        "exam_payload": exam_record.exam_payload,
        "created_at": exam_record.created_at,
    }


@app.get("/mock-exams", tags=["AI Generation"])
def list_mock_exams(document_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(MockExam).order_by(desc(MockExam.created_at))
    if document_id is not None:
        query = query.filter(MockExam.document_id == document_id)
    exams = query.all()
    return [
        {
            "id": e.id,
            "document_id": e.document_id,
            "title": e.title,
            "duration_minutes": e.duration_minutes,
            "total_marks": e.total_marks,
            "difficulty": e.difficulty,
            "exam_payload": e.exam_payload,
            "created_at": e.created_at,
        }
        for e in exams
    ]


@app.post("/grade-exam", tags=["AI Generation"])
def grade_mock_exam_submission(req: GradeMockExamRequest, db: Session = Depends(get_db)):
    """
    Grades a completed Mock Exam submission:
    - Deterministically scores Section A (Multiple Choice)
    - Uses Gemini rubric evaluation for Section B (Short Answer) and Section C (Long Essay)
    - Saves the complete attempt and feedback in PostgreSQL `TestResult`.
    """
    exam = db.query(MockExam).filter(MockExam.id == req.mock_exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Mock exam not found")

    payload = exam.exam_payload
    mcq_results = []
    mcq_score = 0.0
    mcq_max = 0.0

    for item in payload.get("multiple_choice_section", []):
        q_num = str(item["question_number"])
        marks = float(item.get("marks", 2))
        mcq_max += marks
        selected = req.mcq_answers.get(q_num)
        is_correct = selected is not None and int(selected) == int(item["correct_option_index"])
        awarded = marks if is_correct else 0.0
        mcq_score += awarded
        mcq_results.append(
            {
                "question_number": item["question_number"],
                "selected_option": selected,
                "correct_option": item["correct_option_index"],
                "is_correct": is_correct,
                "awarded_marks": awarded,
                "max_marks": marks,
                "explanation": item.get("explanation", ""),
            }
        )

    written_grading = ai_service.grade_written_exam(
        exam_payload=payload,
        short_answers=req.short_answers,
        essay_answers=req.essay_answers,
    )

    written_score = sum(w.awarded_marks for w in written_grading.written_evaluations)
    written_max = sum(w.max_marks for w in written_grading.written_evaluations)

    total_score = round(mcq_score + written_score, 2)
    total_max = max(round(mcq_max + written_max, 2), 1.0)
    percentage = round((total_score / total_max) * 100.0, 1)

    feedback_bundle = {
        "mcq_breakdown": mcq_results,
        "written_evaluations": [w.model_dump() for w in written_grading.written_evaluations],
        "overall_study_recommendations": written_grading.overall_study_recommendations,
    }

    result_record = TestResult(
        user_id=exam.user_id,
        assessment_type="mock_exam",
        mock_exam_id=exam.id,
        score=total_score,
        max_score=total_max,
        percentage=percentage,
        time_taken_seconds=req.time_taken_seconds,
        user_answers={
            "mcq_answers": req.mcq_answers,
            "short_answers": req.short_answers,
            "essay_answers": req.essay_answers,
        },
        grading_feedback=feedback_bundle,
    )
    db.add(result_record)
    db.commit()
    db.refresh(result_record)

    return {
        "result_id": result_record.id,
        "mock_exam_id": exam.id,
        "exam_title": exam.title,
        "score": total_score,
        "max_score": total_max,
        "percentage": percentage,
        "time_taken_seconds": req.time_taken_seconds,
        "grading_feedback": feedback_bundle,
        "completed_at": result_record.completed_at,
    }


# =====================================================================
# 4. Mindmap Generation (/generate-mindmap, /mindmaps)
# =====================================================================

@app.post("/generate-mindmap", tags=["AI Generation"])
def generate_mindmap_endpoint(req: GenerateMindmapRequest, db: Session = Depends(get_db)):
    """Extracts hierarchical syllabus concepts and returns Mermaid.js syntax + interactive tree JSON."""
    doc = _resolve_or_create_document(db, req.document_id, req.custom_subject)

    generated = ai_service.generate_mindmap(
        title=doc.title,
        text=doc.extracted_text,
        focus_topic=req.focus_topic,
        max_depth=req.max_depth,
    )

    mindmap_record = Mindmap(
        user_id=doc.user_id,
        document_id=doc.id,
        title=generated.title,
        root_topic=generated.root_topic,
        mermaid_syntax=generated.mermaid_syntax,
        markmap_markdown=generated.markmap_markdown,
        nodes_json=generated.root_node.model_dump(),
    )
    db.add(mindmap_record)
    db.commit()
    db.refresh(mindmap_record)

    return {
        "id": mindmap_record.id,
        "document_id": doc.id,
        "title": mindmap_record.title,
        "root_topic": mindmap_record.root_topic,
        "mermaid_syntax": mindmap_record.mermaid_syntax,
        "markmap_markdown": mindmap_record.markmap_markdown,
        "root_node": mindmap_record.nodes_json,
        "created_at": mindmap_record.created_at,
    }


@app.get("/mindmaps", tags=["AI Generation"])
def list_mindmaps(document_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(Mindmap).order_by(desc(Mindmap.created_at))
    if document_id is not None:
        query = query.filter(Mindmap.document_id == document_id)
    items = query.all()
    return [
        {
            "id": m.id,
            "document_id": m.document_id,
            "title": m.title,
            "root_topic": m.root_topic,
            "mermaid_syntax": m.mermaid_syntax,
            "markmap_markdown": m.markmap_markdown,
            "root_node": m.nodes_json,
            "created_at": m.created_at,
        }
        for m in items
    ]


# =====================================================================
# 5. Analytics & History (/results, /jobs/{job_id})
# =====================================================================

@app.post("/results", tags=["Analytics & History"])
def save_test_result(req: SubmitResultRequest, db: Session = Depends(get_db)):
    """Persists a completed Quiz or Mock Exam attempt to PostgreSQL."""
    max_s = max(req.max_score, 1.0)
    pct = round((req.score / max_s) * 100.0, 1)

    record = TestResult(
        user_id=1,
        assessment_type=req.assessment_type,
        quiz_id=req.quiz_id,
        mock_exam_id=req.mock_exam_id,
        score=req.score,
        max_score=req.max_score,
        percentage=pct,
        time_taken_seconds=req.time_taken_seconds,
        user_answers=req.user_answers,
        grading_feedback=req.grading_feedback,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return {
        "id": record.id,
        "assessment_type": record.assessment_type,
        "score": record.score,
        "max_score": record.max_score,
        "percentage": record.percentage,
        "time_taken_seconds": record.time_taken_seconds,
        "completed_at": record.completed_at,
    }


@app.get("/results", tags=["Analytics & History"])
def get_results_and_analytics(db: Session = Depends(get_db)):
    """Returns historical attempts and aggregated study performance analytics."""
    results = db.query(TestResult).order_by(desc(TestResult.completed_at)).all()
    total_attempts = len(results)
    avg_percentage = (
        round(sum(r.percentage for r in results) / total_attempts, 1)
        if total_attempts > 0
        else 0.0
    )
    total_study_seconds = sum(r.time_taken_seconds for r in results)

    history = []
    for r in results:
        title = "Assessment Attempt"
        if r.assessment_type == "quiz" and r.quiz:
            title = r.quiz.title
        elif r.assessment_type == "mock_exam" and r.mock_exam:
            title = r.mock_exam.title
        history.append(
            {
                "id": r.id,
                "title": title,
                "assessment_type": r.assessment_type,
                "quiz_id": r.quiz_id,
                "mock_exam_id": r.mock_exam_id,
                "score": r.score,
                "max_score": r.max_score,
                "percentage": r.percentage,
                "time_taken_seconds": r.time_taken_seconds,
                "grading_feedback": r.grading_feedback,
                "completed_at": r.completed_at,
            }
        )

    return {
        "summary": {
            "total_documents": db.query(Document).count(),
            "total_quizzes": db.query(Quiz).count(),
            "total_mock_exams": db.query(MockExam).count(),
            "total_mindmaps": db.query(Mindmap).count(),
            "total_attempts": total_attempts,
            "average_score_percentage": avg_percentage,
            "total_study_time_seconds": total_study_seconds,
        },
        "history": history,
    }


@app.get("/jobs/{job_id}", tags=["Async Queue"])
def check_job_status(job_id: str):
    """Checks the status of an asynchronous Redis RQ background task."""
    return queue_service.get_job_status(job_id)
