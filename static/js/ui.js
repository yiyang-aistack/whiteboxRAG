/**
 * static/index.html - general interactions
 *
 * Content: showToast notifications, HTML escaping helper, intent feedback and answer feedback
 *       (missing recall / wrong answer / citation issue) dialogs, and ESC closing all dialogs.
 * Split out of index.html's inline <script>.
 */

// ==================== Toast notifications ====================
function showToast(type, message) {
    const toast = document.getElementById('toast');
    const icon = document.getElementById('toastIcon');
    const msg = document.getElementById('toastMessage');

    const icons = {
        success: '<svg class="w-5 h-5 text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>',
        error: '<svg class="w-5 h-5 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>',
        warning: '<svg class="w-5 h-5 text-yellow-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>',
        info: '<svg class="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>'
    };

    icon.innerHTML = icons[type] || icons.info;
    msg.textContent = message;
    toast.classList.remove('hidden');

    setTimeout(() => {
        toast.classList.add('hidden');
    }, 3000);
}

// ==================== Helper functions ====================
function lescapeHtm(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ==================== Intent feedback ====================
function openIntentFeedback() {
    document.getElementById('intentFeedbackDialog').classList.remove('hidden');
    document.getElementById('correctedIntentInput').focus();
}

function closeIntentFeedback() {
    document.getElementById('intentFeedbackDialog').classList.add('hidden');
    document.getElementById('correctedIntentInput').value = '';
    document.getElementById('correctionReasonInput').value = '';
    document.getElementById('currentIntentDisplay').innerHTML = '';
}

async function submitIntentFeedback() {
    const correctedIntent = document.getElementById('correctedIntentInput').value.trim();
    const reason = document.getElementById('correctionReasonInput').value.trim();

    if (!correctedIntent) {
        showToast('warning', t('intent_feedback.invalid_intent'));
        return;
    }

    try {
        const data = await fetchJSON('/api/chat/feedback', {
            method: 'POST',
            body: JSON.stringify({
                trace_id: state.currentTraceId,
                query: state.messages[state.messages.length - 1]?.content || '',
                intent_correction: correctedIntent,
                intent_correction_reason: reason,
                kb_id: state.currentKB?.kb_id
            })
        });

        if (data.success) {
            showToast('success', t('intent_feedback.success'));
            closeIntentFeedback();
        } else {
            showToast('error', data.message || t('intent_feedback.failed'));
        }
    } catch (e) {
        console.error('提交意图反馈失败:', e);
        showToast('error', t('intent_feedback.failed'));
    }
}

// ==================== Answer feedback (missing recall / wrong answer / citation issue) ====================
// The backend FeedbackRequest has always supported recall_missing / answer_wrong / citation_issue,
// while the UI only offered an "intent correction" entry point, so the feedback loop was missing
// the three most important types.

function openAnswerFeedback(msgId) {
    state._feedbackMsgId = msgId;
    state._feedbackType = null;
    const typeButtons = document.querySelectorAll('.feedback-type-btn');
    typeButtons.forEach(btn => {
        btn.classList.remove('border-blue-400', 'bg-blue-50');
        btn.classList.add('border-slate-200');
    });
    const textEl = document.getElementById('answerFeedbackText');
    if (textEl) textEl.value = '';
    document.getElementById('answerFeedbackDialog').classList.remove('hidden');
}

function closeAnswerFeedback() {
    document.getElementById('answerFeedbackDialog').classList.add('hidden');
    state._feedbackMsgId = null;
    state._feedbackType = null;
}

function selectFeedbackType(type) {
    state._feedbackType = type;
    document.querySelectorAll('.feedback-type-btn').forEach(btn => {
        const active = btn.getAttribute('data-feedback-type') === type;
        btn.classList.toggle('border-blue-400', active);
        btn.classList.toggle('bg-blue-50', active);
        btn.classList.toggle('border-slate-200', !active);
    });
}

async function submitAnswerFeedback() {
    if (!state._feedbackType) {
        showToast('warning', t('feedback.select_type'));
        return;
    }
    const snap = state._feedbackMsgId ? state.messageTraces[state._feedbackMsgId] : null;
    const traceId = (snap && snap.traceId) || state.currentTraceId;
    const query = (snap && snap.query) || state.traceData.userQuery || '';
    const kbId = (snap && snap.kbId) || (state.currentKB && state.currentKB.kb_id);
    const detail = (document.getElementById('answerFeedbackText') || {}).value || '';

    try {
        const data = await fetchJSON('/api/chat/feedback', {
            method: 'POST',
            body: JSON.stringify({
                trace_id: traceId || '',
                query: query,
                feedback_type: state._feedbackType,
                answer_feedback: detail.trim() || null,
                kb_id: kbId
            })
        });
        const suggested = (data && data.suggested_rules) || [];
        showToast('success', t('feedback.success').replace('{n}', suggested.length));
        closeAnswerFeedback();
    } catch (e) {
        console.error('提交回答反馈失败:', e);
        showToast('error', t('feedback.failed').replace('{error}', e.message || ''));
    }
}

// ESC closes the dialogs
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeCreateKBDialog();
        closeMonitorDialog();
        closeIntentFeedback();
        closeAnswerFeedback();
        closeSimulationDialog();
        closeABTest();
        closeABTestResult();
        closeFileManager();
    }
});
