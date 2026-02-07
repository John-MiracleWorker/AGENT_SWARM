/**
 * Code Panel — File tree and code viewer with live diff highlights.
 */

class CodePanel {
    constructor() {
        this.treeEl = document.getElementById('file-tree');
        this.viewerEl = document.getElementById('code-viewer');
        this.currentFile = null;
    }

    init() {
        document.getElementById('btn-refresh-files').addEventListener('click', () => {
            this.refreshFileTree();
        });

        swarmWS.on('message', (msg) => {
            if (msg.type === 'file_update') {
                this.refreshFileTree();
                // If the updated file is currently open, refresh it
                const data = msg.data || {};
                if (data.path && data.path === this.currentFile) {
                    this.loadFile(data.path);
                }
            }
        });
    }

    async refreshFileTree() {
        try {
            const res = await fetch('/api/files');
            if (!res.ok) return;
            const data = await res.json();
            this._renderTree(data.files || []);
        } catch (e) {
            console.error('File tree refresh failed:', e);
        }
    }

    _renderTree(files, indent = 0) {
        if (indent === 0) {
            this.treeEl.innerHTML = '';
            if (files.length === 0) {
                this.treeEl.innerHTML = '<div class="tree-empty">Empty workspace</div>';
                return;
            }
        }

        files.forEach(f => {
            const item = document.createElement('div');
            item.className = 'tree-item';
            item.style.paddingLeft = `${8 + indent * 16}px`;

            const icon = f.type === 'directory' ? '📁' : this._getFileIcon(f.name);
            const size = f.type === 'file' ? ` (${this._formatSize(f.size)})` : '';

            item.innerHTML = `
                <span class="tree-icon">${icon}</span>
                <span class="tree-name">${f.name}${size}</span>
            `;

            if (f.type === 'file') {
                item.addEventListener('click', () => {
                    document.querySelectorAll('.tree-item.active').forEach(el => el.classList.remove('active'));
                    item.classList.add('active');
                    this.loadFile(f.path);
                });
            } else {
                item.addEventListener('click', async () => {
                    // Toggle open/close directory
                    const next = item.nextElementSibling;
                    if (next && next.classList.contains('tree-children')) {
                        next.remove();
                    } else {
                        try {
                            const res = await fetch(`/api/files?path=${encodeURIComponent(f.path)}`);
                            const data = await res.json();
                            const childContainer = document.createElement('div');
                            childContainer.className = 'tree-children';
                            data.files.forEach(child => {
                                const childItem = this._createTreeItem(child, indent + 1);
                                childContainer.appendChild(childItem);
                            });
                            item.after(childContainer);
                        } catch (e) {
                            console.error('Failed to load directory:', e);
                        }
                    }
                });
            }

            this.treeEl.appendChild(item);
        });
    }

    _createTreeItem(f, indent) {
        const item = document.createElement('div');
        item.className = 'tree-item';
        item.style.paddingLeft = `${8 + indent * 16}px`;

        const icon = f.type === 'directory' ? '📁' : this._getFileIcon(f.name);
        const size = f.type === 'file' ? ` (${this._formatSize(f.size)})` : '';

        item.innerHTML = `
            <span class="tree-icon">${icon}</span>
            <span class="tree-name">${f.name}${size}</span>
        `;

        if (f.type === 'file') {
            item.addEventListener('click', () => {
                document.querySelectorAll('.tree-item.active').forEach(el => el.classList.remove('active'));
                item.classList.add('active');
                this.loadFile(f.path);
            });
        }

        return item;
    }

    async loadFile(path) {
        this.currentFile = path;
        try {
            const res = await fetch(`/api/files/content?path=${encodeURIComponent(path)}`);
            if (!res.ok) throw new Error('File not found');
            const data = await res.json();
            this._renderCode(data.content, path);
        } catch (e) {
            this.viewerEl.innerHTML = `<div class="code-empty"><p>Error loading file</p></div>`;
        }
    }

    _renderCode(content, path) {
        const lines = content.split('\n');
        const lineEls = lines.map((line, i) => {
            const num = i + 1;
            return `<div class="code-line">
                <span class="code-line-number">${num}</span>
                <span class="code-line-content">${this._escapeHtml(line)}</span>
            </div>`;
        }).join('');

        this.viewerEl.innerHTML = `
            <div style="font-size: 11px; color: var(--text-muted); margin-bottom: 8px; font-family: 'JetBrains Mono', monospace;">
                ${path}
            </div>
            <div class="code-content">${lineEls}</div>
        `;
    }

    _getFileIcon(name) {
        const ext = name.split('.').pop()?.toLowerCase();
        const icons = {
            py: '🐍', js: '📜', ts: '📘', jsx: '⚛️', tsx: '⚛️',
            html: '🌐', css: '🎨', json: '📋', md: '📝',
            go: '🔵', rs: '🦀', java: '☕', rb: '💎',
            sh: '🐚', yml: '⚙️', yaml: '⚙️', toml: '⚙️',
            txt: '📄', env: '🔒', sql: '🗃️',
        };
        return icons[ext] || '📄';
    }

    _formatSize(bytes) {
        if (bytes < 1024) return `${bytes}B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
    }

    _escapeHtml(str) {
        return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
}

window.codePanel = new CodePanel();
