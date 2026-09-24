/**
 * static/index.html - streaming chat and source panel rendering
 *
 * Content: streamChat() consumes the SSE stream and renders it; source cards (renderSourcePanel and
 *       the on-demand chunk full text), quality evaluation (renderEvaluation / fetchEvaluation),
 *       debug info (renderDebugInfo), the intent card, sentence-level trust tracing
 *       (renderSentenceTracing) and the recall diagnosis (renderRecallDiagnosis).
 * Split out of index.html's inline <script>; EVAL_GATE_LABEL_KEYS maps the evaluation gate labels.
 */

async function streamChat(message, aiMsgId) {
    state.isStreaming = true;
    state.currentTraceId = null;
    state._pendingIncrementalProvenance = [];  // Reset the incremental provenance cache
    // New Q&A: leave the "history snapshot" mode and go back to the latest data stream
    state.activeTraceMsgId = null;
    state.liveTraceData = null;
    hideSnapshotBanner();
    resetTracePanel();
    // Cache the user's original question for the "overview of this Q&A" section of the business
    // summary view
    state.traceData.userQuery = message;

    // 🔒 Lock the send button + input to prevent duplicate requests
    const sendBtn = document.getElementById('sendBtn');
    const chatInput = document.getElementById('chatInput');
    const sendBtnText = document.getElementById('sendBtnText');
    const sendBtnIcon = document.getElementById('sendBtnIcon');
    if (sendBtn) sendBtn.disabled = true;
    if (chatInput) chatInput.disabled = true;
    if (sendBtnText) sendBtnText.textContent = t('chat.sending');
    if (sendBtnIcon) sendBtnIcon.classList.add('animate-spin');

    try {
        const _chatLang = (window.i18n && i18n.getLang) ? i18n.getLang() : (localStorage.getItem('lang') || 'zh-CN');
        const response = await fetch('/api/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept-Language': _chatLang },
            body: JSON.stringify({
                kb_id: state.currentKB.kb_id,
                query: message,
                stream: true
            })
        });

        if (!response.ok) {
            throw new Error(t('chat.error'));
        }

        let fullContent = '';
        const reader = response.body.getReader();
        const decoder = new TextDecoder();

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const chunk = decoder.decode(value);
            const lines = chunk.split('\n');

            for (const line of lines) {
                if (line.startsWith('event:')) {
                    const eventType = line.slice(6).trim();
                    continue;
                }

                if (line.startsWith('data:')) {
                    const dataStr = line.slice(5).trim();
                    if (!dataStr) continue;

                    try {
                        const data = JSON.parse(dataStr);

                        // Handle the different event types
                        if (data.trace_id) {
                            state.currentTraceId = data.trace_id;
                        }

                        if (data.content) {
                            fullContent += data.content;
                            updateMessageContent(aiMsgId, fullContent);
                            // Cache the AI answer for the business summary view
                            state.traceData.aiAnswer = fullContent;
                        }

                        if (data.count !== undefined && data.results) {
                            // Cache the retrieval quality markers before calling renderSourcePanel,
                            // because renderSourcePanel calls updateSummaryView immediately, so
                            // retrievalQuality / avgRetrievalScore must be set beforehand
                            state.traceData.retrievalQuality = data.retrieval_quality || 'high';
                            state.traceData.avgRetrievalScore = data.avg_retrieval_score || 0;
                            state.traceData.skipped = data.skipped || null;
                            // Update the trace panel (which calls updateSummaryView internally)
                            renderSourcePanel(data.results, data.mode, data.count);
                            // Render the debug info
                            if (data.debug_info) {
                                renderDebugInfo(data.debug_info);
                            }
                            // Retrieval funnel: what made it into the answer, what was dropped and why
                            if (data.funnel) {
                                renderRetrievalFunnel(data.funnel, data.skipped, true);
                            }
                            // The white-box capability is visible by default: expand the panel when the
                            // first retrieval result arrives (while respecting a manual close)
                            if (!state.panelAutoOpened && !state.panelUserClosed && window.innerWidth >= 1280) {
                                state.panelAutoOpened = true;
                                openTracePanel();
                            } else {
                                updateTraceBadge();
                            }
                        }

                        if (data.message && !data.content) {
                            // Status message (retrieving / generating / boundary rejection) turned into
                            // a visible stage hint
                            console.log('Status:', data.message);
                            if (data.trace_id) {
                                state.currentTraceId = data.trace_id;
                            }
                            if (!state._pendingBoundary) {
                                setPipelineStatus(aiMsgId, data.message);
                            }
                        }

                        if (data.intent_info) {
                            // Render the intent recognition card
                            renderIntentCard(data.intent_info);
                        }

                        if (data.sentence_tracing) {
                            // Render the sentence-level tracing (the complete batch event sent after the
                            // backend finishes generating)
                            renderSentenceTracing(data.sentence_tracing, data.drift_analysis);
                        }

                        if (data.sentence_provenance) {
                            // Incremental sentence provenance event: the per-sentence provenance the
                            // backend pushes while generating.
                            // Cache it first (the content may not have arrived / the sentence may not be
                            // complete) and then try to fill the DOM immediately
                            if (!state._pendingIncrementalProvenance) state._pendingIncrementalProvenance = [];
                            state._pendingIncrementalProvenance.push({
                                sentence_idx: data.sentence_idx,
                                sentence_start: data.sentence_start,
                                sentence_end: data.sentence_end,
                                trace: data.sentence_provenance
                            });
                            const msgDiv = document.getElementById(aiMsgId);
                            if (msgDiv) {
                                const cd = msgDiv.querySelector('.assistant-content');
                                if (cd && !cd.classList.contains('event-content')) {
                                    fillIncrementalProvenance(cd, {
                                        sentence_idx: data.sentence_idx,
                                        sentence_start: data.sentence_start,
                                        sentence_end: data.sentence_end,
                                        trace: data.sentence_provenance
                                    });
                                }
                            }
                        }

                        if (data.citation_validation) {
                            // Citation validation (the backend always computed it, the frontend used to drop it)
                            renderCitationValidation(data.citation_validation);
                        }

                        if (data.evaluation) {
                            // Render the evaluation metrics
                            renderEvaluation(data.evaluation);
                        }

                        if (data.contradictions) {
                            // Statements that disagree with the source. The sentences
                            // themselves were already flagged through the
                            // sentence_tracing event (has_contradiction), so storing
                            // the list is enough for the meta bar and the summary view.
                            state.traceData.contradictions = data.contradictions;
                        }

                        if (data.recall_diagnosis) {
                            // Render the recall diagnosis
                            renderRecallDiagnosis(data.recall_diagnosis);
                        }

                        if (data.business_diagnosis) {
                            // Backend business diagnosis (the frontend used to drop it)
                            renderBusinessDiagnosis(data.business_diagnosis);
                        }

                        // Capture boundary metadata from done-event so we can render a
                        // friendly reject card instead of a plain text bubble at the end.
                        if (data.boundary_result) {
                            state._pendingBoundary = data.boundary_result;
                        }

                        // Handle done-event answer: when the pipeline returns in a single
                        // shot (boundary_rejected, retrieval_empty bypass, error fallback,
                        // etc.) the backend puts the final answer directly in the done
                        // event payload instead of streaming token-by-token. Without
                        // this branch the user sees an empty message bubble.
                        // Guard with !fullContent so a normal stream that already
                        // accumulated every token does not append the backend's complete
                        // answer a second time and render duplicated paragraphs.
                        if (data.answer && !data.content && !fullContent) {
                            fullContent = data.answer;
                            if (state._pendingBoundary) {
                                // Boundary-rejected: render the pretty card immediately.
                                updateMessageContent(aiMsgId, fullContent, false, state._pendingBoundary);
                            } else {
                                updateMessageContent(aiMsgId, fullContent);
                            }
                            state.traceData.aiAnswer = fullContent;
                        }

                    } catch (e) {
                        // Ignore parse errors
                    }
                }
            }
        }

        // Done
        state.traceData.aiAnswer = fullContent;
        // Save this Q&A's provenance snapshot before rendering the answer: the "view this trace"
        // button at the bottom of the message relies on that snapshot (so a history message can be
        // reviewed on its own too).
        state.liveTraceData = state.traceData;
        state.messageTraces[aiMsgId] = {
            traceId: state.currentTraceId,
            query: message,
            kbId: state.currentKB ? state.currentKB.kb_id : null,
            data: state.traceData
        };
        if (state._pendingBoundary) {
            updateMessageContent(aiMsgId, fullContent, false, state._pendingBoundary);
            state._pendingBoundary = null;
        } else {
            updateMessageContent(aiMsgId, fullContent, true);
        }
        updateSummaryView();
        state.chatStats.totalRequests++;
        updateMonitorBar();

    } catch (e) {
        console.error('流式对话失败:', e);
        updateMessageContent(aiMsgId, t('chat.stream_failed'), true);
        showToast('error', t('chat.error'));
        state.chatStats.totalErrors++;
    } finally {
        state.isStreaming = false;
        // 🔓 Restore the send button + input (enabled only while a knowledge base is selected)
        if (sendBtn) sendBtn.disabled = !state.currentKB;
        if (chatInput) chatInput.disabled = !state.currentKB;
        if (sendBtnText) sendBtnText.textContent = t('chat.send');
        if (sendBtnIcon) sendBtnIcon.classList.remove('animate-spin');
    }
}

