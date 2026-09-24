/**
 * static/admin.html - file management
 *
 * Content: file list loading and rendering (loadFileList / renderFileList), source preview
 *       (viewRawDoc / closeRawDocModal) and file deletion (deleteFile).
 */

async function loadFileList() {
    if (!state.currentKB) return;

    try {
        const response = await fetch(`/api/knowledge/${state.currentKB.kb_id}/files`);
        const data = await response.json();
        state.fileList = data.data || [];
        renderFileList();
    } catch (error) {
        console.error('加载文件列表失败:', error);
    }
}

function renderFileList() {
    const container = document.getElementById('fileList');
    if (!state.currentKB) {
        container.innerHTML = `<div class="text-center text-slate-400 py-12">${t('chat.no_kb')}</div>`;
        return;
    }

    if (state.fileList.length === 0) {
        container.innerHTML = `<div class="text-center text-slate-400 py-12">${t('admin.no_files')}</div>`;
        return;
    }

    container.innerHTML = state.fileList.map(file => `
        <div class="flex items-center justify-between p-4 bg-slate-50 rounded-lg">
            <div class="flex items-center space-x-3">
                <div class="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
                    <svg class="w-5 h-5 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z"/>
                    </svg>
                </div>
                <div>
                    <h4 class="font-medium text-slate-700">${file.original_name}</h4>
                    <div class="flex items-center space-x-3 text-xs text-slate-500">
                        <span>${file.chunk_count} ${i18n.t('common.chunks')}</span>
                        <span>${formatDate(file.upload_date)}</span>
                    </div>
                </div>
            </div>
            <div class="flex items-center space-x-2">
                <button onclick="viewRawDoc('${file.file_id}')" class="text-blue-600 hover:text-blue-700 text-sm font-medium">
                    ${t('admin.file_preview')}
                </button>
                <button onclick="deleteFile('${file.file_id}')" class="text-red-500 hover:text-red-600 text-sm font-medium">
                    ${t('common.delete')}
                </button>
            </div>
        </div>
    `).join('');
}

async function viewRawDoc(file_id) {
    if (!state.currentKB) return;

    try {
        const response = await fetch(`/api/knowledge/${state.currentKB.kb_id}/raw/${file_id}`);
        const data = await response.json();

        document.getElementById('rawDocTitle').textContent = data.data.original_name;
        document.getElementById('rawDocMeta').textContent = `文件大小: ${formatSize(data.data.size)} | 编码: ${data.data.encoding}`;
        document.getElementById('rawDocContent').textContent = data.data.content;

        document.getElementById('rawDocModal').classList.remove('hidden');
    } catch (error) {
        console.error('查看原始文档失败:', error);
        alert(t('common.failed'));
    }
}

function closeRawDocModal() {
    document.getElementById('rawDocModal').classList.add('hidden');
}

async function deleteFile(file_id) {
    if (!state.currentKB) return;
    if (!confirm(t('admin.delete_confirm'))) return;

    try {
        const response = await fetch(`/api/knowledge/${state.currentKB.kb_id}/files/${file_id}`, {
            method: 'DELETE'
        });
        const data = await response.json();

        if (data.success) {
            await loadFileList();
            await loadChunks();
            await loadKBList();
        }
    } catch (error) {
        console.error('删除文件失败:', error);
        alert(t('common.failed'));
    }
}
