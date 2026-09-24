/**
 * static/index.html - knowledge base management / file management / file upload
 *
 * Split out of index.html's inline <script>. Uses state / t / fetchJSON / escapeHtml from core.js
 * and showToast from ui.js, all resolved at call time, so there is no load-order dependency.
 */

// ==================== Knowledge base management ====================
async function loadKBList() {
    try {
        const data = await fetchJSON('/api/knowledge/list');
        if (data.success) {
            state.kbList = data.data || [];
            renderKBList();
        }
    } catch (e) {
        console.error('加载知识库列表失败:', e);
        showToast('error', '加载知识库列表失败');
    }
}

function renderKBList() {
    const container = document.getElementById('kbList');
    const emptyState = document.getElementById('kbEmptyState');

    if (state.kbList.length === 0) {
        container.innerHTML = '';
        emptyState.classList.remove('hidden');
        return;
    }

    emptyState.classList.add('hidden');
    container.innerHTML = state.kbList.map(kb => `
        <div class="kb-card p-3 rounded-lg border-2 border-slate-200 cursor-pointer hover:border-blue-400 transition-all ${state.currentKB?.kb_id === kb.kb_id ? 'active' : ''}"
             onclick="selectKB('${kb.kb_id}')">
            <div class="flex items-center justify-between mb-1">
                <h3 class="font-medium text-slate-700 text-sm truncate">${escapeHtml(kb.name)}</h3>
                <div class="flex items-center gap-1">
                    <button onclick="event.stopPropagation(); openFileManager('${kb.kb_id}')" class="text-slate-400 hover:text-blue-500" title="${t('kb.file_manager')}">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/>
                        </svg>
                    </button>
                    <button onclick="event.stopPropagation(); deleteKB('${kb.kb_id}')" class="text-slate-400 hover:text-red-500" title="${t('kb.delete_kb')}">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                        </svg>
                    </button>
                </div>
            </div>
            <div class="text-xs text-slate-500">
                <span>${t('kb.doc_count')}: ${kb.document_count}</span>
                <span class="mx-1">•</span>
                <span>${t('kb.vector_count')}: ${kb.chunk_count}</span>
            </div>
        </div>
    `).join('');
}

function selectKB(kbId) {
    const kb = state.kbList.find(k => k.kb_id === kbId);
    if (!kb) return;

    state.currentKB = kb;
    document.getElementById('currentKBNmae').textContent = kb.name;
    document.getElementById('chatInput').disabled = false;
    document.getElementById('sendBtn').disabled = false;
    document.getElementById('sourcePanelToggle').classList.remove('hidden');

    renderKBList();
    clearChat();
    showToast('success', t('kb.switched').replace('{name}', kb.name));
}

function showCreateKBDialog() {
    document.getElementById('createKBDialog').classList.remove('hidden');
    document.getElementById('kbNameInput').focus();
}

function closeCreateKBDialog() {
    document.getElementById('createKBDialog').classList.add('hidden');
    document.getElementById('kbNameInput').value = '';
    document.getElementById('kbDescInput').value = '';
}

async function createKnowledgeBase() {
    const name = document.getElementById('kbNameInput').value.trim();
    const description = document.getElementById('kbDescInput').value.trim();

    if (!name) {
        showToast('warning', '请输入知识库名称');
        return;
    }

    try {
        const data = await fetchJSON('/api/knowledge/create', {
            method: 'POST',
            body: JSON.stringify({ name, description })
        });

        if (data.success) {
            showToast('success', '知识库创建成功');
            closeCreateKBDialog();
            await loadKBList();
            selectKB(data.kb_id);
        } else {
            showToast('error', data.message || '创建失败');
        }
    } catch (e) {
        console.error('创建知识库失败:', e);
        showToast('error', '创建知识库失败');
    }
}

async function deleteKB(kbId) {
    if (!confirm('确定要删除该知识库吗？删除后无法恢复。')) return;

    try {
        const data = await fetchJSON(`/api/knowledge/${kbId}`, { method: 'DELETE' });

        if (data.success) {
            showToast('success', '知识库已删除');
            if (state.currentKB?.kb_id === kbId) {
                state.currentKB = null;
                document.getElementById('currentKBNmae').textContent = t('kb.select_placeholder');
                document.getElementById('chatInput').disabled = true;
                document.getElementById('sendBtn').disabled = true;
            }
            await loadKBList();
        } else {
            showToast('error', data.message || '删除失败');
        }
    } catch (e) {
        console.error('删除知识库失败:', e);
        showToast('error', '删除知识库失败');
    }
}