function renderSourcePanel(results, mode, count) {
    state.traceData.results = results;
    state.traceData.mode = mode;
    state.traceData.count = count;
    updateSummaryView();

    const container = document.getElementById('sourceList');

    if (!results || results.length === 0) {
        container.innerHTML = `
            <div class="text-center text-slate-400 py-8">
                <p class="text-sm">${escapeHtml(t('trace.no_docs'))}</p>
            </div>
        `;
        return;
    }

    // Retrieval mode code -> i18n key; unknown codes are shown verbatim.
    const modeKey = {
        'hybrid': 'trace.mode_hybrid',
        'vector': 'trace.mode_vector',
        'bm25': 'trace.mode_bm25',
        'ensemble': 'trace.mode_ensemble',
        'compressed': 'trace.mode_compressed',
    }[mode];
    const modeLabel = modeKey ? t(modeKey) : mode;

    // Consistent scale: the candidate total / dropped count come from the retrieval funnel and no
    // longer contradict the list length below
    const funnelCounts = (state.traceData.funnel && state.traceData.funnel.counts) || null;
    const candidateCount = funnelCounts ? funnelCounts.merged : null;
    const droppedCount = funnelCounts ? funnelCounts.dropped : 0;

    container.innerHTML = `
        <div class="mb-3 flex items-center justify-between gap-2 flex-wrap">
            <div class="text-xs text-slate-600">
                ${escapeHtml(t('trace.retrieval_mode_label'))} <span class="font-medium text-blue-600">${escapeHtml(modeLabel)}</span>
            </div>
            <div class="text-xs text-slate-600">
                ${escapeHtml(t('trace.entered_answer'))}: <span class="font-medium text-green-700">${results.length}</span>
                ${candidateCount !== null ? ` · ${escapeHtml(t('trace.candidates_total'))}: <span class="font-medium">${candidateCount}</span>` : ''}
                ${droppedCount ? ` · ${escapeHtml(t('trace.dropped_total'))}: <span class="font-medium text-amber-600">${droppedCount}</span>` : ''}
            </div>
        </div>
        <div class="space-y-3">
            ${results.map((r, i) => {
                const fullText = escapeHtml(r.text || '');
                const previewText = fullText.length > 200 ? fullText.substring(0, 200) + '...' : fullText;
                const showExpand = fullText.length > 200;
                const chunkId = String(r.id || '');
                const truncated = r.text_truncated === true;

                const hitReasonsHtml = r.hit_reasons && r.hit_reasons.length > 0 ? `
                    <div class="mt-2 pt-2 border-t border-slate-100">
                        <div class="text-[10px] font-medium text-emerald-600 mb-1">${escapeHtml(t('trace.hit_reasons_title'))}</div>
                        <div class="flex flex-wrap gap-1">
                            ${r.hit_reasons.map(reason => `
                                <span class="text-[10px] bg-emerald-50 text-emerald-700 px-1.5 py-0.5 rounded border border-emerald-200">
                                    ${escapeHtml(hitReasonText(reason))}
                                </span>
                            `).join('')}
                        </div>
                    </div>
                ` : '';

                // Consistent scale: the BM25 / vector / fused scores are all "score type" -> 2
                // decimals + a value-range tooltip.
                // The fused score is the main one, BM25 / vector are its breakdown
                const mainScore = scoreBadge(r.score, {
                    label: t('trace.fused_score'),
                    type: 'score',
                    range: [0, 1]
                });
                const scoreBreakdown = r.bm25_score !== undefined || r.vector_score !== undefined ? `
                    <div class="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-slate-500 mt-1">
                        ${r.bm25_score !== undefined ? `<span>${escapeHtml(t('trace.bm25_norm_label'))} <span class="font-medium text-slate-700">${fmtScore(r.bm25_score)}</span></span>` : ''}
                        ${r.vector_score !== undefined ? `<span>${escapeHtml(t('trace.vector_cos_label'))} <span class="font-medium text-slate-700">${fmtScore(r.vector_score)}</span></span>` : ''}
                    </div>
                ` : '';

                return `
                <div class="border border-slate-200 rounded-lg p-3 hover:shadow-md transition-shadow" data-chunk-id="${escapeHtml(chunkId)}">
                    <div class="flex items-center justify-between mb-2">
                        <span class="text-xs font-medium text-blue-600">${escapeHtml(t('trace.doc_n').replace('{n}', i + 1))}</span>
                        <div class="flex items-center gap-2">
                            ${r.metadata?.chunk_index !== undefined ? `
                                <span class="text-[10px] text-slate-400" title="${escapeHtml(t('trace.chunk_pos_tip'))}">
                                    Chunk: ${r.metadata.chunk_index + 1}/${r.metadata.total_chunks || '-'}
                                </span>
                            ` : ''}
                            <span class="text-xs text-slate-400 flex items-center gap-1">
                                <span>${escapeHtml(t('trace.fused_score'))}:</span>${mainScore}
                            </span>
                        </div>
                    </div>
                    ${scoreBreakdown}
                    <div class="text-xs text-slate-600 whitespace-pre-wrap" id="source-text-${i}">${previewText}</div>
                    ${showExpand ? `
                        <button onclick="toggleSource(${i}, '${fullText.replace(/'/g, "\\'")}')" 
                                class="text-[10px] text-blue-500 hover:text-blue-700 mt-1 flex items-center gap-1">
                            <span id="expand-icon-${i}">▼</span> ${escapeHtml(t('trace.expand_full_text'))}
                        </button>
                    ` : ''}
                    ${truncated ? `
                        <button onclick='loadFullChunk(${i}, ${JSON.stringify(chunkId)})'
                                class="text-[10px] text-blue-500 hover:text-blue-700 mt-1">${escapeHtml(t('trace.load_full_chunk'))}</button>
                    ` : ''}
                    ${hitReasonsHtml}
                    ${r.metadata?.file_name ? `
                        <p class="text-xs text-slate-400 mt-2 pt-2 border-t border-slate-100">
                            ${escapeHtml(t('trace.source_doc').replace('{v}', r.metadata.file_name))}
                        </p>
                    ` : ''}
                </div>
                `;
            }).join('')}
        </div>
    `;
}

/**
 * Replace the text with the complete chunk source on demand (the text sent during streaming is
 * truncated to keep the SSE payload small).
 * It goes through the chunk detail endpoint and reports a failure instead of silently showing half of
 * the content.
 */
async function loadFullChunk(index, chunkId) {
    const el = document.getElementById(`source-text-${index}`);
    if (!el) return;
    el.textContent = t('trace.loading_full_chunk');
    const full = await fetchChunkText(chunkId);
    if (!full || !full.text) {
        el.textContent = t('trace.chunk_fetch_failed');
        return;
    }
    el.textContent = full.text;
}

function toggleSource(index, fullText) {
    const textElement = document.getElementById(`source-text-${index}`);
    const button = document.querySelector(`button[onclick="toggleSource(${index}, '${fullText.replace(/'/g, "\\'")}')"]`);
    const icon = document.getElementById(`expand-icon-${index}`);

    if (!textElement || !button || !icon) return;

    const currentText = textElement.textContent;
    if (currentText.length < fullText.length) {
        textElement.textContent = fullText;
        button.textContent = ' ' + t('trace.collapse_full_text');
        icon.textContent = '▲';
        button.classList.add('text-green-500');
        button.classList.remove('text-blue-500');
    } else {
        const previewText = fullText.length > 200 ? fullText.substring(0, 200) + '...' : fullText;
        textElement.textContent = previewText;
        button.textContent = ' ' + t('trace.expand_full_text');
        icon.textContent = '▼';
        button.classList.add('text-blue-500');
        button.classList.remove('text-green-500');
    }
}

// Gate machine codes -> i18n keys (unknown codes fall back to the raw code, so a
// future gate is visible instead of silently missing).
const EVAL_GATE_LABEL_KEYS = {
    'score': 'trace.gate.score',
    'weight_coverage': 'trace.gate.weight_coverage',
    'not_empty_response': 'trace.gate.not_empty_response',
    'hallucination_within_limit': 'trace.gate.hallucination_within_limit',
    'retrieved_context': 'trace.gate.retrieved_context'
};

