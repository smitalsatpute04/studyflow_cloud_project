from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# =====================================================================
# SQLAlchemy ORM Models (PostgreSQL)
# =====================================================================

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    full_name = Column(String(255), nullable=False, default="StudyFlow Learner")
    created_at = Column(DateTime(timezone=True), default=utc_now)

    documents = relationship("Document", back_populates="user", cascade="all, delete-orphan")
    quizzes = relationship("Quiz", back_populates="user", cascade="all, delete-orphan")
    mock_exams = relationship("MockExam", back_populates="user", cascade="all, delete-orphan")
    mindmaps = relationship("Mindmap", back_populates="user", cascade="all, delete-orphan")
    test_results = relationship("TestResult", back_populates="user", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, default=1)
    title = Column(String(255), nullable=False)
    filename = Column(String(255), nullable=False)
    content_type = Column(String(100), nullable=False)
    file_size_bytes = Column(Integer, nullable=False, default=0)
    s3_key = Column(String(512), nullable=False, unique=True)
    storage_backend = Column(String(50), nullable=False, default="r2")  # 'r2' or 'local'
    extracted_text = Column(Text, nullable=False, default="")
    summary_metadata = Column(JSON, nullable=True)  # Syllabus topics, key concepts
    created_at = Column(DateTime(timezone=True), default=utc_now)

    user = relationship("User", back_populates="documents")
    quizzes = relationship("Quiz", back_populates="document", cascade="all, delete-orphan")
    mock_exams = relationship("MockExam", back_populates="document", cascade="all, delete-orphan")
    mindmaps = relationship("Mindmap", back_populates="document", cascade="all, delete-orphan")


class Quiz(Base):
    """Interactive Flash-Quiz with instant scoring and explanations."""
    __tablename__ = "quizzes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, default=1)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False)
    difficulty = Column(String(50), nullable=False, default="medium")
    num_questions = Column(Integer, nullable=False, default=5)
    questions_json = Column(JSON, nullable=False)  # List of QuizQuestionSchema dicts
    created_at = Column(DateTime(timezone=True), default=utc_now)

    user = relationship("User", back_populates="quizzes")
    document = relationship("Document", back_populates="quizzes")
    results = relationship("TestResult", back_populates="quiz", cascade="all, delete-orphan")


class MockExam(Base):
    """Timed Mock Exam containing MCQ, Short Answer, and Long Essay sections with rubrics."""
    __tablename__ = "mock_exams"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, default=1)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False)
    duration_minutes = Column(Integer, nullable=False, default=45)
    total_marks = Column(Integer, nullable=False, default=100)
    difficulty = Column(String(50), nullable=False, default="medium")
    exam_payload = Column(JSON, nullable=False)  # Full structured MockExamGeneratedSchema
    created_at = Column(DateTime(timezone=True), default=utc_now)

    user = relationship("User", back_populates="mock_exams")
    document = relationship("Document", back_populates="mock_exams")
    results = relationship("TestResult", back_populates="mock_exam", cascade="all, delete-orphan")


class Mindmap(Base):
    """Hierarchical concept map with Mermaid.js syntax and structured node tree."""
    __tablename__ = "mindmaps"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, default=1)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False)
    root_topic = Column(String(255), nullable=False)
    mermaid_syntax = Column(Text, nullable=False)
    markmap_markdown = Column(Text, nullable=True)
    nodes_json = Column(JSON, nullable=False)  # Hierarchical tree structure
    created_at = Column(DateTime(timezone=True), default=utc_now)

    user = relationship("User", back_populates="mindmaps")
    document = relationship("Document", back_populates="mindmaps")


class TestResult(Base):
    """Stores user quiz/mock-exam attempts, scores, time spent, and AI rubric feedback."""
    __tablename__ = "test_results"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, default=1)
    assessment_type = Column(String(50), nullable=False)  # 'quiz' or 'mock_exam'
    quiz_id = Column(Integer, ForeignKey("quizzes.id", ondelete="SET NULL"), nullable=True)
    mock_exam_id = Column(Integer, ForeignKey("mock_exams.id", ondelete="SET NULL"), nullable=True)
    score = Column(Float, nullable=False)
    max_score = Column(Float, nullable=False)
    percentage = Column(Float, nullable=False)
    time_taken_seconds = Column(Integer, nullable=False, default=0)
    user_answers = Column(JSON, nullable=False, default=dict)
    grading_feedback = Column(JSON, nullable=True)  # Detailed per-question breakdown
    completed_at = Column(DateTime(timezone=True), default=utc_now)

    user = relationship("User", back_populates="test_results")
    quiz = relationship("Quiz", back_populates="results")
    mock_exam = relationship("MockExam", back_populates="results")


# =====================================================================
# Pydantic Schemas for Gemini Structured Output & REST API
# =====================================================================

class QuizQuestionSchema(BaseModel):
    id: int = Field(description="1-based question number")
    topic: str = Field(description="Specific sub-topic from the document")
    question: str = Field(description="Clear, unambiguous question stem")
    options: List[str] = Field(description="Exactly 4 answer choices")
    correct_option_index: int = Field(description="0-based index (0 to 3) of the correct option")
    explanation: str = Field(description="Detailed explanation of why the correct option is right and others are wrong")
    difficulty: Literal["easy", "medium", "hard"] = Field(default="medium")


class QuizGeneratedSchema(BaseModel):
    title: str = Field(description="Descriptive title for the flash quiz")
    key_topics_covered: List[str] = Field(description="List of core syllabus topics tested")
    questions: List[QuizQuestionSchema] = Field(description="List of generated flash-quiz questions")


class RubricCriterionSchema(BaseModel):
    criterion: str = Field(description="Specific concept or argument required in the answer")
    points: int = Field(description="Points allocated to this criterion")


