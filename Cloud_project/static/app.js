// StudyFlow AI — Frontend Controller
mermaid.initialize({
  startOnLoad: false,
  theme: 'dark',
  securityLevel: 'loose',
  fontFamily: 'Inter, sans-serif',
});

const state = {
  documents: [],
  quizzes: [],
  activeQuiz: null,
  quizAnswers: {},
  quizStartTime: null,
  exams: [],
  activeExam: null,
  examTimerInterval: null,
  examSecondsRemaining: 0,
  examTotalSeconds: 0,
  mindmaps: [],
  activeMindmap: null,
};

function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const colors = {
    info: 'bg-indigo-600/95 border-indigo-400 text-white',
    success: 'bg-emerald-600/95 border-emerald-400 text-white',
    error: 'bg-rose-600/95 border-rose-400 text-white',
  };
  const el = document.createElement('div');
  el.className = `px-4 py-3 rounded-xl border shadow-xl text-xs font-medium transition-all ${colors[type] || colors.info}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function switchTab(tabName) {
  document.querySelectorAll('.tab-panel').forEach((p) => p.classList.add('hidden'));
  document.getElementById(`tab-${tabName}`).classList.remove('hidden');

  document.querySelectorAll('.tab-btn').forEach((b) => {
    b.classList.remove('border-indigo-500', 'text-indigo-400');
    b.classList.add('border-transparent', 'text-slate-400');
  });
  const activeBtn = document.getElementById(`tab-btn-${tabName}`);
  if (activeBtn) {
    activeBtn.classList.remove('border-transparent', 'text-slate-400');
    activeBtn.classList.add('border-indigo-500', 'text-indigo-400');
  }

  if (tabName === 'analytics') loadAnalytics();
}

// =====================================================================
// Cloud Health Status
// =====================================================================
async function checkHealth() {
  try {
    const res = await fetch('/health');
    const data = await res.json();
    const s = data.services || {};
    document.getElementById('badge-s3').innerHTML = `
      <span class="w-2 h-2 rounded-full ${s.object_storage === 'cloudflare_r2' ? 'bg-emerald-400' : 'bg-cyan-400'}"></span>
      ${s.object_storage === 'cloudflare_r2' ? 'Cloudflare R2' : 'S3 (Local Dev)'}
    `;
    document.getElementById('badge-ai').innerHTML = `
      <span class="w-2 h-2 rounded-full ${s.google_gemini_api === 'live' ? 'bg-emerald-400' : 'bg-violet-400'}"></span>
      ${s.google_gemini_api === 'live' ? 'Gemini 2.5 Live' : 'Gemini Ready'}
    `;
    document.getElementById('badge-redis').innerHTML = `
      <span class="w-2 h-2 rounded-full ${s.redis_worker_queue === 'connected' ? 'bg-emerald-400' : 'bg-amber-400'}"></span>
      ${s.redis_worker_queue === 'connected' ? 'Redis RQ Live' : 'RQ Sync Mode'}
    `;
  } catch (err) {
    console.warn('Health check failed:', err);
  }
}

// =====================================================================
// 1. Document Ingestion & Syllabus Library
// =====================================================================
async function loadDocuments() {
  const res = await fetch('/documents');
  state.documents = await res.json();
  renderDocumentsList();
  populateDocumentSelects();
}

function populateDocumentSelects(forceDefaultTitle = null) {
  const datalist = document.getElementById('documents-datalist');
  if (datalist) {
    datalist.innerHTML = state.documents
      .map((d) => `<option value="${escapeHtml(d.title)}">${escapeHtml(d.filename)}</option>`)
      .join('');
  }

  document.querySelectorAll('.doc-input').forEach((inp) => {
    if (forceDefaultTitle) {
      inp.value = forceDefaultTitle;
    } else if (!inp.value.trim() && state.documents.length > 0) {
      inp.value = state.documents[0].title;
    }
  });
}

function resolveSourceInput(rawText) {
  const cleaned = (rawText || '').trim();
  const matched = state.documents.find(
    (d) => d.title.toLowerCase() === cleaned.toLowerCase() || d.filename.toLowerCase() === cleaned.toLowerCase()
  );
  if (matched) {
    return { document_id: matched.id, custom_subject: matched.title };
  }
  return { document_id: null, custom_subject: cleaned };
}

function renderDocumentsList() {
  const container = document.getElementById('documents-list');
  if (!state.documents.length) {
    container.innerHTML = `
      <div class="bg-slate-900/60 border border-slate-800 rounded-2xl p-8 text-center text-slate-400 text-sm">
        No study documents uploaded yet. Enter a preferred subject or upload a file on the left!
      </div>`;
    return;
  }

  container.innerHTML = state.documents
    .map((doc) => {
      const meta = doc.summary_metadata || {};
      const topics = meta.key_topics || [];
      const kb = Math.max(1, Math.round((doc.file_size_bytes || 0) / 1024));
      return `
      <div class="bg-slate-900/90 border border-slate-800 hover:border-slate-700 rounded-2xl p-5 space-y-3 transition">
        <div class="flex flex-wrap items-start justify-between gap-2">
          <div>
            <div class="flex items-center gap-2">
              <h3 class="font-semibold text-white text-base">${escapeHtml(doc.title)}</h3>
              <span class="text-[11px] px-2 py-0.5 rounded-md bg-slate-800 text-cyan-300 border border-slate-700 font-mono">
                ${escapeHtml(doc.storage_backend.toUpperCase())}: ${kb} KB
              </span>
            </div>
            <p class="text-xs text-slate-400 mt-0.5">${escapeHtml(doc.filename)} • Est. ${meta.estimated_study_hours || 4}h study time</p>
          </div>
          <button onclick="deleteDocument(${doc.id})" class="text-xs text-slate-500 hover:text-rose-400 transition">Delete</button>
        </div>

        ${meta.executive_summary ? `<p class="text-xs text-slate-300 leading-relaxed">${escapeHtml(meta.executive_summary)}</p>` : ''}

        ${
          topics.length
            ? `<div class="flex flex-wrap gap-1.5">
                ${topics
                  .map(
                    (t) =>
                      `<span class="text-[11px] px-2.5 py-0.5 rounded-full bg-indigo-500/10 text-indigo-300 border border-indigo-500/20">${escapeHtml(t)}</span>`
                  )
                  .join('')}
               </div>`
            : ''
        }

        <div class="pt-2 border-t border-slate-800/80 flex flex-wrap items-center justify-between gap-2">
          <span class="text-[11px] text-slate-500 font-mono truncate max-w-xs" title="${escapeHtml(doc.s3_key)}">
            Key: ${escapeHtml(doc.s3_key)}
          </span>
          <div class="flex items-center gap-2">
            <button onclick="quickLaunch('quizzes', ${doc.id})" class="text-xs px-3 py-1.5 rounded-lg bg-violet-600/20 hover:bg-violet-600/30 text-violet-300 border border-violet-500/30 transition">
              + Flash Quiz
            </button>
            <button onclick="quickLaunch('exams', ${doc.id})" class="text-xs px-3 py-1.5 rounded-lg bg-cyan-600/20 hover:bg-cyan-600/30 text-cyan-300 border border-cyan-500/30 transition">
              + Mock Exam
            </button>
            <button onclick="quickLaunch('mindmaps', ${doc.id})" class="text-xs px-3 py-1.5 rounded-lg bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/30 transition">
              + Mindmap
            </button>
          </div>
        </div>
      </div>`;
    })
    .join('');
}

function quickLaunch(tab, docId) {
  switchTab(tab);
  const map = {
    quizzes: 'quiz-doc-input',
    exams: 'exam-doc-input',
    mindmaps: 'mindmap-doc-input',
  };
  const doc = state.documents.find((d) => d.id === docId);
  const inp = document.getElementById(map[tab]);
  if (inp && doc) inp.value = doc.title;
}

async function deleteDocument(docId) {
  await fetch(`/documents/${docId}`, { method: 'DELETE' });
  showToast('Document removed from library', 'info');
  await loadDocuments();
}

document.getElementById('subject-seed-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const subjectInput = document.getElementById('seed-subject-input');
  const notesInput = document.getElementById('seed-subject-notes');
  const replaceCheckbox = document.getElementById('seed-replace-checkbox');
  const btn = document.getElementById('seed-subject-btn');

  const subject = subjectInput.value.trim();
  if (!subject) return;

  btn.disabled = true;
  btn.textContent = 'Synthesizing Subject Syllabus with Gemini...';

  try {
    const res = await fetch('/seed-subject', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        subject,
        custom_notes: notesInput.value.trim() || null,
        replace_existing_demo: replaceCheckbox.checked,
      }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to set subject');
    }
    const createdDoc = await res.json();
    showToast(`Active subject set to "${createdDoc.title}"!`, 'success');
    subjectInput.value = '';
    notesInput.value = '';
    await loadDocuments();
    populateDocumentSelects(createdDoc.title);
  } catch (err) {
    showToast(err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Set Active Subject & Generate Syllabus';
  }
});

document.getElementById('upload-file').addEventListener('change', (e) => {
  const file = e.target.files[0];
  if (file) {
    document.getElementById('upload-file-label').textContent = `${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
  }
});

