/**
 * static/index.html - retrieval diagnostics display
 *
 * Content: citation validation (the citation_validation event), backend business diagnosis (the
 *       business_diagnosis event), the retrieval funnel (hit vs missed) and the on-demand
 *       whole-KB miss scan.
 * Split out of index.html's inline <script>; MISS_ROOT_CAUSE is the miss-reason dictionary.
 */

// ==================== Citation validation (the backend's citation_validation event) ====================
// Mandatory citations are a WhiteBoxRAG differentiator, but the backend's result used to be
// dropped by the frontend - it is displayed here.

function citationValidationHtml(cv) {
    if (!cv) return '';
    const valid = cv.valid_citations || 0;
    const invalid = cv.invalid_citations || 0;
    const found = (cv.found_citations || []).join('、');
    let badge;
    if (invalid > 0) {
        badge = `<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-red-100 text-red-700 font-medium">${t('citation.invalid').replace('{n}', invalid)}</span>`;
    } else if (valid > 0) {
        badge = `<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-green-100 text-green-700 font-medium">${t('citation.valid').replace('{n}', valid)}</span>`;
    } else {
        badge = `<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 font-medium">${t('citation.none')}</span>`;
    }
    return `
        <div class="flex items-center gap-2 flex-wrap mb-1">${badge}</div>
        ${found ? `<div class="text-[10px] text-slate-500">${escapeHtml(t('citation.found').replace('{v}', found))}</div>` : ''}
        ${cv.suggestion ? `<div class="text-[10px] text-slate-600 mt-1">${escapeHtml(cv.suggestion)}</div>` : ''}`;
}

function renderCitationValidation(cv, silent) {
    if (!cv) return;
    state.traceData.citationValidation = cv;
    if (!silent) updateSummaryView();
    const card = document.getElementById('citationValidationCard');
    const content = document.getElementById('citationValidationContent');
    if (!card || !content) return;
    card.classList.remove('hidden');
    content.innerHTML = citationValidationHtml(cv);
}

// ==================== Backend business diagnosis (the business_diagnosis event) ====================
// The backend already provides the business conclusion and the average similarity; this only
// localizes them, and the English original goes to the technical view so that English sentences
// do not pop up inside the Chinese UI.

function bizStatusLabel(status) {
    const map = {
        critical: 'biz.status_critical',
        warning: 'biz.status_warning',
        caution: 'biz.status_caution',
        healthy: 'biz.status_healthy'
    };
    return t(map[status] || 'biz.status_healthy');
}

function bizStatusClass(status) {
    if (status === 'critical') return 'bg-red-100 text-red-700 border-red-200';
    if (status === 'warning') return 'bg-amber-100 text-amber-700 border-amber-200';
    if (status === 'caution') return 'bg-yellow-100 text-yellow-700 border-yellow-200';
    return 'bg-green-100 text-green-700 border-green-200';
}

function renderBusinessDiagnosis(bd, silent) {
    if (!bd) return;
    state.traceData.businessDiagnosis = bd;
    if (!silent) updateSummaryView();
    const card = document.getElementById('businessDiagnosisCard');
    const content = document.getElementById('businessDiagnosisContent');
    if (!card || !content || !bd.overall_status) return;
    card.classList.remove('hidden');
    const suggestions = (bd.suggestions || []).map(s => `<li>• ${escapeHtml(s)}</li>`).join('');
    content.innerHTML = `
        <div class="text-[10px] text-slate-500 mb-1">${escapeHtml(t('biz.raw_note'))}</div>
        <div class="text-xs text-slate-700">${escapeHtml(bd.problem_summary || '')}</div>
        ${bd.root_cause ? `<div class="text-[10px] text-slate-500 mt-1">${escapeHtml(bd.root_cause)}</div>` : ''}
        ${suggestions ? `<ul class="text-[10px] text-slate-600 mt-1 space-y-0.5">${suggestions}</ul>` : ''}`;
}

// ==================== Retrieval funnel: hit vs missed (the white-box core) ====================
// Answers "which chunks hit the knowledge base, which did not, and why not":
// threshold filtering / context compression / top_k truncation are the three drop reasons, each
// reported with its gap or a tuning suggestion.

