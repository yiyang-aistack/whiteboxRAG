/**
 * static/index.html - chat
 *
 * Content: input auto-resize and Enter-to-send, welcome page switching, message bubble rendering,
 *       streaming incremental filling, boundary-rejection card.
 * Split out of index.html's inline <script>; depends on state / t / fetchJSON from core.js and
 * showToast from ui.js.
 */

// ==================== Chat ====================
function handleInputKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
}

function autoResizeTextarea(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 150) + 'px';
}

function clearChat() {
    state.messages = [];
    state.messageTraces = {};
    state.activeTraceMsgId = null;
    state.liveTraceData = null;
    state.chunkTextCache = {};
    hideSnapshotBanner();
    document.getElementById('messageList').innerHTML = '';
    document.getElementById('welcomePage').classList.remove('hidden');
    document.getElementById('messageList').classList.add('hidden');
    document.getElementById('evaluationCard').classList.add('hidden');
    document.getElementById('sourceList').innerHTML = `
        <div class="text-center text-slate-400 py-8">
            <p class="text-sm">${escapeHtml(t('trace.no_trace'))}</p>
            <p class="text-xs mt-1">${escapeHtml(t('trace.no_trace_hint'))}</p>
        </div>
    `;
}

async function sendMessage() {
    if (state.isStreaming) return;

    const input = document.getElementById('chatInput');
    const message = input.value.trim();

    if (!message) return;
    if (!state.currentKB) {
        showToast('warning', t('chat.no_kb'));
        return;
    }

    // Append the user message
    addMessage('user', message);
    input.value = '';
    input.style.height = 'auto';

    // Switch from the welcome page to the message list
    document.getElementById('welcomePage').classList.add('hidden');
    document.getElementById('messageList').classList.remove('hidden');

    // Append an AI message placeholder
    const aiMsgId = addMessage('assistant', '', true);

    // Start the SSE request
    await streamChat(message, aiMsgId);
}

function addMessage(role, content, isPlaceholder = false) {
    const container = document.getElementById('messageList');
    const id = 'msg_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);

    const msgDiv = document.createElement('div');
    msgDiv.id = id;
    msgDiv.className = 'flex ' + (role === 'user' ? 'justify-end' : 'justify-start') + ' mb-4';

    if (role === 'user') {
        msgDiv.innerHTML = `
            <div class="message-user bg-blue-600 text-white px-4 py-2.5 max-w-[75%] text-sm">
                ${escapeHtml(content)}
            </div>
        `;
    } else {
        msgDiv.innerHTML = `
            <div class="message-assistant bg-white border border-slate-200 px-4 py-2.5 max-w-[82%] text-sm shadow-sm">
                <div class="pipeline-status hidden mb-1 text-[11px] text-slate-400"></div>
                  <br>
                <div class="assistant-content ${isPlaceholder ? 'typing-cursor text-slate-400' : 'text-slate-700 event-content'}">${escapeHtml(content)}</div>
                <br>
                <div class="msg-meta hidden mt-2 pt-2 border-t border-slate-100" title="${escapeHtml(t('prov.msg_meta_title'))}"></div>
            </div>
        `;
    }

    container.appendChild(msgDiv);
    container.scrollTop = container.scrollHeight;

    return id;
}

