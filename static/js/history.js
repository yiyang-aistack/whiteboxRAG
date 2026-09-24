/**
 * static/index.html - history and trace replay
 *
 * Content: the history dialog (GET /api/chat/history) and the full trace replay by trace_id
 *       (loadTraceById, which also serves ?trace_id=xxx and the A/B test integration).
 * Split out of index.html's inline <script>.
 */

// ==================== History and trace replay ====================

// History dialog
function loadHistory() {
    const modal = document.getElementById('historyModal');
    const listEl = document.getElementById('historyList');
    modal.classList.remove('hidden');
    listEl.innerHTML = `<div class="text-center text-slate-400 py-8 text-sm">${escapeHtml(t('common.loading'))}</div>`;

    const body = { page: 1, page_size: 50 };
    if (state.currentKB) body.kb_id = state.currentKB;

    const _histLang = (window.i18n && i18n.getLang) ? i18n.getLang() : (localStorage.getItem('lang') || 'zh-CN');
    fetch('/api/chat/history', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json;charset=utf-8', 'Accept-Language': _histLang },
        body: JSON.stringify(body)
    })
        .then(res => res.json())
        .then(data => {
            if (!data.success || !data.data || data.data.length === 0) {
                listEl.innerHTML = `<div class="text-center text-slate-400 py-8 text-sm">${t('history.empty')}</div>`;
                return;
            }
            listEl.innerHTML = data.data.map(item => {
                const time = item.created_at ? new Date(item.created_at).toLocaleString(i18n.getLang()) : '';
                const score = item.evaluation_score;
                const scoreBadge = score !== undefined && score !== null
                    ? `<span class="text-[10px] px-1.5 py-0.5 rounded ${score >= 0.7 ? 'bg-green-100 text-green-700' : score >= 0.4 ? 'bg-amber-100 text-amber-700' : 'bg-red-100 text-red-700'}">${escapeHtml(t('history.score').replace('{s}', (score * 100).toFixed(0)))}</span>`
                    : '';
                return `
                    <div onclick="loadTraceById('${item.trace_id}')"
                         class="p-3 border border-slate-200 rounded-lg hover:border-blue-400 hover:bg-blue-50 cursor-pointer transition-colors">
                        <div class="flex items-start justify-between gap-2">
                            <div class="flex-1 min-w-0">
                                <div class="text-sm text-slate-700 font-medium truncate">${escapeHtml(item.query || t('history.no_query'))}</div>
                                <div class="text-[10px] text-slate-400 mt-0.5">${escapeHtml(time)}</div>
                            </div>
                            ${scoreBadge}
                        </div>
                        <div class="text-xs text-slate-500 mt-1 line-clamp-2">${escapeHtml((item.answer || '').slice(0, 100))}${item.answer && item.answer.length > 100 ? '...' : ''}</div>
                    </div>
                `;
            }).join('');
        })
        .catch(err => {
            listEl.innerHTML = `<div class="text-center text-red-400 py-8 text-sm">${escapeHtml(t('history.load_failed').replace('{e}', err.message))}</div>`;
        });
}

function closeHistoryModal() {
    document.getElementById('historyModal').classList.add('hidden');
}

