/**
 * static/index.html - A/B testing and retrieval default configuration
 *
 * Content: A/B test modal (independent page /static/ab_test.html), variant management, execution, and result rendering;
 *       GET /api/chat/retrieval-defaults fetches default retrieval parameters and populates variant input fields
 *       (the retriever section in config/settings.yaml is the sole source, frontend does not hardcode).
 * Split from index.html's inline <script>.
 */

// ==================== A/B test functionality ====================
function openABTest() {
    //  open new window for A/B test page
    const win = window.open('/static/ab_test.html', 'ab_test_window', 'width=1200,height=850,scrollbars=yes,resizable=yes');
    if (!win) {
        showToast('warning', 'New window blocked by browser, please allow popups and try again');
    }
}

function closeABTest() {
    document.getElementById('abTestDialog').classList.add('hidden');
}

function closeABTestResult() {
    document.getElementById('abTestResultDialog').classList.add('hidden');
}

// ==================== Retrieval Default Configuration (Sole Source: retriever section in config/settings.yaml) ====================
// Retrieval mode / BM25 weight / Similarity threshold / Top K etc. default values all come from the backend API
// GET /api/chat/retrieval-defaults, frontend no longer hardcodes any fallback constants:
// When the backend is unavailable, input fields are left empty, and the request will be null, handled by the backend according to the configuration.
let retrievalDefaults = null;

/** Fetch a default value, return null if not loaded (equivalent to "do not override backend configuration") */
function retrievalDefault(key) {
    const value = retrievalDefaults ? retrievalDefaults[key] : null;
    return value === undefined ? null : value;
}

/** Null/invalid input → null, let the backend handle it (0 is also a valid value) */
function numberOrNull(raw, isInteger) {
    const value = isInteger ? parseInt(raw, 10) : parseFloat(raw);
    return Number.isFinite(value) ? value : null;
}

async function loadRetrievalDefaults() {
    try {
        const data = await fetchJSON('/api/chat/retrieval-defaults');
        if (data.success && data.data) {
            retrievalDefaults = data.data;
        }
    } catch (e) {
        console.error('加载检索默认配置失败:', e);
    }
    // After loading, populate variant forms with default values
    applyRetrievalDefaultsToVariants();
    return retrievalDefaults;
}

/**
 * Apply default values to a variant form.
 * If bm25WeightOverride is null, use the configuration's bm25_weight (variant B can pass vector_weight).
 */
function applyRetrievalDefaultsToVariant(variantEl, bm25WeightOverride) {
    if (!variantEl) return;
    const values = {
        bm25_weight: bm25WeightOverride != null ? bm25WeightOverride : retrievalDefault('bm25_weight'),
        similarity_threshold: retrievalDefault('similarity_threshold'),
        top_k: retrievalDefault('top_k')
    };
    Object.keys(values).forEach(field => {
        const input = variantEl.querySelector(`input[name="${field}"]`);
        if (!input) return;
        if (values[field] != null) input.value = values[field];
        input.title = t('abtest.defaults_hint');
    });
}

/** Apply default values to existing variants in the dialog (variant B uses vector weight, creating a BM25/vector comparison) */
function applyRetrievalDefaultsToVariants() {
    const container = document.getElementById('abTestVariants');
    if (!container) return;
    const bm25Weight = retrievalDefault('bm25_weight');
    const vectorWeight = retrievalDefault('vector_weight');
    Array.from(container.children).forEach((variantEl, index) => {
        applyRetrievalDefaultsToVariant(variantEl, index === 0 ? bm25Weight : vectorWeight);
    });
}