document.getElementById('upload-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const fileInput = document.getElementById('upload-file');
  if (!fileInput.files.length) return;

  const btn = document.getElementById('upload-submit-btn');
  btn.disabled = true;
  btn.textContent = 'Uploading to S3/R2 & Extracting Syllabus...';

  try {
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    const title = document.getElementById('upload-title').value.trim();
    if (title) formData.append('title', title);

    const res = await fetch('/upload', { method: 'POST', body: formData });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Upload failed');
    }
    showToast('Document ingested & analyzed by Gemini!', 'success');
    document.getElementById('upload-form').reset();
    document.getElementById('upload-file-label').textContent = 'Click to choose a file or drag & drop';
    await loadDocuments();
  } catch (err) {
    showToast(err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Upload to Object Storage & Extract Syllabus';
  }
});

// =====================================================================
// 2. Interactive Flash Quizzes
// =====================================================================
async function loadQuizzes() {
  const res = await fetch('/quizzes');
  state.quizzes = await res.json();
  const list = document.getElementById('saved-quizzes-list');
  if (!state.quizzes.length) {
    list.innerHTML = `<p class="text-xs text-slate-500">No quizzes generated yet.</p>`;
    return;
  }
  list.innerHTML = state.quizzes
    .map(
      (q) => `
      <button onclick="openQuiz(${q.id})" class="w-full text-left px-3 py-2 rounded-xl bg-slate-950 hover:bg-slate-800 border border-slate-800 transition flex items-center justify-between">
        <span class="text-xs font-medium text-slate-200 truncate">${escapeHtml(q.title)}</span>
        <span class="text-[10px] px-2 py-0.5 rounded bg-violet-500/20 text-violet-300 uppercase">${escapeHtml(q.difficulty)}</span>
      </button>`
    )
    .join('');
}