function funnelStagesHtml(counts) {
    const stages = [
        { key: 'funnel.stage_merged', n: counts.merged, cls: 'bg-slate-100 text-slate-700' },
        { key: 'funnel.stage_threshold', n: counts.after_threshold, cls: 'bg-amber-100 text-amber-700' },
        { key: 'funnel.stage_compression', n: counts.after_compression, cls: 'bg-purple-100 text-purple-700' },
        { key: 'funnel.stage_kept', n: counts.kept, cls: 'bg-green-100 text-green-700' }
    ];
    return `<div class="flex items-center gap-1 flex-wrap">
        ${stages.map((s, i) => `${i > 0 ? '<span class="text-slate-300">→</span>' : ''}<span class="text-[10px] px-1.5 py-0.5 rounded ${s.cls}">${escapeHtml(t(s.key))} ${s.n}</span>`).join('')}
    </div>`;
}

function droppedReason(item) {
    if (item.stage === 'threshold') {
        return t('funnel.reason_threshold')
            .replace('{t}', fmtScore(item.threshold || 0))
            .replace('{g}', fmtScore(item.gap || 0));
    }
    if (item.stage === 'top_k') {
        return t('funnel.reason_top_k').replace('{k}', item.top_k).replace('{r}', item.rank);
    }
    if (item.stage === 'compression') return t('funnel.reason_compression');
    return item.stage || '';
}

function droppedSuggestion(item) {
    if (item.stage === 'threshold') {
        // Suggested value = this chunk's score - 0.02, just enough to cross the threshold
        const target = Math.max(0, (item.score || 0) - 0.02);
        return t('funnel.suggest_threshold').replace('{v}', target.toFixed(2));
    }
    if (item.stage === 'top_k') return t('funnel.suggest_top_k').replace('{k}', item.top_k);
    if (item.stage === 'compression') return t('funnel.suggest_compression');
    return '';
}

function funnelCandidateHtml(item, opts) {
    const isKept = !!opts.kept;
    const route = (item.in_bm25 && item.in_vector) ? t('funnel.route_both')
        : (item.in_bm25 ? t('funnel.route_bm25') : (item.in_vector ? t('funnel.route_vector') : ''));
    const chunkPos = (item.chunk_index !== undefined && item.chunk_index !== null)
        ? ` · Chunk ${item.chunk_index + 1}/${item.total_chunks || '-'}`
        : '';
    const stageCls = {
        threshold: 'text-amber-600',
        top_k: 'text-purple-600',
        compression: 'text-slate-500'
    }[item.stage] || 'text-slate-500';
    return `
        <div class="border ${isKept ? 'border-green-200 bg-green-50/40' : 'border-slate-200'} rounded-lg p-2">
            <div class="flex items-center justify-between gap-2 mb-1">
                <span class="text-[11px] font-medium text-slate-700 truncate flex-1">${escapeHtml(item.file_name || '-')}${escapeHtml(chunkPos)}</span>
                <span class="text-[10px] px-1.5 py-0.5 rounded ${isKept ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-600'} shrink-0">
                    ${escapeHtml(isKept ? t('funnel.in_prompt') : t('funnel.not_in_prompt'))}
                </span>
            </div>
            <div class="flex items-center gap-2 flex-wrap text-[10px] text-slate-500">
                <span>${escapeHtml(t('funnel.candidate_fused').replace('{v}', fmtScore(item.score || 0)))}</span>
                ${item.bm25_score ? `<span>${escapeHtml(t('funnel.candidate_bm25').replace('{v}', fmtScore(item.bm25_score)))}</span>` : ''}
                ${item.vector_score ? `<span>${escapeHtml(t('funnel.candidate_vector').replace('{v}', fmtScore(item.vector_score)))}</span>` : ''}
                ${route ? `<span class="px-1 rounded bg-slate-100">${escapeHtml(route)}</span>` : ''}
                ${item.compressed ? '<span class="px-1 rounded bg-purple-100 text-purple-700">compressed</span>' : ''}
            </div>
            ${item.text ? `<div class="text-[10px] text-slate-500 mt-1 leading-relaxed">${escapeHtml(item.text.length > 120 ? item.text.slice(0, 120) + '…' : item.text)}</div>` : ''}
            ${isKept ? '' : `
                <div class="text-[10px] mt-1 ${stageCls}">🚫 ${escapeHtml(droppedReason(item))}</div>
                ${droppedSuggestion(item) ? `<div class="text-[10px] text-blue-600 mt-0.5">${escapeHtml(droppedSuggestion(item))}</div>` : ''}`}
        </div>`;
}

