/**
 * static/index.html - white-box answer provenance rendering + the trace snapshot of every message
 *
 * Content: sentence-level tinting of the answer, [Document N] citation chips, contradiction markers,
 *       the message meta bar, and the saving / replay of msgId -> provenance snapshots
 *       (showMessageTrace / backToLiveTrace).
 * Split out of index.html's inline <script>; PROV_LEVELS is the sentence-confidence colour table.
 */

// ==================== White-box answer provenance rendering ====================
// Goal: while reading the answer the user can tell - without leaving it - whether a sentence is
// backed by the knowledge base or made up by the LLM, and the [Document N] citations in the answer
// can be clicked to locate the matching source card on the right.
// Note: hybrid answer mode (the "the following content comes from the LLM..." disclaimer injected
// by the backend) is handled by renderAnswerWithProvenance as well - the disclaimer paragraph becomes
// its own card while the body keeps its sentence-level provenance - so the old
// renderAiContentWithHybridStyle has been replaced and removed.

/**
 * The provenance data currently on display: a history message snapshot when one is being viewed,
 * otherwise the data of the latest Q&A.
 * Read paths such as the modal (showSentenceSource) must use it so they never pick up the sentence
 * index of "another Q&A".
 */
function activeTraceData() {
    if (state.activeTraceMsgId && state.messageTraces[state.activeTraceMsgId]) {
        return state.messageTraces[state.activeTraceMsgId].data;
    }
    return state.traceData;
}

/**
 * Look up "which message's provenance" a clicked element belongs to.
 * Without this constraint, clicking a sentence of message B while the panel shows message A's
 * provenance would read A's sentence index (data crossover).
 */
function traceDataForElement(el) {
    const host = el && el.closest ? el.closest('[data-msg-id]') : null;
    const msgId = host ? host.getAttribute('data-msg-id') : '';
    if (msgId && state.messageTraces[msgId]) {
        return state.messageTraces[msgId].data;
    }
    return activeTraceData();
}

/** Click a sentence to see the supporting source text; it does not interrupt a user selecting text to copy */
function onSentenceClick(idx, ev) {
    const sel = window.getSelection && window.getSelection();
    if (sel && String(sel).length > 0) return;
    showSentenceSource(idx, ev);
}

/** Streaming-stage progress hint (retrieving / generating), hidden automatically once the first token arrives */
function setPipelineStatus(msgId, message) {
    const msgDiv = document.getElementById(msgId);
    if (!msgDiv) return;
    const el = msgDiv.querySelector('.pipeline-status');
    if (!el) return;
    if (!message) {
        el.classList.add('hidden');
        el.innerHTML = '';
        return;
    }
    el.classList.remove('hidden');
    el.innerHTML = `<span class="inline-flex items-center gap-1">${escapeHtml(t('prov.pipeline_running').replace('{msg}', message))}</span>`;
}

/**
 * Sentence splitting for the streaming placeholder layout ONLY.
 *
 * Attribution (which sentence maps to which provenance) no longer depends on the indexes
 * produced here: the backend sends each sentence's character offsets (start/end) in the
 * sentence_provenance / sentence_tracing events and the client matches on those. The
 * separator rules mirror service/text_split.py (decimals, abbreviations, ellipsis,
 * closing quotes and citation markers are not cut apart), so this layout can differ from
 * the backend's sentence boundaries only marginally - and such a difference can just
 * leave a placeholder without a badge, never attach a badge to another sentence.
 *
 * Each segment keeps its raw characters and its offsets (relative to the input text) so
 * the answer's newlines and punctuation survive rendering.
 */
