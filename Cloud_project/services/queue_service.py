import logging
from typing import Any, Callable, Dict, Optional

from config import settings
from database import SessionLocal
from models import Document, MockExam, Quiz
from services.ai_service import ai_service

logger = logging.getLogger(__name__)

try:
    from redis import Redis
    from rq import Queue
    from rq.job import Job
    RQ_AVAILABLE = True
except ImportError:
    RQ_AVAILABLE = False


# =====================================================================
# Background Worker Task Functions (Executed by Redis RQ Worker)
# =====================================================================

def process_document_ingestion_job(document_id: int) -> Dict[str, Any]:
    """Background task to enrich a newly uploaded document with Gemini syllabus metadata."""
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            return {"status": "error", "message": f"Document {document_id} not found"}

        metadata = ai_service.extract_document_metadata(doc.title, doc.extracted_text)
        doc.summary_metadata = metadata
        db.commit()
        return {"status": "completed", "document_id": doc.id, "summary_metadata": metadata}
    finally:
        db.close()


def batch_generate_mock_exam_job(
    document_id: int,
    duration_minutes: int,
    difficulty: str,
    mcq_count: int,
    short_answer_count: int,
    essay_count: int,
    focus_topic: Optional[str] = None,
) -> Dict[str, Any]:
    """Background worker job for generating full timed mock exams."""
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            return {"status": "error", "message": f"Document {document_id} not found"}

        generated = ai_service.generate_mock_exam(
            title=doc.title,
            text=doc.extracted_text,
            duration_minutes=duration_minutes,
            difficulty=difficulty,
            mcq_count=mcq_count,
            short_answer_count=short_answer_count,
            essay_count=essay_count,
            focus_topic=focus_topic,
        )
        exam_record = MockExam(
            user_id=doc.user_id,
            document_id=doc.id,
            title=generated.exam_title,
            duration_minutes=generated.duration_minutes,
            total_marks=generated.total_marks,
            difficulty=difficulty,
            exam_payload=generated.model_dump(),
        )
        db.add(exam_record)
        db.commit()
        db.refresh(exam_record)
        return {"status": "completed", "mock_exam_id": exam_record.id}
    finally:
        db.close()


def batch_generate_quiz_job(
    document_id: int,
    num_questions: int,
    difficulty: str,
    focus_topic: Optional[str] = None,
) -> Dict[str, Any]:
    """Background worker job for generating interactive flash quizzes."""
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            return {"status": "error", "message": f"Document {document_id} not found"}

        generated = ai_service.generate_quiz(
            title=doc.title,
            text=doc.extracted_text,
            num_questions=num_questions,
            difficulty=difficulty,
            focus_topic=focus_topic,
        )
        quiz_record = Quiz(
            user_id=doc.user_id,
            document_id=doc.id,
            title=generated.title,
            difficulty=difficulty,
            num_questions=len(generated.questions),
            questions_json=[q.model_dump() for q in generated.questions],
        )
        db.add(quiz_record)
        db.commit()
        db.refresh(quiz_record)
        return {"status": "completed", "quiz_id": quiz_record.id}
    finally:
        db.close()


class QueueService:
    """
    Manages Redis RQ connection and dispatches asynchronous jobs with automatic
    synchronous fallback when Redis is offline or disabled in local dev.
    """

    def __init__(self) -> None:
        self.redis_conn = None
        self.queue = None

        if RQ_AVAILABLE and settings.ENABLE_ASYNC_WORKER:
            try:
                self.redis_conn = Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
                self.redis_conn.ping()
                self.queue = Queue("studyflow_tasks", connection=self.redis_conn)
            except Exception as exc:
                logger.warning("Redis unavailable (%s); using synchronous execution fallback.", exc)
                self.redis_conn = None
                self.queue = None

    @property
    def is_redis_connected(self) -> bool:
        if self.redis_conn is None:
            return False
        try:
            return bool(self.redis_conn.ping())
        except Exception:
            return False

    def enqueue_or_run(self, func: Callable, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """Enqueues a task on Redis RQ if active; otherwise runs synchronously."""
        if self.queue is not None and self.is_redis_connected:
            job = self.queue.enqueue(func, *args, **kwargs, job_timeout=300)
            return {
                "mode": "async",
                "job_id": job.get_id(),
                "status": job.get_status(),
            }

        result = func(*args, **kwargs)
        return {
            "mode": "sync",
            "job_id": None,
            "status": "finished",
            "result": result,
        }

    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        if not RQ_AVAILABLE or self.redis_conn is None:
            return {"job_id": job_id, "status": "unavailable"}
        try:
            job = Job.fetch(job_id, connection=self.redis_conn)
            return {
                "job_id": job.get_id(),
                "status": job.get_status(),
                "result": job.result if job.is_finished else None,
                "error": str(job.exc_info) if job.is_failed else None,
            }
        except Exception as exc:
            return {"job_id": job_id, "status": "not_found", "error": str(exc)}


queue_service = QueueService()
