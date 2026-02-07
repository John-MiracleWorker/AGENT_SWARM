/**
 * Task Panel — Progress-tracked task overview with auto-complete detection.
 * Shows real-time checklist of tasks and mission completion state.
 */

class TaskPanel {
    constructor() {
        this.tasks = {};
        this.missionComplete = false;
    }

    init() {
        swarmWS.on('message', (msg) => {
            if (msg.type === 'task_assigned' && msg.data) {
                this.updateTask(msg.data);
            }

            // Handle mission completion broadcast
            if (msg.type === 'mission_complete') {
                this._handleMissionComplete(msg.data || {});
            }
        });
    }

    updateTask(task) {
        const id = task.id;
        if (!id) return;

        // Remove from old column if exists
        const existing = document.getElementById(`task-${id}`);
        if (existing) existing.remove();

        this.tasks[id] = task;

        // Add to correct column
        const status = task.status || 'todo';
        const column = document.getElementById(`cards-${status}`);
        if (!column) return;

        const card = document.createElement('div');
        card.className = 'kanban-card';
        card.id = `task-${id}`;

        const assigneeColor = {
            orchestrator: '#FFD700',
            developer: '#00E5FF',
            reviewer: '#AA00FF',
            tester: '#00E676',
        }[task.assignee] || '#888';

        const statusIcon = this._statusIcon(status);

        card.innerHTML = `
            <div class="kanban-card-header">
                <span class="kanban-card-status">${statusIcon}</span>
                <span class="kanban-card-title">${this._escapeHtml(task.title)}</span>
            </div>
            <div class="kanban-card-meta">
                <span style="color: ${assigneeColor}">● ${task.assignee || 'unassigned'}</span>
                <span class="kanban-card-id">#${id}</span>
            </div>
        `;

        card.title = task.description || '';
        column.appendChild(card);

        // Update counts and check completion
        this._updateCounts();
    }

    _statusIcon(status) {
        const icons = {
            'todo': '○',
            'in_progress': '◔',
            'in_review': '◑',
            'done': '●',
            'blocked': '⊘',
        };
        return icons[status] || '○';
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

        const taskBody = document.querySelector('#task-panel .panel-body');
        if (taskBody) {
            taskBody.prepend(banner);
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

        // Add system message
        if (window.terminalPanel) {
            terminalPanel.addSystemMessage('🏁 Mission complete — all tasks finished. Agents stopped.');
        }
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

            // Color gradient based on progress
            if (pct >= 100) {
                fill.style.background = 'linear-gradient(90deg, #00E676, #69F0AE)';
            } else if (pct >= 50) {
                fill.style.background = 'linear-gradient(90deg, var(--accent), #00E5FF)';
            }
        }
        if (text) {
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
