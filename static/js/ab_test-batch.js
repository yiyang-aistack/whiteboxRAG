/**
 * static/ab_test.html - batch testing
 *
 * Content: runBatchTest runs batch testing on queries, renderBatchResults renders the results.
 */

// ==================== Batch Testing ====================
async function runBatchTest() {
    const kbId = document.getElementById('abTestKBId').value;
    if (!kbId) { alert(i18n.t('common.select_knowledge_base')); return; }

    const queries = parseBatchQueries();
    if (queries.length === 0) { alert(_('common.enter_test_queries')); return; }

    const variants = collectVariants();
    if (variants.length < 2) { alert(_('common.at_least_two_variants')); return; }

    // Progress Section
    document.getElementById('progressSection').classList.remove('hidden');
    document.getElementById('resultsSection').classList.add('hidden');
    const startBtn = document.getElementById('startBtn');
    startBtn.disabled = true;
    startBtn.classList.add('opacity-50', 'cursor-not-allowed');
    startBtn.textContent = _('common.batch_test_running');

    // Build progress Steps
    const totalQueries = queries.length;
    const steps = [
        { label: _('common.initialize_batch_test_environment'), weight: 5 },
        ...queries.map((q, i) => ({
            label: `${i18n.t('common.execute_query')} ${i + 1}/${totalQueries}：${q.length > 20 ? q.slice(0, 20) + '...' : q}`,
            weight: 80 / totalQueries
        })),
        { label: _('common.aggregate_metrics_and_generate_report'), weight: 15 }
    ];
    const stepsContainer = document.getElementById('progressSteps');
    stepsContainer.innerHTML = steps.map((s, i) => `
        <div class="step-item flex items-center gap-2 text-sm" data-step="${i}">
            <span class="step-icon w-5 h-5 rounded-full border-2 border-slate-300 flex items-center justify-center text-xs text-slate-400 shrink-0">○</span>
            <span class="step-text text-slate-500">${escapeHtml(s.label)}</span>
        </div>
    `).join('');

    let currentProgress = 0;
    let currentStep = 0;
    markStepActive(0);
    progressTimer = setInterval(() => {
        if (currentProgress < 90) {
            currentProgress += Math.random() * 1.5 + 0.4;
            if (currentProgress > 90) currentProgress = 90;
            updateProgress(currentProgress, steps, currentStep);
            const stepThreshold = steps.slice(0, currentStep + 1).reduce((a, s) => a + s.weight, 0);
            if (currentProgress >= stepThreshold && currentStep < steps.length - 1) {
                markStepDone(currentStep);
                currentStep++;
                markStepActive(currentStep);
            }
        }
    }, 400);

    try {
        const res = await apiFetch('/api/chat/abtest/batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ kb_id: kbId, queries: queries, variants: variants })
        });
        const data = await res.json();
        clearInterval(progressTimer);

        updateProgress(100, steps, steps.length - 1);
        for (let i = 0; i < steps.length - 1; i++) markStepDone(i);
        markStepDone(steps.length - 1);
        document.getElementById('progressStep').textContent = _('common.batch_test_completed');

        setTimeout(() => {
            if (data.success) {
                renderBatchResults(data, variants);
            } else {
                alert(data.detail || data.message || _('common.batch_test_failed'));
            }
        }, 600);
    } catch (e) {
        clearInterval(progressTimer);
        console.error(i18n.t('common.batch_test_failed'), e);
        document.getElementById('progressStep').textContent = i18n.t('common.batch_test_failed');
        document.getElementById('progressBar').className = 'bg-red-500 h-full rounded-full transition-all duration-500';
        alert(_('common.batch_test_failed') + ': ' + e.batch_test_failed_reason || e.message);
    } finally {
        startBtn.disabled = false;
        startBtn.classList.remove('opacity-50', 'cursor-not-allowed');
        startBtn.textContent = _('common.start_test');
    }
}

