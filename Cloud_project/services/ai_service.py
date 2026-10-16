import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from config import settings
from models import (
    ExamGradingOutputSchema,
    MindmapGeneratedSchema,
    MindmapNodeSchema,
    MockExamGeneratedSchema,
    QuizGeneratedSchema,
)

logger = logging.getLogger(__name__)

# Guard against broken system SSLKEYLOGFILE paths on Windows (e.g., virtual_file.log permission error)
os.environ.pop("SSLKEYLOGFILE", None)

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


class DocumentMetadataSchema(BaseModel):
    course_or_subject: str = Field(description="Detected course name or subject domain")
    executive_summary: str = Field(description="Concise 2-3 sentence summary of the material")
    key_topics: List[str] = Field(description="5 to 10 core syllabus topics or concepts found in the document")
    estimated_study_hours: float = Field(description="Estimated hours required to master this material")
    difficulty_level: str = Field(description="Introductory, Intermediate, or Advanced")


class AIService:
    """
    Google Gemini API pipeline using `google-genai` with strict Pydantic structured outputs
    for syllabus extraction, interactive quizzes, timed mock exams, and Mermaid mindmaps.
    """

    def __init__(self) -> None:
        self.api_key = settings.GEMINI_API_KEY
        self.model_name = settings.GEMINI_MODEL
        self.pro_model_name = settings.GEMINI_PRO_MODEL
        self.client = None

        if GENAI_AVAILABLE and self.api_key and self.api_key != "your-gemini-api-key":
            try:
                os.environ.pop("SSLKEYLOGFILE", None)
                self.client = genai.Client(api_key=self.api_key)
            except Exception as exc:
                logger.warning("Failed to initialize Google GenAI client: %s", exc)

    @property
    def is_live(self) -> bool:
        return self.client is not None

    def _truncate_context(self, text: str, max_chars: int = 60000) -> str:
        cleaned = (text or "").strip()
        if len(cleaned) <= max_chars:
            return cleaned
        return cleaned[:max_chars] + "\n...[Document truncated to fit context window]..."

    @staticmethod
    def _sanitize_mermaid_label(label: str) -> str:
        """Ensure Mermaid mindmap labels contain no syntax-breaking characters."""
        clean = re.sub(r"[\(\)\[\]\{\}\"`'<>:;]", " ", label or "Concept")
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean[:48] or "Concept"

    @classmethod
    def build_mermaid_from_tree(cls, root: MindmapNodeSchema) -> str:
        """Deterministically compile a MindmapNodeSchema tree into valid Mermaid.js mindmap syntax."""
        lines = ["mindmap"]
        root_label = cls._sanitize_mermaid_label(root.label)
        lines.append(f"  root(({root_label}))")

        def _walk(nodes: List[MindmapNodeSchema], indent_level: int) -> None:
            indent = "  " * indent_level
            for node in nodes:
                safe_label = cls._sanitize_mermaid_label(node.label)
                lines.append(f"{indent}{safe_label}")
                if node.children:
                    _walk(node.children, indent_level + 1)

        _walk(root.children, 2)
        return "\n".join(lines)

    @classmethod
    def build_markmap_from_tree(cls, root: MindmapNodeSchema) -> str:
        """Compile a MindmapNodeSchema tree into hierarchical Markdown for Markmap."""
        lines = [f"# {root.label}"]

        def _walk(nodes: List[MindmapNodeSchema], depth: int) -> None:
            for node in nodes:
                if depth <= 3:
                    prefix = "#" * depth
                    lines.append(f"{prefix} {node.label}")
                    if node.summary:
                        lines.append(f"- {node.summary}")
                else:
                    indent = "  " * (depth - 4)
                    lines.append(f"{indent}- **{node.label}**: {node.summary}")
                if node.children:
                    _walk(node.children, depth + 1)

        _walk(root.children, 2)
        return "\n".join(lines)

    # -----------------------------------------------------------------
    # 0. Real-Time Custom Subject Syllabus Synthesis
    # -----------------------------------------------------------------
    def generate_subject_syllabus(self, subject: str, custom_notes: Optional[str] = None) -> str:
        """Synthesizes a comprehensive multi-module study syllabus for any user-typed subject."""
        if custom_notes and len(custom_notes.strip()) > 80:
            return f"# {subject}\n\n{custom_notes.strip()}"

        prompt = f"""You are a distinguished university curriculum designer.
Create a comprehensive, structured study guide and syllabus for the subject: "{subject}".
{f'Include these user notes/topics: {custom_notes}' if custom_notes else ''}

Structure the guide in clean Markdown with 5 core modules, detailed explanations of key principles, definitions, formulas/mechanisms, and real-world applications so students can generate rigorous quizzes, mock exams, and mindmaps from it.
"""
        if self.is_live:
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.35),
                )
                if response.text and len(response.text.strip()) > 50:
                    return response.text.strip()
            except Exception as exc:
                logger.error("Gemini syllabus synthesis error, using structured template: %s", exc)

        extra = f"\n\n## User Focus Notes\n{custom_notes.strip()}" if custom_notes else ""
        return f"""# {subject} — Comprehensive Study Syllabus & Core Notes

## Module 1: Foundational Concepts & Theoretical Framework of {subject}
{subject} encompasses the core definitions, historical evolution, and foundational axioms that govern the discipline. Mastering the fundamental terminology and primary abstractions is essential before analyzing complex systems.

## Module 2: Core Mechanisms, Architectures & Analytical Models
This module explores the internal workflows, structural patterns, and mathematical or logical formulations used in {subject}. Key emphasis is placed on how sub-components interact under standard operating assumptions.

## Module 3: Algorithms, Methodologies & Problem-Solving Techniques
Practical execution in {subject} relies on systematic methodologies, step-by-step analytical procedures, and optimization techniques to solve domain-specific challenges accurately.

## Module 4: Performance Trade-offs, Limitations & Edge Cases
Every design decision in {subject} involves balancing competing constraints such as efficiency, scalability, accuracy, and maintainability. Understanding failure modes and edge cases prevents common pitfalls.

## Module 5: Advanced Applications & Real-World Case Studies
Synthesizes Modules 1–4 through contemporary industry and research applications of {subject}, examining end-to-end implementations and best practices.{extra}
"""

    # -----------------------------------------------------------------
    # 1. Syllabus & Document Metadata Extraction
    # -----------------------------------------------------------------
    def extract_document_metadata(self, title: str, text: str) -> Dict[str, Any]:
        context = self._truncate_context(text, max_chars=35000)
        prompt = f"""You are an expert academic curriculum analyst.
Analyze the following study document titled "{title}" and extract its structured syllabus metadata.

DOCUMENT CONTENT:
\"\"\"
{context}
\"\"\"
"""
        if self.is_live:
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=DocumentMetadataSchema,
                        temperature=0.2,
                    ),
                )
                parsed = DocumentMetadataSchema.model_validate_json(response.text)
                return parsed.model_dump()
            except Exception as exc:
                logger.error("Gemini metadata extraction error, using fallback: %s", exc)

        # Fallback extraction derived directly from the uploaded text
        topics = self._extract_fallback_topics(title, text)
        word_count = max(len(text.split()), 1)
        return {
            "course_or_subject": title,
            "executive_summary": (
                f"Study material covering {', '.join(topics[:4])}. "
                f"Contains approximately {word_count} words of academic notes and syllabus concepts."
            ),
            "key_topics": topics,
            "estimated_study_hours": round(max(1.5, min(20.0, word_count / 450)), 1),
            "difficulty_level": "Intermediate",
        }

    # -----------------------------------------------------------------
    # 2. Interactive Flash-Quiz Generation
    # -----------------------------------------------------------------
    def generate_quiz(
        self,
        title: str,
        text: str,
        num_questions: int = 5,
        difficulty: str = "medium",
        focus_topic: Optional[str] = None,
    ) -> QuizGeneratedSchema:
        context = self._truncate_context(text)
        topic_directive = (
            f"Focus specifically on the topic: '{focus_topic}'."
            if focus_topic
            else "Cover a balanced distribution of the most important concepts across the document."
        )

        prompt = f"""You are an expert university professor designing an interactive self-paced Flash Quiz.
Generate exactly {num_questions} multiple-choice questions at a '{difficulty}' difficulty level based strictly on the provided study material titled "{title}".
{topic_directive}

Requirements:
1. Every question must have 4 plausible options (no 'all of the above' clichés unless pedagogically essential).
2. `correct_option_index` must be an integer in [0, 1, 2, 3].
3. Provide a thorough, educational `explanation` citing why the correct answer is right and clarifying common misconceptions.
4. Output strict JSON matching the requested schema.

STUDY MATERIAL:
\"\"\"
{context}
\"\"\"
"""
        if self.is_live:
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=QuizGeneratedSchema,
                        temperature=0.4,
                    ),
                )
                return QuizGeneratedSchema.model_validate_json(response.text)
            except Exception as exc:
                logger.error("Gemini quiz generation error, using fallback: %s", exc)

        return self._fallback_quiz(title, text, num_questions, difficulty, focus_topic)

    # -----------------------------------------------------------------
    # 3. Timed Mock Exam Generator (MCQ + Short Answer + Long Essay)
    # -----------------------------------------------------------------
    def generate_mock_exam(
        self,
        title: str,
        text: str,
        duration_minutes: int = 45,
        difficulty: str = "medium",
        mcq_count: int = 5,
        short_answer_count: int = 3,
        essay_count: int = 1,
        focus_topic: Optional[str] = None,
    ) -> MockExamGeneratedSchema:
        context = self._truncate_context(text)
        topic_directive = f"Prioritize questions on: {focus_topic}." if focus_topic else ""

        prompt = f"""You are a senior university examination chair creating a formal, timed Mock Examination Paper.
Source Document: "{title}"
Difficulty: {difficulty.upper()}
Duration: {duration_minutes} minutes
{topic_directive}

Structure the exam into three rigorous sections:
- Section A: {mcq_count} Multiple-Choice Questions (2 marks each), with 4 options, 0-based `correct_option_index`, and analytical explanation.
- Section B: {short_answer_count} Short-Answer Questions (5 marks each), complete with `expected_keywords`, a concise `sample_answer`, and a point-by-point `rubric` summing to 5 marks.
- Section C: {essay_count} Long-Essay Question(s) (15 marks each), complete with `thesis_guidance`, `sample_outline`, and a granular `rubric` summing to 15 marks.

Ensure `total_marks` equals the exact sum of all questions across Sections A, B, and C.

SOURCE MATERIAL:
\"\"\"
{context}
\"\"\"
"""
        if self.is_live:
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=MockExamGeneratedSchema,
                        temperature=0.35,
                    ),
                )
                return MockExamGeneratedSchema.model_validate_json(response.text)
            except Exception as exc:
                logger.error("Gemini mock exam generation error, using fallback: %s", exc)

        return self._fallback_mock_exam(
            title, text, duration_minutes, difficulty, mcq_count, short_answer_count, essay_count
        )

    # -----------------------------------------------------------------
    # 4. Hierarchical Mindmap Generator (Mermaid.js + JSON Tree)
    # -----------------------------------------------------------------
    def generate_mindmap(
        self,
        title: str,
        text: str,
        focus_topic: Optional[str] = None,
        max_depth: int = 3,
    ) -> MindmapGeneratedSchema:
        context = self._truncate_context(text)
        focus_instruction = (
            f"Center the mindmap around '{focus_topic}'."
            if focus_topic
            else "Map the overarching syllabus architecture and key conceptual hierarchy."
        )

        prompt = f"""You are a knowledge-graph architect. Extract the hierarchical concept tree from the study document "{title}".
{focus_instruction}
Maximum hierarchy depth: {max_depth} levels.

Requirements:
1. Build a rich `root_node` tree where the root has 4 to 6 primary branches, and each branch has 2 to 4 child concepts.
2. Keep every node `label` concise (2 to 5 words, no parentheses, brackets, or quotes).
3. Include a 1-2 sentence `summary` on every node so learners can inspect nodes interactively.
4. Generate valid Mermaid.js `mindmap` syntax in `mermaid_syntax` AND valid hierarchical Markdown in `markmap_markdown`.

DOCUMENT CONTENT:
\"\"\"
{context}
\"\"\"
"""
        if self.is_live:
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=MindmapGeneratedSchema,
                        temperature=0.3,
                    ),
                )
                parsed = MindmapGeneratedSchema.model_validate_json(response.text)
                # Always normalize Mermaid & Markmap from the validated node tree to guarantee syntax safety
                parsed.mermaid_syntax = self.build_mermaid_from_tree(parsed.root_node)
                parsed.markmap_markdown = self.build_markmap_from_tree(parsed.root_node)
                return parsed
            except Exception as exc:
                logger.error("Gemini mindmap generation error, using fallback: %s", exc)

        return self._fallback_mindmap(title, text, focus_topic)

    # -----------------------------------------------------------------
    # 5. AI Rubric Grading for Written Mock Exam Submissions
    # -----------------------------------------------------------------
    def grade_written_exam(
        self,
        exam_payload: Dict[str, Any],
        short_answers: Dict[str, str],
        essay_answers: Dict[str, str],
    ) -> ExamGradingOutputSchema:
        if self.is_live and (short_answers or essay_answers):
            prompt = f"""You are a fair, rigorous academic examiner grading a student's Mock Exam submission against official rubrics.

EXAM RUBRICS & QUESTIONS:
{json.dumps({
    "short_answer_section": exam_payload.get("short_answer_section", []),
    "long_essay_section": exam_payload.get("long_essay_section", []),
}, indent=2)}

STUDENT SHORT ANSWERS:
{json.dumps(short_answers, indent=2)}

STUDENT ESSAY ANSWERS:
{json.dumps(essay_answers, indent=2)}

Evaluate every short answer and long essay question against its rubric criteria. Award partial credit where appropriate, highlight strengths, list missing concepts, and provide actionable study recommendations.
"""
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=ExamGradingOutputSchema,
                        temperature=0.2,
                    ),
                )
                return ExamGradingOutputSchema.model_validate_json(response.text)
            except Exception as exc:
                logger.error("Gemini exam grading error, using heuristic rubric grader: %s", exc)

        # Deterministic keyword + length rubric evaluator fallback
        evaluations = []
        for sa in exam_payload.get("short_answer_section", []):
            q_num = sa["question_number"]
            ans = (short_answers.get(str(q_num), "") or "").strip()
            max_m = sa.get("marks", 5)
            keywords = sa.get("expected_keywords", [])
            matched = [kw for kw in keywords if kw.lower() in ans.lower()]
            missing = [kw for kw in keywords if kw.lower() not in ans.lower()]
            if not ans:
                awarded = 0.0
                feedback = "No response submitted."
            else:
                kw_ratio = len(matched) / max(len(keywords), 1)
                len_ratio = min(1.0, len(ans.split()) / 25.0)
                awarded = round(max_m * (0.65 * kw_ratio + 0.35 * len_ratio), 1)
                feedback = (
                    f"Covered {len(matched)}/{len(keywords)} key concepts. "
                    f"Model answer reference: {sa.get('sample_answer', '')}"
                )
            evaluations.append(
                {
                    "question_number": q_num,
                    "section": "short_answer",
                    "awarded_marks": awarded,
                    "max_marks": max_m,
                    "feedback": feedback,
                    "strengths": matched or (["Attempted response"] if ans else []),
                    "missing_concepts": missing,
                }
            )

        for essay in exam_payload.get("long_essay_section", []):
            q_num = essay["question_number"]
            ans = (essay_answers.get(str(q_num), "") or "").strip()
            max_m = essay.get("marks", 15)
            outline = essay.get("sample_outline", [])
            word_count = len(ans.split())
            if not ans:
                awarded = 0.0
                feedback = "No essay response submitted."
                strengths = []
                missing = outline
            else:
                depth_factor = min(1.0, word_count / 120.0)
                awarded = round(max_m * (0.25 + 0.75 * depth_factor), 1)
                feedback = (
                    f"Essay contains {word_count} words. Compare your structure with the thesis guidance: "
                    f"{essay.get('thesis_guidance', '')}"
                )
                strengths = ["Structured written argument", f"Developed {word_count} words of analysis"]
                missing = outline[1:] if word_count < 80 else []
            evaluations.append(
                {
                    "question_number": q_num,
                    "section": "long_essay",
                    "awarded_marks": awarded,
                    "max_marks": max_m,
                    "feedback": feedback,
                    "strengths": strengths,
                    "missing_concepts": missing,
                }
            )

        return ExamGradingOutputSchema(
            written_evaluations=evaluations,
            overall_study_recommendations=[
                "Review the sample answers and rubric breakdowns for any questions where key domain terms were missed.",
                "Practice synthesizing multi-part definitions into concise bullet points before writing long essays.",
            ],
        )

    # -----------------------------------------------------------------
    # Helper Fallbacks when running offline / without API key
    # -----------------------------------------------------------------
    def _extract_fallback_topics(self, title: str, text: str) -> List[str]:
        lines = [ln.strip(" #-*\t") for ln in (text or "").splitlines() if ln.strip()]
        candidates = []
        for ln in lines:
            if 4 <= len(ln) <= 65 and not ln.endswith("."):
                candidates.append(ln)
            if len(candidates) >= 6:
                break
        if len(candidates) < 4:
            candidates = [
                f"{title} Core Foundations",
                "Architectural Principles & Design",
                "Key Algorithms & Mechanisms",
                "Performance & Trade-off Analysis",
                "Practical Applications & Case Studies",
            ]
        return candidates[:6]

    def _fallback_quiz(
        self,
        title: str,
        text: str,
        num_questions: int,
        difficulty: str,
        focus_topic: Optional[str],
    ) -> QuizGeneratedSchema:
        topics = self._extract_fallback_topics(title, text)
        sentences = [
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+", (text or "").strip())
            if len(s.strip()) > 35
        ]
        questions = []
        for i in range(1, num_questions + 1):
            topic = focus_topic or topics[(i - 1) % len(topics)]
            ref_sentence = (
                sentences[(i - 1) % len(sentences)]
                if sentences
                else f"{topic} establishes the primary structural framework in {title}."
            )
            questions.append(
                {
                    "id": i,
                    "topic": topic,
                    "question": f"Regarding '{topic}' in {title}, which statement best reflects the core principle described in the study material?",
                    "options": [
                        ref_sentence[:140],
                        f"It eliminates the need for state management across all layers of {topic}.",
                        f"It restricts {topic} exclusively to synchronous single-threaded execution.",
                        f"It deprecates standard verification protocols in {title}.",
                    ],
                    "correct_option_index": 0,
                    "explanation": f"Directly supported by the study notes: \"{ref_sentence[:200]}\"",
                    "difficulty": difficulty if difficulty in ("easy", "medium", "hard") else "medium",
                }
            )
        return QuizGeneratedSchema(
            title=f"{title} — Interactive Flash Quiz",
            key_topics_covered=topics[:5],
            questions=questions,
        )

    def _fallback_mock_exam(
        self,
        title: str,
        text: str,
        duration_minutes: int,
        difficulty: str,
        mcq_count: int,
        short_answer_count: int,
        essay_count: int,
    ) -> MockExamGeneratedSchema:
        topics = self._extract_fallback_topics(title, text)
        quiz_fb = self._fallback_quiz(title, text, mcq_count, difficulty, None)
        mcqs = [
            {
                "question_number": idx + 1,
                "question": q.question,
                "options": q.options,
                "correct_option_index": q.correct_option_index,
                "marks": 2,
                "explanation": q.explanation,
            }
            for idx, q in enumerate(quiz_fb.questions)
        ]

        short_items = []
        for idx in range(short_answer_count):
            topic = topics[idx % len(topics)]
            q_num = mcq_count + idx + 1
            short_items.append(
                {
                    "question_number": q_num,
                    "question": f"Explain the core mechanism of '{topic}' and analyze two practical trade-offs discussed in {title}.",
                    "marks": 5,
                    "expected_keywords": [topic.split()[0], "architecture", "trade-off", "efficiency"],
                    "sample_answer": (
                        f"{topic} coordinates key abstractions in {title} by balancing throughput, reliability, "
                        f"and modularity across system components."
                    ),
                    "rubric": [
                        {"criterion": f"Accurate definition and role of {topic}", "points": 2},
                        {"criterion": "Analysis of two concrete trade-offs or constraints", "points": 2},
                        {"criterion": "Clarity and domain terminology", "points": 1},
                    ],
                }
            )

        essay_items = []
        for idx in range(essay_count):
            topic_a = topics[0]
            topic_b = topics[min(1, len(topics) - 1)]
            q_num = mcq_count + short_answer_count + idx + 1
            essay_items.append(
                {
                    "question_number": q_num,
                    "question": (
                        f"Critically evaluate how '{topic_a}' interacts with '{topic_b}' within {title}. "
                        f"Design an end-to-end workflow and justify your architectural decisions."
                    ),
                    "marks": 15,
                    "thesis_guidance": (
                        f"A top-scoring essay will synthesize {topic_a} and {topic_b}, contrasting theoretical "
                        f"guarantees with real-world implementation constraints."
                    ),
                    "sample_outline": [
                        f"1. Foundational overview of {topic_a} and {topic_b}",
                        "2. Architectural interaction & data flow pipeline",
                        "3. Failure modes, edge cases, and mitigation strategies",
                        "4. Critical synthesis and conclusion",
                    ],
                    "rubric": [
                        {"criterion": "Thesis clarity and conceptual synthesis", "points": 4},
                        {"criterion": "Technical depth on architectural interactions", "points": 5},
                        {"criterion": "Evaluation of failure modes and mitigations", "points": 4},
                        {"criterion": "Cohesive structure and academic rigor", "points": 2},
                    ],
                }
            )

        total_marks = (mcq_count * 2) + (short_answer_count * 5) + (essay_count * 15)
        return MockExamGeneratedSchema(
            exam_title=f"{title} — Comprehensive Mock Examination",
            instructions=[
                "Answer all questions across Section A (Multiple Choice), Section B (Short Answer), and Section C (Long Essay).",
                "Manage your time carefully according to the marks allocated per section.",
                "Short-answer and essay responses are evaluated against explicit multi-point rubrics.",
            ],
            duration_minutes=duration_minutes,
            total_marks=total_marks,
            multiple_choice_section=mcqs,
            short_answer_section=short_items,
            long_essay_section=essay_items,
        )

    def _fallback_mindmap(
        self, title: str, text: str, focus_topic: Optional[str]
    ) -> MindmapGeneratedSchema:
        topics = self._extract_fallback_topics(title, text)
        root_label = self._sanitize_mermaid_label(focus_topic or title)
        children = []
        for idx, t in enumerate(topics[:5], start=1):
            clean_t = self._sanitize_mermaid_label(t)
            children.append(
                MindmapNodeSchema(
                    id=f"branch_{idx}",
                    label=clean_t,
                    summary=f"Core syllabus pillar covering {clean_t} concepts and applications.",
                    children=[
                        MindmapNodeSchema(
                            id=f"branch_{idx}_1",
                            label=f"{clean_t[:22]} Principles",
                            summary=f"Foundational theory and definitions for {clean_t}.",
                            children=[],
                        ),
                        MindmapNodeSchema(
                            id=f"branch_{idx}_2",
                            label=f"{clean_t[:22]} Practice",
                            summary=f"Applied workflows, patterns, and problem solving in {clean_t}.",
                            children=[],
                        ),
                    ],
                )
            )
        root_node = MindmapNodeSchema(
            id="root",
            label=root_label,
            summary=f"Central concept map for {title}.",
            children=children,
        )
        return MindmapGeneratedSchema(
            title=f"{title} — Concept Mindmap",
            root_topic=root_label,
            mermaid_syntax=self.build_mermaid_from_tree(root_node),
            markmap_markdown=self.build_markmap_from_tree(root_node),
            root_node=root_node,
        )


ai_service = AIService()
