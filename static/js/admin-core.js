/**
 * static/admin.html - global state / knowledge base list
 *
 * Split out of admin.html's inline <script>; the top-level const / function still live in the global
 * lexical scope, so the HTML inline events can reference them directly (see the <script> list at the
 * bottom of admin.html for the load order).
 * Content: the i18n alias t, the global state object, knowledge base list loading and rendering
 *       (loadKBList / renderKBList) and switching the current knowledge base (selectKB).
 */

const t = i18n.t;
const state = {
    currentKB: null,
    kbList: [],
    fileList: [],
    chunks: [],
    chunkOffset: 0,
    chunkLimit: 20,
    chunkTotal: 0,
    selectedChunk: null
};

async function loadKBList() {
    try {
        const response = await fetch('/api/knowledge/list');
        const data = await response.json();
        state.kbList = data.data || [];
        renderKBList();
    } catch (error) {
        console.error('加载知识库列表失败:', error);
        document.getElementById('kbList').innerHTML = `<div class="text-center text-red-500 py-8">${t('common.failed')}</div>`;
    }
}

function renderKBList() {
    const container = document.getElementById('kbList');
    if (state.kbList.length === 0) {
        container.innerHTML = `<div class="text-center text-slate-400 py-8">${t('common.no_data')}</div>`;
        return;
    }

    container.innerHTML = state.kbList.map(kb => `
        <div class="kb-card p-3 rounded-lg border border-slate-200 cursor-pointer transition-all hover:border-blue-300 ${state.currentKB?.kb_id === kb.kb_id ? 'active' : ''}" onclick="selectKB('${kb.kb_id}')">
            <div class="flex items-center justify-between mb-1">
                <h4 class="font-medium text-slate-700 truncate">${kb.name}</h4>
                <button onclick="event.stopPropagation(); deleteKB('${kb.kb_id}')" class="text-slate-400 hover:text-red-500">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                    </svg>
                </button>
            </div>
            <div class="flex items-center space-x-3 text-xs text-slate-500">
                <span>${kb.document_count} ${i18n.t('common.docs')}</span>
                <span>${kb.chunk_count} ${i18n.t('common.chunks')}</span>
            </div>
            ${kb.description ? `<p class="text-xs text-slate-400 mt-1 truncate">${kb.description}</p>` : ''}
        </div>
    `).join('');
}

async function selectKB(kb_id) {
    const kb = state.kbList.find(k => k.kb_id === kb_id);
    if (!kb) return;

    state.currentKB = kb;
    renderKBList();

    document.getElementById('kbTitle').textContent = kb.name;
    document.getElementById('kbSubtitle').textContent = `ID: ${kb.kb_id} | ${kb.document_count} 文档 | ${kb.chunk_count} 分块`;
    document.getElementById('kbStats').textContent = `创建于 ${formatDate(kb.created_at)}`;

    await loadFileList();
    await loadChunks();
}
