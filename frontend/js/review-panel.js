/**
 * Review Panel — Displays post-completion project review results.
 * Shows review status, score, issues, and strengths.
 */

class ReviewPanel {
    constructor() {
        this.container = document.getElementById('review-content');
        this.statusBadge = document.getElementById('review-status-badge');
        this.scoreEl = document.getElementById('review-score');
        this.cycleEl = document.getElementById('review-cycle');
        this.reviews = [];
        this._currentReview = null;
    }

    init() {
        if (!this.container) return;

        swarmWS.on('message', (msg) => {
            if (msg.type === 'review_result') this.handleReview(msg);
            if (msg.type === 'system' && msg.content && msg.content.includes('Review cycle')) {
                this._setStatus('reviewing', msg.content);
            }
        });
    }

    handleReview(msg) {
        const data = msg.data || {};
        this._currentReview = data;
        this.reviews.push(data);

        // Update status badge
        const status = data.status || 'unknown';
        this._setStatus(status);

        // Update score
        if (this.scoreEl && data.score !== undefined) {
            const scoreClass = data.score >= 80 ? 'high' : data.score >= 50 ? 'medium' : 'low';
            this.scoreEl.className = `review-score ${scoreClass}`;
            this.scoreEl.innerHTML = `
                <span class="review-score-value">${data.score}</span>
                <span class="review-score-label">/100</span>
            `;
        }

        // Update cycle info
        if (this.cycleEl && data.cycle) {
            this.cycleEl.textContent = `Cycle ${data.cycle}/${data.max_cycles || 3}`;
        }

        // Render review content
        this._renderReview(data);

        // Auto-switch to review tab
        this._activateTab();
    }

    _setStatus(status, label) {
        if (!this.statusBadge) return;

        const labels = {
            pending: '⏳ Pending',
            reviewing: '🔍 Reviewing...',
            pass: '✅ Passed',
            needs_changes: '⚠️ Needs Changes',
            error: '❌ Error',
        };

        this.statusBadge.className = `review-status-badge ${status}`;
        this.statusBadge.textContent = label || labels[status] || status;
    }

    _renderReview(data) {
        if (!this.container) return;

        let html = '';

        // Summary
        if (data.summary) {
            html += `<div class="review-summary">${this._escape(data.summary)}</div>`;
        }

        // Strengths
        const strengths = data.strengths || [];
        if (strengths.length > 0) {
            html += `<div class="review-strengths">
                <div class="review-section-title">✨ Strengths</div>
                ${strengths.map(s => `<div class="review-strength-item">${this._escape(s)}</div>`).join('')}
            </div>`;
        }

        // Issues
        const issues = data.issues || [];
        if (issues.length > 0) {
            html += `<div class="review-section-title">🐛 Issues (${issues.length})</div>`;
            html += '<div class="review-issues">';

            // Sort: critical first, then major, then minor
            const order = { critical: 0, major: 1, minor: 2 };
            const sorted = [...issues].sort((a, b) => (order[a.severity] || 2) - (order[b.severity] || 2));

            for (const issue of sorted) {
                html += `
                    <div class="review-issue">
                        <div class="review-issue-header">
                            <span class="severity-badge ${issue.severity || 'minor'}">${issue.severity || 'minor'}</span>
                            <span class="review-issue-title">${this._escape(issue.title || 'Issue')}</span>
                            ${issue.file ? `<span class="review-issue-file">${this._escape(issue.file)}</span>` : ''}
                        </div>
                        <div class="review-issue-desc">${this._escape(issue.description || '')}</div>
                        ${issue.assignee ? `<span class="review-issue-assignee">→ ${issue.assignee}</span>` : ''}
                    </div>
                `;
            }

            html += '</div>';
        }

        // No issues
        if (data.status === 'pass' && issues.length === 0) {
            html += `
                <div class="review-empty" style="padding-top: 20px;">
                    <div class="review-empty-icon">🎉</div>
                    <p>All checks passed — no issues found!</p>
                </div>
            `;
        }

        this.container.innerHTML = html;
    }

    _activateTab() {
        // Click the review tab to switch to it
        const tab = document.querySelector('[data-panel="review-panel"]');
        if (tab) tab.click();
    }

    _escape(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
}

window.reviewPanel = new ReviewPanel();
