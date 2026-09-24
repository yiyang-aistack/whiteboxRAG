/**
 * static/index.html - monitoring dashboard
 *
 * Content: LLM health check (checkLLMHealth), periodic refresh of the monitoring data and chart
 *       (loadMonitorData / renderRequestChart, Chart.js), filling of the retrieval-defaults bar.
 * Split out of index.html's inline <script>; the chart instance requestChart is defined in core.js.
 */

// ==================== Monitoring ====================
function toggleRetrievalDefaults() {
    const bar = document.getElementById('retrievalDefaultsBar');
    const chevron = document.getElementById('retrievalDefaultsChevron');
    const label = document.getElementById('retrievalDefaultsToggleLabel');
    const hidden = bar.classList.toggle('hidden');
    chevron.style.transform = hidden ? '' : 'rotate(180deg)';
    label.textContent = hidden ? t('chat.retrieval.toggle_show') : t('chat.retrieval.toggle_hide');
}

function fillRetrievalDefaults(defaults) {
    if (!defaults) return;
    const modeLabels = {
        'hybrid': t('chat.retrieval.mode_hybrid'),
        'vector': t('chat.retrieval.mode_vector'),
        'bm25': t('chat.retrieval.mode_bm25'),
    };
    document.getElementById('rd-mode').textContent = modeLabels[defaults.mode] || defaults.mode || '-';
    document.getElementById('rd-bm25').textContent = defaults.bm25_weight != null ? Number(defaults.bm25_weight).toFixed(2) : '-';
    document.getElementById('rd-threshold').textContent = defaults.similarity_threshold != null ? Number(defaults.similarity_threshold).toFixed(2) : '-';
    document.getElementById('rd-topk').textContent = defaults.top_k ?? '-';
}

function fillChunkDefaults(defaults) {
    if (!defaults) return;
    const defaultLabel = t('common.default');
    if (defaults.chunk_size != null) {
        const el = document.getElementById('chunkSizeInput');
        if (el) el.placeholder = `${defaultLabel}: ${defaults.chunk_size}`;
    }
    if (defaults.chunk_overlap != null) {
        const el = document.getElementById('chunkOverlapInput');
        if (el) el.placeholder = `${defaultLabel}: ${defaults.chunk_overlap}`;
    }
}

async function checkLLMHealth() {
    try {
        const data = await fetchJSON('/api/chat/health');

        const statusEl = document.getElementById('llmStatus');
        if (data.data?.healthy) {
            statusEl.innerHTML = `
                <span class="w-2 h-2 rounded-full bg-green-500 mr-1"></span>
                <span class="text-green-600">${t('system.normal')}</span>
            `;
        } else {
            statusEl.innerHTML = `
                <span class="w-2 h-2 rounded-full bg-red-500 mr-1"></span>
                <span class="text-red-500">${t('system.offline')}</span>
            `;
        }

        // Fill the retrieval defaults (the display bar and the form defaults share one source:
        // the retriever section of settings.yaml)
        if (data.data?.retrieval_defaults) {
            if (!retrievalDefaults) {
                // Fall back to the same defaults carried by the health check while
                // /api/chat/retrieval-defaults has not returned or failed
                retrievalDefaults = data.data.retrieval_defaults;
                applyRetrievalDefaultsToVariants();
            }
            fillRetrievalDefaults(data.data.retrieval_defaults);
        }
        // Fill the chunking defaults
        if (data.data?.chunk_defaults) {
            fillChunkDefaults(data.data.chunk_defaults);
        }
    } catch (e) {
        console.error('LLM健康检查失败:', e);
    }
}

async function loadMonitorData() {
    document.getElementById('monitorDialog').classList.remove('hidden');

    try {
        // Fetch the statistics
        const statsData = await fetchJSON('/api/monitor/stats');

        if (statsData.success) {
            const summary = statsData.data.summary || {};
            document.getElementById('monTotalRequests').textContent = summary.total_requests || 0;
            document.getElementById('monErrorRate').textContent = (summary.error_rate || 0) + '%';
            document.getElementById('monAvgResponse').textContent = (summary.avg_response_time || 0) + 'ms';
            document.getElementById('monRunningTasks').textContent = statsData.data.tasks?.running || 0;

            // Render the chart
            renderRequestChart(statsData.data.endpoints || {});
        }

        // Fetch the logs
        const logsData = await fetchJSON('/api/monitor/logs?lines=50');

        if (logsData.success) {
            const logsContainer = document.getElementById('recentLogs');
            logsContainer.innerHTML = (logsData.data || []).slice(-20).reverse().map(log => {
                let color = 'text-slate-600';
                if (log.includes('ERROR')) color = 'text-red-600';
                else if (log.includes('WARNING')) color = 'text-yellow-600';
                else if (log.includes('INFO')) color = 'text-blue-600';
                return `<div class="${color}">${escapeHtml(log)}</div>`;
            }).join('');
        }

    } catch (e) {
        console.error('加载监控数据失败:', e);
    }
}

function renderRequestChart(endpoints) {
    const ctx = document.getElementById('requestChart');
    if (!ctx) return;

    const labels = Object.keys(endpoints).slice(0, 6);
    const counts = labels.map(l => endpoints[l]?.count || 0);

    if (requestChart) {
        requestChart.destroy();
    }

    requestChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels.map(l => l.replace('/api/', '')),
            datasets: [{
                label: '请求次数',
                data: counts,
                backgroundColor: 'rgba(59, 130, 246, 0.5)',
                borderColor: 'rgba(59, 130, 246, 1)',
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }
            },
            scales: {
                y: { beginAtZero: true }
            }
        }
    });
}

function closeMonitorDialog() {
    document.getElementById('monitorDialog').classList.add('hidden');
}

function updateMonitorBar() {
    const stats = state.chatStats;
    document.getElementById('totalRequests').textContent = stats.totalRequests;
    const errorRate = stats.totalRequests > 0
        ? ((stats.totalErrors / stats.totalRequests) * 100).toFixed(1)
        : 0;
    document.getElementById('errorRate').textContent = errorRate + '%';
    document.getElementById('avgResponseTime').textContent = stats.avgResponseTime + 'ms';
}
