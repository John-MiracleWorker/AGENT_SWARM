/**
 * Task Panel — Kanban board with TODO / IN_PROGRESS / IN_REVIEW / DONE columns.
 */

class TaskPanel {
    constructor() {
        this.tasks = {};
    }

    init() {
        swarmWS.on('message', (msg) => {
            if (msg.type === 'task_assigned' && msg.data) {
                this.updateTask(msg.data);
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

        card.innerHTML = `
            <div class="kanban-card-title">${this._escapeHtml(task.title)}</div>
            <div class="kanban-card-meta">
                <span style="color: ${assigneeColor}">${task.assignee || 'unassigned'}</span>
                <span>${id}</span>
            </div>
        `;

        card.title = task.description || '';
        column.appendChild(card);

        // Update counts
        this._updateCounts();
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
            fill.style.width = `${(done / total) * 100}%`;
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
