/**
 * static/index.html - global state / initialization / helper functions
 *
 * Split out of index.html's inline <script>; the top-level const / let / function still live in the
 * global lexical scope, so other scripts and the HTML inline event handlers can reference them
 * directly (see the <script> list at the bottom of index.html for the load order).
 * Content: the global state object, DOMContentLoaded initialization, fetchJSON, the trace-panel
 *       glossary TRACE_TERM_MAP and the score / colour / escaping formatting helpers.
 */

// ==================== Global state ====================
// Shorthand alias for the i18n translation function
const t = i18n.t;

const state = {
    currentKB: null,
    kbList: [],
    messages: [],
    currentTraceId: null,
    isStreaming: false,
    // Trace panel view mode: summary (business summary) / tech (technical details)
    traceViewMode: 'summary',
    // Trace data cache, used by the business summary view to derive its display
    traceData: {
        results: null,
        mode: null,
        count: 0,
        debugInfo: null,
        evaluation: null,
        intent: null,
        sentenceTracing: null,
        driftAnalysis: null,
        diagnosis: null,
        // ===== White-box additions: funnel / citation validation / backend diagnosis / miss-scan =====
        funnel: null,
        citationValidation: null,
        businessDiagnosis: null,
        skipped: null
    },
    // Provenance snapshot per message: msgId -> { traceId, query, kbId, data }
    messageTraces: {},
    // Which message's provenance snapshot is currently shown (null = the latest Q&A)
    activeTraceMsgId: null,
    // Provenance data of the latest Q&A (used by "back to latest" after switching to a snapshot)
    liveTraceData: null,
    // Whether the trace panel was auto-opened for this Q&A / closed manually by the user
    panelAutoOpened: false,
    panelUserClosed: false,
    // Chunk full-text cache (chunk_id -> { text, fileName }), used to fetch the complete source on demand
    chunkTextCache: {},
    // Incremental provenance event cache: the per-sentence provenance the backend pushes while
    // generating is buffered here, filled into the matching span as soon as a token arrives during
    // streaming, and merged into traceData.sentenceTracing by the final render
    _pendingIncrementalProvenance: null,
    chatStats: {
        totalRequests: 0,
        totalErrors: 0,
        avgResponseTime: 0
    }
};

let requestChart = null;

// ==================== Initialization ====================
document.addEventListener('DOMContentLoaded', () => {
    i18n.applyI18n();
    loadKBList();
    checkLLMHealth();
    loadRetrievalDefaults();
    // Refresh the LLM status every 30 seconds
    setInterval(checkLLMHealth, 30000);
    // Update the monitoring data every 10 seconds
    setInterval(updateMonitorBar, 10000);
    // Check the URL parameters so ?trace_id=xxx loads the trace automatically (A/B test integration)
    checkUrlForTraceId();
    // Listen for the language switch event and re-apply the translations
    document.addEventListener('langChanged', () => {
        i18n.applyI18n();
    });
});

// ==================== Helper functions ====================
async function fetchJSON(url, options = {}) {
    const _lang = (window.i18n && i18n.getLang) ? i18n.getLang() : (localStorage.getItem('lang') || 'zh-CN');
    const defaultOptions = {
        headers: {
            'Accept': 'application/json;charset=utf-8',
            'Content-Type': 'application/json;charset=utf-8',
            'Accept-Language': _lang
        },
        ...options
    };

    const res = await fetch(url, defaultOptions);

    if (!res.ok) {
        // Prefer the backend's localized `detail` over a bare status code so the UI can
        // show e.g. "miss scan failed: ..." instead of "HTTP error! status: 500".
        let detail = '';
        try {
            const body = await res.text();
            if (body) {
                try {
                    detail = (JSON.parse(body) || {}).detail || '';
                } catch (parseErr) {
                    detail = body.slice(0, 200);
                }
            }
        } catch (readErr) {
            detail = '';
        }
        throw new Error(detail || `HTTP error! status: ${res.status}`);
    }

    const buffer = await res.arrayBuffer();
    const decoder = new TextDecoder('utf-8');
    const text = decoder.decode(buffer);
    const data = JSON.parse(text);
    return data;
}

