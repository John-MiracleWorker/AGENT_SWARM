/**
 * Chat Panel — Real-time communication feed with message filtering and approval cards.
 */

const AGENT_COLORS = {
    orchestrator: '#FFD700',
    developer: '#00E5FF',
    reviewer: '#AA00FF',
    tester: '#00E676',
    system: '#FF6B6B',
    user: '#64B5F6',
};

const AGENT_EMOJIS = {
    orchestrator: '🎯',
    developer: '💻',
    reviewer: '🔍',
    tester: '🧪',
    system: '⚙️',
    user: '👤',
};

const TYPE_LABELS = {
    chat: { label: 'CHAT', color: 'rgba(100,100,200,0.2)' },
    code_update: { label: 'CODE', color: 'rgba(0,229,255,0.2)' },
    file_update: { label: 'FILE', color: 'rgba(0,229,255,0.2)' },
    review_request: { label: 'REVIEW', color: 'rgba(170,0,255,0.2)' },
    review_result: { label: 'REVIEW', color: 'rgba(170,0,255,0.2)' },
    debate: { label: 'DEBATE', color: 'rgba(255,215,0,0.2)' },
    test_result: { label: 'TEST', color: 'rgba(0,230,118,0.2)' },
    task_assigned: { label: 'TASK', color: 'rgba(124,77,255,0.2)' },
    terminal_output: { label: 'TERMINAL', color: 'rgba(100,100,100,0.2)' },
    approval_request: { label: 'APPROVAL', color: 'rgba(255,82,82,0.2)' },
    system: { label: 'SYSTEM', color: 'rgba(255,107,107,0.2)' },
};

class ChatPanel {
    constructor() {
        this.container = document.getElementById('chat-messages');
        this.currentFilter = 'all';
        this.messages = [];
    }

    init() {
        // Filter buttons
        document.querySelectorAll('.filter-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                this.currentFilter = btn.dataset.filter;
                this._applyFilter();
            });
        });

        // Listen for messages
        swarmWS.on('message', (msg) => {
            const type = msg.type;
            // Skip thought messages and agent_status (handled by agents panel)
            if (type === 'thought' || type === 'agent_status' || type === 'connection' || type === 'pong') return;
            this.addMessage(msg);
        });
    }

    clearWelcome() {
        const welcome = this.container.querySelector('.chat-welcome');
        if (welcome) welcome.remove();
    }

    addMessage(msg) {
        this.clearWelcome();

        const sender = msg.sender || 'system';
        const senderRole = msg.sender_role || sender;
        const type = msg.type || 'chat';
        const content = msg.content || '';
        const data = msg.data || {};

        const el = document.createElement('div');
        el.className = 'chat-msg';
        el.dataset.type = type;

        const color = AGENT_COLORS[sender] || AGENT_COLORS.system;
        const emoji = AGENT_EMOJIS[sender] || '🤖';
        const typeInfo = TYPE_LABELS[type] || TYPE_LABELS.chat;
        const time = msg.timestamp
            ? new Date(msg.timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
            : new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

        let contentHtml = this._formatContent(content, type);

        // Approval requests go to sticky banner, not inline
        if (type === 'approval_request' && data.approval_id) {
            this._showApprovalBanner(data, content, sender, senderRole);
        }

        // Add terminal output for terminal messages
        if (type === 'terminal_output' && data.stdout) {
            contentHtml += `<pre>${this._escapeHtml(data.stdout.substring(0, 2000))}</pre>`;
            if (data.stderr) {
                contentHtml += `<pre style="color: var(--danger);">${this._escapeHtml(data.stderr.substring(0, 500))}</pre>`;
            }
        }

        el.innerHTML = `
            <div class="msg-avatar" style="border-color: ${color}">
                ${emoji}
            </div>
            <div class="msg-body">
                <div class="msg-header">
                    <span class="msg-sender" style="color: ${color}">${senderRole}</span>
                    <span class="msg-type-badge" style="background: ${typeInfo.color}; color: ${color}">${typeInfo.label}</span>
                    <span class="msg-time">${time}</span>
                </div>
                <div class="msg-content">${contentHtml}</div>
            </div>
        `;

        this.container.appendChild(el);
        this.messages.push({ el, type });

        // Apply current filter
        if (!this._matchesFilter(type)) {
            el.style.display = 'none';
        }

        // Auto-scroll disabled — user controls scroll position
    }

    _formatContent(content, type) {
        let html = this._escapeHtml(content);

        // Inline code
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

        // Bold
        html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

        // @mentions
        html = html.replace(/@(\w+)/g, (match, name) => {
            const color = AGENT_COLORS[name] || 'var(--accent)';
            return `<span style="color: ${color}; font-weight: 600;">@${name}</span>`;
        });

        // Line breaks
        html = html.replace(/\n/g, '<br>');

        return html;
    }

    _matchesFilter(type) {
        if (this.currentFilter === 'all') return true;
        if (this.currentFilter === 'chat') return type === 'chat' || type === 'system';
        if (this.currentFilter === 'code') return type === 'file_update' || type === 'code_update' || type === 'terminal_output';
        if (this.currentFilter === 'review') return type === 'review_request' || type === 'review_result' || type === 'debate';
        return true;
    }

    _applyFilter() {
        this.messages.forEach(({ el, type }) => {
            el.style.display = this._matchesFilter(type) ? '' : 'none';
        });
    }

    _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    _showApprovalBanner(data, content, sender, senderRole) {
        // Create or get the approval banner container
        let container = document.getElementById('approval-banner-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'approval-banner-container';
            document.body.prepend(container);
        }

        const banner = document.createElement('div');
        banner.className = 'approval-banner';
        banner.dataset.approvalId = data.approval_id;

        const AGENT_COLORS = { orchestrator: '#FFD700', developer: '#00E5FF', reviewer: '#AA00FF', tester: '#00E676' };
        const color = AGENT_COLORS[sender] || '#888';

        banner.innerHTML = `
            <div class="approval-banner-content">
                <span class="approval-banner-icon">⚠️</span>
                <div class="approval-banner-info">
                    <strong style="color: ${color}">${senderRole}</strong> needs approval
                    <div class="approval-banner-detail">${this._escapeHtml(content).substring(0, 120)}</div>
                </div>
                <div class="approval-banner-actions">
                    <button class="btn-approve" id="approve-${data.approval_id}">✅ Approve</button>
                    <button class="btn-reject" id="reject-${data.approval_id}">❌ Reject</button>
                </div>
            </div>
        `;

        container.appendChild(banner);

        // Attach click handlers
        document.getElementById(`approve-${data.approval_id}`).addEventListener('click', () => {
            window.app.approveAction(data.approval_id, true);
            banner.remove();
            if (container.children.length === 0) container.remove();
        });
        document.getElementById(`reject-${data.approval_id}`).addEventListener('click', () => {
            window.app.approveAction(data.approval_id, false);
            banner.remove();
            if (container.children.length === 0) container.remove();
        });
    }
}

window.chatPanel = new ChatPanel();