function openQuiz(quizId) {
  const quiz = state.quizzes.find((q) => q.id === quizId);
  if (!quiz) return;
  state.activeQuiz = quiz;
  state.quizAnswers = {};
  state.quizStartTime = Date.now();
  renderActiveQuiz();
}

function renderActiveQuiz() {
  const stage = document.getElementById('quiz-stage');
  const quiz = state.activeQuiz;
  if (!quiz) return;

  const questions = quiz.questions || [];
  const answeredCount = Object.keys(state.quizAnswers).length;
  let correctCount = 0;
  questions.forEach((q, idx) => {
    if (state.quizAnswers[idx] === q.correct_option_index) correctCount++;
  });

  stage.innerHTML = `
    <div class="space-y-6">
      <div class="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 pb-4">
        <div>
          <span class="text-xs font-semibold uppercase tracking-wider text-violet-400">Interactive Flash-Quiz Mode</span>
          <h3 class="text-xl font-bold text-white">${escapeHtml(quiz.title)}</h3>
        </div>
        <div class="flex items-center gap-3">
          <div class="px-3.5 py-1.5 rounded-xl bg-slate-950 border border-slate-800 text-xs">
            Progress: <span class="font-bold text-white">${answeredCount}/${questions.length}</span>
          </div>
          <div class="px-3.5 py-1.5 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-xs text-emerald-300">
            Live Score: <span class="font-bold">${correctCount}/${questions.length}</span>
          </div>
        </div>
      </div>

      <div class="space-y-5">
        ${questions
          .map((q, qIdx) => {
            const selected = state.quizAnswers[qIdx];
            const isAnswered = selected !== undefined;
            return `
            <div class="p-5 rounded-2xl bg-slate-950/90 border border-slate-800 space-y-3">
              <div class="flex items-center justify-between text-xs text-slate-400">
                <span class="font-mono text-indigo-400">Question ${qIdx + 1} • ${escapeHtml(q.topic || 'Core Concept')}</span>
                <span class="uppercase text-[11px] px-2 py-0.5 rounded bg-slate-900 border border-slate-800">${escapeHtml(q.difficulty || 'medium')}</span>
              </div>
              <p class="text-sm font-medium text-white leading-relaxed">${escapeHtml(q.question)}</p>
              <div class="grid grid-cols-1 gap-2 pt-1">
                ${(q.options || [])
                  .map((opt, optIdx) => {
                    let btnClass = 'bg-slate-900 hover:bg-slate-800 border-slate-800 text-slate-200';
                    if (isAnswered) {
                      if (optIdx === q.correct_option_index) {
                        btnClass = 'bg-emerald-500/20 border-emerald-500 text-emerald-200 font-medium';
                      } else if (optIdx === selected) {
                        btnClass = 'bg-rose-500/20 border-rose-500 text-rose-200';
                      } else {
                        btnClass = 'bg-slate-900/40 border-slate-800/60 text-slate-500';
                      }
                    }
                    return `
                    <button onclick="selectQuizOption(${qIdx}, ${optIdx})" ${isAnswered ? 'disabled' : ''}
                      class="text-left px-4 py-2.5 rounded-xl border text-xs transition flex items-center justify-between ${btnClass}">
                      <span><strong class="mr-2">${String.fromCharCode(65 + optIdx)}.</strong> ${escapeHtml(opt)}</span>
                    </button>`;
                  })
                  .join('')}
              </div>
              ${
                isAnswered
                  ? `<div class="mt-3 p-3.5 rounded-xl bg-indigo-950/40 border border-indigo-500/30 text-xs text-indigo-200 leading-relaxed">
                      <strong class="text-indigo-300">Explanation:</strong> ${escapeHtml(q.explanation)}
                     </div>`
                  : ''
              }
            </div>`;
          })
          .join('')}
      </div>

      ${
        answeredCount === questions.length && questions.length > 0
          ? `<div class="p-5 rounded-2xl bg-gradient-to-r from-indigo-900/40 to-violet-900/40 border border-indigo-500/40 flex flex-wrap items-center justify-between gap-4">
              <div>
                <h4 class="text-base font-bold text-white">Quiz Complete! Final Score: ${correctCount} / ${questions.length} (${Math.round((correctCount / questions.length) * 100)}%)</h4>
                <p class="text-xs text-slate-300">Save this attempt to your PostgreSQL study history to track mastery over time.</p>
              </div>
              <button onclick="saveQuizAttempt()" class="px-5 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shadow-lg">
                Save Attempt to PostgreSQL
              </button>
             </div>`
          : ''
      }
    </div>
  `;
}