function addABTestVariant() {
    const variantsContainer = document.getElementById('abTestVariants');
    const variants = variantsContainer.children;
    const nextLetter = String.fromCharCode(65 + variants.length);
    const variantName = `变体${nextLetter}`;

    const html = `
        <div class="border border-slate-200 rounded-lg p-3 bg-slate-50">
            <div class="flex items-center gap-2 mb-2">
                <span class="text-sm font-medium text-slate-700">${variantName}</span>
                <button onclick="removeABTestVariant(this)" class="text-red-500 hover:text-red-600 text-xs">Remove</button>
            </div>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                <input type="hidden" name="variant_name" value="${variantName}">
                <div>
                    <label class="block text-xs text-slate-500 mb-1">Retrieval mode</label>
                    <select name="retrieval_mode" class="w-full border border-slate-200 rounded px-2 py-1 text-sm">
                        <option value="">Default</option>
                        <option value="hybrid">Hybrid retrieval</option>
                        <option value="vector">Vector retrieval</option>
                        <option value="bm25">BM25 retrieval</option>
                    </select>
                </div>
                <div>
                    <label class="block text-xs text-slate-500 mb-1">BM25 weight</label>
                    <input type="number" name="bm25_weight" step="0.1" min="0" max="1" class="w-full border border-slate-200 rounded px-2 py-1 text-sm">
                </div>
                <div>
                    <label class="block text-xs text-slate-500 mb-1">Similarity threshold</label>
                    <input type="number" name="similarity_threshold" step="0.05" min="0" max="1" class="w-full border border-slate-200 rounded px-2 py-1 text-sm">
                </div>
                <div>
                    <label class="block text-xs text-slate-500 mb-1">Top-k</label>
                    <input type="number" name="top_k" min="1" max="20" class="w-full border border-slate-200 rounded px-2 py-1 text-sm">
                </div>
            </div>
        </div>
    `;

    variantsContainer.insertAdjacentHTML('beforeend', html);
    applyRetrievalDefaultsToVariant(variantsContainer.lastElementChild, null);
}

function removeABTestVariant(btn) {
    const variantsContainer = document.getElementById('abTestVariants');
    if (variantsContainer.children.length <= 2) {
        showToast('warning', t('abtest.alert_min_variants'));
        return;
    }
    btn.parentElement.parentElement.remove();
}

async function runABTest() {
    const kbId = document.getElementById('abTestKBId').value;
    const query = document.getElementById('abTestQuery').value.trim();

    if (!kbId) {
        showToast('warning', t('chat.no_kb'));
        return;
    }
    if (!query) {
        showToast('warning', t('abtest.alert_enter_query'));
        return;
    }

    const variantsContainer = document.getElementById('abTestVariants');
    const variants = [];

    variantsContainer.querySelectorAll('div.border').forEach((div, index) => {
        const name = div.querySelector('input[name="variant_name"]').value;
        const retrievalMode = div.querySelector('select[name="retrieval_mode"]').value;
        const bm25Weight = numberOrNull(div.querySelector('input[name="bm25_weight"]').value, false);
        const similarityThreshold = numberOrNull(div.querySelector('input[name="similarity_threshold"]').value, false);
        const topK = numberOrNull(div.querySelector('input[name="top_k"]').value, true);

        variants.push({
            name: name,
            retrieval_mode: retrievalMode || null,
            bm25_weight: bm25Weight,
            similarity_threshold: similarityThreshold,
            top_k: topK
        });
    });

    closeABTest();
    showToast('info', t('abtest.alert_running'));


    try {
        // explicitly pass ?lang=: the A/B answer language is determined by the backend
        // get_lang_from_request(),
        // and the fetchJSON's Accept-Language header is set to the same language as service/i18n.py's 'en-US'.
        const data = await fetchJSON(`/api/chat/abtest?lang=${encodeURIComponent(i18n.getLang())}`, {
            method: 'POST',
            body: JSON.stringify({
                kb_id: kbId,
                query: query,
                variants: variants
            })
        });

        if (data.success) {
            renderABTestResult(data);
        } else {
            showToast('error', data.message || t('abtest.alert_failed'));
        }
    } catch (e) {
        console.error('A/B test failed:', e);
        showToast('error', t('abtest.alert_failed'));
    }
}

