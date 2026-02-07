/**
 * Agents Panel — Renders live agent status cards with thought bubbles.
 * Supports dynamic agent spawning and removal.
 */

class AgentsPanel {
    constructor() {
        this.container = document.getElementById('agents-list');
        this.countEl = document.getElementById('agent-count');
        this.agents = {};
    }

    init() {
        swarmWS.on('message', (msg) => {
            if (msg.type === 'agent_status') {
                // Handle spawn/kill events
                if (msg.data?.event === 'agent_spawned') {
                    this._handleSpawn(msg.data);
                } else if (msg.data?.event === 'agent_killed') {
                    this._handleKill(msg.data);
                } else {
                    this.updateAgent(msg.data || msg);
                }
            }
            if (msg.type === 'thought') {
                this.updateThought(msg.sender, msg.content);
            }
        });
    }

    setAgents(agentList) {
        this.container.innerHTML = '';
        this.agents = {};
        agentList.forEach(a => this.updateAgent(a));
        this.countEl.textContent = agentList.length;
    }

    updateAgent(data) {
        const id = data.id || data.sender;
        if (!id) return;

        if (!this.agents[id]) {
            this.agents[id] = data;
            this._createCard(data);
        } else {
            Object.assign(this.agents[id], data);
            this._updateCard(data);
        }

        this.countEl.textContent = Object.keys(this.agents).length;
    }

    updateThought(agentId, thought) {
        const el = document.getElementById(`thought-${agentId}`);
        if (el) {
            el.textContent = thought;
            el.classList.add('visible');
            // Auto-hide after 15 seconds
            clearTimeout(el._hideTimeout);
            el._hideTimeout = setTimeout(() => {
                el.classList.remove('visible');
            }, 15000);
        }
    }

    _handleSpawn(data) {
        const agentData = {
            id: data.id,
            role: data.role,
            color: data.color,
            emoji: data.emoji,
            status: data.status || 'idle',
        };
        this.agents[data.id] = agentData;
        this._createCard(agentData, true);
        this.countEl.textContent = Object.keys(this.agents).length;
    }

    _handleKill(data) {
        const card = document.getElementById(`agent-${data.id}`);
        if (card) {
            card.classList.add('agent-card-removing');
            setTimeout(() => card.remove(), 400);
        }
        delete this.agents[data.id];
        this.countEl.textContent = Object.keys(this.agents).length;
    }

    _createCard(agent, isSpawn = false) {
        const card = document.createElement('div');
        card.className = 'agent-card' + (isSpawn ? ' agent-card-spawning' : '');
        card.id = `agent-${agent.id}`;
        card.style.setProperty('--agent-color', agent.color || '#888');

        // Check if this is a spawned agent (has a dash + number)
        const isSpawned = /\-\d+$/.test(agent.id);
        const killBtn = isSpawned
            ? `<button class="agent-kill-btn" onclick="agentsPanel.killAgent('${agent.id}')" title="Remove agent">✕</button>`
            : '';

        card.innerHTML = `
            <div class="agent-card-header">
                <span class="agent-emoji">${agent.emoji || '🤖'}</span>
                <div class="agent-info">
                    <div class="agent-name">${agent.id}</div>
                    <div class="agent-role">${agent.role || ''}</div>
                </div>
                <span class="agent-status-badge ${agent.status || 'idle'}" id="badge-${agent.id}">
                    ${agent.status || 'idle'}
                </span>
                ${killBtn}
            </div>
            <div class="agent-thought" id="thought-${agent.id}"></div>
        `;

        this.container.appendChild(card);

        // Remove spawn animation class after it plays
        if (isSpawn) {
            setTimeout(() => card.classList.remove('agent-card-spawning'), 600);
        }
    }

    _updateCard(agent) {
        const badge = document.getElementById(`badge-${agent.id}`);
        if (badge) {
            badge.className = `agent-status-badge ${agent.status || 'idle'}`;
            badge.textContent = agent.status || 'idle';
        }
    }

    async killAgent(agentId) {
        try {
            const resp = await fetch(`/api/agents/${agentId}/kill`, { method: 'POST' });
            if (!resp.ok) {
                console.warn(`Failed to kill agent: ${agentId}`);
            }
        } catch (e) {
            console.error('Kill agent error:', e);
        }
    }
}

window.agentsPanel = new AgentsPanel();
