/**
 * Diff Panel — File diff viewer with syntax-highlighted unified diff display.
 * Integrates with the code panel to show Git diffs for files and commits.
 */

class DiffPanel {
    constructor() {
        this.isShowingDiff = false;
    }

    init() {
        this._injectDiffToggle();
    }

    _injectDiffToggle() {
        const codeHeader = document.querySelector('.panel-header .tab-group');
        if (!codeHeader) return;

        const diffTab = document.createElement('button');
        diffTab.className = 'tab-btn';
        diffTab.id = 'diff-tab-btn';
        diffTab.textContent = '📊 Diff';
        diffTab.addEventListener('click', () => this.toggleDiffView());
        codeHeader.appendChild(diffTab);
    }

    async toggleDiffView() {
        const btn = document.getElementById('diff-tab-btn');
        const codeContent = document.getElementById('code-content') || document.querySelector('.code-content') || document.getElementById('code-viewer');

        if (this.isShowingDiff) {
            // Switch back to normal code view
            this.isShowingDiff = false;
            if (btn) btn.classList.remove('active');
            // Restore code view via existing panel
            if (window.codePanel) window.codePanel.refresh?.();
            return;
        }

        this.isShowingDiff = true;
        if (btn) btn.classList.add('active');

        // Fetch diff
        try {
            const resp = await fetch('/api/git/diff');
            const data = resp.ok ? await resp.json() : {};
            const diff = data.diff || 'No changes detected';

            if (codeContent) {
                codeContent.innerHTML = `
                    <div class="diff-viewer">
                        <div class="diff-header">
                            <span>📊 Uncommitted Changes</span>
                            <button class="diff-refresh" onclick="window.diffPanel.toggleDiffView(); window.diffPanel.toggleDiffView();">🔄</button>
                        </div>
                        <pre class="diff-content">${this._renderDiff(diff)}</pre>
                    </div>
                `;
            }
        } catch (e) {
            if (codeContent) {
                codeContent.innerHTML = '<div class="diff-viewer"><p class="error">Failed to load diff</p></div>';
            }
        }
    }

    async showCommitDiff(sha) {
        const codeContent = document.getElementById('code-content') || document.querySelector('.code-content') || document.getElementById('code-viewer');
        if (!codeContent) return;

        try {
            const resp = await fetch(`/api/git/diff?sha=${sha}`);
            const data = resp.ok ? await resp.json() : {};
            const diff = data.diff || 'No diff available';

            codeContent.innerHTML = `
                <div class="diff-viewer">
                    <div class="diff-header">
                        <span>📊 Commit: ${sha.substring(0, 8)}</span>
                        <button class="diff-close" onclick="window.diffPanel.isShowingDiff = false; window.codePanel?.refresh?.();">✕</button>
                    </div>
                    <pre class="diff-content">${this._renderDiff(diff)}</pre>
                </div>
            `;
            this.isShowingDiff = true;
        } catch (e) {
            console.error('Failed to load commit diff:', e);
        }
    }

    _renderDiff(diffText) {
        return diffText.split('\n').map(line => {
            const escaped = this._escapeHtml(line);
            if (line.startsWith('+') && !line.startsWith('+++')) {
                return `<span class="diff-add">${escaped}</span>`;
            } else if (line.startsWith('-') && !line.startsWith('---')) {
                return `<span class="diff-del">${escaped}</span>`;
            } else if (line.startsWith('@@')) {
                return `<span class="diff-hunk">${escaped}</span>`;
            } else if (line.startsWith('diff ') || line.startsWith('index ')) {
                return `<span class="diff-meta">${escaped}</span>`;
            }
            return escaped;
        }).join('\n');
    }

    _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }
}

window.diffPanel = new DiffPanel();