function updateMessageContent(msgId, content, isDone = false, boundaryResult = null) {
    const msgDiv = document.getElementById(msgId);
    if (!msgDiv) return;

    const contentDiv = msgDiv.querySelector('.assistant-content');
    if (contentDiv) {
        contentDiv.classList.remove('typing-cursor', 'text-slate-400');
        if (boundaryResult) {
            // Boundary-rejected: use the dedicated card renderer.
            contentDiv.classList.add('event-content');
            contentDiv.innerHTML = renderBoundaryCard(content, boundaryResult);
            setPipelineStatus(msgId, '');
        } else if (isDone) {
            contentDiv.classList.add('event-content');
            // Merge the incremental provenance event cache into state.traceData so the side
            // panel also gets the complete list.
            // This must happen BEFORE rendering: renderAnswerWithProvenance reads
            // sentenceTracing, so merging afterwards would drop the incremental provenance of
            // the final answer (sentence tinting / badges missing entirely), or even fall back
            // to the badge-less rendering because sentenceTracing is empty.
            if (state._pendingIncrementalProvenance && state._pendingIncrementalProvenance.length > 0) {
                const existing = state.traceData.sentenceTracing || [];
                // Fill in the sentences the final trace has not covered yet, matching by
                // CHARACTER OFFSET (every item of the backend's sentence_tracing carries
                // start/end); the sentence index is only a fallback for an older backend
                // that sends no offsets.
                state._pendingIncrementalProvenance.forEach(ip => {
                    const tr = ip.trace || {};
                    let at = -1;
                    if (typeof tr.start === 'number') {
                        at = existing.findIndex(item => item && item.start === tr.start);
                    }
                    if (at < 0 && typeof ip.sentence_idx === 'number' && !existing[ip.sentence_idx]) {
                        at = ip.sentence_idx;
                    }
                    if (at >= 0 && !existing[at]) existing[at] = tr;
                });
                state.traceData.sentenceTracing = existing;
                state._pendingIncrementalProvenance = null;
            }
            // Defensive reset: drop the placeholder spans / incremental badges left over from the
            // streaming stage, then render the final answer from scratch
            contentDiv.innerHTML = '';
            // Once streaming is done: sentence-level provenance tinting + clickable citation
            // chips (the white-box core).
            contentDiv.innerHTML = renderAnswerWithProvenance(content, msgId);
            setPipelineStatus(msgId, '');
            renderMessageMeta(msgId);
        } else {
            // While streaming: use the pre-placeholder span structure, split into one container per
            // sentence, and wait for the sentence_provenance events to fill them
            contentDiv.classList.remove('event-content');
            const segs = splitSentencesForProvenance(content);
            // Fill each span through textContent (5-10x faster than innerHTML) while keeping the DOM structure
            const htmlParts = segs.map((seg, i) => {
                if (!seg.text && !seg.raw.trim()) return '';
                // The span wraps the sentence text only; the newline tail between paragraphs sits
                // outside the span and renderTail turns it into <br> plus an optional paragraph
                // divider, so the badge and the start of the next paragraph stay clearly apart
                return `<span class="prov-sentence prov-placeholder" data-msg-id="${msgId}" data-sentence-idx="${i}" data-sentence-start="${seg.start}" data-sentence-end="${seg.end}"></span>`
                    + renderTail(seg.tail);
            });
            contentDiv.innerHTML = htmlParts.join('');
            // Then fill every span through textContent (avoids the innerHTML parsing cost)
            // while stripping the trailing newlines (the tail already sits outside the span), so a
            // span holds the sentence body only
            const spans = contentDiv.querySelectorAll('.prov-sentence');
            let j = 0;
            segs.forEach(seg => {
                if (!seg.text && !seg.raw.trim()) return;
                if (spans[j]) {
                    // Strip the trailing newlines of seg.raw (the tail was moved outside the span)
                    const bareText = seg.raw.replace(/[\r\n]+$/, '');
                    spans[j].textContent = bareText;
                }
                j++;
            });
            // If incremental provenance data has already arrived, fill in the class / badge right away
            if (state._pendingIncrementalProvenance && state._pendingIncrementalProvenance.length > 0) {
                state._pendingIncrementalProvenance.forEach(ip => {
                    fillIncrementalProvenance(contentDiv, ip);
                });
            }
            // Hide the "retrieving / generating" stage hint once the first token arrives
            setPipelineStatus(msgId, '');
        }
    }

    // Scroll to the bottom
    const container = document.getElementById('messageList');
    container.scrollTop = container.scrollHeight;
}

/**
 * Fill a streaming placeholder span with the backend's incremental
 * sentence_provenance event, so the user sees each sentence's provenance while the
 * answer is still being written.
 *
 * Location is decided by CHARACTER OFFSET, not by sentence index: the backend sends
 * the sentence's start/end inside the full answer, and the text the client accumulated
 * comes from the same frames, so the offsets are directly comparable. Even when the
 * two sides segment slightly differently the worst case is a placeholder without a
 * badge - never a badge attached to a different sentence (which is what index-based
 * alignment used to do).
 */
function fillIncrementalProvenance(contentDiv, ip) {
    const trace = ip.trace || {};
    const span = findPlaceholderSpan(contentDiv, ip.sentence_start, ip.sentence_end, ip.sentence_idx);
    if (!span) return;
    const level = trace.confidence_level || 'unknown';
    const meta = PROV_LEVELS[level];
    if (meta) {
        span.classList.remove('prov-placeholder');
        span.classList.add(meta.cls);
        if (meta.show) {
            const badge = document.createElement('span');
            badge.className = 'prov-badge';
            badge.style = meta.badgeStyle;
            badge.textContent = t(meta.badgeKey);
            badge.title = t(meta.tipKey);
            span.insertBefore(badge, span.firstChild);
        }
    } else {
        span.classList.remove('prov-placeholder');
    }
}

/**
 * Find the placeholder span for a pair of character offsets:
 *   1. exact start/end match;
 *   2. otherwise the span CONTAINING the range (when the two sides segment slightly
 *      differently);
 *   3. only an older backend without offsets falls back to the sentence index.
 */
function findPlaceholderSpan(contentDiv, start, end, idx) {
    if (typeof start === 'number' && typeof end === 'number') {
        const spans = contentDiv.querySelectorAll('.prov-sentence.prov-placeholder');
        let containing = null;
        spans.forEach(el => {
            const s = Number(el.dataset.sentenceStart);
            const e = Number(el.dataset.sentenceEnd);
            if (!Number.isFinite(s) || !Number.isFinite(e)) return;
            if (s === start && e === end && !containing) containing = el;
            if (!containing && s <= start && end <= e) containing = el;
        });
        if (containing) return containing;
    }
    if (typeof idx === 'number') {
        return contentDiv.querySelector(`.prov-sentence.prov-placeholder[data-sentence-idx="${idx}"]`);
    }
    return null;
}

