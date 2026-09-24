/**
 * static/ab_test.html - result rendering
 *
 * Content: renderResults() renders the A/B test comparison results (metric cards, individual answer comparisons, conclusion prompt).
 */

// ==================== Result rendering ====================
function renderResults(data) {
    const comparison = data.comparison || {};

    // Summary bar
    let summaryHtml = `<span><strong>${_('common.query')}:</strong> ${escapeHtml(data.query)}</span>`;
    summaryHtml += `<span><strong>${_('common.kb_id')}:</strong> ${escapeHtml(data.kb_id)}</span>`;
    if (comparison.best_by_evaluation) {
        summaryHtml += `<span><strong>${_('common.best_eval')}:</strong> <span class="text-green-600 font-medium">${escapeHtml(comparison.best_by_evaluation.name)} (${comparison.best_by_evaluation.score.toFixed(3)})</span></span>`;
    }
    if (comparison.best_by_recall) {
        summaryHtml += `<span><strong>${_('common.best_recall')}:</strong> <span class="text-blue-600 font-medium">${escapeHtml(comparison.best_by_recall.name)} (${comparison.best_by_recall.count})</span></span>`;
    }
    if (comparison.best_by_drift_rate) {
        summaryHtml += `<span><strong>${_('common.best_drift_rate')}:</strong> <span class="text-purple-600 font-medium">${escapeHtml(comparison.best_by_drift_rate.name)} (${(comparison.best_by_drift_rate.drift_rate * 100).toFixed(0)}%)</span></span>`;
    }
    document.getElementById('resultSummary').innerHTML = summaryHtml;

    // Variant cards
    let html = '<div class="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">';

    data.variants.forEach(variant => {
        const evalScore = variant.evaluation?.overall_score || 0;
        const driftRate = variant.drift_analysis?.drift_rate || 0;
        const contradictionCount = variant.contradiction_count || 0;

        html += `
            <div class="border border-slate-200 rounded-xl overflow-hidden">
                <div class="bg-gradient-to-r from-blue-500 to-blue-600 text-white p-3">
                    <div class="flex items-center justify-between">
                        <span class="font-medium text-lg">${escapeHtml(variant.name)}</span>
                        <span class="text-sm opacity-80">${_('common.config')}: ${variant.config.retrieval_mode || _('common.default')} | ${_('common.duration')}: ${(variant.duration || 0).toFixed(2)}s</span>
                    </div>
                </div>
                <div class="p-4 space-y-3">
                    <div class="grid grid-cols-3 gap-2 text-xs">
                        <div class="bg-green-50 rounded-lg p-2 text-center">
                            <div class="text-green-600 font-medium">${_('common.eval_score')}</div>
                            <div class="text-lg font-bold">${evalScore.toFixed(3)}</div>
                        </div>
                        <div class="bg-blue-50 rounded-lg p-2 text-center">
                            <div class="text-blue-600 font-medium">${_('common.recall_count')}</div>
                            <div class="text-lg font-bold">${variant.context_count}</div>
                        </div>
                        <div class="bg-purple-50 rounded-lg p-2 text-center">
                            <div class="text-purple-600 font-medium">${_('common.drift_rate')}</div>
                            <div class="text-lg font-bold ${driftRate > 0.3 ? 'text-red-600' : 'text-green-600'}">${(driftRate * 100).toFixed(0)}%</div>
                        </div>
                    </div>
                    <div class="text-xs text-slate-500">
                        <div class="font-medium mb-1">${_('common.config_details')}</div>
                        <div>${_('common.bm25_weight')}: ${variant.config.bm25_weight} | ${_('common.similarity_threshold')}: ${variant.config.similarity_threshold} | ${_('common.top_k')}: ${variant.config.top_k}</div>
                    </div>
                    ${contradictionCount > 0 ? `
                        <div class="bg-red-50 rounded-lg p-2">
                            <div class="text-xs text-red-600 font-medium">${_('common.contradiction_count')}: ${contradictionCount}</div>
                        </div>
                    ` : ''}
                    <div>
                        <div class="text-xs text-slate-500 font-medium mb-1">${_('common.answer')}</div>
                        <div class="text-sm text-slate-700 bg-slate-50 rounded-lg p-2 max-h-40 overflow-y-auto">${escapeHtml(variant.answer)}</div>
                    </div>
                    <div>
                        <div class="text-xs text-slate-500 font-medium mb-1">${_('common.retrieved_documents')}</div>
                        <div class="space-y-1 max-h-32 overflow-y-auto">
                            ${variant.context.map(ctx => `
                                <div class="text-[10px] text-slate-600 bg-slate-50 rounded px-2 py-1">
                                    ${escapeHtml(ctx.metadata?.file_name || _('common.unknown_file_name'))} - ${escapeHtml((ctx.text || '').slice(0, 60))}...
                                </div>
                            `).join('')}
                        </div>
                    </div>
                    ${variant.trace_id ? `
                        <div class="pt-2 border-t border-slate-100">
                            <button onclick="window.open('/static/index.html?trace_id=${variant.trace_id}', '_blank')"
                                    class="w-full text-xs text-blue-600 hover:text-blue-700 hover:bg-blue-50 rounded-lg py-1.5 transition-colors flex items-center justify-center gap-1">
                                ${_('abtest.view_trace')}
                            </button>
                        </div>
                    ` : ''}
                </div>
            </div>
        `;
    });

    html += '</div>';

    // Statistics summary bar
    if (comparison.summary) {
        html += `
            <div class="mt-4 border border-slate-200 rounded-xl p-4">
                <div class="font-medium text-slate-700 mb-3">${i18n.t('abtest.stats_summary')}</div>
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                    <div class="bg-slate-50 rounded-lg p-2">
                        <div class="text-slate-500">${_('common.avg_answer_length')}</div>
                        <div class="font-medium">${comparison.summary.avg_answer_length.toFixed(0)}</div>
                    </div>
                    <div class="bg-slate-50 rounded-lg p-2">
                        <div class="text-slate-500">${_('common.avg_recall_count')}</div>
                        <div class="font-medium">${comparison.summary.avg_context_count.toFixed(1)}</div>
                    </div>
                    <div class="bg-slate-50 rounded-lg p-2">
                        <div class="text-slate-500">${_('common.avg_evaluation_score')}</div>
                        <div class="font-medium">${comparison.summary.avg_evaluation_score.toFixed(3)}</div>
                    </div>
                    <div class="bg-slate-50 rounded-lg p-2">
                        <div class="text-slate-500">${_('common.avg_drift_rate')}</div>
                        <div class="font-medium">${(comparison.summary.avg_drift_rate * 100).toFixed(0)}%</div>
                    </div>
                </div>
            </div>
        `;
    }

    document.getElementById('resultContent').innerHTML = html;
    document.getElementById('resultsSection').classList.remove('hidden');
    document.getElementById('resultsSection').scrollIntoView({ behavior: 'smooth' });
}