/**
 * Tooltip of the overall-score badge: how that score came about (rubric version, before / after the
 * adjustments, the hard gates it failed, the quality flags).
 * It shows backend fields only - the frontend recomputes no scoring logic, so its scale cannot drift
 * away from the backend's.
 */
function evalBadgeTooltip(evaluation) {
    const lines = [];
    if (evaluation.rubric_version) {
        lines.push(t('trace.rubric_label').replace('{v}', evaluation.rubric_version));
    }
    const raw = evaluation.raw_score;
    const finalScore = evaluation.overall_score;
    if (raw !== null && raw !== undefined && finalScore !== null
        && finalScore !== undefined && Math.abs(raw - finalScore) > 1e-6) {
        lines.push(t('trace.raw_score_label').replace('{s}', Math.round(raw * 100)));
    }
    const failures = evaluation.gate_failures || [];
    if (failures.length) {
        lines.push(t('trace.gate_failed_label'));
        failures.forEach(name => lines.push('· ' + t(EVAL_GATE_LABEL_KEYS[name] || name)));
    }
    (evaluation.quality_flags || []).forEach(flag => lines.push('· ' + flag));
    return lines.join('\n');
}

function renderEvaluation(evaluation) {
    if (!evaluation) return;
    state.traceData.evaluation = evaluation;
    updateSummaryView();

    const card = document.getElementById('evaluationCard');
    const badge = document.getElementById('overallScoreBadge');
    const metricsContainer = document.getElementById('evaluationMetrics');

    card.classList.remove('hidden');

    const overallScore = evaluation.overall_score;
    const isPassing = evaluation.is_passing;

    if (overallScore !== null && overallScore !== undefined) {
        const scorePercent = Math.round(overallScore * 100);
        // The colour follows the backend's verdict: in rubric v2 is_passing and overall_score share
        // one source (the score + the explicit hard gates), so a display like "85 points - fail" that
        // contradicts itself can no longer appear; once it passes, yellow / green is picked by the
        // margin.
        let badgeColor = 'bg-green-100 text-green-700';
        if (!isPassing) badgeColor = 'bg-red-100 text-red-700';
        else if (scorePercent < 85) badgeColor = 'bg-yellow-100 text-yellow-700';

        badge.className = `text-xs px-2 py-0.5 rounded-full font-medium cursor-help ${badgeColor}`;
        // The overall score uses the percentage scale (0-100), so "0.xx decimals" and "xx%" are no longer mixed
        badge.textContent = t('trace.score_of_hundred')
            .replace('{s}', scorePercent)
            .replace('{r}', isPassing ? t('trace.pass') : t('trace.fail'));
        badge.title = evalBadgeTooltip(evaluation);
    } else {
        badge.textContent = '';
        badge.title = '';
    }

    // Every sub-metric goes through the global metricRow:
    //  ratio type (recall / faithfulness / usage ratio, ...)  -> integer percentage + colour + tooltip
    //  count type (response length, ...)                    -> integer + unit
    //  score type (average score / standard deviation, ...) -> 2 decimals
    let html = '';
    for (const [key, metric] of Object.entries(evaluation.metrics || {})) {
        const passBadge = (metric.is_pass === true || metric.is_pass === false)
            ? (metric.is_pass
                ? `<span class="w-1.5 h-1.5 rounded-full bg-green-500 shrink-0" title="${escapeHtml(t('trace.metric_pass_tip'))}"></span>`
                : `<span class="w-1.5 h-1.5 rounded-full bg-red-500 shrink-0" title="${escapeHtml(t('trace.metric_fail_tip'))}"></span>`)
            : '';
        // Render the main label + value with the global metricRow and attach the is_pass dot on the right
        const info = TRACE_TERM_MAP[key] || null;
        const name = termName(info, key);
        const desc = termDesc(info);
        const type = (info && info.type) || (key === 'response_length' ? 'count'
            : (key === 'retrieval_score_avg' || key === 'retrieval_score_std' ? 'score' : 'ratio'));
        const questionIcon = desc ? `<svg class="w-3 h-3 inline ml-1 text-slate-400 cursor-help" fill="none" stroke="currentColor" viewBox="0 0 24 24" title="${escapeHtml(desc)}"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>` : '';
        const val = scoreBadge(metric.value, { label: name, type, range: (info && info.range) || [0, 1] });
        html += `
            <div class="flex items-center justify-between text-xs gap-2">
                <span class="text-slate-600 whitespace-nowrap">${escapeHtml(name)}${questionIcon}</span>
                <div class="flex items-center gap-1 shrink-0">
                    ${val}
                    ${passBadge}
                </div>
            </div>
        `;
    }

    metricsContainer.innerHTML = html;
}