// Detect and render the segmented style of hybrid answer mode
// Knowledge base disclaimer part -> grey notice box; LLM content -> normal answer style
// -------------------------------------------------------------------
// Boundary-rejection friendly card renderer
// Backend sends boundary_result in the done-event payload when the query is
// rejected by boundary detection (out-of-domain, vague, blacklist, etc.).
// This function converts the raw answer into a styled card with icon,
// reason label, and topic suggestions so the user understands *why* they
// got rejected and *what* to try next.
// -------------------------------------------------------------------
function renderBoundaryCard(answer, boundaryResult) {
    if (!boundaryResult) return escapeHtml(answer || '').replace(/\n/g, '<br>');

    const detector = boundaryResult.detector || '';
    const reasonEn = boundaryResult.reason || '';
    const matchedTopics = boundaryResult.matched_topics || [];

    // Pick visual style and localized label by detector type.
    const styleMap = {
        'keyword_blacklist': {
            icon: '🚫', iconBg: 'bg-red-100', iconFg: 'text-red-600',
            cardBorder: 'border-red-200', cardBg: 'bg-red-50',
            labelKey: 'boundary.label_blacklist'
        },
        'semantic': {
            icon: matchedTopics.length ? 'ℹ️' : '💡',
            iconBg: matchedTopics.length ? 'bg-blue-100' : 'bg-amber-100',
            iconFg: matchedTopics.length ? 'text-blue-600' : 'text-amber-600',
            cardBorder: matchedTopics.length ? 'border-blue-200' : 'border-amber-200',
            cardBg: matchedTopics.length ? 'bg-blue-50' : 'bg-amber-50',
            labelKey: matchedTopics.length ? 'boundary.label_out_of_scope' : 'boundary.label_vague'
        },
        'retrieval_empty': {
            icon: '🔍', iconBg: 'bg-slate-100', iconFg: 'text-slate-500',
            cardBorder: 'border-slate-200', cardBg: 'bg-slate-50',
            labelKey: 'boundary.label_no_content'
        },
        'retrieval_low_score': {
            icon: '⚠️', iconBg: 'bg-amber-100', iconFg: 'text-amber-600',
            cardBorder: 'border-amber-200', cardBg: 'bg-amber-50',
            labelKey: 'boundary.label_low_score'
        }
    };
    const s = styleMap[detector] || {
        icon: 'ℹ️', iconBg: 'bg-slate-100', iconFg: 'text-slate-500',
        cardBorder: 'border-slate-200', cardBg: 'bg-slate-50',
        labelKey: 'boundary.label_rejected'
    };

    // The backend reports the reason in English; map the known ones to an i18n key so the
    // text is shown in the UI language instead of always being Chinese.
    const reasonKeyMap = {
        'no clear business topic': 'boundary.reason_no_topic',
        'Query is vague, no clear business topic': 'boundary.reason_vague',
        'out of business scope': 'boundary.reason_out_of_scope',
        'No relevant documents found': 'boundary.reason_no_docs',
    };
    let reasonText = reasonEn;
    for (const [needle, key] of Object.entries(reasonKeyMap)) {
        if (reasonEn.includes(needle) || reasonEn === needle) { reasonText = t(key); break; }
    }

    // Build topic suggestion row (only when semantic match exists but query didn't align).
    let suggestionHtml = '';
    if (matchedTopics.length > 0) {
        suggestionHtml = `
            <div class="mt-2 pt-2 border-t border-current/10">
                <div class="text-[11px] font-medium mb-1 opacity-70">${escapeHtml(t('boundary.try_ask'))}</div>
                <div class="flex flex-wrap gap-1">
                    ${matchedTopics.slice(0, 5).map(topic =>
                        `<span class="text-[11px] px-1.5 py-0.5 rounded bg-white/70 border border-current/20">${escapeHtml(topic)}</span>`
                    ).join('')}
                </div>
            </div>`;
    } else if (detector === 'semantic') {
        suggestionHtml = `
            <div class="mt-2 pt-2 border-t border-current/10 text-[11px] opacity-70">
                ${escapeHtml(t('boundary.be_specific'))}
            </div>`;
    }

    return `
        <div class="border ${s.cardBorder} ${s.cardBg} rounded-lg p-3">
            <div class="flex items-start gap-2">
                <div class="shrink-0 w-6 h-6 rounded-full ${s.iconBg} ${s.iconFg} flex items-center justify-center text-sm">${s.icon}</div>
                <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2 mb-1">
                        <span class="text-xs font-semibold ${s.iconFg}">${escapeHtml(t(s.labelKey))}</span>
                        <span class="text-[10px] opacity-50">${escapeHtml(reasonText)}</span>
                    </div>
                    <div class="text-sm text-slate-700 leading-relaxed">${escapeHtml(answer || '').replace(/\n/g, '<br>')}</div>
                    ${suggestionHtml}
                </div>
            </div>
        </div>`;
}