function splitSentencesForProvenance(text) {
    const parts = String(text === null || text === undefined ? '' : text).split(/([。！？.!?\n])/);
    const segs = [];
    let cursor = 0;
    for (let i = 0; i < parts.length; i += 2) {
        const core = parts[i] === undefined ? '' : parts[i];
        const delim = parts[i + 1] === undefined ? '' : parts[i + 1];
        const raw = core + delim;
        if (core.trim().length === 0) {
            // Empty segment (newlines only): merge it into the previous sentence's tail instead of
            // its raw text, so the previous raw stays "full sentence + delimiter" while the trailing
            // newlines live in tail on their own.
            // At render time the badge hugs the full stop and the tail sits outside the span as
            // paragraph spacing.
            if (segs.length > 0) {
                segs[segs.length - 1].tail += raw;
            } else {
                segs.push({ raw: raw, text: '', idx: -1, tail: '', start: cursor, end: cursor });
            }
            cursor += raw.length;
            continue;
        }
        const leading = core.length - core.trimStart().length;
        const trailing = core.length - core.trimEnd().length;
        segs.push({
            raw: raw,
            text: core.trim(),
            idx: -1,
            tail: '',
            start: cursor + leading,
            end: cursor + core.length - trailing
        });
        cursor += raw.length;
    }
    let n = 0;
    segs.forEach(s => { if (s.text) s.idx = n++; });
    return segs;
}

/**
 * Turn the trailing newlines of a sentence into real HTML structure.
 *
 * tailStr is the trailing newline string split out by splitSentencesForProvenance and can be:
 *   ""      - directly followed by the next sentence, no extra newline
 *   "\n"    - a single newline between sentences
 *   "\n\n"+ - between paragraphs (a more visible separator)
 *
 * The return value is appended right after the closing tag of the .prov-sentence span so the badge
 * hugs the full stop while the newlines / paragraph spacing stay outside the span.
 */
function renderTail(tailStr) {
    if (!tailStr) return '';
    const nlCount = (tailStr.match(/\n/g) || []).length;
    if (nlCount === 0) return '';
    const brHtml = '<br>'.repeat(nlCount);
    if (nlCount >= 2) {
        // Paragraph level: add a faint dashed line next to the <br> so that the paragraph spacing and
        // the badge binding are clearly distinguishable
        return `${brHtml}<span class="prov-tail prov-tail-para"></span>`;
    }
    return `${brHtml}<span class="prov-tail"></span>`;
}

/**
 * Count how many backend sentence indexes the "stripped / merged" prefix occupies.
 * Only segments ending with a delimiter are counted: when the last segment has no terminator (e.g.
 * the model omitted the newline and the backend merged the disclaimer text and the body into one
 * sentence) it does not occupy a separate index.
 */
function countProvenanceSegmentsConsumed(text) {
    return splitSentencesForProvenance(text).filter(s => s.text && /[。！？.!?\n]$/.test(s.raw)).length;
}

// Sentence confidence -> visual style (only risky sentences get a badge, so a wall of labels does not hurt readability)
const PROV_LEVELS = {
    // Citation-verified: the sentence carries [Document N] and that document is in this
    // answer's retrieved context (basis = citation, not similarity)
    citation_verified: {
        cls: 'citation_verified', badgeKey: 'prov.badge_citation', tipKey: 'prov.tip_citation',
        show: true, badgeStyle: 'background:#ccfbf1;color:#0f766e;'
    },
    direct_quote: {
        cls: 'quoted', badgeKey: 'prov.badge_quoted', tipKey: 'prov.tip_quoted',
        show: false, badgeStyle: 'background:#dcfce7;color:#15803d;'
    },
    summary: {
        cls: 'summary', badgeKey: 'prov.badge_summary', tipKey: 'prov.tip_summary',
        show: true, badgeStyle: 'background:#fef9c3;color:#a16207;'
    },
    low_confidence: {
        cls: 'low_confidence', badgeKey: 'prov.badge_low', tipKey: 'prov.tip_low',
        show: true, badgeStyle: 'background:#ffedd5;color:#c2410c;'
    },
    drift: {
        cls: 'no_source', badgeKey: 'prov.badge_none', tipKey: 'prov.tip_none',
        show: true, badgeStyle: 'background:#fee2e2;color:#b91c1c;'
    },
    no_source: {
        cls: 'no_source', badgeKey: 'prov.badge_none', tipKey: 'prov.tip_none',
        show: true, badgeStyle: 'background:#fee2e2;color:#b91c1c;'
    },
    // Unverified: the tracing component failed (e.g. embeddings unavailable), which is
    // not a hallucination, so it is shown in neutral grey
    unverified: {
        cls: 'unverified', badgeKey: 'prov.badge_unverified', tipKey: 'prov.tip_unverified',
        show: true, badgeStyle: 'background:#e2e8f0;color:#475569;'
    }
};