function selectQuizOption(qIdx, optIdx) {
  if (state.quizAnswers[qIdx] !== undefined) return;
  state.quizAnswers[qIdx] = optIdx;
  renderActiveQuiz();
}

async function saveQuizAttempt() {
  const quiz = state.activeQuiz;
  if (!quiz) return;
  const questions = quiz.questions || [];
  let correctCount = 0;
  questions.forEach((q, idx) => {
    if (state.quizAnswers[idx] === q.correct_option_index) correctCount++;
  });
  const elapsed = Math.max(1, Math.round((Date.now() - (state.quizStartTime || Date.now())) / 1000));

  const res = await fetch('/results', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      assessment_type: 'quiz',
      quiz_id: quiz.id,
      score: correctCount,
      max_score: questions.length,
      time_taken_seconds: elapsed,
      user_answers: state.quizAnswers,
    }),
  });
  if (res.ok) {
    showToast('Quiz score saved to PostgreSQL analytics!', 'success');
  }
}

document.getElementById('quiz-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('quiz-generate-btn');
  btn.disabled = true;
  btn.textContent = 'Generating with Gemini...';

  try {
    const source = resolveSourceInput(document.getElementById('quiz-doc-input').value);
    const payload = {
      document_id: source.document_id,
      custom_subject: source.custom_subject,
      num_questions: Number(document.getElementById('quiz-count').value),
      difficulty: document.getElementById('quiz-difficulty').value,
      focus_topic: document.getElementById('quiz-topic').value.trim() || null,
    };
    const res = await fetch('/generate-quiz', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to generate quiz');
    }
    const quiz = await res.json();
    await Promise.all([loadDocuments(), loadQuizzes()]);
    openQuiz(quiz.id);
    showToast('Interactive Flash Quiz ready!', 'success');
  } catch (err) {
    showToast(err.message || 'Failed to generate quiz', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Generate Interactive Quiz';
  }
});

// =====================================================================
// 3. Timed Mock Exams & AI Rubric Evaluation
// =====================================================================
async function loadMockExams() {
  const res = await fetch('/mock-exams');
  state.exams = await res.json();
  const list = document.getElementById('saved-exams-list');
  if (!state.exams.length) {
    list.innerHTML = `<p class="text-xs text-slate-500">No mock papers generated yet.</p>`;
    return;
  }
  list.innerHTML = state.exams
    .map(
      (ex) => `
      <button onclick="openMockExam(${ex.id})" class="w-full text-left px-3 py-2 rounded-xl bg-slate-950 hover:bg-slate-800 border border-slate-800 transition flex items-center justify-between">
        <span class="text-xs font-medium text-slate-200 truncate">${escapeHtml(ex.title)}</span>
        <span class="text-[10px] px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-300">${ex.total_marks}m • ${ex.duration_minutes}min</span>
      </button>`
    )
    .join('');
}