// ==================== File management ====================
async function openFileManager(kbId) {
    if (kbId) {
        const kb = state.kbList.find(k => k.kb_id === kbId);
        if (!kb) {
            showToast('error', '知识库不存在');
            return;
        }
        state.currentKB = kb;
        document.getElementById('currentKBNmae').textContent = kb.name;
        renderKBList();
    }

    if (!state.currentKB) {
        showToast('warning', t('chat.no_kb'));
        return;
    }

    document.getElementById('fileManagerDialog').classList.remove('hidden');
    document.getElementById('fmKBName').textContent = state.currentKB.name;
    await refreshFileList();
}

function closeFileManager() {
    document.getElementById('fileManagerDialog').classList.add('hidden');
}

async function refreshFileList() {
    if (!state.currentKB) return;

    try {
        const data = await fetchJSON(`/api/knowledge/${state.currentKB.kb_id}/files`);

        if (data.success) {
            const files = data.data || [];
            renderFileList(files);
        } else {
            showToast('error', data.message || '获取文件列表失败');
        }
    } catch (e) {
        console.error('获取文件列表失败:', e);
        showToast('error', '获取文件列表失败');
    }
}

function renderFileList(files) {
    const container = document.getElementById('fileListContainer');
    const countEl = document.getElementById('fileCountLabel');

    if (countEl) countEl.textContent = t('file_manager.total_files').replace('{count}', files.length);

    if (files.length === 0) {
        container.innerHTML = `
            <div class="text-center text-slate-400 py-8">
                <svg class="w-12 h-12 mx-auto mb-3 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
                </svg>
                <p class="text-sm">暂无文件</p>
                <p class="text-xs mt-1">点击下方按钮上传文件</p>
            </div>
        `;
        return;
    }

    container.innerHTML = `
        <div class="grid grid-cols-1 gap-3">
            ${files.map(file => `
                <div class="border border-slate-200 rounded-lg p-3 hover:shadow-md transition-shadow flex items-center justify-between">
                    <div class="flex items-center gap-3">
                        <div class="w-10 h-10 rounded-lg bg-blue-50 flex items-center justify-center">
                            <svg class="w-5 h-5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/>
                            </svg>
                        </div>
                        <div>
                            <p class="text-sm font-medium text-slate-700">${escapeHtml(file.original_name)}</p>
                            <p class="text-xs text-slate-500">
                                ${file.chunk_count || 0} 个片段 | 
                                ${file.upload_date ? new Date(file.upload_date).toLocaleString('zh-CN') : '未知时间'}
                            </p>
                        </div>
                    </div>
                    <div class="flex items-center gap-1">
                        <button onclick="updateFile('${file.file_id}')" class="text-slate-400 hover:text-blue-600 p-1.5 rounded hover:bg-blue-50" title="更新文件">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/>
                            </svg>
                        </button>
                        <button onclick="deleteFile('${file.file_id}', '${escapeHtml(file.original_name)}')" class="text-slate-400 hover:text-red-600 p-1.5 rounded hover:bg-red-50" title="删除文件">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                            </svg>
                        </button>
                    </div>
                </div>
            `).join('')}
        </div>
    `;
}

function handleFMFileSelect(e) {
    const files = Array.from(e.target.files);
    uploadFiles(files);
    e.target.value = '';
    refreshFileList();
}

async function deleteFile(fileId, fileName) {
    if (!confirm(`确定要删除文件 "${fileName}" 吗？删除后无法恢复。`)) return;

    if (!state.currentKB) return;

    try {
        const data = await fetchJSON(`/api/knowledge/${state.currentKB.kb_id}/files/${fileId}`, {
            method: 'DELETE'
        });

        if (data.success) {
            showToast('success', '文件已删除');
            await refreshFileList();
            await loadKBList();
        } else {
            showToast('error', data.message || '删除失败');
        }
    } catch (e) {
        console.error('删除文件失败:', e);
        showToast('error', '删除文件失败');
    }
}

function updateFile(fileId) {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.pdf,.docx,.doc,.txt,.xlsx,.xls,.pptx';
    input.onchange = async (e) => {
        const files = Array.from(e.target.files);
        if (files.length === 0) return;

        const file = files[0];

        try {
            // Delete the old file first
            await fetchJSON(`/api/knowledge/${state.currentKB.kb_id}/files/${fileId}`, {
                method: 'DELETE'
            });

            // Then upload the new file
            await uploadSingleFile(file);
            await refreshFileList();
            await loadKBList();
            showToast('success', '文件更新成功');
        } catch (err) {
            console.error('更新文件失败:', err);
            showToast('error', '更新文件失败');
        }
    };
    input.click();
}