// Load the trace data by trace_id and render it into the panel
async function loadTraceById(traceId) {
    if (!traceId) return;
    closeHistoryModal();

    // Show the trace panel
    const panel = document.getElementById('sourcePanel');
    if (panel) panel.classList.remove('hidden');
    const toggle = document.getElementById('sourcePanelToggle');
    if (toggle) toggle.classList.remove('hidden');

    // Reset the panel (a history record is a standalone Q&A, so leave the "message snapshot" mode)
    state.activeTraceMsgId = null;
    resetTracePanel();
    state.currentTraceId = traceId;

    try {
        const data = await fetchJSON(`/api/chat/trace/${traceId}`);
        if (!data.success) {
            showToast('error', t('trace.load_failed'));
            return;
        }

        const trace = data.data;
        if (!trace) {
            showToast('error', t('trace.empty'));
            return;
        }

        // Populate state.traceData
        state.traceData.userQuery = trace.query || '';
        state.traceData.aiAnswer = trace.final_answer || '';

        // Prefer the top-level fields
        if (trace.intent_info) {
            state.traceData.intent = trace.intent_info;
        }
        if (trace.sentence_tracing) {
            state.traceData.sentenceTracing = trace.sentence_tracing;
        }
        if (trace.contradictions) {
            // Replay the answer/source discrepancies stored with this trace
            state.traceData.contradictions = trace.contradictions;
        }
        if (trace.evaluation) {
            state.traceData.evaluation = trace.evaluation;
        }

        // Extract the retrieval and diagnostics data from the stages
        if (trace.stages) {
            for (const stage of trace.stages) {
                const stageName = stage.stage || stage.name;
                if (stageName === 'retrieval') {
                    const out = stage.output || {};
                    const details = stage.details || {};
                    // The actual retrieval results live in details.merged_results
                    const rawResults = details.merged_results || details.results || [];
                    // Normalize the result shape: merged_results carries file_name / chunk_index
                    // at the top level, but renderSourcePanel and updateSummaryView expect
                    // metadata.file_name
                    const results = rawResults.map(r => ({
                        ...r,
                        metadata: r.metadata || {
                            file_name: r.file_name || t('trace.unknown_doc'),
                            chunk_index: r.chunk_index,
                            total_chunks: r.total_chunks
                        }
                    }));
                    const mode = out.mode || details.mode || 'hybrid';
                    const count = out.count || results.length;
                    const avgScore = results.length > 0 ? results.reduce((s, r) => s + (r.score || 0), 0) / results.length : 0;
                    state.traceData.retrievalQuality = avgScore > 0.4 ? 'high' : 'low';
                    state.traceData.avgRetrievalScore = avgScore;
                    state.traceData.results = results;
                    state.traceData.mode = mode;
                    state.traceData.count = count;
                    state.traceData.debugInfo = details;
                    // Retrieval funnel of a history record (only newer traces carry a funnel, older
                    // records fall back to showing the results only)
                    if (details.funnel) {
                        state.traceData.funnel = details.funnel;
                    }
                }
                // The output of the sentence_tracing stage carries drift_rate and other statistics
                if (stageName === 'sentence_tracing' && stage.output) {
                    state.traceData.driftAnalysis = stage.output;
                }
                if (stageName === 'recall_diagnosis' && stage.output) {
                    state.traceData.diagnosis = stage.output;
                }
                // The intent_classification stage serves as a fallback
                if (stageName === 'intent_classification' && stage.output && !state.traceData.intent) {
                    state.traceData.intent = stage.output;
                }
            }
        }

        // Render each panel
        if (state.traceData.results) {
            renderSourcePanel(state.traceData.results, state.traceData.mode, state.traceData.count);
        }
        if (state.traceData.debugInfo) {
            renderDebugInfo(state.traceData.debugInfo);
        }
        if (state.traceData.intent) {
            renderIntentCard(state.traceData.intent);
        }
        if (state.traceData.sentenceTracing) {
            renderSentenceTracing(state.traceData.sentenceTracing, state.traceData.driftAnalysis);
        }
        if (state.traceData.evaluation) {
            renderEvaluation(state.traceData.evaluation);
        }
        if (state.traceData.diagnosis) {
            renderRecallDiagnosis(state.traceData.diagnosis);
        }
        if (state.traceData.funnel) {
            renderRetrievalFunnel(state.traceData.funnel, state.traceData.skipped, true);
        }
        // Refresh the summary view
        updateSummaryView();
        // A loaded history record becomes the "latest trace to return to"
        state.liveTraceData = state.traceData;

        // Show the historical Q&A in the chat area (read-only)
        const msgList = document.getElementById('messageList');
        if (msgList && state.traceData.userQuery) {
            // Hide the welcome page and show the message list
            const welcomePage = document.getElementById('welcomePage');
            if (welcomePage) welcomePage.classList.add('hidden');
            msgList.classList.remove('hidden');
            msgList.innerHTML = '';
            addMessage('user', state.traceData.userQuery);
            const historyMsgId = addMessage('assistant', state.traceData.aiAnswer || t('trace.no_answer'));
            // The historical answer also gets sentence-level provenance tinting and is bound to its
            // provenance snapshot (which enables feedback traceback)
            state.messageTraces[historyMsgId] = {
                traceId: traceId,
                query: state.traceData.userQuery,
                kbId: state.currentKB ? state.currentKB.kb_id : null,
                data: state.traceData
            };
            const contentDiv = document.getElementById(historyMsgId)?.querySelector('.assistant-content');
            if (contentDiv) {
                contentDiv.classList.add('event-content');
                contentDiv.innerHTML = renderAnswerWithProvenance(state.traceData.aiAnswer || '', historyMsgId);
            }
            renderMessageMeta(historyMsgId);
        }

        showToast('success', t('trace.history_loaded'));

    } catch (err) {
        console.error('加载溯源失败:', err);
        showToast('error', t('trace.load_failed_detail').replace('{e}', err.message));
    }
}

// Check the URL parameters on page load so ?trace_id=xxx loads the trace automatically
function checkUrlForTraceId() {
    const params = new URLSearchParams(window.location.search);
    const traceId = params.get('trace_id');
    if (traceId) {
        loadTraceById(traceId);
    }
}
