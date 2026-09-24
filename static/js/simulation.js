/**
 * static/index.html - what-if analysis (drop a chunk, what happens to the answer)
 *
 * Calls POST /api/chat/simulate: select evidence chunks -> regenerate the answer -> compare it with
 * the original answer.
 * Split out of index.html's inline <script>.
 */

// ==================== What-if analysis (drop a chunk, what happens to the answer) ====================
// Calls the backend POST /api/chat/simulate: manually select evidence chunks -> regenerate the
// answer -> compare it with the original answer.

function simulationCandidates(td) {
    const kept = (td.funnel && td.funnel.kept) || null;
    if (kept && kept.length > 0) {
        // The funnel text is a 200-character preview: flag it as truncated and fetch the full text
        // on demand at run time
        return kept.map(k => ({
            id: String(k.id),
            text: k.text || '',
            text_truncated: true,
            score: k.score,
            metadata: {
                file_name: k.file_name,
                chunk_index: k.chunk_index,
                total_chunks: k.total_chunks
            }
        }));
    }
    return (td.results || []).map(r => ({
        id: String(r.id),
        text: r.text || '',
        text_truncated: r.text_truncated === true,
        score: r.score,
        metadata: r.metadata || {}
    }));
}

function closeSimulationDialog() {
    document.getElementById('simulationDialog').classList.add('hidden');
}

function openSimulationDialog() {
    const td = activeTraceData();
    const candidates = simulationCandidates(td);
    if (candidates.length === 0) {
        showToast('warning', t('sim.no_evidence'));
        return;
    }
    state._simCandidates = candidates;
    const listEl = document.getElementById('simChunkList');
    listEl.innerHTML = candidates.map((c, i) => {
        const md = c.metadata || {};
        const pos = (md.chunk_index !== undefined && md.chunk_index !== null)
            ? ` · Chunk ${md.chunk_index + 1}/${md.total_chunks || '-'}`
            : '';
        return `
            <label class="sim-chunk-item flex items-start gap-2 p-2 rounded-lg border border-slate-200 hover:bg-slate-50 cursor-pointer" data-index="${i}">
                <input type="checkbox" checked class="mt-0.5 sim-chunk-check">
                <span class="flex-1 min-w-0">
                    <span class="flex items-center justify-between gap-2">
                        <span class="text-[11px] font-medium text-slate-700 truncate">${escapeHtml(md.file_name || '-')}${escapeHtml(pos)}</span>
                        <span class="text-[10px] text-slate-400 shrink-0">${fmtScore(c.score || 0)}</span>
                    </span>
                    <span class="block text-[10px] text-slate-500 mt-0.5">${escapeHtml((c.text || '').slice(0, 90))}</span>
                </span>
            </label>`;
    }).join('');
    document.getElementById('simQueryText').textContent = td.userQuery || '';
    document.getElementById('simResult').innerHTML = '';
    document.getElementById('simStatus').innerHTML = '';
    document.getElementById('simCompareOriginal').checked = true;
    document.getElementById('simulationDialog').classList.remove('hidden');
}

function simMetricsLine(contextCount, sentenceCount, drift) {
    const parts = [
        `${t('sim.context_count')} ${contextCount}`,
        `${t('sim.sentence_count')} ${sentenceCount}`
    ];
    if (drift && drift.drift_rate !== undefined) {
        parts.push(`${t('sim.drift')} ${fmtRatio(drift.drift_rate)}`);
    }
    return `<div class="text-[10px] text-slate-500 mb-1">${escapeHtml(parts.join(' · '))}</div>`;
}

function simDeltaChip(label, text, tone) {
    const cls = tone === 'bad' ? 'text-red-600 border-red-200'
        : (tone === 'good' ? 'text-green-600 border-green-200' : 'text-slate-600 border-slate-200');
    return `<span class="text-[10px] px-2 py-0.5 rounded border bg-white ${cls}">${escapeHtml(label)} <span class="font-medium">${escapeHtml(text)}</span></span>`;
}

function simulationInsight(cmp) {
    if (!cmp) return '';
    const ctxDiff = cmp.context_count_diff || 0;
    const driftDiff = cmp.drift_rate_diff || 0;
    if (ctxDiff === 0) return t('sim.insight_no_change');
    if (driftDiff > 0.1) {
        return t('sim.insight_support').replace('{n}', Math.abs(ctxDiff)).replace('{d}', fmtRatio(driftDiff));
    }
    if (driftDiff <= 0) {
        return t('sim.insight_redundant').replace('{n}', Math.abs(ctxDiff)).replace('{d}', fmtRatio(driftDiff));
    }
    return t('sim.insight_minor').replace('{n}', Math.abs(ctxDiff)).replace('{d}', fmtRatio(driftDiff));
}

