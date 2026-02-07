/**
 * Features Panel — Integrated UI for Budget, Mission History, Checkpoints, Memory, Plugins, Workspaces.
 * Adds sidebar controls and modal dialogs for all 10 agent swarm features.
 */

class FeaturesPanel {
    constructor() {
        this.budgetPolling = null;
    }

    init() {
        this._injectBudgetBar();
        this._injectFeatureButtons();
        this._injectModals();
        this._startBudgetPolling();
        this._setupThoughtFeed();
        this._setupDebateViz();
    }

    // ── Budget Bar ──────────────────────────────────────────

    _injectBudgetBar() {
        const bar = document.getElementById('mission-controls');
        if (!bar) return;

        const budgetEl = document.createElement('div');
        budgetEl.id = 'budget-display';
        budgetEl.className = 'budget-display';
        budgetEl.innerHTML = `
            <div class="budget-inner">
                <span class="budget-icon">💰</span>
                <div class="budget-bar-wrap">
                    <div class="budget-bar-fill" id="budget-fill" style="width: 0%"></div>
                </div>
                <span class="budget-text" id="budget-text">$0.00 / $1.00</span>
                <button class="budget-settings-btn" onclick="window.features.showBudgetSettings()" title="Budget Settings">⚙️</button>
            </div>
        `;
        bar.appendChild(budgetEl);
    }

    _startBudgetPolling() {
        this.budgetPolling = setInterval(async () => {
            try {
                const resp = await fetch('/api/budget');
                if (!resp.ok) return;
                const data = await resp.json();
                this._updateBudgetUI(data);
            } catch (e) { /* quiet */ }
        }, 3000);
    }

    _updateBudgetUI(data) {
        const fill = document.getElementById('budget-fill');
        const text = document.getElementById('budget-text');
        if (!fill || !text) return;

        const pct = data.percentage || 0;
        const spent = data.spent || 0;
        const limit = data.limit || 1;

        fill.style.width = `${Math.min(pct, 100)}%`;
        text.textContent = `$${spent.toFixed(4)} / $${limit.toFixed(2)}`;

        // Color coding
        if (pct >= 90) {
            fill.style.background = 'var(--danger)';
        } else if (pct >= 70) {
            fill.style.background = 'var(--warning)';
        } else {
            fill.style.background = 'var(--success)';
        }
    }

    async showBudgetSettings() {
        const resp = await fetch('/api/budget');
        const data = resp.ok ? await resp.json() : { limit: 1.0 };

        const limit = prompt(`Set budget limit (USD).\nCurrent: $${data.limit?.toFixed(2) || '1.00'}\nSpent: $${data.spent?.toFixed(4) || '0.00'}\n\nEnter new limit (0 = unlimited):`, data.limit || 1);
        if (limit !== null && !isNaN(parseFloat(limit))) {
            await fetch(`/api/budget?limit_usd=${parseFloat(limit)}`, { method: 'POST' });
        }
    }

    // ── Feature Buttons in Navbar ──────────────────────────

    _injectFeatureButtons() {
        const bar = document.getElementById('mission-controls');
        if (!bar) return;

        const btnGroup = document.createElement('div');
        btnGroup.className = 'feature-buttons';
        btnGroup.innerHTML = `
            <button class="feature-btn" onclick="window.features.showHistory()" title="Mission History">📜</button>
            <button class="feature-btn" onclick="window.features.showMemory()" title="Agent Memory">🧠</button>
            <button class="feature-btn" onclick="window.features.showCheckpoints()" title="Safety Checkpoints">🚧</button>
            <button class="feature-btn" onclick="window.features.showPlugins()" title="Tools & Plugins">🔧</button>
            <button class="feature-btn" onclick="window.features.showWorkspaces()" title="Workspaces">📁</button>
        `;
        bar.appendChild(btnGroup);
    }

    // ── Modals ──────────────────────────────────────────────