// ==================== Trace panel: glossary and score-scale conventions ====================
// The backend sends machine codes (evaluation metric keys, sentence confidence levels).
// Each map entry stores the i18n key of the localized name plus the score scale used to
// render the value: `<key>` is the name, `<key>.desc` is the tooltip (see i18n.js).
const TRACE_TERM_MAP = {
    // Evaluation metrics
    // `retrieval_recall` and `context_usage_ratio` are rubric v1 keys: they still
    // appear in traces written before the rubric change, so the mapping stays (their
    // localized labels now say what those numbers really were). New traces carry
    // `retrieval_score_avg` and `citation_coverage`.
    'retrieval_recall': { key: 'trace.term.retrieval_recall', type: 'score', range: [0, 1] },
    'retrieval_score_avg': { key: 'trace.term.retrieval_score_avg', type: 'score', range: [0, 1] },
    'retrieval_score_std': { key: 'trace.term.retrieval_score_std', type: 'score', range: [0, 0.5] },
    'answer_faithfulness': { key: 'trace.term.answer_faithfulness', type: 'ratio', range: [0, 1] },
    'answer_relevance': { key: 'trace.term.answer_relevance', type: 'ratio', range: [0, 1] },
    'context_usage_ratio': { key: 'trace.term.context_usage_ratio', type: 'ratio', range: [0, 1] },
    'citation_coverage': { key: 'trace.term.citation_coverage', type: 'ratio', range: [0, 1] },
    'empty_response': { key: 'trace.term.empty_response', type: 'ratio', range: [0, 1] },
    'response_length': { key: 'trace.term.response_length', type: 'count' },
    'semantic_faithfulness': { key: 'trace.term.semantic_faithfulness', type: 'ratio', range: [0, 1] },
    'semantic_consistency': { key: 'trace.term.semantic_consistency', type: 'ratio', range: [0, 1] },
    'answer_coverage_ratio': { key: 'trace.term.answer_coverage_ratio', type: 'ratio', range: [0, 1] },
    'hallucination_rate': { key: 'trace.term.hallucination_rate', type: 'ratio', range: [0, 1] },
    'rejection_accuracy': { key: 'trace.term.rejection_accuracy', type: 'ratio', range: [0, 1] },
    // Sentence-level confidence levels
    'citation_verified': { key: 'trace.term.citation_verified' },
    'direct_quote': { key: 'trace.term.direct_quote' },
    'summary': { key: 'trace.term.summary' },
    'low_confidence': { key: 'trace.term.low_confidence' },
    'drift': { key: 'trace.term.drift' },
    'no_source': { key: 'trace.term.no_source' },
    'unverified': { key: 'trace.term.unverified' },
    // Drift analysis fields
    'drift_rate': { key: 'trace.term.drift_rate', type: 'ratio', range: [0, 1] },
    'total_sentences': { key: 'trace.term.total_sentences', type: 'count' },
    'direct_quote_count': { key: 'trace.term.direct_quote_count', type: 'count' },
    'summary_count': { key: 'trace.term.summary_count', type: 'count' },
    'drifted_count': { key: 'trace.term.drifted_count', type: 'count' },
    'citation_verified_count': { key: 'trace.term.citation_verified_count', type: 'count' },
    'unverified_count': { key: 'trace.term.unverified_count', type: 'count' }
};

/** Localized display name of a glossary entry (falls back to the raw code). */
function termName(entry, fallback) {
    return (entry && entry.key) ? t(entry.key) : (fallback || '');
}

/** Localized tooltip of a glossary entry; '' when the entry has no description. */
function termDesc(entry) {
    return (entry && entry.key) ? t(entry.key + '.desc') : '';
}


// HTML escaping to prevent XSS
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
}