function renderABTestResult(data) {
    document.getElementById('abResultQuery').textContent = data.query;
    document.getElementById('abResultKBId').textContent = data.kb_id;

    const comparison = data.comparison || {};
    document.getElementById('abBestEval').textContent = comparison.best_by_evaluation ? 
        `${comparison.best_by_evaluation.name} (${comparison.best_by_evaluation.score})` : '-';
    document.getElementById('abBestRecall').textContent = comparison.best_by_recall ? 
        `${comparison.best_by_recall.name} (${comparison.best_by_recall.count})` : '-';
    document.getElementById('abBestDrift').textContent = comparison.best_by_drift_rate ? 
        `${comparison.best_by_drift_rate.name} (${(comparison.best_by_drift_rate.drift_rate * 100).toFixed(0)}%)` : '-';

    let html = '<div class="grid grid-cols-1 md:grid-cols-2 gap-4">';

    data.variants.forEach((variant, index) => {
        const evalScore = variant.evaluation?.overall_score || 0;
        const driftRate = variant.drift_analysis?.drift_rate || 0;
        const contradictionCount = variant.contradiction_count || 0;

        html += `
            <div class="border border-slate-200 rounded-xl overflow-hidden">
                <div class="bg-gradient-to-r from-blue-500 to-blue-600 text-white p-3">
                    <div class="flex items-center justify-between">
                        <span class="font-medium text-lg">${variant.name}</span>
                        <span class="text-sm opacity-80">${t('abtest.config_details')}: ${variant.config.retrieval_mode || t('abtest.default')}</span>
                    </div>
                </div>
                <div class="p-4 space-y-3">
                    <div class="grid grid-cols-3 gap-2 text-xs">
                        <div class="bg-green-50 rounded-lg p-2 text-center">
                            <div class="text-green-600 font-medium">${t('abtest.evaluation_score')}</div>
                            <div class="text-lg font-bold">${evalScore.toFixed(3)}</div>
                        </div>
                        <div class="bg-blue-50 rounded-lg p-2 text-center">
                            <div class="text-blue-600 font-medium">${t('abtest.recall_count')}</div>
                            <div class="text-lg font-bold">${variant.context_count}</div>
                        </div>
                        <div class="bg-purple-50 rounded-lg p-2 text-center">
                            <div class="text-purple-600 font-medium">${t('abtest.drift_rate')}</div>
                            <div class="text-lg font-bold ${driftRate > 0.3 ? 'text-red-600' : 'text-green-600'}">${(driftRate * 100).toFixed(0)}%</div>
                        </div>
                    </div>
                    <div class="text-xs text-slate-500">
                        <div class="font-medium mb-1">${t('abtest.config_details')}</div>
                        <div>${t('abtest.bm25_weight')}: ${variant.config.bm25_weight}</div>
                        <div>${t('abtest.similarity_threshold')}: ${variant.config.similarity_threshold}</div>
                        <div>${t('abtest.top_k')}: ${variant.config.top_k}</div>
                    </div>
                    ${contradictionCount > 0 ? `
                        <div class="bg-red-50 rounded-lg p-2">
                            <div class="text-xs text-red-600 font-medium">${t('abtest.contradiction_count')} ${contradictionCount}</div>
                        </div>
                    ` : ''}
                    <div>
                        <div class="text-xs text-slate-500 font-medium mb-1">${t('abtest.answer')}</div>
                        <div class="text-sm text-slate-700 bg-slate-50 rounded-lg p-2 line-clamp-3">${escapeHtml(variant.answer)}</div>
                    </div>
                    <div>
                        <div class="text-xs text-slate-500 font-medium mb-1">${t('abtest.retrieved_documents')}</span></div>
                        <div class="space-y-1 max-h-32 overflow-y-auto">
                            ${variant.context.map((ctx, i) => `
                                <div class="text-[10px] text-slate-600 bg-slate-50 rounded px-2 py-1">
                                    ${ctx.metadata?.file_name || t('abtest.unknown')} - ${ctx.text.slice(0, 50)}...
                                </div>
                            `).join('')}
                        </div>
                    </div>
                </div>
            </div>
        `;
    });

    html += '</div>';

    if (comparison.summary) {
        html += `
            <div class="mt-4 border border-slate-200 rounded-xl p-4">
                <div class="font-medium text-slate-700 mb-3">${t('abtest.statistics_summary')}</div>
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                    <div class="bg-slate-50 rounded-lg p-2">
                        <div class="text-slate-500 font-medium">${t('abtest.avg_answer_length')}</div>
                        <div class="font-medium">${comparison.summary.avg_answer_length.toFixed(0)}</div>
                    </div>
                    <div class="bg-slate-50 rounded-lg p-2">
                        <div class="text-slate-500 font-medium">${t('abtest.avg_recall_count')}</div>
                        <div class="font-medium">${comparison.summary.avg_context_count.toFixed(1)}</div>
                    </div>
                    <div class="bg-slate-50 rounded-lg p-2">
                        <div class="text-slate-500 font-medium">${t('abtest.avg_evaluation_score')}</div>
                        <div class="font-medium">${comparison.summary.avg_evaluation_score.toFixed(3)}</div>
                    </div>
                    <div class="bg-slate-50 rounded-lg p-2">
                        <div class="text-slate-500 font-medium">${t('abtest.avg_drift_rate')}</div>
                        <div class="font-medium">${(comparison.summary.avg_drift_rate * 100).toFixed(0)}%</div>
                    </div>
                </div>
            </div>
        `;
    }

    document.getElementById('abTestResultContent').innerHTML = html;
    document.getElementById('abTestResultDialog').classList.remove('hidden');
}