async function runSimulation() {
    const td = activeTraceData();
    const query = td.userQuery || '';
    const kbId = activeTraceKbId();
    const candidates = state._simCandidates || [];
    const statusEl = document.getElementById('simStatus');
    const runBtn = document.getElementById('simRunBtn');
    if (!kbId || !query || candidates.length === 0) return;
    if (state._simRunning) return;   // Prevent double submission (one simulation costs several LLM calls)

    const items = Array.prototype.slice.call(document.querySelectorAll('#simChunkList .sim-chunk-item'));
    const selected = [];
    const excluded = [];

    state._simRunning = true;
    if (runBtn) { runBtn.disabled = true; runBtn.textContent = t('sim.running'); }
    statusEl.innerHTML = `<span class="text-blue-600">${escapeHtml(t('sim.running'))}</span>`;
    try {
        for (const item of items) {
            const c = candidates[parseInt(item.getAttribute('data-index'), 10)];
            if (!item.querySelector('input[type=checkbox]').checked) {
                excluded.push(c.id);
                continue;
            }
            let text = c.text || '';
            // The text from streaming / the funnel is a truncated preview: the simulation must use
            // the complete source, otherwise the conclusion is skewed
            if (c.text_truncated || text.length < 40) {
                const full = await fetchChunkText(c.id);
                if (full && full.text) text = full.text;
            }
            selected.push({ id: c.id, text: text, score: c.score, metadata: c.metadata || {} });
        }

        if (selected.length === 0) {
            statusEl.innerHTML = `<span class="text-amber-600">${escapeHtml(t('sim.need_one'))}</span>`;
            return;
        }

        const data = await fetchJSON('/api/chat/simulate', {
            method: 'POST',
            body: JSON.stringify({
                kb_id: kbId,
                query: query,
                selected_chunks: selected,
                excluded_chunk_ids: excluded,
                compare_with_original: document.getElementById('simCompareOriginal').checked
            })
        });
        statusEl.innerHTML = '';
        renderSimulationResult(data, td);
    } catch (e) {
        console.error('假设分析失败:', e);
        statusEl.innerHTML = `<span class="text-red-600">${escapeHtml(t('sim.failed').replace('{error}', e.message || ''))}</span>`;
    } finally {
        state._simRunning = false;
        if (runBtn) { runBtn.disabled = false; runBtn.textContent = t('sim.run'); }
    }
}

function renderSimulationResult(data, td) {
    const originalResult = data.original_result || null;
    const originalAnswer = (originalResult && originalResult.answer) || td.aiAnswer || '';
    const originalDrift = (originalResult && originalResult.drift_analysis) || td.driftAnalysis || null;
    const originalTracing = (originalResult && originalResult.sentence_tracing) || td.sentenceTracing || null;
    const originalContext = originalResult ? (originalResult.context_count || 0) : (td.results || []).length;

    // The citation slots of the simulated answer follow the filtered_chunks order; they must not be
    // clickable to locate a real card
    const simCv = { citation_to_chunk_map: {}, missing_citations: [], valid_citations: 0, invalid_citations: 0 };
    (data.filtered_chunks || []).forEach((c, i) => { simCv.citation_to_chunk_map[String(i + 1)] = String(c.id); });

    const simAnswerHtml = renderAnswerWithProvenance(data.answer || '', '', {
        tracing: data.sentence_tracing || null,
        citationValidation: simCv,
        interactive: false
    });
    const originalHtml = renderAnswerWithProvenance(originalAnswer, '', {
        tracing: originalTracing,
        citationValidation: td.citationValidation || null,
        interactive: false
    });

    const cmp = data.comparison || null;
    const deltas = [];
    if (cmp) {
        deltas.push(simDeltaChip(t('sim.delta_context'), String(cmp.context_count_diff || 0),
            cmp.context_count_diff < 0 ? 'bad' : 'neutral'));
        deltas.push(simDeltaChip(t('sim.delta_answer_len'), String(cmp.answer_length_diff || 0), 'neutral'));
        deltas.push(simDeltaChip(t('sim.delta_drift'),
            (cmp.drift_rate_diff >= 0 ? '+' : '') + fmtRatio(cmp.drift_rate_diff || 0),
            cmp.drift_rate_diff > 0.1 ? 'bad' : (cmp.drift_rate_diff <= 0 ? 'good' : 'neutral')));
    }

    const insight = simulationInsight(cmp);

    document.getElementById('simResult').innerHTML = `
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div class="border border-slate-200 rounded-lg p-3">
                <div class="text-[11px] font-semibold text-slate-700 mb-1">${escapeHtml(t('sim.original_answer'))}</div>
                ${simMetricsLine(originalContext, (originalTracing || []).length, originalDrift)}
                <div class="text-xs text-slate-700 event-content">${originalHtml}</div>
            </div>
            <div class="border border-blue-200 bg-blue-50/30 rounded-lg p-3">
                <div class="text-[11px] font-semibold text-blue-700 mb-1">${escapeHtml(t('sim.simulated_answer'))}</div>
                ${simMetricsLine(data.chunk_count || 0, (data.sentence_tracing || []).length, data.drift_analysis || null)}
                <div class="text-xs text-slate-700 event-content">${simAnswerHtml}</div>
            </div>
        </div>
        ${deltas.length ? `<div class="mt-3 flex items-center gap-2 flex-wrap">${deltas.join('')}</div>` : ''}
        ${insight ? `<div class="mt-2 text-[11px] leading-relaxed text-slate-600 bg-slate-50 border border-slate-200 rounded p-2">💡 ${escapeHtml(insight)}</div>` : ''}
        ${data.error ? `<div class="mt-2 text-[11px] text-red-600">${escapeHtml(t('sim.model_error').replace('{error}', data.error))}</div>` : ''}
    `;
}
