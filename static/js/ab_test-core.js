/**
 * static/ab_test.html - alias / state / initialization
 *
 * split from ab_test.html's inline script; top-level const / let / function as global scope,
 * HTML inline events and other scripts can directly reference them (see bottom of ab_test.html's <script> list).
 * Content: i18n alias _, uiLang(), variantIdx / progressTimer / currentMode / retrievalDefaults state variables,
 *       init() initializes the A/B test page, must be called after all other scripts.
 *       single/batch mode switch, batch query file parsing, knowledge base list loading.
 */

// Abbr Alias for i18n.t()
const _ = (key) => i18n.t(key);
let variantIdx = 0;
let progressTimer = null;
let currentMode = 'single'; // 'single' | 'batch'
// Retrieval defaults (mode / BM25 weight / similarity threshold / top_k / switch)
let retrievalDefaults = null;

// ==================== Language: with UI language ====================
/**
 * Supported locales ('zh-CN' / 'en-US').
 *
 * The value must come from i18n.getLang(): the server side (service/i18n.py) reads the same code,
 * so the UI language and the answer language stay in sync.
 *
 */
function uiLang() {
    return (window.i18n && i18n.getLang) ? i18n.getLang() : (localStorage.getItem('lang') || 'zh-CN');
}

/**
 * API request wrapper with UI language.
 *
 * The A/B test response language is decided by the backend's get_lang_from_request().
 * Priority: explicit parameter > ?lang= query parameter > Accept-Language header > default language.
 * So even if an intermediate proxy strips the request headers, the response language stays
 * consistent with the UI language.
 */
async function apiFetch(url, options = {}) {
    const lang = uiLang();
    const separator = url.includes('?') ? '&' : '?';
    const headers = Object.assign({ 'Accept-Language': lang }, options.headers || {});
    return fetch(`${url}${separator}lang=${encodeURIComponent(lang)}`, Object.assign({}, options, { headers }));
}

// ==================== Initialization ====================
async function init() {
    i18n.applyI18n();
    await Promise.all([loadKnowledgeBases(), loadRetrievalDefaults()]);
    // By default, use hybrid / vector / bm25 modes for variants A/B/C.
    // Variant A (hybrid)  → BM25 weight (settings.yaml config value)
    // Variant B (vector)  → vector weight (1 - bm25_weight)
    // Variant C (bm25)    → config default value (bm25_weight)
    const bm25Weight = defaultsOf('bm25_weight');
    const vectorWeight = defaultsOf('vector_weight');
    addVariant(i18n.t('abtest.variant_a'), bm25Weight, 'hybrid');
    addVariant(i18n.t('abtest.variant_b'), vectorWeight, 'vector');
    addVariant(i18n.t('abtest.variant_c'), null, 'bm25');
    // Listen for batch query input, update count in real-time
    const batchInput = document.getElementById('batchQueriesInput');
    batchInput.addEventListener('input', updateBatchQueryCount);
}

/** Get a default value from retrievalDefaults, or null if not loaded */
function defaultsOf(key) {
    const value = retrievalDefaults ? retrievalDefaults[key] : null;
    return value === undefined ? null : value;
}

/** Load retrieval defaults from API */
async function loadRetrievalDefaults() {
    try {
        const res = await apiFetch('/api/chat/retrieval-defaults');
        const data = await res.json();
        if (data.success && data.data) {
            retrievalDefaults = data.data;
        }
    } catch (e) {
        console.error(i18n.t('abtest.alert_load_defaults_failed'), e);
    }
    return retrievalDefaults;
}

function updateBatchQueryCount() {
    const queries = parseBatchQueries();
    document.getElementById('batchQueryCount').textContent = queries.length;
}

function parseBatchQueries() {
    const text = document.getElementById('batchQueriesInput').value || '';
    return text.split('\n').map(s => s.trim()).filter(s => s.length > 0).slice(0, 50);
}

function switchMode(mode) {
    currentMode = mode;
    const singleBtn = document.getElementById('modeSingleBtn');
    const batchBtn = document.getElementById('modeBatchBtn');
    const singleSec = document.getElementById('singleQuerySection');
    const batchSec = document.getElementById('batchQuerySection');
    const hint = document.getElementById('batchHint');
    if (mode === 'batch') {
        singleBtn.className = 'px-3 py-1.5 text-sm rounded-lg transition-colors bg-slate-100 text-slate-600 hover:bg-slate-200';
        batchBtn.className = 'px-3 py-1.5 text-sm rounded-lg transition-colors bg-blue-600 text-white';
        singleSec.classList.add('hidden');
        batchSec.classList.remove('hidden');
        hint.classList.remove('hidden');
    } else {
        singleBtn.className = 'px-3 py-1.5 text-sm rounded-lg transition-colors bg-blue-600 text-white';
        batchBtn.className = 'px-3 py-1.5 text-sm rounded-lg transition-colors bg-slate-100 text-slate-600 hover:bg-slate-200';
        singleSec.classList.remove('hidden');
        batchSec.classList.add('hidden');
        hint.classList.add('hidden');
    }
}

function handleBatchFile(event) {
    const file = event.target.files[0];
    if (!file) return;
    if (file.size > 1024 * 1024) {
        alert(i18n.t('abtest.alert_file_too_large'));
        return;
    }
    const reader = new FileReader();
    reader.onload = function(e) {
        const content = e.target.result;
        // Append content to textarea (keeping existing content)
        const ta = document.getElementById('batchQueriesInput');
        const existing = ta.value.trim();
        ta.value = existing ? (existing + '\n' + content) : content;
        updateBatchQueryCount();
    };
    reader.onerror = function() { alert(i18n.t('abtest.alert_file_read_failed')); };
    reader.readAsText(file, 'UTF-8');
    // Clear input field for next selection
    event.target.value = '';
}

async function loadKnowledgeBases() {
    try {
        const res = await apiFetch('/api/knowledge/list');
        const data = await res.json();
        const select = document.getElementById('abTestKBId');
        if (data.success && data.data) {
            data.data.forEach(kb => {
                select.innerHTML += `<option value="${kb.kb_id}">${kb.name}</option>`;
            });
        }
    } catch (e) {
        console.error(i18n.t('abtest.alert_load_knowledge_bases_failed'), e);
    }
}