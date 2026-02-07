/**
 * Folder Picker — Modal directory browser for selecting workspace folder.
 */

class FolderPicker {
    constructor() {
        this.modal = document.getElementById('folder-picker-modal');
        this.pathEl = document.getElementById('modal-current-path');
        this.listEl = document.getElementById('folder-list');
        this.currentPath = '';
        this.selectedPath = '';
        this.onSelect = null;
    }

    init() {
        document.getElementById('btn-folder-picker').addEventListener('click', () => {
            this.open();
        });

        document.getElementById('modal-close').addEventListener('click', () => {
            this.close();
        });

        document.getElementById('btn-select-folder').addEventListener('click', () => {
            if (this.currentPath) {
                this.selectedPath = this.currentPath;
                if (this.onSelect) this.onSelect(this.selectedPath);
                this.close();
            }
        });

        // Close on overlay click
        this.modal.addEventListener('click', (e) => {
            if (e.target === this.modal) this.close();
        });

        // Escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !this.modal.classList.contains('hidden')) {
                this.close();
            }
        });
    }

    open(startPath = '') {
        this.modal.classList.remove('hidden');
        this.browse(startPath || this.currentPath || '');
    }

    close() {
        this.modal.classList.add('hidden');
    }

    async browse(path) {
        this.listEl.innerHTML = '<div class="loading-spinner"></div>';

        try {
            const url = path ? `/api/browse?path=${encodeURIComponent(path)}` : '/api/browse';
            const res = await fetch(url);
            if (!res.ok) throw new Error('Browse failed');
            const data = await res.json();

            this.currentPath = data.current;
            this.pathEl.textContent = data.current;
            this.listEl.innerHTML = '';

            // Parent directory link
            if (data.parent) {
                const parentItem = document.createElement('div');
                parentItem.className = 'folder-item';
                parentItem.innerHTML = `<span class="folder-icon">⬆️</span> <span>..</span>`;
                parentItem.addEventListener('click', () => this.browse(data.parent));
                this.listEl.appendChild(parentItem);
            }

            // Directories first, then files
            const dirs = data.entries.filter(e => e.type === 'directory');
            const files = data.entries.filter(e => e.type === 'file');

            dirs.forEach(entry => {
                const item = document.createElement('div');
                item.className = 'folder-item';
                item.innerHTML = `<span class="folder-icon">📁</span> <span>${entry.name}</span>`;
                item.addEventListener('click', () => this.browse(entry.path));
                this.listEl.appendChild(item);
            });

            files.forEach(entry => {
                const item = document.createElement('div');
                item.className = 'folder-item';
                item.style.opacity = '0.5';
                item.innerHTML = `<span class="folder-icon">📄</span> <span>${entry.name}</span>`;
                this.listEl.appendChild(item);
            });

            if (data.entries.length === 0) {
                const empty = document.createElement('div');
                empty.className = 'folder-item';
                empty.style.color = 'var(--text-muted)';
                empty.textContent = 'Empty directory';
                this.listEl.appendChild(empty);
            }

        } catch (e) {
            this.listEl.innerHTML = `<div class="folder-item" style="color: var(--danger)">Error: ${e.message}</div>`;
        }
    }
}

window.folderPicker = new FolderPicker();
