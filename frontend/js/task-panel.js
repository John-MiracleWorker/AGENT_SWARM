/**
 * Task Panel — Enhanced task overview with agent filtering and checkboxes.
 * Shows all tasks across all agents with real-time status updates.
 */

class TaskPanel {
    constructor() {
        this.tasks = {};
        this.missionComplete = false;
        this.activeFilter = 'all'; // 'all' or agent role name
    }

    init() {
        swarmWS.on('message', (msg) => {
            if (msg.type === 'task_assigned' && msg.data) {
                // Could be a single task or a list
                if (Array.isArray(msg.data.tasks)) {
                    msg.data.tasks.forEach(t => this._ingestTask(t));
                } else {
                    this._ingestTask(msg.data);
                }
                this._render();
            }

            if (msg.type === 'mission_complete') {
                this._handleMissionComplete(msg.data || {});
            }
        });

        // Build filter bar
        this._buildFilterBar();
    }

    _ingestTask(task) {
        const id = task.id;
        if (!id) return;
        this.tasks[id] = { ...this.tasks[id], ...task };
    }

    _buildFilterBar() {
        const board = document.getElementById('kanban-board');
        if (!board) return;

        // Insert filter bar above the kanban columns
        let filterBar = document.getElementById('task-filter-bar');
        if (!filterBar) {
            filterBar = document.createElement('div');
            filterBar.id = 'task-filter-bar';
            filterBar.className = 'task-filter-bar';
            board.parentElement.insertBefore(filterBar, board);
        }

        this._updateFilterBar();
    }

    _updateFilterBar() {
        const filterBar = document.getElementById('task-filter-bar');
        if (!filterBar) return;

        // Collect unique assignees
        const assignees = new Set();
        Object.values(this.tasks).forEach(t => {
            if (t.assignee) assignees.add(t.assignee);
        });

        const colors = {
            orchestrator: '#FFD700',
            developer: '#00E5FF',
            reviewer: '#AA00FF',
            tester: '#00E676',
        };

        let html = `<button class="task-filter-btn ${this.activeFilter === 'all' ? 'active' : ''}" data-agent="all">
            All <span class="task-filter-count">${Object.keys(this.tasks).length}</span>
        </button>`;

        for (const agent of assignees) {
            const count = Object.values(this.tasks).filter(t => t.assignee === agent).length;
            const color = colors[agent] || '#888';
            const isActive = this.activeFilter === agent ? 'active' : '';
            html += `<button class="task-filter-btn ${isActive}" data-agent="${agent}" style="--agent-color: ${color}">
                <span class="filter-dot" style="background: ${color}"></span>
                ${this._escapeHtml(agent)} <span class="task-filter-count">${count}</span>
            </button>`;
        }

        filterBar.innerHTML = html;

        // Attach click handlers
        filterBar.querySelectorAll('.task-filter-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                this.activeFilter = btn.dataset.agent;
                this._render();
            });
        });
    }

    _render() {
        this._updateFilterBar();
        this._renderCards();
        this._updateCounts();
    }

    _renderCards() {
        const statuses = ['todo', 'in_progress', 'in_review', 'done'];

        // Clear all columns
        statuses.forEach(s => {
            const col = document.getElementById(`cards-${s}`);
            if (col) col.innerHTML = '';
        });

        // Get filtered tasks
        const filtered = Object.values(this.tasks).filter(t => {
            if (this.activeFilter === 'all') return true;
            return t.assignee === this.activeFilter;
        });

        // Sort: most recently updated first within each status
        for (const task of filtered) {
            const status = task.status || 'todo';
            const column = document.getElementById(`cards-${status}`);
            if (!column) continue;

            const card = document.createElement('div');
            card.className = 'kanban-card';
            card.id = `task-${task.id}`;

            const assigneeColor = {
                orchestrator: '#FFD700',
                developer: '#00E5FF',
                reviewer: '#AA00FF',
                tester: '#00E676',
            }[task.assignee] || '#888';

            const isDone = status === 'done';
            const checkboxIcon = isDone ? '☑' : status === 'in_progress' ? '◧' : '☐';
            const checkboxClass = isDone ? 'checked' : status === 'in_progress' ? 'partial' : '';
            const titleClass = isDone ? 'task-done' : '';

            card.innerHTML = `
                <div class="kanban-card-header">
                    <span class="task-checkbox ${checkboxClass}">${checkboxIcon}</span>
                    <span class="kanban-card-title ${titleClass}">${this._escapeHtml(task.title)}</span>
                </div>
                ${task.description ? `<div class="kanban-card-desc">${this._escapeHtml(task.description).substring(0, 80)}${task.description.length > 80 ? '…' : ''}</div>` : ''}
                <div class="kanban-card-meta">
                    <span style="color: ${assigneeColor}">● ${task.assignee || 'unassigned'}</span>
                    <span class="kanban-card-id">#${task.id}</span>
                </div>
            `;

            card.title = task.description || '';
            column.appendChild(card);
        }
    }

    _handleMissionComplete(data) {
        this.missionComplete = true;
        const summary = data.summary || {};

        // Update progress bar to 100%
        const fill = document.getElementById('progress-fill');
        const text = document.getElementById('progress-text');
        if (fill) {
            fill.style.width = '100%';
            fill.style.background = 'linear-gradient(90deg, #00E676, #69F0AE)';
        }
        if (text) {
            text.textContent = '✅ Complete';
            text.style.color = '#00E676';
        }

        // Show completion banner
        const banner = document.createElement('div');
        banner.className = 'mission-complete-banner';
        banner.innerHTML = `
            <div class="mission-complete-icon">🏁</div>
            <div class="mission-complete-text">
                <strong>Mission Complete</strong>
                <span>${summary.total || Object.keys(this.tasks).length} tasks finished</span>
            </div>
        `;

        const board = document.getElementById('kanban-board');
        if (board && board.parentElement) {
            board.parentElement.insertBefore(banner, board);
        }

        // Update app state
        if (window.app) {
            window.app.missionActive = false;
            if (window.app.tokenUpdateInterval) {
                clearInterval(window.app.tokenUpdateInterval);
            }
        }

        // Flash the stop button to green
        const stopBtn = document.getElementById('btn-stop');
        if (stopBtn) {
            stopBtn.textContent = '✅ Done';
            stopBtn.style.background = 'rgba(0, 230, 118, 0.2)';
            stopBtn.style.color = '#00E676';
            stopBtn.style.borderColor = '#00E676';
        }

        // Mark all tasks as done visually
        this._render();
    }

    _updateCounts() {
        const statuses = ['todo', 'in_progress', 'in_review', 'done'];
        statuses.forEach(s => {
            const column = document.getElementById(`cards-${s}`);
            const count = document.getElementById(`count-${s}`);
            if (column && count) {
                count.textContent = column.children.length;
            }
        });

        // Update progress bar
        const total = Object.keys(this.tasks).length;
        const done = Object.values(this.tasks).filter(t => t.status === 'done').length;
        const fill = document.getElementById('progress-fill');
        const text = document.getElementById('progress-text');

        if (fill && total > 0) {
            const pct = (done / total) * 100;
            fill.style.width = `${pct}%`;

            if (pct >= 100) {
                fill.style.background = 'linear-gradient(90deg, #00E676, #69F0AE)';
            } else if (pct >= 50) {
                fill.style.background = 'linear-gradient(90deg, var(--accent), #00E5FF)';
            }
        }
        if (text && !this.missionComplete) {
            text.textContent = `${done}/${total} tasks`;
        }
    }

    _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
}

window.taskPanel = new TaskPanel();