// Score conventions:
//  score type (similarity, fused score, ...)  -> always 2 decimals, e.g. 0.53
//  ratio type (ratio, share, recall rate, ...) -> always an integer percentage, e.g. 53%
//  count type (characters, items, sentences)  -> plain integer + the unit (chars / items / sentences)
function fmtScore(v) {
    if (v === null || v === undefined || Number.isNaN(+v)) return '-';
    return (+v).toFixed(2);
}
function fmtRatio(v) {
    if (v === null || v === undefined || Number.isNaN(+v)) return '-';
    const p = Math.round(+v * 100);
    // Keep 0 < v < 1% as 1% to avoid the misleading "0%", while 0 stays 0%
    if (v > 0 && p === 0) return '<1%';
    return p + '%';
}
function fmtCount(v, unit) {
    if (v === null || v === undefined || Number.isNaN(+v)) return '-';
    return `${Math.round(+v)}${unit || ''}`;
}
// ===== Theme-aware colour helpers =====
const IS_DARK_THEME = () => document.documentElement.classList.contains('dark');

/**
 * Return a harmonized hex colour value for the given theme.
 * In dark mode the saturation drops and the brightness rises so the colour does not glare or
 * wash out on a dark background; in light mode the semantics stay but the colour is kept from
 * being over-saturated (emerald / teal instead of pure green and friends).
 */
function themeLevelColor(level) {
    const LIGHT = {
        'citation_verified': '#0d9488',
        'direct_quote': '#16a34a',
        'summary': '#ca8a04',
        'low_confidence': '#ea580c',
        'drift': '#dc2626',
        'no_source': '#dc2626',
        'unverified': '#64748b',
    };
    const DARK = {
        'citation_verified': '#2dd4bf',
        'direct_quote': '#4ade80',
        'summary': '#facc15',
        'low_confidence': '#fb923c',
        'drift': '#f87171',
        'no_source': '#f87171',
        'unverified': '#94a3b8',
    };
    const map = IS_DARK_THEME() ? DARK : LIGHT;
    return map[level] || (IS_DARK_THEME() ? '#94a3b8' : '#6b7280');
}

// Wrapper that adds a value-range and level-colour tooltip to a score
function scoreBadge(v, { label = '', type = 'score', range = [0, 1] } = {}) {
    const text = type === 'ratio' ? fmtRatio(v) : (type === 'count' ? fmtCount(v) : fmtScore(v));
    // Use the Tailwind dark: variant so each theme gets its own harmonized colour set
    let color = 'text-slate-700 dark:text-slate-300';
    if (type === 'ratio' || type === 'score') {
        const num = +v;
        if (type === 'ratio') {
            if (num >= 0.8) color = 'text-green-700 dark:text-green-400';
            else if (num >= 0.6) color = 'text-slate-700 dark:text-slate-300';
            else if (num >= 0.4) color = 'text-yellow-700 dark:text-yellow-400';
            else color = 'text-red-700 dark:text-red-400';
        } else {
            if (num >= 0.7) color = 'text-green-700 dark:text-green-400';
            else if (num >= 0.5) color = 'text-slate-700 dark:text-slate-300';
            else if (num >= 0.3) color = 'text-yellow-700 dark:text-yellow-400';
            else color = 'text-red-700 dark:text-red-400';
        }
    }
    const title = label ? `title="${escapeHtml(t('trace.value_range').replace('{l}', label).replace('{a}', range[0]).replace('{b}', range[1]))}"` : '';
    return `<span class="font-medium ${color}" ${title}>${text}</span>`;
}
// Metric row: localized name + question-mark tooltip + a value in the unified scale
function metricRow(metricKey, rawValue, metricOverride = null) {
    const info = metricOverride || TRACE_TERM_MAP[metricKey] || null;
    const name = termName(info, metricKey);
    const desc = termDesc(info);
    const type = (info && info.type) || (typeof rawValue === 'number' && rawValue > 1 ? 'count' : 'score');
    const questionIcon = desc ? `<svg class="w-3 h-3 inline ml-1 text-slate-400 cursor-help" fill="none" stroke="currentColor" viewBox="0 0 24 24" title="${escapeHtml(desc)}"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>` : '';
    const valueBadge = scoreBadge(rawValue, {
        label: name,
        type: type,
        range: (info && info.range) || [0, 1]
    });
    return `
        <div class="flex items-center justify-between text-xs gap-2">
            <span class="text-slate-600 whitespace-nowrap">${escapeHtml(name)}${questionIcon}</span>
            ${valueBadge}
        </div>
    `;
}
