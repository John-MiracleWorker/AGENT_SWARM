/**
 * Code Panel — Enhanced file explorer with tree navigation,
 * file tabs, syntax highlighting, and live updates.
 */

class CodePanel {
    constructor() {
        this.treeEl = document.getElementById('file-tree');
        this.viewerEl = document.getElementById('code-viewer');
        this.currentFile = null;
        this.openTabs = [];           // [{path, name}]
        this.expandedDirs = new Set();
        this._fileTreeData = [];
    }

    init() {
        document.getElementById('btn-refresh-files').addEventListener('click', () => {
            this.refreshFileTree();
        });

        swarmWS.on('message', (msg) => {
            if (msg.type === 'file_update') {
                this.refreshFileTree();
                const data = msg.data || {};
                if (data.path && data.path === this.currentFile) {
                    this.loadFile(data.path);
                }
            }
        });

        // Auto-refresh every 8s during active mission
        setInterval(() => {
            if (window.app?.missionActive) this.refreshFileTree();
        }, 8000);
    }

    // ─── File Tree ─────────────────────────────────────────────

    async refreshFileTree() {
        try {
            const res = await fetch('/api/files');
            if (!res.ok) return;
            const data = await res.json();
            this._fileTreeData = data.files || [];
            this._renderTree(this._fileTreeData);
        } catch (e) {
            console.error('File tree refresh failed:', e);
        }
    }

    _renderTree(files) {
        this.treeEl.innerHTML = '';
        if (files.length === 0) {
            this.treeEl.innerHTML = '<div class="tree-empty">Empty workspace</div>';
            return;
        }

        // Sort: dirs first, then files, alphabetical
        const sorted = [...files].sort((a, b) => {
            if (a.type !== b.type) return a.type === 'directory' ? -1 : 1;
            return a.name.localeCompare(b.name);
        });

        sorted.forEach(f => {
            const item = this._createTreeItem(f, 0);
            this.treeEl.appendChild(item);

            // Restore expanded state
            if (f.type === 'directory' && this.expandedDirs.has(f.path)) {
                this._loadDirChildren(item, f.path, 1);
            }
        });
    }

    _createTreeItem(f, indent) {
        const item = document.createElement('div');
        item.className = 'tree-item';
        item.style.paddingLeft = `${8 + indent * 16}px`;
        item.dataset.path = f.path;
        item.dataset.type = f.type;

        const isExpanded = this.expandedDirs.has(f.path);

        if (f.type === 'directory') {
            const arrow = isExpanded ? '▾' : '▸';
            item.innerHTML = `
                <span class="tree-arrow">${arrow}</span>
                <span class="tree-icon">📁</span>
                <span class="tree-name">${f.name}</span>
            `;
            item.addEventListener('click', () => this._toggleDir(item, f.path, indent));
        } else {
            const icon = this._getFileIcon(f.name);
            const size = this._formatSize(f.size);
            item.innerHTML = `
                <span class="tree-arrow-spacer"></span>
                <span class="tree-icon">${icon}</span>
                <span class="tree-name">${f.name}</span>
                <span class="tree-size">${size}</span>
            `;
            item.addEventListener('click', () => {
                document.querySelectorAll('.tree-item.active').forEach(el => el.classList.remove('active'));
                item.classList.add('active');
                this.openFile(f.path, f.name);
            });

            // Highlight active file
            if (f.path === this.currentFile) {
                item.classList.add('active');
            }
        }

        return item;
    }

    async _toggleDir(item, dirPath, indent) {
        if (this.expandedDirs.has(dirPath)) {
            // Collapse
            this.expandedDirs.delete(dirPath);
            item.querySelector('.tree-arrow').textContent = '▸';
            const next = item.nextElementSibling;
            if (next && next.classList.contains('tree-children')) {
                next.remove();
            }
        } else {
            // Expand
            this.expandedDirs.add(dirPath);
            item.querySelector('.tree-arrow').textContent = '▾';
            await this._loadDirChildren(item, dirPath, indent + 1);
        }
    }

