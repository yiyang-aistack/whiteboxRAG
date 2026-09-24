/**
 * static/ab_test.html - execute test with progress bar
 *
 * Content: runTest() submits POST /api/chat/abtest as per mode branch, progress bar, and step status updates
 *       (updateProgress / markStepActive / markStepDone).
 */

// ==================== Execute test with progress bar ====================
async function runTest() {
    if (currentMode === 'batch') {
        return runBatchTest();
    }
    const kbId = document.getElementById('abTestKBId').value;
    const query = document.getElementById('abTestQuery').value.trim();

    if (!kbId) { alert(i18n.t('abtest.alert_select_kb')); return; }
    if (!query) { alert(i18n.t('abtest.alert_enter_query')); return; }

    const variants = collectVariants();
    if (variants.length < 2) { alert(i18n.t('abtest.alert_min_variants')); return; }

    // Show progress bar, keep config area visible
    document.getElementById('progressSection').classList.remove('hidden');
    document.getElementById('resultsSection').classList.add('hidden');
    document.getElementById('startBtn').disabled = true;
    document.getElementById('startBtn').classList.add('opacity-50', 'cursor-not-allowed');
    document.getElementById('startBtn').textContent = i18n.t('abtest.testing');

    // Build progress steps
    const steps = [
        { label: i18n.t('common.init_test_env'), weight: 10 },
        ...variants.map(v => ({
            label: `${i18n.t('common.execute_variant')} ${v.name} (${v.retrieval_mode || i18n.t('common.default_retrieval_mode')})`,
            weight: 60 / variants.length
        })),
        { label: i18n.t('common.analyze_comparison_results'), weight: 15 },
        { label: i18n.t('common.generate_report'), weight: 15 }
    ];

    const stepsContainer = document.getElementById('progressSteps');
    stepsContainer.innerHTML = steps.map((s, i) => `
        <div class="step-item flex items-center gap-2 text-sm" data-step="${i}">
            <span class="step-icon w-5 h-5 rounded-full border-2 border-slate-300 flex items-center justify-center text-xs text-slate-400 shrink-0">○</span>
            <span class="step-text text-slate-500">${s.label}</span>
        </div>
    `).join('');

    // Simulate progress update (API is single request, cannot get real-time progress)
    let currentProgress = 0;
    let currentStep = 0;
    markStepActive(0);

    progressTimer = setInterval(() => {
        if (currentProgress < 90) {
            currentProgress += Math.random() * 2.5 + 0.8;
            if (currentProgress > 90) currentProgress = 90;
            updateProgress(currentProgress, steps, currentStep);

            // Advance step indicator when progress reaches threshold for next step
            const stepThreshold = steps.slice(0, currentStep + 1).reduce((a, s) => a + s.weight, 0);
            if (currentProgress >= stepThreshold && currentStep < steps.length - 1) {
                markStepDone(currentStep);
                currentStep++;
                markStepActive(currentStep);
            }
        }
    }, 300);

    // Send API request (apiFetch will include interface language, ensure response language matches interface)
    try {
        const res = await apiFetch('/api/chat/abtest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ kb_id: kbId, query: query, variants: variants })
        });
        const data = await res.json();

        clearInterval(progressTimer);

        // Complete all steps
        updateProgress(100, steps, steps.length - 1);
        for (let i = 0; i < steps.length; i++) {
            if (i < steps.length - 1) markStepDone(i);
        }
        markStepDone(steps.length - 1);
        document.getElementById('progressStep').textContent = _('common.test_completed');

        setTimeout(() => {
            if (data.success) {
                renderResults(data);
            } else {
                alert(data.detail || data.message || _('common.test_failed'));
            }
        }, 600);

    } catch (e) {
        clearInterval(progressTimer);
        console.error(i18n.t('abtest.alert_test_failed'), e);
        document.getElementById('progressStep').textContent = i18n.t('abtest.alert_test_failed');
        document.getElementById('progressBar').className = 'bg-red-500 h-full rounded-full transition-all duration-500';
        alert(i18n.t('abtest.alert_test_failed') + ':  ' + e.message);
    } finally {
        document.getElementById('startBtn').disabled = false;
        document.getElementById('startBtn').classList.remove('opacity-50', 'cursor-not-allowed');
        document.getElementById('startBtn').textContent = _('common.start_test');
    }
}

function updateProgress(percent, steps, currentStep) {
    document.getElementById('progressBar').style.width = percent + '%';
    document.getElementById('progressPercent').textContent = Math.round(percent) + '%';
    if (steps[currentStep] && percent < 100) {
        document.getElementById('progressStep').textContent = steps[currentStep].label + '...';
    }
}

function markStepActive(idx) {
    const item = document.querySelector(`[data-step="${idx}"]`);
    if (item) {
        const icon = item.querySelector('.step-icon');
        const text = item.querySelector('.step-text');
        icon.className = 'step-icon w-5 h-5 rounded-full border-2 border-blue-500 flex items-center justify-center text-xs text-blue-500 shrink-0 animate-pulse';
        icon.textContent = '●';
        text.className = 'step-text text-blue-600 font-medium';
    }
}

function markStepDone(idx) {
    const item = document.querySelector(`[data-step="${idx}"]`);
    if (item) {
        const icon = item.querySelector('.step-icon');
        const text = item.querySelector('.step-text');
        icon.className = 'step-icon w-5 h-5 rounded-full bg-green-500 flex items-center justify-center text-xs text-white shrink-0';
        icon.textContent = '✓';
        text.className = 'step-text text-green-600';
    }
}