function openMockExam(examId) {
  const exam = state.exams.find((e) => e.id === examId);
  if (!exam) return;
  state.activeExam = exam;
  if (state.examTimerInterval) clearInterval(state.examTimerInterval);

  state.examTotalSeconds = (exam.duration_minutes || 30) * 60;
  state.examSecondsRemaining = state.examTotalSeconds;

  renderMockExamPaper();
  state.examTimerInterval = setInterval(() => {
    if (state.examSecondsRemaining > 0) {
      state.examSecondsRemaining--;
      const timerEl = document.getElementById('exam-countdown');
      if (timerEl) {
        const m = String(Math.floor(state.examSecondsRemaining / 60)).padStart(2, '0');
        const s = String(state.examSecondsRemaining % 60).padStart(2, '0');
        timerEl.textContent = `${m}:${s}`;
      }
    }
  }, 1000);
}

function renderMockExamPaper(gradingResult = null) {
  const stage = document.getElementById('exam-stage');
  const exam = state.activeExam;
  if (!exam) return;

  const p = exam.exam_payload || {};
  const mcqs = p.multiple_choice_section || [];
  const shorts = p.short_answer_section || [];
  const essays = p.long_essay_section || [];

  stage.innerHTML = `
    <div class="space-y-6">
      <!-- Exam Header & Countdown Timer -->
      <div class="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 pb-4">
        <div>
          <span class="text-xs font-semibold uppercase tracking-wider text-cyan-400">Timed University Mock Examination</span>
          <h3 class="text-xl font-bold text-white">${escapeHtml(exam.title)}</h3>
          <p class="text-xs text-slate-400 mt-0.5">Total Marks: ${exam.total_marks} • Difficulty: ${escapeHtml(exam.difficulty.toUpperCase())}</p>
        </div>
        <div class="flex items-center gap-3">
          <div class="px-4 py-2 rounded-xl bg-slate-950 border border-cyan-500/40 text-right">
            <span class="block text-[10px] uppercase tracking-wider text-slate-400">Time Remaining</span>
            <span id="exam-countdown" class="font-mono text-lg font-bold text-cyan-300">
              ${String(Math.floor(state.examSecondsRemaining / 60)).padStart(2, '0')}:${String(state.examSecondsRemaining % 60).padStart(2, '0')}
            </span>
          </div>
        </div>
      </div>

      ${
        gradingResult
          ? `<div class="p-5 rounded-2xl bg-emerald-950/40 border border-emerald-500/40 space-y-3">
              <div class="flex items-center justify-between">
                <h4 class="text-lg font-bold text-white">Official AI Rubric Evaluation: ${gradingResult.score} / ${gradingResult.max_score} (${gradingResult.percentage}%)</h4>
                <span class="text-xs px-3 py-1 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">Saved to PostgreSQL</span>
              </div>
              <div class="text-xs text-slate-300 space-y-1">
                <strong class="text-emerald-300">Study Recommendations:</strong>
                <ul class="list-disc list-inside space-y-1">
                  ${(gradingResult.grading_feedback?.overall_study_recommendations || [])
                    .map((r) => `<li>${escapeHtml(r)}</li>`)
                    .join('')}
                </ul>
              </div>
             </div>`
          : ''
      }

      <form id="mock-exam-submission-form" class="space-y-8">
        <!-- SECTION A: MULTIPLE CHOICE -->
        <div class="space-y-4">
          <h4 class="text-sm font-bold uppercase tracking-wider text-indigo-400 border-b border-slate-800 pb-2">
            Section A: Multiple Choice (${mcqs.length} Questions)
          </h4>
          ${mcqs
            .map(
              (q) => `
            <div class="p-4 rounded-xl bg-slate-950 border border-slate-800 space-y-2.5">
              <div class="flex justify-between text-xs text-slate-400">
                <span class="font-semibold text-slate-200">Q${q.question_number}. ${escapeHtml(q.question)}</span>
                <span class="text-cyan-400 font-mono shrink-0 ml-2">[${q.marks} Marks]</span>
              </div>
              <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-1">
                ${(q.options || [])
                  .map(
                    (opt, oIdx) => `
                  <label class="flex items-center gap-2.5 p-2.5 rounded-lg bg-slate-900 hover:bg-slate-800 border border-slate-800 cursor-pointer text-xs text-slate-200">
                    <input type="radio" name="mcq_${q.question_number}" value="${oIdx}" class="text-cyan-500 focus:ring-0" />
                    <span>${escapeHtml(opt)}</span>
                  </label>`
                  )
                  .join('')}
              </div>
            </div>`
            )
            .join('')}
        </div>

        <!-- SECTION B: SHORT ANSWER -->
        <div class="space-y-4">
          <h4 class="text-sm font-bold uppercase tracking-wider text-violet-400 border-b border-slate-800 pb-2">
            Section B: Short Answer with Analytical Rubrics (${shorts.length} Questions)
          </h4>
          ${shorts
            .map(
              (q) => `
            <div class="p-4 rounded-xl bg-slate-950 border border-slate-800 space-y-3">
              <div class="flex justify-between text-xs">
                <span class="font-semibold text-slate-200">Q${q.question_number}. ${escapeHtml(q.question)}</span>
                <span class="text-violet-400 font-mono shrink-0 ml-2">[${q.marks} Marks]</span>
              </div>
              <textarea name="short_${q.question_number}" rows="3" placeholder="Write your concise technical answer here..."
                class="w-full rounded-xl bg-slate-900 border border-slate-800 p-3 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-violet-500"></textarea>
              <details class="text-xs text-slate-400 bg-slate-900/60 rounded-lg p-2.5 border border-slate-800/80">
                <summary class="cursor-pointer font-medium text-violet-300">Inspect Official Grading Rubric &amp; Expected Keywords</summary>
                <div class="mt-2 space-y-1.5 pl-2">
                  <p><strong>Expected Keywords:</strong> ${(q.expected_keywords || []).map(escapeHtml).join(', ')}</p>
                  <p><strong>Sample Answer:</strong> ${escapeHtml(q.sample_answer)}</p>
                  <ul class="list-disc list-inside text-slate-300">
                    ${(q.rubric || []).map((r) => `<li>${escapeHtml(r.criterion)} (${r.points} pts)</li>`).join('')}
                  </ul>
                </div>
              </details>
            </div>`
            )
            .join('')}
        </div>

        <!-- SECTION C: LONG ESSAY -->
        <div class="space-y-4">
          <h4 class="text-sm font-bold uppercase tracking-wider text-emerald-400 border-b border-slate-800 pb-2">
            Section C: Long Essay &amp; Architectural Synthesis (${essays.length} Question)
          </h4>
          ${essays
            .map(
              (q) => `
            <div class="p-4 rounded-xl bg-slate-950 border border-slate-800 space-y-3">
              <div class="flex justify-between text-xs">
                <span class="font-semibold text-slate-200">Q${q.question_number}. ${escapeHtml(q.question)}</span>
                <span class="text-emerald-400 font-mono shrink-0 ml-2">[${q.marks} Marks]</span>
              </div>
              <textarea name="essay_${q.question_number}" rows="6" placeholder="Develop your structured essay response..."
                class="w-full rounded-xl bg-slate-900 border border-slate-800 p-3 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-emerald-500"></textarea>
              <details class="text-xs text-slate-400 bg-slate-900/60 rounded-lg p-2.5 border border-slate-800/80">
                <summary class="cursor-pointer font-medium text-emerald-300">Inspect Thesis Guidance &amp; 15-Point Rubric</summary>
                <div class="mt-2 space-y-1.5 pl-2">
                  <p><strong>Thesis Guidance:</strong> ${escapeHtml(q.thesis_guidance)}</p>
                  <ul class="list-disc list-inside text-slate-300">
                    ${(q.rubric || []).map((r) => `<li>${escapeHtml(r.criterion)} (${r.points} pts)</li>`).join('')}
                  </ul>
                </div>
              </details>
            </div>`
            )
            .join('')}
        </div>

        <button type="submit" id="grade-exam-submit-btn"
          class="w-full py-3 px-5 rounded-xl bg-gradient-to-r from-cyan-600 to-indigo-600 hover:from-cyan-500 hover:to-indigo-500 text-white font-semibold text-sm shadow-xl transition">
          Submit Mock Paper for AI Rubric Grading
        </button>
      </form>
    </div>
  `;

  document.getElementById('mock-exam-submission-form').addEventListener('submit', submitMockExamForGrading);
}

