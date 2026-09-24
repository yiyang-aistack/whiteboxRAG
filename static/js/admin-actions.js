/**
 * static/admin.html - knowledge base actions / upload / formatting helpers
 *
 * Content: chunk selection and tab switching (selectChunk / switchTab), knowledge base creation and
 *       deletion (createKB / closeCreateKBModal / submitCreateKB / deleteKB), file upload
 *       (uploadFile / closeUploadModal / submitUpload), refresh (refreshKB) and the date / size
 *       formatting helpers (formatDate / formatSize).
 */

function selectChunk(chunk_id) {
    state.selectedChunk = null;
}

function switchTab(tabName) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => {
        el.classList.remove('text-blue-600', 'border-blue-600');
        el.classList.add('text-slate-500');
    });

    document.getElementById(`tab-${tabName}-content`).classList.add('active');
    const btn = document.querySelector(`button[onclick="switchTab('${tabName}')"]`);
    btn.classList.remove('text-slate-500');
    btn.classList.add('text-blue-600', 'border-blue-600');

    if (tabName === 'vectors') {
        renderVectorDetail();
    }
}

function createKB() {
    document.getElementById('createKBModal').classList.remove('hidden');
}

function closeCreateKBModal() {
    document.getElementById('createKBModal').classList.add('hidden');
    document.getElementById('newKBName').value = '';
    document.getElementById('newKBDesc').value = '';
}

async function submitCreateKB() {
    const name = document.getElementById('newKBName').value.trim();
    const desc = document.getElementById('newKBDesc').value.trim();

    if (!name) {
        alert(t('kb.name_required'));
        return;
    }

    try {
        const response = await fetch('/api/knowledge/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, description: desc })
        });
        const data = await response.json();

        if (data.success) {
            closeCreateKBModal();
            await loadKBList();
        } else {
            alert(data.message || t('common.failed'));
        }
    } catch (error) {
        console.error('创建知识库失败:', error);
        alert(t('common.failed'));
    }
}

async function deleteKB(kb_id) {
    if (!confirm(t('kb.delete_confirm'))) return;

    try {
        const response = await fetch(`/api/knowledge/${kb_id}`, {
            method: 'DELETE'
        });
        const data = await response.json();

        if (data.success) {
            state.currentKB = null;
            await loadKBList();
            document.getElementById('kbTitle').textContent = t('kb.select_placeholder');
            document.getElementById('kbSubtitle').textContent = t('admin.kb_subtitle');
            document.getElementById('kbStats').textContent = '';
            renderFileList();
            renderChunkList();
            state.selectedChunk = null;
            renderVectorDetail();
        }
    } catch (error) {
        console.error('删除知识库失败:', error);
        alert(t('common.failed'));
    }
}

function uploadFile() {
    document.getElementById('uploadModal').classList.remove('hidden');
}

function closeUploadModal() {
    document.getElementById('uploadModal').classList.add('hidden');
    document.getElementById('uploadFileInput').value = '';
}

async function submitUpload() {
    const input = document.getElementById('uploadFileInput');
    if (!input.files || input.files.length === 0) {
        alert(t('admin.select_file'));
        return;
    }

    if (!state.currentKB) {
        alert(t('chat.no_kb'));
        return;
    }

    const file = input.files[0];
    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch(`/api/knowledge/${state.currentKB.kb_id}/upload`, {
            method: 'POST',
            body: formData
        });
        const data = await response.json();

        if (data.success) {
            closeUploadModal();
            await loadFileList();
            await loadKBList();
            alert(t('admin.upload_success'));
        } else {
            alert(data.message || t('admin.upload_failed'));
        }
    } catch (error) {
        console.error('上传文件失败:', error);
        alert(t('admin.upload_failed'));
    }
}

function refreshKB() {
    loadKBList();
    if (state.currentKB) {
        loadFileList();
        loadChunks();
    }
}

function formatDate(dateStr) {
    if (!dateStr) return '-';
    try {
        const date = new Date(dateStr);
        return date.toLocaleString('zh-CN', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit'
        });
    } catch {
        return dateStr;
    }
}

function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}