    async _loadDirChildren(item, dirPath, indent) {
        try {
            const res = await fetch(`/api/files?path=${encodeURIComponent(dirPath)}`);
            const data = await res.json();
            const childContainer = document.createElement('div');
            childContainer.className = 'tree-children';

            const sorted = [...(data.files || [])].sort((a, b) => {
                if (a.type !== b.type) return a.type === 'directory' ? -1 : 1;
                return a.name.localeCompare(b.name);
            });

            sorted.forEach(child => {
                const childItem = this._createTreeItem(child, indent);
                childContainer.appendChild(childItem);

                // Recursively expand any children that were expanded before
                if (child.type === 'directory' && this.expandedDirs.has(child.path)) {
                    this._loadDirChildren(childItem, child.path, indent + 1);
                }
            });

            // Remove old children if any
            const next = item.nextElementSibling;
            if (next && next.classList.contains('tree-children')) {
                next.remove();
            }
            item.after(childContainer);
        } catch (e) {
            console.error('Failed to load directory:', e);
        }
    }

    // ─── File Tabs ─────────────────────────────────────────────

    openFile(path, name) {
        // Add tab if not already open
        if (!this.openTabs.find(t => t.path === path)) {
            this.openTabs.push({ path, name: name || path.split('/').pop() });
        }
        this.currentFile = path;
        this._renderTabs();
        this.loadFile(path);
    }

    closeTab(path, event) {
        if (event) event.stopPropagation();
        this.openTabs = this.openTabs.filter(t => t.path !== path);

        if (this.currentFile === path) {
            if (this.openTabs.length > 0) {
                const last = this.openTabs[this.openTabs.length - 1];
                this.currentFile = last.path;
                this.loadFile(last.path);
            } else {
                this.currentFile = null;
                this.viewerEl.innerHTML = `<div class="code-empty"><p>Select a file to view</p></div>`;
            }
        }

        this._renderTabs();
    }

    _renderTabs() {
        let tabBar = this.viewerEl.parentElement.querySelector('.file-tab-bar');
        if (!tabBar) {
            tabBar = document.createElement('div');
            tabBar.className = 'file-tab-bar';
            this.viewerEl.parentElement.insertBefore(tabBar, this.viewerEl);
        }

        if (this.openTabs.length === 0) {
            tabBar.style.display = 'none';
            return;
        }

        tabBar.style.display = 'flex';
        tabBar.innerHTML = this.openTabs.map(tab => {
            const active = tab.path === this.currentFile ? 'active' : '';
            const icon = this._getFileIcon(tab.name);
            return `
                <div class="file-tab ${active}" data-path="${tab.path}" onclick="codePanel.openFile('${tab.path.replace(/'/g, "\\'")}', '${tab.name.replace(/'/g, "\\'")}')">
                    <span class="file-tab-icon">${icon}</span>
                    <span class="file-tab-name">${tab.name}</span>
                    <span class="file-tab-close" onclick="codePanel.closeTab('${tab.path.replace(/'/g, "\\'")}', event)">×</span>
                </div>
            `;
        }).join('');
    }

    // ─── File Viewer ───────────────────────────────────────────

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
        const ext = path.split('.').pop()?.toLowerCase() || '';
        const lang = this._getLang(ext);
        const lines = content.split('\n');

        const lineEls = lines.map((line, i) => {
            const num = i + 1;
            const highlighted = this._highlightSyntax(this._escapeHtml(line), lang);
            return `<div class="code-line">
                <span class="code-line-number">${num}</span>
                <span class="code-line-content">${highlighted}</span>
            </div>`;
        }).join('');

        const fileName = path.split('/').pop();
        const icon = this._getFileIcon(fileName);
        const lineCount = lines.length;
        const byteCount = new Blob([content]).size;

