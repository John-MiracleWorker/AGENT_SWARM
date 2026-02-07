/**
 * Agents Panel — Renders live agent status cards with thought bubbles.
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
                this.updateAgent(msg.data || msg);
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

    _createCard(agent) {
        const card = document.createElement('div');
        card.className = 'agent-card';
        card.id = `agent-${agent.id}`;
        card.style.setProperty('--agent-color', agent.color || '#888');

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
            </div>
            <div class="agent-thought" id="thought-${agent.id}"></div>
        `;

        this.container.appendChild(card);
    }

    _updateCard(agent) {
        const badge = document.getElementById(`badge-${agent.id}`);
        if (badge) {
            badge.className = `agent-status-badge ${agent.status || 'idle'}`;
            badge.textContent = agent.status || 'idle';
        }
    }
}

window.agentsPanel = new AgentsPanel();
