/**
 * static/admin.html - chunks and vectors
 *
 * Content: chunk list and pagination (loadChunks / renderChunkList / renderChunkPagination /
 *       setChunkPage) and the vector details (viewVector / renderVectorDetail).
 */

async function loadChunks() {
    if (!state.currentKB) return;

    state.chunkLimit = parseInt(document.getElementById('chunkLimit').value) || 20;

    try {
        const response = await fetch(`/api/knowledge/${state.currentKB.kb_id}/chunks?limit=${state.chunkLimit}&offset=${state.chunkOffset}`);
        const data = await response.json();

        state.chunks = data.data || [];
        state.chunkTotal = data.total || 0;

        renderChunkList();
        renderChunkPagination();
    } catch (error) {
        console.error('加载分块列表失败:', error);
    }
}

function renderChunkList() {
    const container = document.getElementById('chunkList');
    const countEl = document.getElementById('chunkCount');

    if (!state.currentKB) {
        container.innerHTML = `<div class="text-center text-slate-400 py-12">${t('chat.no_kb')}</div>`;
        countEl.textContent = '';
        return;
    }

    countEl.textContent = `共 ${state.chunkTotal} 个分块`;

    if (state.chunks.length === 0) {
        container.innerHTML = `<div class="text-center text-slate-400 py-12">${t('admin.no_chunks')}</div>`;
        return;
    }

    container.innerHTML = state.chunks.map(chunk => `
        <div class="p-4 bg-slate-50 rounded-lg border border-slate-200 hover:border-blue-300 cursor-pointer transition-colors" onclick="selectChunk('${chunk.id}')">
            <div class="flex items-start justify-between mb-2">
                <span class="text-xs font-mono text-slate-400">${chunk.id.slice(0, 8)}...</span>
                <button onclick="event.stopPropagation(); viewVector('${chunk.id}')" class="text-blue-600 hover:text-blue-700 text-xs font-medium">
                    ${t('admin.view_vector')}
                </button>
            </div>
            <p class="text-sm text-slate-700 line-clamp-3">${chunk.text}</p>
            ${chunk.metadata && Object.keys(chunk.metadata).length > 0 ? `
                <div class="mt-2 flex flex-wrap gap-2">
                    ${Object.entries(chunk.metadata).map(([k, v]) => `
                        <span class="text-xs px-2 py-1 bg-slate-200 text-slate-600 rounded">${k}: ${String(v).slice(0, 20)}</span>
                    `).join('')}
                </div>
            ` : ''}
        </div>
    `).join('');
}

function renderChunkPagination() {
    const container = document.getElementById('chunkPagination');
    const pages = Math.ceil(state.chunkTotal / state.chunkLimit);

    if (pages <= 1) {
        container.innerHTML = '';
        return;
    }

    let html = '';

    if (state.chunkOffset > 0) {
        html += `<button onclick="setChunkPage(0)" class="px-3 py-1 text-sm border border-slate-300 rounded hover:bg-slate-100">${i18n.t('common.page_first')}</button>`;
        html += `<button onclick="setChunkPage(${state.chunkOffset - state.chunkLimit})" class="px-3 py-1 text-sm border border-slate-300 rounded hover:bg-slate-100">${i18n.t('common.page_prev')}</button>`;
    }

    for (let i = 0; i < pages; i++) {
        const offset = i * state.chunkLimit;
        const isActive = offset === state.chunkOffset;
        html += `<button onclick="setChunkPage(${offset})" class="px-3 py-1 text-sm border rounded ${isActive ? 'bg-blue-600 text-white border-blue-600' : 'border-slate-300 hover:bg-slate-100'}">${i + 1}</button>`;
    }

    if (state.chunkOffset + state.chunkLimit < state.chunkTotal) {
        html += `<button onclick="setChunkPage(${state.chunkOffset + state.chunkLimit})" class="px-3 py-1 text-sm border border-slate-300 rounded hover:bg-slate-100">${i18n.t('common.page_next')}</button>`;
        html += `<button onclick="setChunkPage(${Math.max(0, (pages - 1) * state.chunkLimit)})" class="px-3 py-1 text-sm border border-slate-300 rounded hover:bg-slate-100">${i18n.t('common.page_last')}</button>`;
    }

    container.innerHTML = html;
}