        this.viewerEl.innerHTML = `
            <div class="code-header">
                <span class="code-header-path">${icon} ${path}</span>
                <span class="code-header-meta">${lineCount} lines · ${this._formatSize(byteCount)} · ${lang || 'text'}</span>
            </div>
            <div class="code-content">${lineEls}</div>
        `;
    }

    // ─── Lightweight Syntax Highlighting ───────────────────────

    _highlightSyntax(escaped, lang) {
        if (!lang) return escaped;

        // Comments
        if (lang === 'python' || lang === 'shell') {
            escaped = escaped.replace(/(#.*)$/, '<span class="syn-comment">$1</span>');
        }
        if (['javascript', 'typescript', 'java', 'go', 'rust', 'css'].includes(lang)) {
            escaped = escaped.replace(/(\/\/.*)$/, '<span class="syn-comment">$1</span>');
        }
        if (lang === 'html') {
            escaped = escaped.replace(/(&lt;!--.+?--&gt;)/g, '<span class="syn-comment">$1</span>');
        }

        // Strings (double-quoted and single-quoted)
        escaped = escaped.replace(/(&quot;[^&]*?&quot;|&#39;[^&]*?&#39;|"[^"]*?"|'[^']*?'|`[^`]*?`)/g,
            '<span class="syn-string">$1</span>');

        // Keywords
        const kwMap = {
            python: /\b(def|class|import|from|return|if|elif|else|for|while|try|except|finally|with|as|yield|async|await|True|False|None|self|raise|pass|break|continue|lambda|in|not|and|or|is)\b/g,
            javascript: /\b(const|let|var|function|return|if|else|for|while|class|new|this|import|export|from|default|async|await|try|catch|throw|true|false|null|undefined|typeof|instanceof|switch|case|break|continue|yield)\b/g,
            typescript: /\b(const|let|var|function|return|if|else|for|while|class|new|this|import|export|from|default|async|await|try|catch|throw|true|false|null|undefined|typeof|instanceof|interface|type|enum|implements|extends|readonly|private|public|protected)\b/g,
            html: /\b(DOCTYPE|html|head|body|div|span|script|style|link|meta|title|class|id|src|href|rel|type)\b/g,
            css: /\b(margin|padding|border|color|background|font|display|flex|grid|position|top|left|right|bottom|width|height|overflow|transition|transform|opacity|z-index|var|calc|rgba|hsla|none|auto|inherit|solid|important)\b/g,
            json: /\b(true|false|null)\b/g,
            shell: /\b(if|then|else|fi|for|do|done|while|case|esac|function|return|exit|echo|export|source|cd|ls|rm|cp|mv|mkdir|chmod|chown|grep|sed|awk|cat|head|tail|find|xargs|sudo|apt|pip|npm)\b/g,
            go: /\b(func|package|import|return|if|else|for|range|switch|case|break|go|chan|select|defer|type|struct|interface|map|var|const|true|false|nil|make|new|len|append|error)\b/g,
            rust: /\b(fn|let|mut|const|struct|enum|impl|trait|use|mod|pub|return|if|else|for|while|loop|match|self|Self|true|false|None|Some|Ok|Err|where|type|async|await|move|unsafe)\b/g,
        };

        const kw = kwMap[lang];
        if (kw) {
            // Only highlight keywords that aren't inside strings/comments
            escaped = escaped.replace(kw, (match) => {
                return `<span class="syn-keyword">${match}</span>`;
            });
        }

        // Numbers
        escaped = escaped.replace(/\b(\d+\.?\d*)\b/g, '<span class="syn-number">$1</span>');

        // HTML tags
        if (lang === 'html') {
            escaped = escaped.replace(/(&lt;\/?)([\w-]+)/g, '$1<span class="syn-tag">$2</span>');
            escaped = escaped.replace(/([\w-]+)(=)/g, '<span class="syn-attr">$1</span>$2');
        }

        // Function calls
        if (['python', 'javascript', 'typescript', 'go', 'rust'].includes(lang)) {
            escaped = escaped.replace(/\b([\w]+)\(/g, '<span class="syn-func">$1</span>(');
        }

        return escaped;
    }

    // ─── Utilities ─────────────────────────────────────────────

    _getLang(ext) {
        const map = {
            py: 'python', js: 'javascript', ts: 'typescript', jsx: 'javascript', tsx: 'typescript',
            html: 'html', htm: 'html', css: 'css', json: 'json', md: 'markdown',
            go: 'go', rs: 'rust', java: 'java', rb: 'ruby',
            sh: 'shell', bash: 'shell', zsh: 'shell',
            yml: 'yaml', yaml: 'yaml', toml: 'toml',
            sql: 'sql', txt: 'text', env: 'shell',
        };
        return map[ext] || '';
    }

    _getFileIcon(name) {
        const ext = name.split('.').pop()?.toLowerCase();
        const icons = {
            py: '🐍', js: '📜', ts: '📘', jsx: '⚛️', tsx: '⚛️',
            html: '🌐', css: '🎨', json: '📋', md: '📝',
            go: '🔵', rs: '🦀', java: '☕', rb: '💎',
            sh: '🐚', yml: '⚙️', yaml: '⚙️', toml: '⚙️',
            txt: '📄', env: '🔒', sql: '🗃️',
            png: '🖼️', jpg: '🖼️', gif: '🖼️', svg: '🖼️',
            gitignore: '🙈',
        };
        return icons[ext] || '📄';
    }

    _formatSize(bytes) {
        if (!bytes && bytes !== 0) return '';
        if (bytes < 1024) return `${bytes}B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
    }

    _escapeHtml(str) {
        return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
}

window.codePanel = new CodePanel();