function funnelHtml(funnel, skipped) {
    const counts = funnel.counts || {};
    const kept = funnel.kept || [];
    const dropped = funnel.dropped || [];
    const params = funnel.params || {};
    const skippedNotice = (skipped && skipped.potential_misses)
        ? `<div class="mt-2 text-[10px] leading-relaxed text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">${escapeHtml(t('funnel.misses_skipped'))}</div>`
        : '';
    return `
        <div class="text-[10px] text-slate-500 mb-1.5">${escapeHtml(t('funnel.title'))}</div>
        ${funnelStagesHtml(counts)}
        <div class="text-[10px] text-slate-400 mt-1">
            ${escapeHtml(t('trace.candidates_total'))} ${counts.merged || 0} · ${escapeHtml(t('trace.entered_answer'))} ${counts.kept || 0} · ${escapeHtml(t('trace.dropped_total'))} ${counts.dropped || 0}
            ${params.similarity_threshold !== undefined ? escapeHtml(t('funnel.params_threshold').replace('{v}', fmtScore(params.similarity_threshold))) : ''}
            ${params.top_k !== undefined ? ` · top_k ${params.top_k}` : ''}
        </div>
        ${skippedNotice}
        <div class="mt-3">
            <div class="text-[11px] font-semibold text-green-700 mb-1.5">${escapeHtml(t('funnel.kept_title'))} (${kept.length})</div>
            <div class="space-y-2">
                ${kept.length ? kept.map(item => funnelCandidateHtml(item, { kept: true })).join('')
                    : `<div class="text-[10px] text-slate-400">${escapeHtml(t('funnel.no_dropped'))}</div>`}
            </div>
        </div>
        <div class="mt-3">
            <div class="text-[11px] font-semibold text-slate-600 mb-1.5">${escapeHtml(t('funnel.dropped_title'))} (${dropped.length})</div>
            <div class="space-y-2">
                ${dropped.length ? dropped.map(item => funnelCandidateHtml(item, { kept: false })).join('')
                    : `<div class="text-[10px] text-slate-400">${escapeHtml(t('funnel.no_dropped'))}</div>`}
            </div>
        </div>
        <div class="mt-3 pt-3 border-t border-slate-200">
            <div class="flex items-center gap-2 flex-wrap mb-1">
                <button onclick="runMissScan()" class="text-[10px] px-2 py-1 rounded border border-blue-200 text-blue-600 hover:bg-blue-50 transition-colors">${escapeHtml(t('miss.button'))}</button>
                <button onclick="openSimulationDialog()" class="text-[10px] px-2 py-1 rounded border border-purple-200 text-purple-600 hover:bg-purple-50 transition-colors">${escapeHtml(t('sim.button'))}</button>
                <span class="text-[10px] text-slate-400">${escapeHtml(t('funnel.actions_hint'))}</span>
            </div>
            <div class="miss-scan-result"></div>
        </div>`;
}

function renderRetrievalFunnel(funnel, skipped, silent) {
    if (!funnel || !funnel.counts) return;
    state.traceData.funnel = funnel;
    state.traceData.skipped = skipped || null;
    if (!silent) updateSummaryView();
    const summaryEl = document.getElementById('summaryFunnelContent');
    if (summaryEl) summaryEl.innerHTML = funnelHtml(funnel, skipped);
    const techCard = document.getElementById('funnelPanel');
    const techEl = document.getElementById('funnelPanelContent');
    if (techCard && techEl) {
        techCard.classList.remove('hidden');
        techEl.innerHTML = funnelHtml(funnel, skipped);
    }
    updateTraceBadge();
}

// ==================== Miss scan (on demand, whole KB) ====================
// A regular Q&A never scans the whole KB (it would double the retrieval time), so the funnel only
// covers the chunks that made it into the candidate list. This button covers the part that was
// never retrieved: the backend re-embeds the chunks and compares them against the threshold.