/** Inline enhancement (the input must already be escaped to avoid HTML injection) */
function applyInlineFormatting(escaped) {
    return escaped
        .replace(/\*\*([^*\n]{1,80})\*\*/g, '<strong>$1</strong>')
        .replace(/`([^`\n]{1,120})`/g, '<code class="px-1 rounded bg-slate-100 dark:bg-slate-800 text-[11px]">$1</code>');
}

/**
 * Citation markers [Document N] / [doc N] / [chunk_N] / [N] -> chip (valid / invalid colouring).
 * @param {string} escapedText already escaped text
 * @param {object} [cvOverride] explicit citation validation data (defaults to the displayed trace)
 * @param {boolean} [linkEnabled] whether the chip can be clicked to locate the card (the simulated
 *                               answer of a what-if run cannot locate a real card)
 */
function renderCitationChips(escapedText, cvOverride, linkEnabled) {
    const cv = cvOverride !== undefined ? cvOverride : activeTraceData().citationValidation;
    const map = (cv && cv.citation_to_chunk_map) || {};
    const invalid = new Set(((cv && cv.missing_citations) || []).map(String));
    const clickable = linkEnabled !== false;
    const chip = (numStr) => {
        const n = parseInt(numStr, 10);
        let cls = 'neutral';
        let title = t('prov.chip_tip').replace('{n}', n);
        if (invalid.has(String(numStr)) || invalid.has(String(n))) {
            cls = 'invalid';
            title = t('prov.chip_invalid_tip').replace('{n}', n);
        } else if (map[String(n)]) {
            cls = 'valid';
        }
        const onclick = clickable ? ` onclick="event.stopPropagation();focusSourceBySlot(${n}, event)"` : '';
        return `<span class="prov-chip ${cls}"${onclick}`
            + ` title="${escapeHtml(title)}">📄${n}</span>`;
    };
    return escapedText
        .replace(/\[\s*(?:[Dd]ocument|[Dd]oc|文档|chunk)\s*_?\s*(\d+)\s*\]/g, (m, num) => chip(num))
        .replace(/\[\s*(\d{1,2})\s*\]/g, (m, num) => chip(num));
}

/**
 * Build render segments from the backend's sentence offsets (relative to the full
 * answer text).
 *
 * Every item of the backend's sentence_tracing carries start/end/index; this slices the
 * body by those offsets and rebuilds the paragraph spacing (tail) from the text between
 * two sentences. As a result:
 *   - which sentence maps to which provenance is decided by the backend, the client no
 *     longer guesses sentence indexes;
 *   - the hybrid-mode "knowledge base declaration" prefix (offsets below bodyStart) is
 *     kept out of the body and rendered by the card above.
 * Returning null means this provenance data has no offsets (older records) and the
 * caller falls back to the local splitter.
 *
 * @param {string} body      answer body (declaration prefix already removed)
 * @param {Array}  tracing   backend sentence_tracing
 * @param {number} bodyStart offset of the body inside the full answer
 * @returns {Array|null}     segments shaped like splitSentencesForProvenance's
 */
function segmentsFromTrace(body, tracing, bodyStart) {
    const items = [];
    (tracing || []).forEach((trace, i) => {
        if (!trace || typeof trace.start !== 'number' || typeof trace.end !== 'number') return;
        if (trace.end <= bodyStart) return;          // declaration prefix: rendered by the card
        const start = Math.max(trace.start, bodyStart) - bodyStart;
        const end = trace.end - bodyStart;
        if (start < 0 || end > body.length || start >= end) return;
        items.push({ idx: i, start: start, end: end, text: body.slice(start, end) });
    });
    if (items.length === 0) return null;
    items.sort((a, b) => a.start - b.start);

    const segs = [];
    let cursor = 0;
    items.forEach(item => {
        const gap = body.slice(cursor, item.start);
        if (gap.trim() && segs.length > 0) {
            // Body text between two attributed sentences: kept as its own segment, so it
            // gets no badge and no text is swallowed.
            segs.push({ raw: gap, text: gap.trim(), idx: -1, start: cursor, end: cursor + gap.length, tail: '' });
        } else if (segs.length > 0) {
            segs[segs.length - 1].tail += gap;
        }
        segs.push({ raw: item.text, text: item.text, idx: item.idx, start: item.start, end: item.end, tail: '' });
        cursor = item.end;
    });
    const rest = body.slice(cursor);
    if (rest.trim()) {
        segs.push({ raw: rest, text: rest.trim(), idx: -1, start: cursor, end: cursor + rest.length, tail: '' });
    } else if (rest && segs.length > 0) {
        segs[segs.length - 1].tail += rest;
    }
    return segs;
}

/**
 * Render the answer body: sentence-level provenance tinting + citation chips; in hybrid answer mode
 * the "knowledge base disclaimer" paragraph becomes its own card.
 * Without sentence-level trace data the answer is still rendered sentence by sentence (the citation
 * chips stay clickable and the paragraph structure is preserved), but no sentence can get a trace,
 * so none of them carries a confidence class / badge.
 *
 * @param {string} content the answer text
 * @param {string} msgId   owning message id (written into data-msg-id so that clicking a sentence or
 *                         a citation reads this message's provenance)
 * @param {object} [opts]  optional overrides: { tracing, citationValidation, interactive } - the
 *                         "simulated answer" of a what-if run passes its own tracing and must not
 *                         click through to a real card
 */
function renderAnswerWithProvenance(content, msgId, opts) {
    const td = activeTraceData();
    const options = opts || {};
    const tracing = options.tracing !== undefined ? options.tracing : td.sentenceTracing;
    const cvOverride = options.citationValidation !== undefined ? options.citationValidation : td.citationValidation;
    const interactive = options.interactive !== false;
    const text = content || '';

    // Hybrid answer mode marker (injected by the backend's _ensure_hybrid_format)
    const markerRegex = /以下内容(?:来自|由)大模型(?:自身知识|补充生成)[，,]?仅供参考[：:]?\s*/;
    const match = text.match(markerRegex);
    let prefix = '';
    let body = text;
    let offset = 0;
    let bodyStart = 0;
    let kbNoteHtml = '';
    if (match) {
        prefix = text.substring(0, match.index);
        body = text.substring(match.index + match[0].length);
        bodyStart = match.index + match[0].length;
        // The disclaimer paragraph is itself split into sentences by the backend ("the knowledge base
        // has no ..." + "the following content comes from the LLM"), so the body's sentence indexes
        // must be shifted as a whole - by the count of the "consumed prefix", not just by the prefix
        // a visible card shows.
        offset = countProvenanceSegmentsConsumed(text.substring(0, bodyStart));
        kbNoteHtml = prefix.trim()
            ? `<div class="bg-amber-50 border border-amber-200 dark:bg-amber-900/20 dark:border-amber-700/40 rounded-lg px-3 py-2 mb-2 text-xs text-amber-800 leading-relaxed">
                   <div class="font-semibold mb-1">${t('renderer.kb_match_note')}</div>
                   <div>${escapeHtml(prefix.trim()).replace(/\n/g, '<br>')}</div>
               </div>`
            : '';
    }

    // Without sentence-level trace data there is no falling back to a "flat string" render any more:
    // the same splitSentencesForProvenance + renderTail per-sentence span structure below is used, so
    // the paragraph spacing, the clickable citation chips and the CSS tinting containers match the
    // traced run exactly - only trace stays null -> meta never matches -> no sentence carries a
    // confidence class / badge.
    // Both paths share the same rendering code so structural drift cannot make the styles diverge.
    const hasTracing = !!(tracing && tracing.length > 0);

    // Prefer segments built from the backend's sentence offsets: which sentence maps to
    // which provenance is then decided by the backend, instead of the client re-deriving
    // sentence indexes with its own splitter (the root cause of the misaligned badges).
    // The local splitter + index shift path stays as the fallback for offset-less data.
    const tracedSegs = hasTracing ? segmentsFromTrace(body, tracing, bodyStart) : null;
    const segs = tracedSegs || splitSentencesForProvenance(body);
    const traceIdxShift = tracedSegs ? 0 : offset;
    const html = segs.map(seg => {
        // Empty seg (tail only, no body): turn the tail straight into HTML line breaks
        if (!seg.text) return escapeHtml(seg.raw);
        const globalIdx = seg.idx + traceIdxShift;
        const trace = hasTracing ? tracing[globalIdx] : null;
        const level = (trace && trace.confidence_level) || 'unknown';
        const meta = PROV_LEVELS[level];
        // Strip the trailing newlines from raw so the inline markup carries no
        // paragraph separator and the badge hugs the full stop.
        const bareRaw = seg.raw.replace(/[\r\n]+$/, '');
        const inline = renderCitationChips(applyInlineFormatting(escapeHtml(bareRaw)), cvOverride, interactive);
        const onclick = interactive ? ` onclick="onSentenceClick(${globalIdx}, event)"` : '';
        // Newlines from seg.tail are emitted outside the span so the badge is not
        // pushed to the start of the next paragraph.
        const tailHtml = renderTail(seg.tail);
        // A statement that disagrees with the source is marked independently of the
        // confidence level: "well supported by chunk A" and "contradicts chunk A on
        // the number" can both be true at once.
        const contradicts = !!(trace && trace.has_contradiction);
        const contraHtml = contradicts ? contradictionBadgeHtml(trace) : '';
        const contraCls = contradicts ? ' has-contradiction' : '';
        if (!meta) {
            return `<span class="prov-sentence${contraCls}" title="${escapeHtml(t('prov.click_sentence'))}"`
                + `${onclick}>${contraHtml}${inline}</span>${tailHtml}`;
        }
        const title = `${t('prov.click_sentence')} · ${t(meta.tipKey)}`;
        const badge = meta.show
            ? `<span class="prov-badge" style="${meta.badgeStyle}">${t(meta.badgeKey)}</span>`
            : '';
        return `<span class="prov-sentence ${meta.cls}${contraCls}" title="${escapeHtml(title)}"`
            + `${onclick}>${contraHtml}${badge}${inline}</span>${tailHtml}`;
    }).join('');

    return kbNoteHtml + `<div class="prov-answer" data-msg-id="${escapeHtml(msgId || '')}">${html}</div>`;
}

/**
 * Inline marker for a sentence that disagrees with the retrieved source.
 *
 * The tooltip lists the findings (the same text the trace panel shows), so the
 * marker is not just "something is wrong" but "these two values differ".
 */
function contradictionBadgeHtml(trace) {
    const items = (trace && trace.contradictions) || [];
    if (!items.length) return '';
    const detail = items.map(item => item.explanation || '').filter(Boolean).join('\n');
    return `<span class="prov-badge prov-contra-badge" title="${escapeHtml(detail)}">`
        + `${escapeHtml(t('trace.contradiction_badge'))}</span>`;
}

/** The "provenance summary + actions" bar at the bottom of a message: every answer carries its own confidence summary and a trace entry point */
function renderMessageMeta(msgId) {
    const msgDiv = document.getElementById(msgId);
    if (!msgDiv) return;
    const meta = msgDiv.querySelector('.msg-meta');
    if (!meta) return;

    const td = state.messageTraces[msgId] ? state.messageTraces[msgId].data : state.traceData;
    const st = td.sentenceTracing || [];
    const drift = td.driftAnalysis || null;
    const total = (drift && drift.total_sentences) || st.length;
    const solid = (drift && drift.direct_quote_count) || 0;
    const summaryCount = (drift && drift.summary_count) || 0;
    const docs = (td.results || []).length;
    const score = (td.evaluation && td.evaluation.overall_score !== undefined && td.evaluation.overall_score !== null)
        ? Math.round((td.evaluation.overall_score || 0) * 100)
        : null;
    const driftRate = (drift && drift.drift_rate !== undefined) ? drift.drift_rate : null;
    const contradictionCount = (td.contradictions || []).length;

    const chips = [];
    if (total > 0) {
        chips.push(`<span class="px-1.5 py-0.5 rounded bg-green-50 text-green-700 border border-green-200">${t('prov.solid_of_total').replace('{a}', solid).replace('{b}', total)}</span>`);
    }
    // Statements disagreeing with the source come first: they are the actionable ones
    if (contradictionCount > 0) {
        chips.push(`<span class="px-1.5 py-0.5 rounded bg-red-50 text-red-700 border-red-200">${t('trace.contradiction_count').replace('{n}', contradictionCount)}</span>`);
    }
    if (summaryCount > 0) {
        chips.push(`<span class="px-1.5 py-0.5 rounded bg-yellow-50 text-yellow-700 border border-yellow-200">${t('prov.legend_summary')} ${summaryCount}</span>`);
    }
    if (docs > 0) {
        chips.push(`<span class="px-1.5 py-0.5 rounded bg-blue-50 text-blue-700 border border-blue-200">${t('prov.docs_covered').replace('{n}', docs)}</span>`);
    }
    if (driftRate !== null) {
        const cls = driftRate > 0.3 ? 'bg-red-50 text-red-700 border-red-200' : 'bg-slate-50 text-slate-600 border-slate-200';
        chips.push(`<span class="px-1.5 py-0.5 rounded border ${cls}">${t('prov.drift_rate').replace('{p}', fmtRatio(driftRate))}</span>`);
    }
    if (score !== null) {
        chips.push(`<span class="px-1.5 py-0.5 rounded bg-slate-50 text-slate-700 border border-slate-200">${t('prov.score').replace('{s}', score)}</span>`);
    }
    if (chips.length === 0) {
        chips.push(`<span class="text-slate-400">${t('prov.no_provenance')}</span>`);
    }

    meta.innerHTML = `
        <div class="prov-legend mb-1.5">${chips.join('')}</div>
        <div class="flex items-center gap-2 flex-wrap">
            <button onclick="showMessageTrace('${msgId}')" class="text-[10px] px-2 py-0.5 rounded border border-blue-200 text-blue-600 hover:bg-blue-50 transition-colors">${t('prov.view_trace')}</button>
            <button onclick="openAnswerFeedback('${msgId}')" class="text-[10px] px-2 py-0.5 rounded border border-slate-200 text-slate-600 hover:bg-slate-50 transition-colors">${t('prov.feedback')}</button>
            <span class="prov-legend text-slate-400">${t('prov.legend_quoted')} · ${t('prov.legend_summary')} · ${t('prov.legend_low')} · ${t('prov.legend_none')}</span>
        </div>`;
    meta.classList.remove('hidden');
}

// ==================== The provenance snapshot of every message ====================
// The problem: the trace panel is a single global state, so in a multi-turn conversation earlier
// messages had no provenance of their own.
// Now a snapshot is saved at the end of every Q&A, so a history message can be reviewed on its own
// and the user can switch back to "latest".

function activeTraceKbId() {
    if (state.activeTraceMsgId && state.messageTraces[state.activeTraceMsgId]) {
        return state.messageTraces[state.activeTraceMsgId].kbId;
    }
    return state.currentKB && state.currentKB.kb_id;
}

/** Redraw the whole panel from a given provenance dataset (switching to a history snapshot and going back to the latest both end up here) */
function renderTraceFromData(td) {
    const live = state.liveTraceData || state.traceData;
    resetTracePanelDom();
    state.traceData = td;
    try {
        if (td.results) renderSourcePanel(td.results, td.mode, td.count);
        if (td.debugInfo) renderDebugInfo(td.debugInfo);
        if (td.intent) renderIntentCard(td.intent);
        if (td.sentenceTracing) renderSentenceTracing(td.sentenceTracing, td.driftAnalysis);
        if (td.evaluation) renderEvaluation(td.evaluation);
        if (td.diagnosis) renderRecallDiagnosis(td.diagnosis);
        if (td.citationValidation) renderCitationValidation(td.citationValidation, true);
        if (td.businessDiagnosis) renderBusinessDiagnosis(td.businessDiagnosis, true);
        if (td.funnel) renderRetrievalFunnel(td.funnel, td.skipped, true);
        updateSummaryView();
    } finally {
        state.traceData = live;
    }
}

function showMessageTrace(msgId) {
    const snap = state.messageTraces[msgId];
    if (!snap) {
        showToast('info', t('trace.snapshot_missing'));
        return;
    }
    // The latest answer: use the live data directly (which is this answer right now) and hide the
    // "history snapshot" banner
    if (snap.data === state.liveTraceData) {
        state.activeTraceMsgId = null;
        hideSnapshotBanner();
        renderTraceFromData(state.liveTraceData);
        openTracePanel();
        return;
    }
    if (!state.liveTraceData) state.liveTraceData = state.traceData;
    state.activeTraceMsgId = msgId;
    renderTraceFromData(snap.data);
    renderSnapshotBanner(snap);
    openTracePanel();
}

function backToLiveTrace() {
    state.activeTraceMsgId = null;
    renderTraceFromData(state.liveTraceData || state.traceData);
    hideSnapshotBanner();
}

function renderSnapshotBanner(snap) {
    const banner = document.getElementById('traceSnapshotBanner');
    if (!banner) return;
    const q = (snap.query || '').length > 40 ? snap.query.substring(0, 40) + '…' : (snap.query || '');
    banner.innerHTML = `
        <span class="flex-1 truncate" title="${escapeHtml(snap.query || '')}">${escapeHtml(t('trace.snapshot_viewing'))}<span class="font-medium">${escapeHtml(q)}</span></span>
        <button onclick="backToLiveTrace()" class="shrink-0 text-[10px] px-2 py-0.5 rounded border border-blue-200 text-blue-600 hover:bg-blue-50 transition-colors">${t('trace.back_to_latest')}</button>`;
    banner.classList.remove('hidden');
}

function hideSnapshotBanner() {
    const banner = document.getElementById('traceSnapshotBanner');
    if (banner) banner.classList.add('hidden');
}

function openTracePanel() {
    const panel = document.getElementById('sourcePanel');
    if (panel) panel.classList.remove('hidden');
    state.panelUserClosed = false;
    updateTraceBadge();
}

/** The kept / dropped counts must be visible even when the panel is collapsed, otherwise the white-box capability is hidden by default */
function updateTraceBadge() {
    const badge = document.getElementById('traceBadge');
    if (!badge) return;
    const td = activeTraceData();
    const counts = td.funnel && td.funnel.counts;
    if (!counts) {
        badge.textContent = '';
        return;
    }
    badge.textContent = `✓${counts.kept} ✗${counts.dropped}`;
    badge.className = 'ml-1 text-[10px] font-medium ' + (counts.dropped > 0 ? 'text-amber-600' : 'text-green-600');
}

/** Click a [Document N] in the answer -> locate the matching source card on the right (after making sure the panel shows that message's provenance) */
function focusSourceBySlot(slot, ev) {
    const el = ev && ev.currentTarget ? ev.currentTarget : null;
    const host = el && el.closest ? el.closest('[data-msg-id]') : null;
    const msgId = host ? host.getAttribute('data-msg-id') : '';
    const snap = msgId ? state.messageTraces[msgId] : null;
    if (snap && snap.data !== state.liveTraceData) {
        // The citation comes from a history message: switch the panel to that message's provenance
        // snapshot first, otherwise the matching card cannot be located
        showMessageTrace(msgId);
    }
    const cv = traceDataForElement(el).citationValidation;
    const map = (cv && cv.citation_to_chunk_map) || {};
    const chunkId = map[String(slot)];
    if (!chunkId) {
        showToast('info', t('trace.source_not_found'));
        return;
    }
    focusSourceCard(chunkId);
}

function focusSourceCard(chunkId) {
    // The source cards live in the retrieval-chain group of the "technical details" view: switch the
    // view, expand the group, then highlight
    switchTraceView('tech');
    const list = document.getElementById('sourceList');
    let card = null;
    if (list) {
        card = Array.prototype.slice.call(list.querySelectorAll('[data-chunk-id]'))
            .find(el => el.getAttribute('data-chunk-id') === String(chunkId)) || null;
    }
    if (!card) {
        showToast('info', t('trace.source_not_found'));
        return;
    }
    const details = card.closest('details');
    if (details) details.open = true;
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    card.classList.add('prov-focus');
    setTimeout(() => card.classList.remove('prov-focus'), 2200);
}

/**
 * Fetch the full chunk text on demand: the chunk body sent during streaming is truncated while the
 * provenance modal needs the complete source.
 * Returns null on failure and the caller falls back to the preview text.
 */
async function fetchChunkText(chunkId) {
    if (!chunkId) return null;
    if (state.chunkTextCache[chunkId]) return state.chunkTextCache[chunkId];
    const kbId = activeTraceKbId();
    if (!kbId) return null;
    try {
        const data = await fetchJSON(`/api/knowledge/${encodeURIComponent(kbId)}/chunks/${encodeURIComponent(chunkId)}`);
        const d = (data && data.data) || {};
        if (!d.text) return null;
        const entry = { text: d.text, fileName: (d.metadata || {}).file_name };
        state.chunkTextCache[chunkId] = entry;
        return entry;
    } catch (e) {
        console.warn('拉取分块全文失败:', e);
        return null;
    }
}

/** Hit reason: the backend's explanation is English, so the localized text is assembled here from type + value */
function hitReasonText(hr) {
    if (!hr) return '';
    const val = hr.value;
    switch (hr.type) {
        case 'keyword_match': {
            const kws = Array.isArray(val) ? val.join('、') : String(val || '');
            return t('trace.hit_keyword').replace('{v}', kws);
        }
        case 'bm25_score':
            return t('trace.hit_bm25').replace('{v}', fmtScore(val || 0));
        case 'semantic_similarity':
            return t('trace.hit_vector').replace('{v}', fmtScore(val || 0));
        case 'score_contribution':
            return t('trace.hit_hybrid')
                .replace('{a}', fmtScore((val && val.bm25_contribution) || 0))
                .replace('{b}', fmtScore((val && val.vector_contribution) || 0))
                .replace('{c}', fmtScore((val && val.final_score) || 0));
        case 'metadata_match':
            return t('trace.hit_metadata').replace('{v}', (val && val.file_type) || '');
        case 'score_threshold':
            return t('trace.hit_threshold')
                .replace('{s}', fmtScore((val && val.score) || 0))
                .replace('{t}', fmtScore((val && val.threshold) || 0));
        default:
            return hr.explanation || '';
    }
}