async function submitMockExamForGrading(e) {
  e.preventDefault();
  const exam = state.activeExam;
  if (!exam) return;

  const btn = document.getElementById('grade-exam-submit-btn');
  btn.disabled = true;
  btn.textContent = 'Evaluating Answers Against Rubrics...';

  const formData = new FormData(e.target);
  const mcqAnswers = {};
  const shortAnswers = {};
  const essayAnswers = {};

  for (const [key, val] of formData.entries()) {
    if (key.startsWith('mcq_')) mcqAnswers[key.replace('mcq_', '')] = Number(val);
    if (key.startsWith('short_')) shortAnswers[key.replace('short_', '')] = String(val);
    if (key.startsWith('essay_')) essayAnswers[key.replace('essay_', '')] = String(val);
  }

  const timeSpent = Math.max(1, state.examTotalSeconds - state.examSecondsRemaining);
  if (state.examTimerInterval) clearInterval(state.examTimerInterval);

  try {
    const res = await fetch('/grade-exam', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mock_exam_id: exam.id,
        mcq_answers: mcqAnswers,
        short_answers: shortAnswers,
        essay_answers: essayAnswers,
        time_taken_seconds: timeSpent,
      }),
    });
    const result = await res.json();
    showToast(`Mock Exam Graded: ${result.percentage}%!`, 'success');
    renderMockExamPaper(result);
  } catch (err) {
    showToast('Error grading exam', 'error');
  }
}