// ==================== File upload ====================
function handleDragOver(e) {
    e.preventDefault();
    e.currentTarget.classList.add('drag-over');
}

function handleDragLeave(e) {
    e.currentTarget.classList.remove('drag-over');
}

function handleDrop(e) {
    e.preventDefault();
    e.currentTarget.classList.remove('drag-over');
    const files = Array.from(e.dataTransfer.files);
    uploadFiles(files);
}

function handleFileSelect(e) {
    const files = Array.from(e.target.files);
    uploadFiles(files);
    e.target.value = '';
}

function toggleChunkSettings() {
    const panel = document.getElementById('chunkSettingsPanel');
    const icon = document.getElementById('chunkSettingsIcon');
    if (panel.classList.contains('hidden')) {
        panel.classList.remove('hidden');
        icon.textContent = t('trace.collapse');
    } else {
        panel.classList.add('hidden');
        icon.textContent = t('trace.expand');
    }
}

async function uploadFiles(files) {
    if (!state.currentKB) {
        showToast('warning', t('chat.no_kb'));
        return;
    }

    if (files.length === 0) return;

    const validFiles = files.filter(f => {
        const ext = '.' + f.name.split('.').pop().toLowerCase();
        const allowed = ['.pdf', '.docx', '.doc', '.txt', '.xlsx', '.xls', '.pptx'];
        if (!allowed.includes(ext)) {
            showToast('warning', t('kb.unsupported_format').replace('{name}', f.name));
            return false;
        }
        return true;
    });

    if (validFiles.length === 0) return;

    for (const file of validFiles) {
        await uploadSingleFile(file);
    }
}

async function uploadSingleFile(file) {
    const progressEl = document.getElementById('uploadProgress');
    const fileNameEl = document.getElementById('uploadFileName');
    const percentEl = document.getElementById('uploadPercent');
    const progressBar = document.getElementById('uploadProgressBar');

    progressEl.classList.remove('hidden');
    fileNameEl.textContent = file.name;
    percentEl.textContent = '0%';
    progressBar.style.width = '0%';

    try {
        const formData = new FormData();
        formData.append('file', file);

        // Read the optional custom chunking parameters
        const chunkSize = document.getElementById('chunkSizeInput')?.value;
        const chunkOverlap = document.getElementById('chunkOverlapInput')?.value;
        if (chunkSize && parseInt(chunkSize) > 0) {
            formData.append('chunk_size', chunkSize);
        }
        if (chunkOverlap && parseInt(chunkOverlap) >= 0) {
            formData.append('chunk_overlap', chunkOverlap);
        }

        // Use XMLHttpRequest so the upload progress can be tracked
        await new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();

            xhr.upload.addEventListener('progress', (e) => {
                if (e.lengthComputable) {
                    const percent = Math.round((e.loaded / e.total) * 80);
                    percentEl.textContent = percent + '%';
                    progressBar.style.width = percent + '%';
                }
            });

            xhr.addEventListener('load', () => {
                if (xhr.status === 200) {
                    const data = JSON.parse(xhr.responseText);
                    if (data.success) {
                        // Poll the task status
                        pollTaskStatus(data.task_id, () => {
                            percentEl.textContent = '100%';
                            progressBar.style.width = '100%';
                            showToast('success', t('kb.upload_done').replace('{name}', file.name));
                            setTimeout(() => {
                                progressEl.classList.add('hidden');
                                loadKBList(); // Refresh the knowledge base list
                            }, 1000);
                            resolve();
                        });
                    } else {
                        showToast('error', data.message || '上传失败');
                        reject(new Error(data.message));
                    }
                } else {
                    showToast('error', '上传失败');
                    reject(new Error('上传失败'));
                }
            });

            xhr.addEventListener('error', () => {
                showToast('error', '上传失败');
                reject(new Error('上传失败'));
            });

            xhr.open('POST', `/api/knowledge/${state.currentKB.kb_id}/upload`);
            xhr.send(formData);
        });

    } catch (e) {
        console.error('上传文件失败:', e);
        progressEl.classList.add('hidden');
    }
}

function pollTaskStatus(taskId, onComplete) {
    const poll = async () => {
        try {
            const data = await fetchJSON(`/api/monitor/task/${taskId}`);

            if (data.success && data.data) {
                const status = data.data.status;

                if (status === 'completed') {
                    onComplete();
                    return;
                } else if (status === 'failed') {
                    showToast('error', t('kb.process_failed').replace('{error}', data.data.error || t('kb.unknown_error')));
                    return;
                }

                // Keep polling
                setTimeout(poll, 1000);
            }
        } catch (e) {
            console.error('查询任务状态失败:', e);
        }
    };

    setTimeout(poll, 1000);
}
