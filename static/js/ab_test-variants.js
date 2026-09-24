/**
 * static/ab_test.html - variant management
 *
 * Content: addVariant generates variant forms (input fields never have literal default values), applyVariantDefaults populates defaults from
 *       config/settings.yaml's retriever section, removeVariant removes variants.
 */

// ==================== Variant management ====================
function addVariant(name, bm25Weight, retrievalMode) {
    if (!name) {
        variantIdx++;
        name = `${i18n.t('common.variant')}${String.fromCharCode(65 + variantIdx)}`;
    } else {
        variantIdx++;
    }
    const container = document.getElementById('variantsContainer');
    const html = `
        <div class="border border-slate-200 rounded-lg p-3 bg-slate-50 variant-item">
            <div class="flex items-center gap-2 mb-2">
                <span class="text-sm font-medium text-slate-700">${name}</span>
                <button onclick="removeVariant(this)" class="text-red-500 hover:text-red-600 text-xs">${i18n.t('abtest.delete_variant')}</button>
            </div>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                <input type="hidden" name="variant_name" value="${name}">
                <div>
                    <label class="block text-xs text-slate-500 mb-1">${i18n.t('abtest.retrieval_mode')}</label>
                    <select name="retrieval_mode" class="w-full border border-slate-200 rounded px-2 py-1 text-sm">
                        <option value="">${i18n.t('common.default')}</option>
                        <option value="hybrid">${i18n.t('abtest.mode_hybrid')}</option>
                        <option value="vector">${i18n.t('abtest.mode_vector')}</option>
                        <option value="bm25">${i18n.t('abtest.mode_bm25')}</option>
                    </select>
                </div>
                <div>
                    <label class="block text-xs text-slate-500 mb-1">${i18n.t('abtest.bm25_weight')}</label>
                    <input type="number" name="bm25_weight" step="0.1" min="0" max="1" class="w-full border border-slate-200 rounded px-2 py-1 text-sm">
                </div>
                <div>
                    <label class="block text-xs text-slate-500 mb-1">${i18n.t('abtest.similarity_threshold')}</label>
                    <input type="number" name="similarity_threshold" step="0.05" min="0" max="1" class="w-full border border-slate-200 rounded px-2 py-1 text-sm">
                </div>
                <div>
                    <label class="block text-xs text-slate-500 mb-1">${i18n.t('abtest.top_k')}</label>
                    <input type="number" name="top_k" min="1" max="20" class="w-full border border-slate-200 rounded px-2 py-1 text-sm">
                </div>
            </div>
        </div>
    `;
    container.insertAdjacentHTML('beforeend', html);
    applyVariantDefaults(container.lastElementChild, bm25Weight, retrievalMode);
}

/**
 * Populate a variant form with default values from config/settings.yaml.
 * Use the config's bm25_weight when bm25Weight is null.
 * Keep the retrievalMode empty (showing "default") when null, otherwise fill the corresponding option.
 */
function applyVariantDefaults(variantEl, bm25Weight, retrievalMode) {
    if (!variantEl) return;
    if (retrievalMode) {
        const select = variantEl.querySelector('select[name="retrieval_mode"]');
        if (select) select.value = retrievalMode;
    }
    const values = {
        bm25_weight: bm25Weight != null ? bm25Weight : defaultsOf('bm25_weight'),
        similarity_threshold: defaultsOf('similarity_threshold'),
        top_k: defaultsOf('top_k')
    };
    Object.keys(values).forEach(field => {
        const input = variantEl.querySelector(`input[name="${field}"]`);
        if (!input) return;
        if (values[field] != null) input.value = values[field];
        input.title = i18n.t('abtest.defaults_hint');
    });
}

function removeVariant(btn) {
    const container = document.getElementById('variantsContainer');
    if (container.children.length <= 2) {
        alert(i18n.t('abtest.alert_min_variants'));
        return;
    }
    btn.closest('.variant-item').remove();
}