    _injectModals() {
        const overlay = document.createElement('div');
        overlay.id = 'feature-modal-overlay';
        overlay.className = 'modal-overlay hidden';
        overlay.innerHTML = `
            <div class="modal-dialog" id="feature-modal">
                <div class="modal-header">
                    <h3 id="modal-title"></h3>
                    <button class="modal-close" onclick="window.features.closeModal()">✕</button>
                </div>
                <div class="modal-body" id="modal-body"></div>
            </div>
        `;
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) this.closeModal();
        });
        document.body.appendChild(overlay);
    }

    openModal(title, bodyHtml) {
        document.getElementById('modal-title').textContent = title;
        document.getElementById('modal-body').innerHTML = bodyHtml;
        document.getElementById('feature-modal-overlay').classList.remove('hidden');
    }

    closeModal() {
        document.getElementById('feature-modal-overlay').classList.add('hidden');
    }

    // ── Mission History ────────────────────────────────────

    async showHistory() {
        this.openModal('📜 Mission History', '<div class="loading">Loading...</div>');
        try {
            const resp = await fetch('/api/missions/history');
            const missions = resp.ok ? await resp.json() : [];

            if (!missions.length) {
                document.getElementById('modal-body').innerHTML = `
                    <div class="empty-state">
                        <span style="font-size:2rem">📭</span>
                        <p>No completed missions yet</p>
                    </div>
                `;
                return;
            }

            const html = missions.map(m => `
                <div class="history-card" onclick="window.features.showMissionDetail('${m.mission_id}')">
                    <div class="history-header">
                        <span class="history-status ${m.status}">${m.status === 'completed' ? '✅' : '❌'}</span>
                        <span class="history-goal">${this._escapeHtml(m.goal || 'Unknown goal')}</span>
                    </div>
                    <div class="history-meta">
                        <span>💰 $${(m.cost_usd || 0).toFixed(4)}</span>
                        <span>⏱ ${this._formatDuration(m.duration_seconds || 0)}</span>
                        <span>🤖 ${(m.agents || []).length} agents</span>
                        <span>📅 ${new Date(m.timestamp || 0).toLocaleDateString()}</span>
                    </div>
                </div>
            `).join('');

            document.getElementById('modal-body').innerHTML = `<div class="history-list">${html}</div>`;
        } catch (e) {
            document.getElementById('modal-body').innerHTML = `<div class="error">Failed to load history</div>`;
        }
    }

    async showMissionDetail(missionId) {
        try {
            const resp = await fetch(`/api/missions/history/${missionId}`);
            const m = resp.ok ? await resp.json() : null;
            if (!m) return;

            const tasksHtml = (m.tasks || []).map(t => `
                <div class="task-item ${t.status || ''}">${t.status === 'done' ? '✅' : '⬜'} ${this._escapeHtml(t.title || '')}</div>
            `).join('');

            this.openModal('Mission Detail', `
                <h4>${this._escapeHtml(m.goal || '')}</h4>
                <div class="detail-meta">
                    <div>💰 Cost: $${(m.cost_usd || 0).toFixed(4)}</div>
                    <div>⏱ Duration: ${this._formatDuration(m.duration_seconds || 0)}</div>
                    <div>📁 Workspace: ${this._escapeHtml(m.workspace_path || '')}</div>
                    <div>🤖 Agents: ${(m.agents || []).join(', ')}</div>
                </div>
                <h5>Tasks</h5>
                <div class="task-list">${tasksHtml || '<p>No tasks recorded</p>'}</div>
            `);
        } catch (e) { /* quiet */ }
    }

    // ── Agent Memory ───────────────────────────────────────

    async showMemory() {
        this.openModal('🧠 Agent Memory', '<div class="loading">Loading...</div>');
        try {
            const resp = await fetch('/api/memory');
            const memories = resp.ok ? await resp.json() : [];

            if (!memories.length) {
                document.getElementById('modal-body').innerHTML = `
                    <div class="empty-state">
                        <span style="font-size:2rem">🧠</span>
                        <p>No learned lessons yet. Agents learn from completed missions.</p>
                    </div>
                `;
                return;
            }

            const html = memories.map(m => `
                <div class="memory-card">
                    <div class="memory-header">
                        <span class="memory-type ${m.lesson_type || 'pattern'}">${m.lesson_type || 'pattern'}</span>
                        <span class="memory-agent">${m.agent_role || 'unknown'}</span>
                        <button class="memory-delete" onclick="window.features.deleteMemory('${m.id}')" title="Delete">🗑</button>
                    </div>
                    <div class="memory-lesson">${this._escapeHtml(m.lesson || '')}</div>
                    ${m.context ? `<div class="memory-context">${this._escapeHtml(m.context)}</div>` : ''}
                </div>
            `).join('');

            document.getElementById('modal-body').innerHTML = `<div class="memory-list">${html}</div>`;
        } catch (e) {
            document.getElementById('modal-body').innerHTML = `<div class="error">Failed to load memory</div>`;
        }
    }

    async deleteMemory(memoryId) {
        await fetch(`/api/memory/${memoryId}`, { method: 'DELETE' });
        this.showMemory();
    }

    // ── Checkpoints ────────────────────────────────────────

    async showCheckpoints() {
        this.openModal('🚧 Safety Checkpoints', '<div class="loading">Loading...</div>');
        try {
            const resp = await fetch('/api/checkpoints');
            const rules = resp.ok ? await resp.json() : [];

            const html = rules.map(r => `
                <div class="checkpoint-card">
                    <div class="checkpoint-header">
                        <span class="checkpoint-trigger ${r.trigger || ''}">${r.trigger || 'custom'}</span>
                        <span class="checkpoint-label">${this._escapeHtml(r.label || '')}</span>
                        ${!r.default ? `<button class="checkpoint-delete" onclick="window.features.deleteCheckpoint('${r.id}')" title="Remove">✕</button>` : ''}
                    </div>
                    <div class="checkpoint-action">Action: <strong>${r.action || 'pause'}</strong></div>
                </div>
            `).join('');

            document.getElementById('modal-body').innerHTML = `
                <div class="checkpoint-list">${html}</div>
                <div class="checkpoint-add">
                    <h4>Add Custom Rule</h4>
                    <input id="cp-pattern" placeholder="Regex pattern (e.g., DROP.*TABLE)" />
                    <select id="cp-action"><option value="pause">Pause</option><option value="confirm">Confirm</option></select>
                    <button class="btn-primary" onclick="window.features.addCheckpoint()">Add Rule</button>
                </div>
            `;
        } catch (e) {
            document.getElementById('modal-body').innerHTML = `<div class="error">Failed to load checkpoints</div>`;
        }
    }

    async addCheckpoint() {
        const pattern = document.getElementById('cp-pattern')?.value;
        const action = document.getElementById('cp-action')?.value;
        if (!pattern) return;
        await fetch(`/api/checkpoints?trigger=custom&pattern=${encodeURIComponent(pattern)}&action=${action}`, { method: 'POST' });
        this.showCheckpoints();
    }

    async deleteCheckpoint(ruleId) {
        await fetch(`/api/checkpoints/${ruleId}`, { method: 'DELETE' });
        this.showCheckpoints();
    }

    // ── Plugins ────────────────────────────────────────────

    async showPlugins() {
        this.openModal('🔧 Tools & Plugins', '<div class="loading">Loading...</div>');
        try {
            const resp = await fetch('/api/plugins');
            const tools = resp.ok ? await resp.json() : [];

            const html = tools.map(t => `
                <div class="plugin-card">
                    <div class="plugin-header">
                        <span class="plugin-name">${this._escapeHtml(t.name || '')}</span>
                        <span class="plugin-enabled ${t.enabled ? 'on' : 'off'}">${t.enabled ? '✅' : '❌'}</span>
                    </div>
                    <div class="plugin-desc">${this._escapeHtml(t.description || '')}</div>
                    <div class="plugin-cmd"><code>${this._escapeHtml(t.command || '')}</code></div>
                </div>
            `).join('');

            document.getElementById('modal-body').innerHTML = `
                <p style="opacity:0.7;margin-bottom:12px">Agents can invoke these tools via the <code>use_tool</code> action.</p>
                <div class="plugin-list">${html}</div>
            `;
        } catch (e) {
            document.getElementById('modal-body').innerHTML = `<div class="error">Failed to load plugins</div>`;
        }
    }

    // ── Workspaces ─────────────────────────────────────────

    async showWorkspaces() {
        this.openModal('📁 Workspaces', '<div class="loading">Loading...</div>');
        try {
            const resp = await fetch('/api/workspaces');
            const workspaces = resp.ok ? await resp.json() : [];

            const html = workspaces.map(ws => `
                <div class="workspace-card ${ws.active ? 'active' : ''}" onclick="window.features.switchWorkspace('${ws.id}')">
                    <span class="workspace-indicator">${ws.active ? '🟢' : '⚪'}</span>
                    <div class="workspace-info">
                        <span class="workspace-name">${this._escapeHtml(ws.name || '')}</span>
                        <span class="workspace-path">${this._escapeHtml(ws.path || '')}</span>
                    </div>
                </div>
            `).join('');

            document.getElementById('modal-body').innerHTML = `
                <div class="workspace-list">${html || '<div class="empty-state"><p>No workspaces registered</p></div>'}</div>
                <div class="workspace-add">
                    <input id="ws-path" placeholder="/path/to/workspace" />
                    <input id="ws-name" placeholder="Name (optional)" />
                    <button class="btn-primary" onclick="window.features.addWorkspace()">Add Workspace</button>
                </div>
            `;
        } catch (e) {
            document.getElementById('modal-body').innerHTML = `<div class="error">Failed to load workspaces</div>`;
        }
    }

    async addWorkspace() {
        const path = document.getElementById('ws-path')?.value;
        const name = document.getElementById('ws-name')?.value || '';
        if (!path) return;
        await fetch(`/api/workspaces?path=${encodeURIComponent(path)}&name=${encodeURIComponent(name)}`, { method: 'POST' });
        this.showWorkspaces();
    }

    async switchWorkspace(wsId) {
        await fetch(`/api/workspaces/${wsId}/activate`, { method: 'POST' });
        this.showWorkspaces();
    }

    // ── Live Reasoning Feed (Thought Chains) ───────────────

    _setupThoughtFeed() {
        if (!window.swarmWS) return;
        window.swarmWS.on('message', (msg) => {
            if (msg.type !== 'thought') return;
            this._addThoughtBubble(msg);
        });
    }

    _addThoughtBubble(msg) {
        const container = document.getElementById('chat-messages');
        if (!container) return;

        const sender = msg.sender || 'system';
        const senderRole = msg.sender_role || sender;
        const content = msg.content || '';
        const color = { orchestrator: '#FFD700', developer: '#00E5FF', reviewer: '#AA00FF', tester: '#00E676' }[sender] || '#888';
        const emoji = { orchestrator: '🎯', developer: '💻', reviewer: '🔍', tester: '🧪' }[sender] || '🤖';
        const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

        const el = document.createElement('div');
        el.className = 'chat-msg thought-bubble';
        el.dataset.type = 'thought';
        el.innerHTML = `
            <div class="msg-avatar" style="border-color: ${color}">${emoji}</div>
            <div class="msg-body">
                <div class="msg-header">
                    <span class="msg-sender" style="color: ${color}">${senderRole}</span>
                    <span class="msg-type-badge thought-badge">💭 THINKING</span>
                    <span class="msg-time">${time}</span>
                    <button class="thought-toggle" onclick="this.closest('.thought-bubble').classList.toggle('collapsed')">▼</button>
                </div>
                <div class="msg-content thought-content">${this._escapeHtml(content)}</div>
            </div>
        `;

        container.appendChild(el);
        container.scrollTop = container.scrollHeight;
    }

    // ── Debate Visualization ───────────────────────────────

    _setupDebateViz() {
        if (!window.swarmWS) return;
        window.swarmWS.on('message', (msg) => {
            if (msg.type !== 'debate') return;
            this._addDebateCard(msg);
        });
    }

    _addDebateCard(msg) {
        const container = document.getElementById('chat-messages');
        if (!container) return;

        const sender = msg.sender || 'system';
        const senderRole = msg.sender_role || sender;
        const content = msg.content || '';
        const data = msg.data || {};
        const color = { orchestrator: '#FFD700', developer: '#00E5FF', reviewer: '#AA00FF', tester: '#00E676' }[sender] || '#888';
        const emoji = { orchestrator: '🎯', developer: '💻', reviewer: '🔍', tester: '🧪' }[sender] || '🤖';
        const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

        const el = document.createElement('div');
        el.className = 'chat-msg debate-card';
        el.dataset.type = 'debate';
        el.innerHTML = `
            <div class="msg-avatar" style="border-color: ${color}">${emoji}</div>
            <div class="msg-body">
                <div class="msg-header">
                    <span class="msg-sender" style="color: ${color}">${senderRole}</span>
                    <span class="msg-type-badge debate-badge">⚔️ DEBATE</span>
                    <span class="msg-time">${time}</span>
                </div>
                <div class="msg-content debate-content">
                    <div class="debate-thread" style="border-left: 3px solid ${color}; padding-left: 12px;">
                        ${this._escapeHtml(content)}
                    </div>
                    ${data.position ? `<div class="debate-position"><strong>Position:</strong> ${this._escapeHtml(data.position)}</div>` : ''}
                    ${data.target ? `<div class="debate-target">Responding to: <span style="color: var(--accent)">@${this._escapeHtml(data.target)}</span></div>` : ''}
                </div>
            </div>
        `;

        container.appendChild(el);
        container.scrollTop = container.scrollHeight;
    }

    // ── Helpers ─────────────────────────────────────────────

    _formatDuration(seconds) {
        if (seconds < 60) return `${Math.round(seconds)}s`;
        if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
        return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
    }

    _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }
}

window.features = new FeaturesPanel();
