/**
 * static/admin.html - page initialization
 *
 * Content: apply i18n on DOMContentLoaded, load the knowledge base list and bind the upload-zone
 * click and the language switch event.
 */

document.addEventListener('DOMContentLoaded', () => {
    i18n.applyI18n();
    loadKBList();

    document.querySelector('#uploadModal .border-dashed').addEventListener('click', () => {
        document.getElementById('uploadFileInput').click();
    });

    document.querySelector('#uploadModal .border-dashed').addEventListener('dragover', (e) => {
        e.preventDefault();
        e.currentTarget.classList.add('border-blue-500', 'bg-blue-50');
    });

    document.querySelector('#uploadModal .border-dashed').addEventListener('dragleave', (e) => {
        e.currentTarget.classList.remove('border-blue-500', 'bg-blue-50');
    });

    document.querySelector('#uploadModal .border-dashed').addEventListener('drop', (e) => {
        e.preventDefault();
        e.currentTarget.classList.remove('border-blue-500', 'bg-blue-50');
        if (e.dataTransfer.files.length > 0) {
            document.getElementById('uploadFileInput').files = e.dataTransfer.files;
        }
    });

    // Re-render the dynamic content when the language changes
    document.addEventListener('langChanged', () => {
        renderKBList();
        if (state.currentKB) {
            renderFileList();
            renderChunkList();
            renderVectorDetail();
        }
    });
});