function setChunkPage(offset) {
    state.chunkOffset = Math.max(0, offset);
    loadChunks();
}

async function viewVector(chunk_id) {
    if (!state.currentKB) return;

    try {
        const response = await fetch(`/api/knowledge/${state.currentKB.kb_id}/chunks/${chunk_id}`);
        const data = await response.json();

        const info = data.data;
        state.selectedChunk = info;

        switchTab('vectors');
        renderVectorDetail();
    } catch (error) {
        console.error('获取向量详情失败:', error);
        alert(t('common.failed'));
    }
}

function renderVectorDetail() {
    const container = document.getElementById('vectorContent');
    const statsEl = document.getElementById('vectorStats');

    if (!state.selectedChunk) {
        container.innerHTML = `<div class="text-center text-slate-400 py-12">${t('admin.no_vector')}</div>`;
        statsEl.textContent = '';
        return;
    }

    const info = state.selectedChunk;
    const embedInfo = info.embedding_info || {};

    statsEl.textContent = `维度: ${embedInfo.dimension || '-'} | 最小值: ${embedInfo.min_value?.toFixed(4) || '-'} | 最大值: ${embedInfo.max_value?.toFixed(4) || '-'}`;

    container.innerHTML = `
        <div class="p-4 bg-slate-50 rounded-lg">
            <h4 class="font-semibold text-slate-700 mb-3">${t('admin.chunk_text')}</h4>
            <pre class="code-preview text-sm text-slate-600 whitespace-pre-wrap">${info.text || '(空)'}</pre>
        </div>

        <div class="p-4 bg-slate-50 rounded-lg">
            <h4 class="font-semibold text-slate-700 mb-3">${t('admin.chunk_metadata')}</h4>
            <pre class="code-preview text-sm text-slate-600">${JSON.stringify(info.metadata || {}, null, 2)}</pre>
        </div>

        <div class="p-4 bg-slate-50 rounded-lg">
            <h4 class="font-semibold text-slate-700 mb-3">${t('admin.vector_preview')}</h4>
            <div class="grid grid-cols-2 gap-4 mb-4">
                <div class="bg-white p-3 rounded-lg border border-slate-200">
                    <span class="text-xs text-slate-500">${t('admin.vector_dimension')}</span>
                    <p class="text-lg font-semibold text-slate-700">${embedInfo.dimension || '-'}</p>
                </div>
                <div class="bg-white p-3 rounded-lg border border-slate-200">
                    <span class="text-xs text-slate-500">${t('admin.vector_mean')}</span>
                    <p class="text-lg font-semibold text-slate-700">${embedInfo.avg_value?.toFixed(4) || '-'}</p>
                </div>
                <div class="bg-white p-3 rounded-lg border border-slate-200">
                    <span class="text-xs text-slate-500">${t('admin.vector_min')}</span>
                    <p class="text-lg font-semibold text-slate-700">${embedInfo.min_value?.toFixed(4) || '-'}</p>
                </div>
                <div class="bg-white p-3 rounded-lg border border-slate-200">
                    <span class="text-xs text-slate-500">${t('admin.vector_max')}</span>
                    <p class="text-lg font-semibold text-slate-700">${embedInfo.max_value?.toFixed(4) || '-'}</p>
                </div>
            </div>

            <h5 class="text-sm font-medium text-slate-600 mb-2">${t('admin.vector_preview')}</h5>
            <pre class="code-preview text-sm text-slate-600">${JSON.stringify(embedInfo.sample_values || [], null, 2)}</pre>
        </div>
    `;
}