class MCQExamItemSchema(BaseModel):
    question_number: int
    question: str
    options: List[str] = Field(description="4 options labeled A, B, C, D or plain strings")
    correct_option_index: int = Field(description="0-based index of the correct choice")
    marks: int = Field(default=2)
    explanation: str = Field(description="Rationale for the correct answer")


class ShortAnswerExamItemSchema(BaseModel):
    question_number: int
    question: str
    marks: int = Field(default=5)
    expected_keywords: List[str] = Field(description="Key domain terms expected in a full-credit response")
    sample_answer: str = Field(description="Concise model answer")
    rubric: List[RubricCriterionSchema] = Field(description="Point-by-point grading rubric")


class LongEssayExamItemSchema(BaseModel):
    question_number: int
    question: str
    marks: int = Field(default=15)
    thesis_guidance: str = Field(description="What a strong thesis and structure should look like")
    sample_outline: List[str] = Field(description="Key sections/arguments expected in the essay")
    rubric: List[RubricCriterionSchema] = Field(description="Detailed grading rubric summing to marks")


class MockExamGeneratedSchema(BaseModel):
    exam_title: str = Field(description="Formal academic title for the mock paper")
    instructions: List[str] = Field(description="General instructions for examinees")
    duration_minutes: int = Field(description="Recommended time limit in minutes")
    total_marks: int = Field(description="Sum of all question marks across sections")
    multiple_choice_section: List[MCQExamItemSchema]
    short_answer_section: List[ShortAnswerExamItemSchema]
    long_essay_section: List[LongEssayExamItemSchema]


class MindmapNodeSchema(BaseModel):
    id: str = Field(description="Unique alphanumeric node ID, e.g. node_1")
    label: str = Field(description="Concise concept name (max 45 chars, no parentheses or quotes)")
    summary: str = Field(description="1-2 sentence explanation of this concept")
    children: List["MindmapNodeSchema"] = Field(default_factory=list)


MindmapNodeSchema.model_rebuild()


class MindmapGeneratedSchema(BaseModel):
    title: str = Field(description="Title of the mindmap")
    root_topic: str = Field(description="Central concept of the mindmap")
    mermaid_syntax: str = Field(
        description="Valid Mermaid.js mindmap syntax starting with 'mindmap' and indented hierarchy"
    )
    markmap_markdown: str = Field(
        description="Hierarchical Markdown headings (#, ##, ###, -) compatible with Markmap"
    )
    root_node: MindmapNodeSchema = Field(description="Hierarchical JSON concept tree")


# =====================================================================
# API Request / Response Payloads
# =====================================================================

class CreateSubjectRequest(BaseModel):
    subject: str = Field(..., min_length=2, description="User's preferred subject or course title")
    custom_notes: Optional[str] = Field(
        default=None,
        description="Optional syllabus outline or study notes typed directly by the user",
    )
    replace_existing_demo: bool = Field(
        default=True,
        description="If true, replaces the default pre-seeded syllabus with this subject",
    )


class GenerateQuizRequest(BaseModel):
    document_id: Optional[int] = None
    custom_subject: Optional[str] = Field(
        default=None,
        description="Free-typed subject, topic, or study text when generating without an uploaded document",
    )
    num_questions: int = Field(default=5, ge=3, le=20)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    focus_topic: Optional[str] = None
    async_mode: bool = False


class GenerateTestRequest(BaseModel):
    document_id: Optional[int] = None
    custom_subject: Optional[str] = Field(
        default=None,
        description="Free-typed subject or topic when generating a mock exam without an uploaded document",
    )
    duration_minutes: int = Field(default=45, ge=15, le=180)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    mcq_count: int = Field(default=5, ge=2, le=15)
    short_answer_count: int = Field(default=3, ge=1, le=10)
    essay_count: int = Field(default=1, ge=1, le=4)
    focus_topic: Optional[str] = None
    async_mode: bool = False


class GenerateMindmapRequest(BaseModel):
    document_id: Optional[int] = None
    custom_subject: Optional[str] = Field(
        default=None,
        description="Free-typed subject or topic when generating a mindmap without an uploaded document",
    )
    focus_topic: Optional[str] = None
    max_depth: int = Field(default=3, ge=2, le=5)


class SubmitResultRequest(BaseModel):
    assessment_type: Literal["quiz", "mock_exam"]
    quiz_id: Optional[int] = None
    mock_exam_id: Optional[int] = None
    score: float
    max_score: float
    time_taken_seconds: int = 0
    user_answers: Dict[str, Any] = Field(default_factory=dict)
    grading_feedback: Optional[Dict[str, Any]] = None


class GradeMockExamRequest(BaseModel):
    mock_exam_id: int
    mcq_answers: Dict[str, int] = Field(
        default_factory=dict,
        description="Map of question_number (str) -> selected option index (int)",
    )
    short_answers: Dict[str, str] = Field(
        default_factory=dict,
        description="Map of question_number (str) -> student written response",
    )
    essay_answers: Dict[str, str] = Field(
        default_factory=dict,
        description="Map of question_number (str) -> student essay response",
    )
    time_taken_seconds: int = 0


class WrittenQuestionGradeSchema(BaseModel):
    question_number: int
    section: Literal["short_answer", "long_essay"]
    awarded_marks: float
    max_marks: int
    feedback: str
    strengths: List[str]
    missing_concepts: List[str]


class ExamGradingOutputSchema(BaseModel):
    written_evaluations: List[WrittenQuestionGradeSchema]
    overall_study_recommendations: List[str]


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    filename: str
    content_type: str
    file_size_bytes: int
    s3_key: str
    storage_backend: str
    summary_metadata: Optional[Dict[str, Any]] = None
    text_preview: str = ""
    created_at: datetime
