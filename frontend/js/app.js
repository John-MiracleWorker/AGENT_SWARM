/**
 * App — Main application controller for Agent Swarm.
 * Initializes all panels, manages state, handles mission lifecycle.
 */

class App {
    constructor() {
        this.missionActive = false;
        this.selectedFolder = '';
        this.tokenUpdateInterval = null;
        this._initialized = false;
    }

    init() {
        // Guard against double initialization
        if (this._initialized) {
            console.warn('🐝 App already initialized — skipping');
            return;
        }
        this._initialized = true;

        // Initialize all panels
        agentsPanel.init();
        chatPanel.init();
        codePanel.init();
        taskPanel.init();
        terminalPanel.init();
        folderPicker.init();
        if (window.features) window.features.init();
        if (window.diffPanel) window.diffPanel.init();

        // Connect WebSocket
        swarmWS.connect();

        // Folder picker callback
        folderPicker.onSelect = (path) => {
            this.selectedFolder = path;
            const label = document.getElementById('folder-label');
            const btn = document.getElementById('btn-folder-picker');
            label.textContent = path.split('/').pop() || path;
            btn.classList.add('selected');
            btn.title = path;
            this._updateLaunchButton();
        };

        // Goal input
        const goalInput = document.getElementById('goal-input');
        goalInput.addEventListener('input', () => this._updateLaunchButton());
        goalInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !document.getElementById('btn-launch').disabled) {
                this.launchMission();
            }
        });

        // Launch button
        document.getElementById('btn-launch').addEventListener('click', () => this.launchMission());

        // Control buttons
        document.getElementById('btn-pause').addEventListener('click', () => this.pauseMission());
        document.getElementById('btn-resume').addEventListener('click', () => this.resumeMission());
        document.getElementById('btn-stop').addEventListener('click', () => this.stopMission());
        document.getElementById('btn-git-sync').addEventListener('click', () => this.syncToGit());

        // User message input
        const msgInput = document.getElementById('user-message-input');
        msgInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') this.sendMessage();
        });
        document.getElementById('btn-send').addEventListener('click', () => this.sendMessage());

        // WebSocket connection handler
        swarmWS.on('connected', () => {
            terminalPanel.addSystemMessage('WebSocket connected');
        });

        swarmWS.on('disconnected', () => {
            terminalPanel.addSystemMessage('WebSocket disconnected — reconnecting...');
        });

        // Token usage update
        swarmWS.on('message', (msg) => {
            // Update token display on any message (we'll poll periodically too)
            if (this.missionActive && !this.tokenUpdateInterval) {
                this.tokenUpdateInterval = setInterval(() => this._updateTokens(), 5000);
            }
        });

        console.log('🐝 Agent Swarm initialized');
    }

    async launchMission() {
        const goal = document.getElementById('goal-input').value.trim();
        if (!goal || !this.selectedFolder) return;

        try {
            const res = await fetch('/api/missions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    goal: goal,
                    workspace_path: this.selectedFolder,
                }),
            });

            if (!res.ok) {
                const err = await res.json();
                alert(`Failed to launch: ${err.detail || 'Unknown error'}`);
                return;
            }

            const data = await res.json();

            // Switch to active mission UI
            this.missionActive = true;
            document.getElementById('mission-setup').classList.add('hidden');
            document.getElementById('mission-active').classList.remove('hidden');
            document.getElementById('mission-buttons').classList.remove('hidden');
            document.getElementById('active-goal-text').textContent = goal;

            // Enable chat input
            document.getElementById('user-message-input').disabled = false;
            document.getElementById('btn-send').disabled = false;

            // Set agents
            if (data.agents) {
                agentsPanel.setAgents(data.agents);
            }

            // Refresh file tree
            codePanel.refreshFileTree();

            // Start token updates
            this.tokenUpdateInterval = setInterval(() => this._updateTokens(), 5000);

            terminalPanel.addSystemMessage(`🚀 Mission launched: ${goal}`);
            terminalPanel.addSystemMessage(`📁 Workspace: ${this.selectedFolder}`);

        } catch (e) {
            console.error('Launch failed:', e);
            alert('Failed to launch mission. Is the server running?');
        }
    }

    async pauseMission() {
        try {
            await fetch('/api/missions/pause', { method: 'POST' });
            document.getElementById('btn-pause').classList.add('hidden');
            document.getElementById('btn-resume').classList.remove('hidden');
        } catch (e) {
            console.error('Pause failed:', e);
        }
    }

    async resumeMission() {
        try {
            await fetch('/api/missions/resume', { method: 'POST' });
            document.getElementById('btn-resume').classList.add('hidden');
            document.getElementById('btn-pause').classList.remove('hidden');
        } catch (e) {
            console.error('Resume failed:', e);
        }
    }

    async stopMission() {
        // Use custom modal instead of confirm() which gets auto-dismissed by WS activity
        const confirmed = await this._showConfirmDialog(
            '🛑 Stop Mission',
            'Stop the mission? All agents will be halted.',
            'Stop Mission'
        );
        if (!confirmed) return;

        try {
            await fetch('/api/missions/stop', { method: 'POST' });
            this.missionActive = false;

            document.getElementById('mission-setup').classList.remove('hidden');
            document.getElementById('mission-active').classList.add('hidden');
            document.getElementById('mission-buttons').classList.add('hidden');
            document.getElementById('user-message-input').disabled = true;
            document.getElementById('btn-send').disabled = true;
            document.getElementById('btn-pause').classList.remove('hidden');
            document.getElementById('btn-resume').classList.add('hidden');

            if (this.tokenUpdateInterval) {
                clearInterval(this.tokenUpdateInterval);
                this.tokenUpdateInterval = null;
            }

            terminalPanel.addSystemMessage('🛑 Mission stopped');
        } catch (e) {
            console.error('Stop failed:', e);
        }
    }

    _showConfirmDialog(title, message, actionLabel = 'Confirm') {
        return new Promise((resolve) => {
            const overlay = document.getElementById('confirm-modal');
            document.getElementById('confirm-modal-title').textContent = title;
            document.getElementById('confirm-modal-message').textContent = message;
            document.getElementById('confirm-ok').textContent = actionLabel;
            overlay.classList.remove('hidden');

            const cleanup = () => {
                overlay.classList.add('hidden');
                okBtn.removeEventListener('click', onOk);
                cancelBtn.removeEventListener('click', onCancel);
                overlay.removeEventListener('click', onOverlay);
            };

            const onOk = () => { cleanup(); resolve(true); };
            const onCancel = () => { cleanup(); resolve(false); };
            const onOverlay = (e) => { if (e.target === overlay) { cleanup(); resolve(false); } };

            const okBtn = document.getElementById('confirm-ok');
            const cancelBtn = document.getElementById('confirm-cancel');
            okBtn.addEventListener('click', onOk);
            cancelBtn.addEventListener('click', onCancel);
            overlay.addEventListener('click', onOverlay);
        });
    }

    async sendMessage() {
        const input = document.getElementById('user-message-input');
        const content = input.value.trim();
        if (!content) return;

        try {
            await fetch('/api/missions/message', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content }),
            });
            input.value = '';
        } catch (e) {
            console.error('Send failed:', e);
        }
    }

    async approveAction(approvalId, approved) {
        try {
            await fetch(`/api/missions/approve/${approvalId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ approved }),
            });
        } catch (e) {
            console.error('Approval failed:', e);
        }
    }

    async syncToGit() {
        const btn = document.getElementById('btn-git-sync');
        const originalText = btn.textContent;
        btn.disabled = true;
        btn.textContent = '⏳ Syncing...';

        try {
            const message = prompt('Commit message (leave blank for default):', '') || '';
            if (message === null) { // User cancelled
                btn.disabled = false;
                btn.textContent = originalText;
                return;
            }

            const res = await fetch(`/api/git/sync?message=${encodeURIComponent(message)}`, {
                method: 'POST',
            });
            const data = await res.json();

            if (data.ok) {
                const parts = [];
                if (data.committed) parts.push(`✅ Committed: ${data.sha}`);
                else parts.push(`ℹ️ ${data.message}`);
                if (data.pushed) parts.push(`📤 Pushed to ${data.branch} → ${data.remote}`);
                if (data.remote_note) parts.push(`📌 ${data.remote_note}`);
                terminalPanel.addSystemMessage(`🔄 Git Sync: ${parts.join(' | ')}`);
                btn.textContent = '✅ Synced!';
            } else {
                terminalPanel.addSystemMessage(`❌ Git Sync failed: ${data.error}`);
                btn.textContent = '❌ Failed';
            }

            setTimeout(() => {
                btn.textContent = originalText;
                btn.disabled = false;
            }, 2000);

        } catch (e) {
            console.error('Git sync failed:', e);
            terminalPanel.addSystemMessage(`❌ Git Sync error: ${e.message}`);
            btn.textContent = originalText;
            btn.disabled = false;
        }
    }

    async _updateTokens() {
        try {
            const res = await fetch('/api/usage');
            if (!res.ok) return;
            const data = await res.json();
            const global = data.global || {};

            document.getElementById('token-count').textContent =
                this._formatNumber(global.total_tokens || 0);
            document.getElementById('token-cost').textContent =
                `$${(global.estimated_cost_usd || 0).toFixed(3)}`;
        } catch (e) {
            // Silently fail — not critical
        }
    }

    _updateLaunchButton() {
        const goal = document.getElementById('goal-input').value.trim();
        document.getElementById('btn-launch').disabled = !goal || !this.selectedFolder;
    }

    _formatNumber(n) {
        if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
        if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
        return String(n);
    }
}

// Initialize on DOM ready
window.app = new App();
document.addEventListener('DOMContentLoaded', () => window.app.init());