const MISS_ROOT_CAUSE = {
    threshold_edge: { labelKey: 'miss.cause_threshold_edge', tipKey: 'miss.tip_threshold_edge' },
    keyword_missing: { labelKey: 'miss.cause_keyword_missing', tipKey: 'miss.tip_keyword_missing' },
    embedding_mismatch: { labelKey: 'miss.cause_embedding_mismatch', tipKey: 'miss.tip_embedding_mismatch' },
    partial_match: { labelKey: 'miss.cause_partial_match', tipKey: 'miss.tip_partial_match' }
};

function missCauseInfo(cause) {
    const info = MISS_ROOT_CAUSE[cause] || {};
    return {
        label: info.labelKey ? t(info.labelKey) : (cause || ''),
        tip: info.tipKey ? t(info.tipKey) : ''
    };
}

function missScanResultHtml(data) {
    const misses = data.potential_misses || [];
    const meta = t('miss.meta')
        .replace('{n}', data.retrieved_count || 0)
        .replace('{s}', data.duration || 0)
        .replace('{l}', data.scan_limit || 0);

    if (!data.diagnostic_enabled) {
        return `<div class="text-[10px] leading-relaxed text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">${escapeHtml(t('miss.disabled'))}</div>`;
    }
    if (misses.length === 0) {
        return `<div class="text-[10px] leading-relaxed text-slate-500 bg-slate-50 border border-slate-200 rounded p-2">${escapeHtml(meta)}<br>${escapeHtml(t('miss.none'))}</div>`;
    }
    return `
        <div class="text-[10px] text-slate-500 mb-1">${escapeHtml(meta)}</div>
        <div class="space-y-2">
            ${misses.map(m => {
                const info = missCauseInfo(m.root_cause);
                const md = m.metadata || {};
                const pos = (md.chunk_index !== undefined && md.chunk_index !== null)
                    ? ` · Chunk ${md.chunk_index + 1}/${md.total_chunks || '-'}`
                    : '';
                const metrics = t('miss.metrics')
                    .replace('{s}', fmtScore(m.similarity || 0))
                    .replace('{t}', fmtScore(data.threshold || 0))
                    .replace('{k}', m.keyword_overlap || 0);
                return `
                    <div class="border border-amber-200 bg-amber-50/40 rounded-lg p-2">
                        <div class="flex items-center justify-between gap-2 mb-1">
                            <span class="text-[11px] font-medium text-slate-700 truncate flex-1">${escapeHtml(md.file_name || t('miss.unknown_doc'))}${escapeHtml(pos)}</span>
                            <span class="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 shrink-0" title="${escapeHtml(info.tip)}">${escapeHtml(info.label)}</span>
                        </div>
                        <div class="text-[10px] text-slate-500">${escapeHtml(metrics)}</div>
                        ${m.text_preview ? `<div class="text-[10px] text-slate-500 mt-1 leading-relaxed">${escapeHtml(m.text_preview)}</div>` : ''}
                    </div>`;
            }).join('')}
        </div>`;
}

function setMissScanResult(html) {
    document.querySelectorAll('.miss-scan-result').forEach(el => { el.innerHTML = html; });
}

async function runMissScan() {
    const td = activeTraceData();
    const snap = state.activeTraceMsgId ? state.messageTraces[state.activeTraceMsgId] : null;
    const query = td.userQuery || (snap && snap.query) || '';
    const kbId = activeTraceKbId();
    if (!kbId || !query) {
        showToast('warning', t('miss.no_query'));
        return;
    }
    setMissScanResult(`<div class="text-[10px] text-blue-600">${escapeHtml(t('miss.running'))}</div>`);
    try {
        const data = await fetchJSON('/api/chat/miss-scan', {
            method: 'POST',
            body: JSON.stringify({ kb_id: kbId, query: query })
        });
        setMissScanResult(missScanResultHtml(data));
    } catch (e) {
        console.error('漏召回扫描失败:', e);
        setMissScanResult(`<div class="text-[10px] text-red-600">${escapeHtml(t('miss.failed').replace('{error}', e.message || ''))}</div>`);
    }
}