function renderBatchResults(data, variants) {
    // Result Summary
    let summaryHtml = `<span><strong>${_('common.test_type')}: </strong> ${_('common.batch_test')}</span>`;
    summaryHtml += `<span><strong>${_('common.knowledge_id')}: </strong> ${escapeHtml(data.kb_id)}</span>`;
    summaryHtml += `<span><strong>${_('common.question_count')}: </strong> ${data.query_count}</span>`;
    summaryHtml += `<span><strong>${_('common.variant_count')}: </strong> ${variants.length}</span>`;
    if (data.task_id) {
        summaryHtml += `<span class="text-slate-400 text-xs"> trace ID: ${escapeHtml(data.task_id)}</span>`;
    }
    document.getElementById('resultSummary').innerHTML = summaryHtml;

    let html = '';

    // 1) Variant Metrics Table (Core)
    if (data.summary && data.summary.length) {
        const bestEval = data.best_by_eval;
        const bestRecall = data.best_by_recall;
        const bestDrift = data.best_by_drift;
        html += `
            <div class="border border-slate-200 rounded-xl overflow-hidden mb-4">
                <div class="bg-slate-50 px-4 py-2 border-b border-slate-200 font-medium text-slate-700">${i18n.t('abtest.variant_metrics')}</div>
                <div class="overflow-x-auto">
                    <table class="w-full text-sm">
                        <thead class="bg-slate-50 text-slate-500 text-xs uppercase">
                            <tr>
                                <th class="px-4 py-2 text-left">${_('common.variant')}</th>
                                <th class="px-4 py-2 text-center">${_('common.avg_eval_score')}</th>
                                <th class="px-4 py-2 text-center">${_('common.avg_recall_count')}</th>
                                <th class="px-4 py-2 text-center">${_('common.avg_drift_rate')}</th>
                                <th class="px-4 py-2 text-center">${_('common.mark')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${data.summary.map(s => {
                                const isBestEval = bestEval && bestEval.name === s.name;
                                const isBestRecall = bestRecall && bestRecall.name === s.name;
                                const isBestDrift = bestDrift && bestDrift.name === s.name;
                                const badges = [];
                                if (isBestEval) badges.push(`<span class="text-green-600 text-[10px]">${_('common.best_eval')}</span>`);
                                if (isBestRecall) badges.push(`<span class="text-blue-600 text-[10px]">${_('common.best_recall')}</span>`);
                                if (isBestDrift) badges.push(`<span class="text-purple-600 text-[10px]">${_('common.low_drift')}</span>`);
                                return `
                                    <tr class="border-t border-slate-100 ${isBestEval ? 'bg-green-50' : ''}">
                                        <td class="px-4 py-2 font-medium text-slate-700">${escapeHtml(s.name)}</td>
                                        <td class="px-4 py-2 text-center ${isBestEval ? 'text-green-600 font-bold' : 'text-slate-600'}">${s.avg_eval_score.toFixed(4)}</td>
                                        <td class="px-4 py-2 text-center ${isBestRecall ? 'text-blue-600 font-bold' : 'text-slate-600'}">${s.avg_context_count.toFixed(1)}</td>
                                        <td class="px-4 py-2 text-center ${isBestDrift ? 'text-purple-600 font-bold' : 'text-slate-600'}">${(s.avg_drift_rate * 100).toFixed(1)}%</td>
                                        <td class="px-4 py-2 text-center space-y-1">${badges.join('<br>')}</td>
                                    </tr>
                                `;
                            }).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }

    // 2) Query Details
    if (data.results && data.results.length) {
        html += `
            <div class="border border-slate-200 rounded-xl overflow-hidden">
                <div class="bg-slate-50 px-4 py-2 border-b border-slate-200 font-medium text-slate-700">${_('abtest.detail_section')}</div>
                <div class="p-3 space-y-3">
                    ${data.results.map((r, qi) => `
                        <div class="border border-slate-100 rounded-lg p-3 bg-white">
                            <div class="text-sm font-medium text-slate-700 mb-2">
                                <span class="text-slate-400 mr-2">Q${qi + 1}.</span>${escapeHtml(r.query)}
                            </div>
                            <div class="grid grid-cols-1 md:grid-cols-${Math.min(r.variants.length, 4)} gap-2">
                                ${r.variants.map((v, vi) => {
                                    const evalScore = v.eval_score || 0;
                                    const driftRate = v.drift_rate || 0;
                                    return `
                                        <div class="bg-slate-50 rounded-lg p-2 text-xs">
                                            <div class="font-medium text-slate-700 mb-1">${escapeHtml(v.name)}</div>
                                            <div class="grid grid-cols-3 gap-1 text-center">
                                                <div>
                                                    <div class="text-slate-400 text-[10px]">${_('common.eval')}</div>
                                                    <div class="font-bold text-green-600">${evalScore.toFixed(3)}</div>
                                                </div>
                                                <div>
                                                    <div class="text-slate-400 text-[10px]">${_('common.recall')}</div>
                                                    <div class="font-bold text-blue-600">${v.context_count}</div>
                                                </div>
                                                <div>
                                                    <div class="text-slate-400 text-[10px]">${_('common.drift')}</div>
                                                    <div class="font-bold ${driftRate > 0.3 ? 'text-red-600' : 'text-purple-600'}">${(driftRate * 100).toFixed(0)}%</div>
                                                </div>
                                            </div>
                                            ${v.trace_id ? `
                                                <button onclick="window.open('/static/index.html?trace_id=${v.trace_id}', '_blank')"
                                                        class="mt-2 w-full text-[10px] text-blue-600 hover:text-blue-700 hover:bg-blue-50 rounded py-1 transition-colors">
                                                    ${i18n.t('abtest.view_trace')}
                                                </button>
                                            ` : ''}
                                        </div>
                                    `;
                                }).join('')}
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    }

    document.getElementById('resultContent').innerHTML = html;
    document.getElementById('resultsSection').classList.remove('hidden');
    document.getElementById('resultsSection').scrollIntoView({ behavior: 'smooth' });
}