function renderDebugInfo(debugInfo) {
    if (!debugInfo) return;

    const panel = document.getElementById('debugPanel');
    const content = document.getElementById('debugContent');

    panel.classList.remove('hidden');

    const STEP_TITLE_KEY = {
        'tokenize': 'trace.debug.step.tokenize',
        'bm25_search': 'trace.debug.step.bm25_search',
        'bm25_complete': 'trace.debug.step.bm25_complete',
        'embedding': 'trace.debug.step.embedding',
        'vector_search': 'trace.debug.step.vector_search',
        'vector_complete': 'trace.debug.step.vector_complete',
        'merge': 'trace.debug.step.merge',
        'merge_complete': 'trace.debug.step.merge_complete',
        'filter': 'trace.debug.step.filter',
        'filter_complete': 'trace.debug.step.filter_complete',
        'compression': 'trace.debug.step.compression',
        'compression_complete': 'trace.debug.step.compression_complete',
        'error': 'trace.debug.step.error',
        'query_rewrite': 'trace.debug.step.query_rewrite',
        'typo_correct': 'trace.debug.step.typo_correct',
        'synonym_expand': 'trace.debug.step.synonym_expand',
        'hit_attribution': 'trace.debug.step.hit_attribution',
        'recall_diagnostic': 'trace.debug.step.recall_diagnostic',
        'recall_diagnostic_complete': 'trace.debug.step.recall_diagnostic_complete',
        'recall_diagnostic_error': 'trace.debug.step.recall_diagnostic_error',
    };
    const STEP_ICON = {
        'tokenize': '📝',
        'bm25_search': '🔍',
        'bm25_complete': '✅',
        'embedding': '🧠',
        'vector_search': '🔮',
        'vector_complete': '✅',
        'merge': '🔀',
        'merge_complete': '✅',
        'filter': '🔬',
        'filter_complete': '✅',
        'compression': '📦',
        'compression_complete': '✅',
        'error': '❌',
        'query_rewrite': '🔄',
        'typo_correct': '✏️',
        'synonym_expand': '🗂️'
    };

    const getStepTitle = (stepId) => t(STEP_TITLE_KEY[stepId]) || stepId;

    let html = '';

    if (debugInfo.rewrite) {
        html += `<div class="bg-cyan-50 border border-cyan-200 rounded-lg p-2 mb-3">`;
        html += `<div class="text-xs font-semibold text-cyan-800 mb-1">${t('trace.debug.rewrite_title')}</div>`;
        html += `<div class="text-xs text-cyan-900 mb-1">
            <span class="inline-block bg-cyan-100 text-cyan-700 px-1.5 py-0.5 rounded text-[10px] mr-1.5">${t('trace.debug.original_query')}</span>
            ${escapeHtml(debugInfo.rewrite.original_query || '')}
        </div>`;
        if (debugInfo.rewrite.rewritten_query && debugInfo.rewrite.rewritten_query !== debugInfo.rewrite.original_query) {
            html += `<div class="text-xs text-green-800 mb-1">
                <span class="inline-block bg-green-100 text-green-700 px-1.5 py-0.5 rounded text-[10px] mr-1.5">${t('trace.debug.standardized_query')}</span>
                ${escapeHtml(debugInfo.rewrite.rewritten_query || '')}
            </div>`;
        }
        if (debugInfo.rewrite.rewrite_reason) {
            html += `<div class="text-[10px] text-cyan-600">${t('trace.debug.note')} ${escapeHtml(debugInfo.rewrite.rewrite_reason)}</div>`;
        }
        if (debugInfo.rewrite.key_terms && debugInfo.rewrite.key_terms.length > 0) {
            html += `<div class="mt-1.5"><span class="text-[10px] text-cyan-600">${t('trace.debug.extract_keywords')}</span> ${debugInfo.rewrite.key_terms.map(kw => `<span class="bg-cyan-100 text-cyan-700 px-1.5 py-0.5 rounded text-[10px]">${escapeHtml(kw)}</span>`).join(' ')}</div>`;
        }
        if (debugInfo.rewrite.term_mappings && debugInfo.rewrite.term_mappings.length > 0) {
            html += `<div class="mt-1.5"><span class="text-[10px] text-cyan-600">${t('trace.debug.synonym_expansion')}</span>`;
            debugInfo.rewrite.term_mappings.forEach(m => {
                html += `<div class="ml-1 mt-0.5"><span class="text-cyan-700">${escapeHtml(m.original)}</span> → ${m.synonyms.map(s => `<span class="bg-green-100 text-green-700 px-1 rounded text-[9px]">${escapeHtml(s)}</span>`).join(' ')}</div>`;
            });
            html += `</div>`;
        }
        html += `</div>`;
    }

    const finalQuery = debugInfo.rewrite?.rewritten_query || debugInfo.query || '';
    html += `<div class="mb-3">
        <div class="text-xs font-semibold text-amber-800 mb-1">${t('trace.debug.final_query_title')}</div>
        <div class="text-xs text-slate-700 bg-amber-50 border border-amber-100 rounded px-2 py-1.5 break-words">
            ${escapeHtml(finalQuery)}
        </div>
    </div>`;

    if (debugInfo.query_tokens && debugInfo.query_tokens.length > 0) {
        html += `<div class="bg-blue-50 border border-blue-200 rounded-lg p-2 mb-3">`;
        html += `<div class="text-xs font-semibold text-blue-700 mb-1">${t('trace.debug.tokenize_title')}</div>`;
        html += `<div class="flex flex-wrap gap-1">${debugInfo.query_tokens.map(tok => `<span class="bg-blue-100 text-blue-800 px-2 py-0.5 rounded text-xs">${escapeHtml(tok)}</span>`).join(' ')}</div>`;
        html += `<div class="text-[10px] text-blue-500 mt-1">${t('trace.debug.tokenize_note')}</div>`;
        html += `</div>`;
    }

    if (debugInfo.params) {
        const modeKey = ({
            'hybrid': 'trace.debug.mode_hybrid',
            'vector': 'trace.debug.mode_vector',
            'bm25': 'trace.debug.mode_bm25',
            'ensemble': 'trace.debug.mode_ensemble',
            'compressed': 'trace.debug.mode_compressed',
        })[debugInfo.params.mode];
        const modeLabel = modeKey ? t(modeKey) : (debugInfo.params.mode || '');
        const scenarioLabel = debugInfo.params.scenario_id || t('trace.debug.params_default');
        html += `<div class="bg-slate-50 border border-slate-200 rounded-lg p-2 mb-3">`;
        html += `<div class="text-xs font-semibold text-slate-700 mb-2">${t('trace.debug.params_title')}</div>`;
        html += `<div class="grid grid-cols-2 gap-x-3 gap-y-1">`;
        html += `<div class="text-xs"><span class="text-slate-500">${t('trace.debug.params_mode')}</span> <span class="font-medium text-slate-800">${escapeHtml(modeLabel)}</span></div>`;
        html += `<div class="text-xs"><span class="text-slate-500">${t('trace.debug.params_top_k')}</span> <span class="font-medium text-slate-800">${debugInfo.params.top_k ?? '-'}</span></div>`;
        html += `<div class="text-xs"><span class="text-slate-500">${t('trace.debug.params_bm25_weight')}</span> <span class="font-medium text-slate-800">${debugInfo.params.bm25_weight ?? '-'}</span></div>`;
        html += `<div class="text-xs"><span class="text-slate-500">${t('trace.debug.params_vector_weight')}</span> <span class="font-medium text-slate-800">${debugInfo.params.vector_weight ?? '-'}</span></div>`;
        html += `<div class="text-xs"><span class="text-slate-500">${t('trace.debug.params_similarity_threshold')}</span> <span class="font-medium text-slate-800">${debugInfo.params.similarity_threshold ?? '-'}</span></div>`;
        html += `<div class="text-xs"><span class="text-slate-500">${t('trace.debug.params_scenario')}</span> <span class="font-medium text-slate-800">${escapeHtml(scenarioLabel)}</span></div>`;
        html += `</div>`;
        if (debugInfo.params.mode === 'hybrid' || debugInfo.params.mode === 'ensemble') {
            const bW = debugInfo.params.bm25_weight ?? 0.4;
            const vW = debugInfo.params.vector_weight ?? 0.6;
            html += `<div class="mt-2 bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 rounded px-2.5 py-2 text-[10px] text-amber-900 dark:text-amber-200 leading-relaxed">
                <div class="font-semibold text-amber-900 dark:text-amber-100 mb-1">${t('trace.debug.params_formula')}</div>
                <div class="bg-white dark:bg-slate-900 border border-amber-300 dark:border-amber-700 rounded px-2 py-1.5 mb-1.5 shadow-inner">
                    <code class="font-mono text-[11px] text-slate-800 dark:text-amber-300 break-all">${t('trace.debug.params_formula_detail', { bw: bW, vw: vW })}</code>
                </div>
                <div class="text-amber-800 dark:text-amber-300/90">${t('trace.debug.params_formula_bm25_note')}</div>
                <div class="text-amber-800 dark:text-amber-300/90">${t('trace.debug.params_formula_threshold_note')}</div>
            </div>`;
        }
        if (debugInfo.embedding_model) {
            html += `<div class="text-[10px] text-purple-600 mt-2">${t('trace.debug.params_embedding_model', { name: debugInfo.embedding_model })}</div>`;
        }
        html += `</div>`;
    }

    if (debugInfo.steps && debugInfo.steps.length > 0) {
        html += `<div class="bg-slate-50 border border-slate-200 rounded-lg p-2 mb-3">`;
        html += `<div class="text-xs font-semibold text-slate-700 mb-2">${t('trace.debug.steps_title')}</div>`;
        html += `<div class="relative pl-3 border-l-2 border-amber-300 space-y-2">`;
        debugInfo.steps.forEach((step, idx) => {
            const stepId = step.step || '';
            const titleKey = STEP_TITLE_KEY[stepId];
            const title = titleKey ? t(titleKey) : (step.message || stepId || t('trace.debug.steps_fallback'));
            const icon = STEP_ICON[stepId] || '➡️';
            const hasResults = step.results && step.results.length > 0;
            const resultsDomId = `step-results-${idx}`;
            html += `<div class="relative">`;
            html += `<div class="absolute -left-[19px] top-0 w-3 h-3 bg-amber-400 rounded-full border-2 border-white"></div>`;
            html += `<div class="flex items-center gap-1 text-xs font-medium text-slate-700">`;
            html += `<span>${icon} ${escapeHtml(title)}</span>`;
            if (hasResults) {
                html += `<button onclick="(function(b,c){if(b)b.classList.toggle('rotate-180');if(c)c.classList.toggle('hidden');})(this.querySelector('svg'),document.getElementById('${resultsDomId}'))" class="step-chevron ml-1 inline-flex items-center justify-center w-4 h-4 rounded hover:bg-amber-100 dark:hover:bg-amber-900/40 transition-colors" title="${step.results.length} items">`;
                html += `<svg width="10" height="10" viewBox="0 0 10 10" fill="none" class="transition-transform duration-200"><path d="M2 3.5L5 6.5L8 3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
                html += `</button>`;
            }
            html += `</div>`;
            if (step.message && step.message !== title && !step.details) {
                html += `<div class="text-[10px] text-slate-500 mt-0.5">${escapeHtml(step.message)}</div>`;
            } else if (step.details) {
                html += `<div class="text-[10px] text-slate-500 mt-0.5">${escapeHtml(step.details)}</div>`;
            }
            if (step.tokens && step.tokens.length > 0) {
                html += `<div class="flex flex-wrap gap-0.5 mt-1">${step.tokens.map(tk => `<span class="bg-blue-100 text-blue-700 px-1 py-0.5 rounded text-[10px]">${escapeHtml(tk)}</span>`).join(' ')}</div>`;
            }
            if (hasResults) {
                html += `<div id="${resultsDomId}" class="mt-2 ml-2 border-l-2 border-purple-300 dark:border-purple-700 pl-2 space-y-1">`;
                step.results.forEach((r) => {
                    const scoreText = scoreBadge(r.score, { label: t('trace.debug.chunk_score_label'), type: 'score', range: [0, 1] });
                    html += `<div class="bg-purple-50 dark:bg-purple-900/30 rounded p-1.5">`;
                    html += `<div class="flex justify-between items-center mb-0.5">`;
                    html += `<span class="text-[10px] font-medium text-purple-700 dark:text-purple-300">${t('trace.debug.chunk_label')} ${r.index ?? (r.chunk_index !== undefined ? r.chunk_index + 1 : '-')}</span>`;
                    html += `<span class="text-[10px]">${t('trace.debug.score_label')} ${scoreText}</span>`;
                    html += `</div>`;
                    html += `<div class="text-[10px] text-purple-800 dark:text-purple-200 truncate">${escapeHtml(r.text ? (r.text.length > 60 ? r.text.slice(0, 60) + '…' : r.text) : '')}</div>`;
                    html += `<div class="text-[9px] text-purple-500 dark:text-purple-400 mt-0.5">`;
                    html += `${t('trace.debug.file_label')} ${escapeHtml(r.file_name || t('trace.debug.unknown_file'))}`;
                    if (r.chunk_index !== undefined) {
                        html += ` · Chunk: ${r.chunk_index + 1}/${r.total_chunks ?? '-'}`;
                    }
                    html += `</div>`;
                    html += `</div>`;
                });
                html += `</div>`;
            }
            html += `</div>`;
        });
        html += `</div></div>`;
    }

    const renderRouteList = (list, title, subtitle, color, scoreLabel, scoreType) => {
        if (!list || list.length === 0) return '';
        const colorMap = {
            yellow: { bg: 'yellow', text: 'yellow-800', sub: 'yellow-600', card: 'yellow-100/50' },
            purple: { bg: 'purple', text: 'purple-800', sub: 'purple-600', card: 'purple-100/50' },
            green:  { bg: 'green',  text: 'green-800',  sub: 'green-600',  card: 'green-100/50' },
        }[color] || colorMap.green;
        let out = `<div class="bg-${colorMap.bg}-50 border border-${colorMap.bg}-200 rounded-lg p-2 mb-3">`;
        out += `<div class="flex items-center justify-between mb-2">
            <div class="text-xs font-semibold text-${colorMap.text}">${escapeHtml(title)} <span class="font-normal text-[10px] text-${colorMap.sub}">(${list.length})</span></div>
            <span class="text-[9px] text-${colorMap.sub}">${escapeHtml(subtitle)}</span>
        </div>`;
        out += `<div class="space-y-2">`;
        list.slice(0, 5).forEach((r, i) => {
            const scoreVal = r.score;
            const scoreText = scoreBadge(scoreVal, { label: scoreLabel, type: scoreType || 'score', range: [0, 1] });
            out += `<div class="bg-${colorMap.card} rounded p-2">`;
            out += `<div class="flex justify-between items-center mb-1">`;
            out += `<span class="text-xs font-medium text-${colorMap.text}">${t('trace.debug.route_doc')} ${i + 1}</span>`;
            out += `<span class="text-[11px]">${scoreLabel}: ${scoreText}</span>`;
            out += `</div>`;
            out += `<div class="text-xs text-${colorMap.bg}-900 truncate">${escapeHtml(r.text ? (r.text.length > 80 ? r.text.slice(0, 80) + '…' : r.text) : '')}</div>`;
            out += `<div class="text-[10px] text-${colorMap.sub} mt-1">`;
            out += `${t('trace.debug.route_file')} ${escapeHtml(r.file_name || t('trace.debug.route_unknown'))}`;
            if (r.chunk_index !== undefined) {
                out += ` · Chunk: ${r.chunk_index + 1}/${r.total_chunks || '-'}`;
            }
            out += `</div>`;
            out += `</div>`;
        });
        if (list.length > 5) out += `<div class="text-[10px] text-${colorMap.sub} text-center">${t('trace.debug.route_show_top5')}</div>`;
        out += `</div></div>`;
        return out;
    };

    const bm25List = debugInfo.bm25_results || [];
    const vectorList = debugInfo.vector_results || [];
    const mergedList = debugInfo.merged_results || [];
    if (bm25List.length > 0 || vectorList.length > 0 || mergedList.length > 0) {
        const chunkKey = (r) => `${r.file_name || ''}#${r.chunk_index ?? ''}`;
        const bm25Keys = new Set(bm25List.map(chunkKey));
        const vectorKeys = new Set(vectorList.map(chunkKey));
        const mergedKeys = new Set(mergedList.map(chunkKey));

        const hitBadge = (key) => {
            const inBm25 = bm25Keys.has(key);
            const inVec = vectorKeys.has(key);
            if (inBm25 && inVec) return { text: t('trace.debug.compare_hit_both'), cls: 'bg-indigo-100 text-indigo-700' };
            if (inBm25) return { text: t('trace.debug.compare_hit_bm25_only'), cls: 'bg-yellow-100 text-yellow-700' };
            if (inVec) return { text: t('trace.debug.compare_hit_vector_only'), cls: 'bg-purple-100 text-purple-700' };
            return { text: t('trace.debug.compare_not_crossed'), cls: 'bg-slate-100 text-slate-500' };
        };

        const renderCompareCol = (list, title, barColor, scoreLabel) => {
            const top = list.slice(0, 5);
            let items = '';
            if (top.length === 0) {
                items = `<div class="text-[10px] text-slate-400 text-center py-3">${t('trace.debug.compare_empty')}</div>`;
            } else {
                const maxScore = Math.max(...top.map(r => r.score || 0), 0.0001);
                items = top.map((r, i) => {
                    const score = r.score || 0;
                    const barWidth = Math.max(2, (score / maxScore) * 100);
                    const key = chunkKey(r);
                    const badge = hitBadge(key);
                    const fileName = r.file_name ? (r.file_name.length > 14 ? r.file_name.slice(0, 14) + '…' : r.file_name) : `${t('trace.debug.compare_chunk_prefix')}${i+1}`;
                    return `
                        <div class="bg-slate-50 rounded p-1.5">
                            <div class="flex items-center justify-between mb-1 gap-1">
                                <span class="text-[10px] text-slate-600 truncate flex-1" title="${escapeHtml(r.file_name || '')}">${escapeHtml(fileName)}</span>
                                <span class="text-[9px] px-1 py-0.5 rounded font-medium shrink-0 ${badge.cls}">${badge.text}</span>
                            </div>
                            <div class="flex items-center gap-1.5">
                                <div class="flex-1 h-2 bg-slate-200 rounded-full overflow-hidden">
                                    <div class="h-full ${barColor} rounded-full" style="width:${barWidth}%"></div>
                                </div>
                                <span class="text-[10px] font-medium text-slate-700 shrink-0 w-8 text-right">${fmtScore(score)}</span>
                            </div>
                        </div>
                    `;
                }).join('');
            }
            return `
                <div class="rounded-lg border border-slate-200 overflow-hidden">
                    <div class="px-2 py-1.5 bg-slate-50 border-b border-slate-200">
                        <div class="text-[11px] font-semibold text-slate-700">${title}</div>
                        <div class="text-[9px] text-slate-400">${scoreLabel}</div>
                    </div>
                    <div class="p-1.5 space-y-1.5">${items}</div>
                </div>
            `;
        };

        html += `<div class="bg-white border border-blue-200 rounded-lg p-2 mb-3">`;
        html += `<div class="flex items-center justify-between mb-2">
            <div class="text-xs font-semibold text-blue-700">${t('trace.debug.compare_title')}</div>
            <span class="text-[9px] text-slate-400">${t('trace.debug.compare_bar_note')}</span>
        </div>`;
        html += `<div class="grid grid-cols-3 gap-1.5">`;
        html += renderCompareCol(bm25List, t('trace.debug.compare_bm25_title'), 'bg-yellow-400', t('trace.debug.compare_bm25_subtitle'));
        html += renderCompareCol(vectorList, t('trace.debug.compare_vector_title'), 'bg-purple-400', t('trace.debug.compare_vector_subtitle'));
        html += renderCompareCol(mergedList, t('trace.debug.compare_merged_title'), 'bg-green-400', t('trace.debug.compare_merged_subtitle'));
        html += `</div>`;
        const onlyBm25 = [...bm25Keys].filter(k => !vectorKeys.has(k)).length;
        const onlyVec = [...vectorKeys].filter(k => !bm25Keys.has(k)).length;
        const both = [...bm25Keys].filter(k => vectorKeys.has(k)).length;
        html += `<div class="mt-2 flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-[10px] text-slate-500">
            <span>${t('trace.debug.compare_bm25_count', { n: `<b class="text-slate-700">${bm25List.length}</b>` })}</span>
            <span>${t('trace.debug.compare_vector_count', { n: `<b class="text-slate-700">${vectorList.length}</b>` })}</span>
            <span>${t('trace.debug.compare_merged_count', { n: `<b class="text-slate-700">${mergedList.length}</b>` })}</span>
            <span class="text-indigo-600">${t('trace.debug.compare_both_hit', { n: `<b>${both}</b>` })}</span>
            <span class="text-yellow-600">${t('trace.debug.compare_bm25_only_count', { n: `<b>${onlyBm25}</b>` })}</span>
            <span class="text-purple-600">${t('trace.debug.compare_vector_only_count', { n: `<b>${onlyVec}</b>` })}</span>
        </div>`;
        html += `</div>`;
    }

    html += renderRouteList(
        debugInfo.bm25_results,
        t('trace.debug.bm25_title'),
        t('trace.debug.bm25_subtitle'),
        'yellow',
        t('trace.debug.bm25_score_label'),
        'score'
    );
    html += renderRouteList(
        debugInfo.vector_results,
        t('trace.debug.vector_title'),
        t('trace.debug.vector_subtitle'),
        'purple',
        t('trace.debug.vector_score_label'),
        'score'
    );
    if (debugInfo.merged_results && debugInfo.merged_results.length > 0) {
        html += `<div class="bg-green-50 border border-green-200 rounded-lg p-2">`;
        html += `<div class="flex items-center justify-between mb-2">
            <div class="text-xs font-semibold text-green-800">${t('trace.debug.merged_title')} <span class="font-normal text-[10px] text-green-600">(${debugInfo.merged_results.length}${t('trace.debug.merged_count_label')})</span></div>
            <span class="text-[9px] text-green-600">${t('trace.debug.merged_subtitle')}</span>
        </div>`;
        html += `<div class="space-y-2">`;
        debugInfo.merged_results.slice(0, 5).forEach((r, i) => {
            const mergedText = scoreBadge(r.score, { label: t('trace.debug.merged_score_label'), type: 'score', range: [0, 1] });
            const bm25Text = r.bm25_score !== undefined ? fmtScore(r.bm25_score) : '-';
            const vectorText = r.vector_score !== undefined ? fmtScore(r.vector_score) : '-';
            html += `<div class="bg-green-100/50 rounded p-2">`;
            html += `<div class="flex justify-between items-center mb-1">`;
            html += `<span class="text-xs font-medium text-green-800">${t('trace.debug.route_doc')} ${i + 1}</span>`;
            html += `<span class="text-[11px]">${t('trace.debug.merged_score_label')}: ${mergedText}</span>`;
            html += `</div>`;
            if (r.bm25_score !== undefined || r.vector_score !== undefined) {
                html += `<div class="text-[10px] text-green-600 mb-1 flex flex-wrap gap-x-3 gap-y-0.5">
                    <span>${t('trace.debug.merged_bm25_norm')} <span class="font-medium text-green-700">${bm25Text}</span></span>
                    <span>${t('trace.debug.merged_vector_cos')} <span class="font-medium text-green-700">${vectorText}</span></span>
                </div>`;
            }
            html += `<div class="text-xs text-green-900">${escapeHtml(r.text ? (r.text.length > 100 ? r.text.slice(0, 100) + '…' : r.text) : '')}</div>`;
            html += `<div class="text-[10px] text-green-600 mt-1">`;
            html += `${t('trace.debug.route_file')} ${escapeHtml(r.file_name || t('trace.debug.route_unknown'))}`;
            if (r.chunk_index !== undefined) {
                html += ` · Chunk: ${r.chunk_index + 1}/${r.total_chunks || '-'}`;
            }
            html += `</div>`;
            html += `</div>`;
        });
        if (debugInfo.merged_results.length > 5) html += `<div class="text-[10px] text-green-600 text-center">${t('trace.debug.merged_top5_hint')}</div>`;
        html += `</div></div>`;
    }

    content.innerHTML = html;
}

function toggleDebugPanel() {
    const content = document.getElementById('debugContent');
    content.classList.toggle('hidden');
}

async function fetchEvaluation(traceId) {
    try {
        const data = await fetchJSON(`/api/chat/trace/${traceId}`);
        if (data.success) {
            if (data.data?.evaluation) {
                renderEvaluation(data.data.evaluation);
            }
            if (data.data?.debug_info) {
                renderDebugInfo(data.data.debug_info);
            }
            if (data.data?.intent_info) {
                renderIntentCard(data.data.intent_info);
            }
            if (data.data?.sentence_tracing) {
                renderSentenceTracing(data.data.sentence_tracing, data.data.drift_analysis);
            }
            if (data.data?.contradictions) {
                state.traceData.contradictions = data.data.contradictions;
            }
            if (data.data?.recall_diagnosis) {
                renderRecallDiagnosis(data.data.recall_diagnosis);
            }
        }
    } catch (e) {
        console.error('获取评估结果失败:', e);
    }
}

function renderIntentCard(intentInfo) {
    if (!intentInfo) return;
    state.traceData.intent = intentInfo;
    updateSummaryView();

    const card = document.getElementById('intentCard');
    card.classList.remove('hidden');

    const confRatio = +(intentInfo.confidence ?? 0);
    const confPercent = Math.round(confRatio * 100);
    let confCls = 'bg-green-100 text-green-700';
    if (confPercent < 50) confCls = 'bg-red-100 text-red-700';
    else if (confPercent < 75) confCls = 'bg-yellow-100 text-yellow-700';

    let html = `
        <div class="flex items-start justify-between mb-2">
            <div class="flex items-center gap-2">
                <span class="text-lg">🎯</span>
                <span class="font-medium text-slate-800">${escapeHtml(t('trace.intent_title'))}</span>
            </div>
            <div class="flex items-center gap-2">
                <span class="text-xs px-2 py-0.5 rounded-full font-medium ${confCls}"
                      title="${escapeHtml(t('trace.intent_conf_tip'))}">
                    ${escapeHtml(t('trace.intent_conf_pct').replace('{p}', confPercent))}
                </span>
                <button onclick="openIntentFeedback()" class="text-[10px] text-red-500 hover:text-red-700 hover:bg-red-50 px-1.5 py-0.5 rounded transition-colors" title="${escapeHtml(t('trace.intent_fix_tip'))}">
                    ${escapeHtml(t('trace.intent_fix'))}
                </button>
            </div>
        </div>
        <div class="bg-gradient-to-r from-blue-50 to-cyan-50 rounded-lg p-3 mb-2">
            <div class="text-sm font-semibold text-blue-800 mb-1">${escapeHtml(intentInfo.intent_type || t('trace.intent_unknown'))}</div>
            <div class="text-xs text-slate-600">${escapeHtml(intentInfo.interpretation || '')}</div>
        </div>
    `;

    if (intentInfo.keywords && intentInfo.keywords.length > 0) {
        html += `
            <div class="mb-2">
                <div class="text-[10px] font-medium text-slate-500 mb-1">${escapeHtml(t('trace.intent_keywords'))}</div>
                <div class="flex flex-wrap gap-1">
                    ${intentInfo.keywords.map(k => `
                        <span class="text-xs bg-indigo-100 text-indigo-700 px-2 py-0.5 rounded">${escapeHtml(k)}</span>
                    `).join('')}
                </div>
            </div>
        `;
    }

    if (intentInfo.constraints) {
        const constraintsHtml = [];
        if (intentInfo.constraints.time && intentInfo.constraints.time.length > 0) {
            constraintsHtml.push(`
                <div class="flex items-center gap-1">
                    <span class="text-[10px] text-orange-500">⏰</span>
                    <span class="text-xs text-slate-600">${escapeHtml(t('trace.constraint_time').replace('{v}', intentInfo.constraints.time.map(c => c.value).join(', ')))}</span>
                </div>
            `);
        }
        if (intentInfo.constraints.region && intentInfo.constraints.region.length > 0) {
            constraintsHtml.push(`
                <div class="flex items-center gap-1">
                    <span class="text-[10px] text-purple-500">📍</span>
                    <span class="text-xs text-slate-600">${escapeHtml(t('trace.constraint_region').replace('{v}', intentInfo.constraints.region.map(c => c.value).join(', ')))}</span>
                </div>
            `);
        }
        if (constraintsHtml.length > 0) {
            html += `
                <div class="bg-slate-50 rounded-lg p-2">
                    ${constraintsHtml.join('')}
                </div>
            `;
        }
    }

    if (intentInfo.business_context) {
        html += `
            <div class="mt-2 text-[10px] text-slate-500 italic">
                ${escapeHtml(t('trace.business_context_label').replace('{v}', intentInfo.business_context))}
            </div>
        `;
    }

    document.getElementById('intentCardContent').innerHTML = html;
}

function renderSentenceTracing(sentenceTracing, driftAnalysis) {
    if (!sentenceTracing || sentenceTracing.length === 0) return;
    state.traceData.sentenceTracing = sentenceTracing;
    state.traceData.driftAnalysis = driftAnalysis;
    updateSummaryView();

    const card = document.getElementById('sentenceTracingCard');
    card.classList.remove('hidden');

    // Colours are handled centrally by themeLevelColor, which picks a value per theme
    const themeLevelColorLocal = themeLevelColor;

    // Attribution statistics: citation-verified (basis = marker) / similarity heuristic /
    // unverified (tracing failed).
    const attributionCounts = sentenceTracing.reduce((acc, item) => {
        const key = (item && item.attribution) || 'none';
        acc[key] = (acc[key] || 0) + 1;
        return acc;
    }, {});
    const unverifiedCount = sentenceTracing.filter(item => item && item.tracing_error).length;
    const contradictedCount = sentenceTracing.filter(item => item && item.has_contradiction).length;
    const attributionSummaryHtml = `
        <div class="text-[10px] text-slate-500 mb-1">
            ${escapeHtml(t('trace.attribution_counts')
                .replace('{a}', attributionCounts['citation'] || 0)
                .replace('{b}', attributionCounts['similarity'] || 0)
                .replace('{c}', unverifiedCount))}
            ${contradictedCount > 0 ? `<span class="ml-1 px-1.5 py-0.5 rounded border bg-red-50 text-red-700 border-red-200">${escapeHtml(t('trace.contradiction_count').replace('{n}', contradictedCount))}</span>` : ''}
        </div>
        <div class="text-[10px] text-slate-400 mb-3 leading-relaxed" title="${escapeHtml(t('trace.heuristic_note'))}">
            ${escapeHtml(t('trace.heuristic_note'))}
        </div>`;

    let html = `
        <div class="flex items-start justify-between mb-3 gap-2">
            <div class="flex items-center gap-2">
                <span class="text-lg">📋</span>
                <span class="font-medium text-slate-800">${escapeHtml(t('trace.sentence_level_title'))}</span>
                <svg class="w-3 h-3 text-slate-400 cursor-help" fill="none" stroke="currentColor" viewBox="0 0 24 24"
                     title="${escapeHtml(t('trace.sentence_level_tip'))}">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
                </svg>
            </div>
            ${driftAnalysis ? `
                <div class="text-xs text-right shrink-0">
                    <div class="flex items-center justify-end gap-1 mb-0.5">
                        <span class="text-slate-500">${escapeHtml(t('trace.drift_rate_label'))}</span>
                        <span class="${driftAnalysis.drift_rate > 0.3 ? 'text-red-600 font-medium' : 'text-green-600'}">
                            ${fmtRatio(driftAnalysis.drift_rate)}
                        </span>
                    </div>
                    <div class="text-slate-400 text-[10px]" title="${escapeHtml(t('trace.counts_tip'))}">
                        ${fmtCount(driftAnalysis.citation_verified_count || 0, t('trace.count_abbr_citation'))} +
                        ${fmtCount(driftAnalysis.direct_quote_count || 0, t('trace.count_abbr_direct'))} +
                        ${fmtCount(driftAnalysis.summary_count || 0, t('trace.count_abbr_summary'))} /
                        ${fmtCount(driftAnalysis.total_sentences || 0, t('trace.count_abbr_sentence'))}
                    </div>
                </div>
            ` : ''}
        </div>
        ${attributionSummaryHtml}
        <div class="space-y-2">
    `;

    sentenceTracing.forEach((trace, i) => {
        const level = trace.confidence_level || 'unknown';
        const color = themeLevelColorLocal(level);
        const info = TRACE_TERM_MAP[level] || null;
        const label = termName(info, t('trace.term.unknown'));
        // Attribution route: citation (evidence) / similarity heuristic (lead) / none
        const attributionLabel = trace.attribution === 'citation'
            ? t('trace.attribution_citation')
            : (trace.attribution === 'similarity' ? t('trace.attribution_similarity') : t('trace.attribution_none'));

        // sources: prefer text_preview (already returned by the backend), falling back to chunk_id
        const sourcesHtml = trace.sources && trace.sources.length > 0 ? `
            <div class="mt-1.5 space-y-1">
                <div class="text-[10px] text-slate-400 mb-0.5">${escapeHtml(t('trace.source_chunks_note'))}</div>
                ${trace.sources.map(s => {
                    const scoreBadgeHtml = scoreBadge(s.score, { label: t('trace.match_degree'), type: 'score', range: [0, 1] });
                    let preview = s.text_preview || s.text || '';
                    preview = preview.length > 120 ? preview.slice(0, 120) + '…' : preview;
                    const idPart = s.chunk_id ? `<span class="text-[10px] text-slate-400 mr-1">${escapeHtml(s.chunk_id)}</span>` : '';
                    const citedTag = s.cited
                        ? `<span class="text-[10px] text-teal-600 font-medium mr-1">${escapeHtml(t('trace.attribution_citation'))}</span>`
                        : '';
                    return `
                        <div class="bg-slate-50 rounded border-l-2 px-2 py-1.5 text-[10px]" style="border-left-color:${color}">
                            <div class="flex items-center justify-between mb-0.5">
                                <span class="truncate">${citedTag}${idPart}</span>
                                <span>${escapeHtml(t('trace.match_degree'))}: ${scoreBadgeHtml}</span>
                            </div>
                            <div class="text-slate-600 whitespace-pre-wrap">${escapeHtml(preview || t('trace.no_preview'))}</div>
                        </div>
                    `;
                }).join('')}
            </div>
        ` : (trace.tracing_error
            ? `<div class="mt-1 text-[10px] text-slate-500 italic">${escapeHtml(t('trace.tracing_unavailable'))}</div>`
            : `<div class="mt-1 text-[10px] text-red-500 italic">${escapeHtml(t('trace.no_source_found'))}</div>`);

        const driftWarning = trace.is_drift ? `
            <div class="mt-1 px-2 py-1 bg-red-50 rounded text-[10px] text-red-600">
                ${escapeHtml(t('trace.drift_reason_label'))}${escapeHtml(trace.drift_reason || t('trace.drift_reason_default'))}
            </div>
        ` : '';

        // Contradiction details: what the answer states vs what the source states,
        // with the sentence of the source it was compared against.
        const contraItems = trace.contradictions || [];
        const contradictionHtml = contraItems.length ? `
            <div class="mt-1 px-2 py-1 bg-red-50 border border-red-200 rounded text-[10px] text-red-700 space-y-0.5">
                <div class="font-medium">${escapeHtml(t('trace.contradiction_title'))}</div>
                ${contraItems.map(item => `<div>• ${escapeHtml(item.explanation || '')}</div>`).join('')}
                ${contraItems.map(item => item.source_sentence
                    ? `<div class="text-red-500/90">${escapeHtml(t('trace.contradiction_source'))}${escapeHtml(item.source_sentence)}</div>`
                    : '').join('')}
            </div>
        ` : '';

        const levelDesc = termDesc(info);
        const levelBadgeDesc = levelDesc ? `title="${escapeHtml(levelDesc)}"` : '';
        const similarityNote = (trace.similarity_level && trace.attribution === 'citation')
            ? `title="${escapeHtml(t('trace.similarity_note').replace('{v}', trace.similarity_level))}"`
            : '';
        html += `
            <div class="border border-slate-200 rounded-lg p-2 hover:bg-slate-50">
                <div class="flex items-center gap-2 mb-1 flex-wrap">
                    <span class="text-[10px] font-medium text-slate-400">${escapeHtml(t('trace.sentence_n').replace('{n}', i + 1))}</span>
                    <span class="text-[10px] px-1.5 py-0.5 rounded"
                          style="background-color: ${color}20; color: ${color}" ${levelBadgeDesc} ${similarityNote}>
                        ${label}
                    </span>
                    <span class="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-500">
                        ${escapeHtml(t('trace.attribution_label'))}: ${escapeHtml(attributionLabel)}
                    </span>
                </div>
                <div class="text-xs text-slate-700 leading-relaxed">${escapeHtml(trace.sentence)}</div>
                ${sourcesHtml}
                ${driftWarning}
                ${contradictionHtml}
            </div>
        `;
    });

    html += '</div>';
    document.getElementById('sentenceTracingContent').innerHTML = html;
}

function renderRecallDiagnosis(diagnosis) {
    if (!diagnosis || !diagnosis.enabled) return;
    state.traceData.diagnosis = diagnosis;
    updateSummaryView();

    const card = document.getElementById('recallDiagnosisCard');
    card.classList.remove('hidden');

    const severityColors = {
        'high': 'bg-red-100 text-red-700 border-red-200',
        'medium': 'bg-yellow-100 text-yellow-700 border-yellow-200',
        'low': 'bg-blue-100 text-blue-700 border-blue-200'
    };

    const statusColors = {
        'critical': 'text-red-600',
        'warning': 'text-yellow-600',
        'healthy': 'text-green-600'
    };

    const statusIcons = {
        'critical': '🔴',
        'warning': '🟡',
        'healthy': '🟢'
    };

    let html = `
        <div class="flex items-start justify-between mb-3">
            <div class="flex items-center gap-2">
                <span class="text-lg">🔍</span>
                <span class="font-medium text-slate-800">${escapeHtml(t('trace.diag_title'))}</span>
            </div>
            ${diagnosis.summary ? `
                <div class="text-xs">
                    <span class="${statusColors[diagnosis.summary.overall_status] || 'text-slate-600'}">
                        ${statusIcons[diagnosis.summary.overall_status] || ''}
                        ${escapeHtml(diagnosis.summary.overall_status === 'critical' ? t('trace.diag_critical')
                            : diagnosis.summary.overall_status === 'warning' ? t('trace.diag_warning') : t('trace.diag_healthy'))}
                    </span>
                    ${diagnosis.summary.total_issues > 0 || diagnosis.summary.total_warnings > 0 ? `
                        <span class="text-slate-400 ml-1">
                            ${escapeHtml(t('trace.diag_counts')
                                .replace('{i}', diagnosis.summary.total_issues)
                                .replace('{w}', diagnosis.summary.total_warnings))}
                        </span>
                    ` : ''}
                </div>
            ` : ''}
        </div>
    `;

    if (diagnosis.summary?.suggestions && diagnosis.summary.suggestions.length > 0) {
        html += `
            <div class="mb-3 p-2 bg-blue-50 rounded-lg border border-blue-100">
                <div class="text-[10px] font-medium text-blue-700 mb-1">${escapeHtml(t('trace.diag_suggestions'))}</div>
                <ul class="space-y-1">
                    ${diagnosis.summary.suggestions.map(s => `
                        <li class="text-[10px] text-blue-600">• ${escapeHtml(s)}</li>
                    `).join('')}
                </ul>
            </div>
        `;
    }

    if (diagnosis.diagnosis_items && diagnosis.diagnosis_items.length > 0) {
        html += `<div class="space-y-2">`;

        diagnosis.diagnosis_items.forEach(item => {
            const colorClass = severityColors[item.severity] || severityColors['medium'];
            html += `
                <div class="border ${colorClass} border-opacity-30 rounded-lg p-2">
                    <div class="flex items-center gap-2 mb-1">
                        <span class="text-[10px] font-medium">${item.title}</span>
                        <span class="text-[9px] px-1.5 py-0.5 rounded border ${colorClass}">
                            ${escapeHtml(item.severity === 'high' ? t('trace.severity_high') : item.severity === 'medium' ? t('trace.severity_medium') : t('trace.severity_low'))}
                        </span>
                    </div>
                    <div class="text-[10px] text-slate-600">${escapeHtml(item.description)}</div>
                </div>
            `;
        });

        html += `</div>`;
    }

    if (diagnosis.potential_misses && diagnosis.potential_misses.length > 0) {
        // Root-cause label style map
        const rootCauseCls = {
            'threshold_edge': 'bg-orange-100 text-orange-700',
            'keyword_missing': 'bg-yellow-100 text-yellow-700',
            'embedding_mismatch': 'bg-red-100 text-red-700',
            'partial_match': 'bg-blue-100 text-blue-700'
        };
        html += `
            <div class="mt-3">
                <div class="text-[10px] font-medium text-slate-700 mb-2">${escapeHtml(t('trace.diag_potential_misses').replace('{n}', diagnosis.potential_misses.length))}</div>
                <div class="space-y-2">
                    ${diagnosis.potential_misses.slice(0, 5).map(doc => {
                        const causeKey = doc.root_cause || 'partial_match';
                        // The backend keeps an English label in root_cause_zh; prefer the
                        // localized miss-cause label derived from the structured code.
                        const causeZh = missCauseInfo(causeKey).label;
                        const causeCls = rootCauseCls[causeKey] || rootCauseCls.partial_match;
                        const simBadge = scoreBadge(doc.similarity, { label: t('trace.similarity_label'), type: 'score', range: [0, 1] });
                        const fileName = doc.metadata?.file_name || doc.id?.slice(0, 12) || t('trace.unknown_doc');
                        return `
                        <div class="border border-slate-200 rounded-lg p-2 hover:bg-slate-50">
                            <div class="flex items-center justify-between mb-1 gap-1">
                                <span class="text-[10px] text-slate-600 truncate flex-1" title="${escapeHtml(doc.metadata?.file_name || '')}">${escapeHtml(fileName)}</span>
                                <span class="text-[9px] px-1.5 py-0.5 rounded font-medium shrink-0 ${causeCls}"
                                      title="${escapeHtml(t('trace.diag_root_cause_tip').replace('{v}', causeZh))}">${escapeHtml(causeZh)}</span>
                            </div>
                            <div class="flex items-center justify-between mb-1 text-[10px]">
                                <span class="text-slate-500">${escapeHtml(t('trace.similarity_label'))}: ${simBadge}</span>
                                <span class="text-slate-400">${escapeHtml(t('trace.keyword_overlap').replace('{n}', fmtCount(doc.keyword_overlap || 0, '')))}</span>
                            </div>
                            <div class="text-[10px] text-slate-600 line-clamp-2">${escapeHtml(doc.text_preview)}</div>
                            <div class="text-[9px] text-slate-400 mt-1">${escapeHtml(doc.reason)}</div>
                        </div>
                    `;}).join('')}
                </div>
            </div>
        `;
    }

    if (diagnosis.metadata_filter_analysis && diagnosis.metadata_filter_analysis.filtered_count > 0) {
        const mfa = diagnosis.metadata_filter_analysis;
        html += `
            <div class="mt-3 p-2 bg-orange-50 rounded-lg border border-orange-100">
                <div class="text-[10px] font-medium text-orange-700 mb-1">${escapeHtml(t('trace.diag_metadata_filter'))}</div>
                <div class="text-[10px] text-slate-600">${escapeHtml(mfa.summary)}</div>
                ${mfa.filtered_docs && mfa.filtered_docs.length > 0 ? `
                    <div class="mt-2 space-y-1">
                        ${mfa.filtered_docs.slice(0, 3).map(doc => `
                            <div class="text-[9px] text-slate-500">
                                • ${escapeHtml(doc.id)}: ${escapeHtml(doc.filters_applied.join(', '))}
                            </div>
                        `).join('')}
                    </div>
                ` : ''}
            </div>
        `;
    }

    if (diagnosis.score_threshold_analysis && diagnosis.score_threshold_analysis.near_threshold_count > 0) {
        const sta = diagnosis.score_threshold_analysis;
        const avgBadge = sta.avg_score !== undefined ? scoreBadge(sta.avg_score, { label: t('trace.diag_avg_score_label'), type: 'score', range: [0, 1] }) : '-';
        html += `
            <div class="mt-3 p-2 bg-purple-50 rounded-lg border border-purple-100">
                <div class="text-[10px] font-medium text-purple-700 mb-1">${escapeHtml(t('trace.diag_score_threshold'))}</div>
                <div class="text-[10px] text-slate-600">${escapeHtml(sta.summary)}</div>
                <div class="flex items-center justify-between mt-1 text-[10px]">
                    <span class="text-slate-500">${escapeHtml(t('trace.diag_current_threshold').replace('{v}', fmtScore(sta.threshold)))}</span>
                    <span class="text-slate-500">${escapeHtml(t('trace.diag_avg_score_label'))}: ${avgBadge}</span>
                </div>
            </div>
        `;
    }

    if (diagnosis.mode_difference_analysis && 
        (diagnosis.mode_difference_analysis.bm25_only_count > 0 || 
         diagnosis.mode_difference_analysis.vector_only_count > 0)) {
        const mda = diagnosis.mode_difference_analysis;
        html += `
            <div class="mt-3 p-2 bg-indigo-50 rounded-lg border border-indigo-100">
                <div class="text-[10px] font-medium text-indigo-700 mb-1">${escapeHtml(t('trace.diag_mode_diff'))}</div>
                <div class="text-[10px] text-slate-600">${escapeHtml(mda.summary)}</div>
                ${mda.high_value_misses && mda.high_value_misses.length > 0 ? `
                    <div class="mt-2 text-[10px] text-red-600">
                        ${escapeHtml(t('trace.diag_high_value_misses').replace('{n}', mda.high_value_misses.length))}
                    </div>
                ` : ''}
            </div>
        `;
    }

    if (diagnosis.keyword_gap_analysis && diagnosis.keyword_gap_analysis.missing_keywords && 
        diagnosis.keyword_gap_analysis.missing_keywords.length > 0) {
        const kga = diagnosis.keyword_gap_analysis;
        html += `
            <div class="mt-3 p-2 bg-cyan-50 rounded-lg border border-cyan-100">
                <div class="text-[10px] font-medium text-cyan-700 mb-1">${escapeHtml(t('trace.diag_keyword_gap'))}</div>
                <div class="text-[10px] text-slate-600">${escapeHtml(kga.summary)}</div>
                ${kga.missing_keywords && kga.missing_keywords.length > 0 ? `
                    <div class="mt-2 flex flex-wrap gap-1">
                        ${kga.missing_keywords.slice(0, 5).map(kw => `
                            <span class="text-[9px] bg-cyan-100 text-cyan-700 px-1.5 py-0.5 rounded">
                                ${escapeHtml(kw)}
                            </span>
                        `).join('')}
                    </div>
                ` : ''}
            </div>
        `;
    }

    document.getElementById('recallDiagnosisContent').innerHTML = html;
}