document.getElementById('exam-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('exam-generate-btn');
  btn.disabled = true;
  btn.textContent = 'Generating Timed Paper & Rubrics...';

  try {
    const source = resolveSourceInput(document.getElementById('exam-doc-input').value);
    const payload = {
      document_id: source.document_id,
      custom_subject: source.custom_subject,
      duration_minutes: Number(document.getElementById('exam-duration').value),
      difficulty: document.getElementById('exam-difficulty').value,
      mcq_count: Number(document.getElementById('exam-mcq-count').value),
      short_answer_count: Number(document.getElementById('exam-short-count').value),
      essay_count: Number(document.getElementById('exam-essay-count').value),
    };
    const res = await fetch('/generate-test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to generate mock exam');
    }
    const exam = await res.json();
    await Promise.all([loadDocuments(), loadMockExams()]);
    openMockExam(exam.id);
    showToast('Timed Mock Examination generated!', 'success');
  } catch (err) {
    showToast(err.message || 'Failed to generate mock exam', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Generate Timed Mock Paper';
  }
});

// =====================================================================
// 4. Interactive Mindmaps (Mermaid.js + Hierarchical Tree Explorer)
// =====================================================================
async function loadMindmaps() {
  const res = await fetch('/mindmaps');
  state.mindmaps = await res.json();
  const list = document.getElementById('saved-mindmaps-list');
  if (!state.mindmaps.length) {
    list.innerHTML = `<p class="text-xs text-slate-500">No mindmaps generated yet.</p>`;
    return;
  }
  list.innerHTML = state.mindmaps
    .map(
      (m) => `
      <button onclick="openMindmap(${m.id})" class="w-full text-left px-3 py-2 rounded-xl bg-slate-950 hover:bg-slate-800 border border-slate-800 transition flex items-center justify-between">
        <span class="text-xs font-medium text-slate-200 truncate">${escapeHtml(m.title)}</span>
        <span class="text-[10px] px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300">Mermaid</span>
      </button>`
    )
    .join('');
}

async function openMindmap(mindmapId) {
  const mm = state.mindmaps.find((m) => m.id === mindmapId);
  if (!mm) return;
  state.activeMindmap = mm;
  const stage = document.getElementById('mindmap-stage');

  stage.innerHTML = `
    <div class="space-y-6">
      <div class="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 pb-4">
        <div>
          <span class="text-xs font-semibold uppercase tracking-wider text-emerald-400">Hierarchical Knowledge Graph</span>
          <h3 class="text-xl font-bold text-white">${escapeHtml(mm.title)}</h3>
        </div>
        <button onclick="downloadMindmapSvg()" class="text-xs px-3.5 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700">
          Download SVG
        </button>
      </div>

      <!-- Mermaid Visual Diagram -->
      <div id="mermaid-render-box" class="p-6 rounded-2xl bg-slate-950 border border-slate-800 overflow-x-auto flex justify-center min-h-[280px]"></div>

      <!-- Interactive Concept Node Cards -->
      <div>
        <h4 class="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-3">Interactive Concept Breakdown</h4>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
          ${((mm.root_node && mm.root_node.children) || [])
            .map(
              (branch) => `
              <div class="p-4 rounded-xl bg-slate-950 border border-slate-800 space-y-2">
                <h5 class="text-sm font-semibold text-emerald-300">${escapeHtml(branch.label)}</h5>
                <p class="text-xs text-slate-400">${escapeHtml(branch.summary)}</p>
                <div class="flex flex-wrap gap-1.5 pt-1">
                  ${(branch.children || [])
                    .map(
                      (leaf) =>
                        `<span class="text-[11px] px-2 py-0.5 rounded bg-slate-900 border border-slate-800 text-slate-300" title="${escapeHtml(leaf.summary)}">${escapeHtml(leaf.label)}</span>`
                    )
                    .join('')}
                </div>
              </div>`
            )
            .join('')}
        </div>
      </div>

      <details class="text-xs text-slate-400 bg-slate-950 rounded-xl p-3 border border-slate-800">
        <summary class="cursor-pointer font-medium text-slate-300">View Raw Mermaid.js &amp; Markmap Syntax</summary>
        <pre class="mt-3 p-3 rounded-lg bg-slate-900 text-emerald-300 overflow-x-auto text-[11px]">${escapeHtml(mm.mermaid_syntax)}</pre>
      </details>
    </div>
  `;

  try {
    const renderBox = document.getElementById('mermaid-render-box');
    const uniqueId = `mermaid_svg_${Date.now()}`;
    const { svg } = await mermaid.render(uniqueId, mm.mermaid_syntax);
    renderBox.innerHTML = svg;
  } catch (err) {
    document.getElementById('mermaid-render-box').innerHTML = `<pre class="text-xs text-emerald-300">${escapeHtml(mm.mermaid_syntax)}</pre>`;
  }
}

