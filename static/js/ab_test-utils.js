/**
 * static/ab_test.html - tool functions / variant collection
 *
 * Content: escapeHtml, numberOrNull, collectVariants.
 */

// ==================== Tool functions ====================
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
}

// ==================== Variant collection ====================
function numberOrNull(raw, isInteger) {
    const value = isInteger ? parseInt(raw, 10) : parseFloat(raw);
    return Number.isFinite(value) ? value : null;
}

function collectVariants() {
    const variants = [];
    document.querySelectorAll('.variant-item').forEach(div => {
        variants.push({
            name: div.querySelector('input[name="variant_name"]').value,
            retrieval_mode: div.querySelector('select[name="retrieval_mode"]').value || null,
            bm25_weight: numberOrNull(div.querySelector('input[name="bm25_weight"]').value, false),
            similarity_threshold: numberOrNull(div.querySelector('input[name="similarity_threshold"]').value, false),
            top_k: numberOrNull(div.querySelector('input[name="top_k"]').value, true),
            // Switches query_rewrite_enabled and rerank_enabled are from config
            query_rewrite_enabled: defaultsOf('rerank_enabled')
        });
    });
    return variants;
}