function downloadMindmapSvg() {
  const svgEl = document.querySelector('#mermaid-render-box svg');
  if (!svgEl) return;
  const blob = new Blob([svgEl.outerHTML], { type: 'image/svg+xml;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${(state.activeMindmap?.root_topic || 'mindmap').replace(/\s+/g, '_')}.svg`;
  a.click();
}

document.getElementById('mindmap-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('mindmap-generate-btn');
  btn.disabled = true;
  btn.textContent = 'Building Concept Hierarchy...';

  try {
    const source = resolveSourceInput(document.getElementById('mindmap-doc-input').value);
    const payload = {
      document_id: source.document_id,
      custom_subject: source.custom_subject,
      focus_topic: document.getElementById('mindmap-topic').value.trim() || null,
      max_depth: 3,
    };
    const res = await fetch('/generate-mindmap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to generate mindmap');
    }
    const mm = await res.json();
    await Promise.all([loadDocuments(), loadMindmaps()]);
    openMindmap(mm.id);
    showToast('Interactive Mindmap generated!', 'success');
  } catch (err) {
    showToast(err.message || 'Failed to generate mindmap', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Generate Concept Mindmap';
  }
});

// =====================================================================
// 5. Analytics & History (PostgreSQL)
// =====================================================================
async function loadAnalytics() {
  const res = await fetch('/results');
  const data = await res.json();
  const s = data.summary || {};

  const kpis = [
    { label: 'Documents Ingested', value: s.total_documents ?? 0, color: 'text-indigo-400' },
    { label: 'Flash Quizzes', value: s.total_quizzes ?? 0, color: 'text-violet-400' },
    { label: 'Mock Exams', value: s.total_mock_exams ?? 0, color: 'text-cyan-400' },
    { label: 'Mindmaps Built', value: s.total_mindmaps ?? 0, color: 'text-emerald-400' },
    { label: 'Total Attempts', value: s.total_attempts ?? 0, color: 'text-amber-400' },
    { label: 'Average Mastery', value: `${s.average_score_percentage ?? 0}%`, color: 'text-rose-400' },
  ];

  document.getElementById('analytics-kpis').innerHTML = kpis
    .map(
      (k) => `
      <div class="bg-slate-900/90 border border-slate-800 rounded-2xl p-4">
        <span class="text-[11px] uppercase tracking-wider text-slate-400 block">${escapeHtml(k.label)}</span>
        <span class="text-2xl font-bold ${k.color} mt-1 block">${escapeHtml(k.value)}</span>
      </div>`
    )
    .join('');

  const historyList = document.getElementById('analytics-history-list');
  const history = data.history || [];
  if (!history.length) {
    historyList.innerHTML = `<p class="text-xs text-slate-500 py-6 text-center">No completed quiz or mock exam attempts recorded yet.</p>`;
    return;
  }

  historyList.innerHTML = history
    .map(
      (h) => `
      <div class="p-4 rounded-xl bg-slate-950 border border-slate-800 flex flex-wrap items-center justify-between gap-4">
        <div>
          <div class="flex items-center gap-2">
            <span class="text-[10px] uppercase font-semibold px-2 py-0.5 rounded ${
              h.assessment_type === 'mock_exam'
                ? 'bg-cyan-500/20 text-cyan-300'
                : 'bg-violet-500/20 text-violet-300'
            }">${escapeHtml(h.assessment_type.replace('_', ' '))}</span>
            <h4 class="text-sm font-semibold text-white">${escapeHtml(h.title)}</h4>
          </div>
          <p class="text-xs text-slate-400 mt-1">
            Completed ${new Date(h.completed_at).toLocaleString()} • Time spent: ${h.time_taken_seconds}s
          </p>
        </div>
        <div class="text-right">
          <span class="text-lg font-bold ${h.percentage >= 75 ? 'text-emerald-400' : 'text-amber-400'}">
            ${h.score} / ${h.max_score} (${h.percentage}%)
          </span>
        </div>
      </div>`
    )
    .join('');
}

// Initialize Dashboard on Page Load
window.addEventListener('DOMContentLoaded', async () => {
  await Promise.all([checkHealth(), loadDocuments(), loadQuizzes(), loadMockExams(), loadMindmaps()]);
});
