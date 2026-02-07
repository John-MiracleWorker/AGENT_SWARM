/**
 * Terminal Panel — Live terminal output from agent-executed commands.
 */

class TerminalPanel {
    constructor() {
        this.container = document.getElementById('terminal-output');
    }

    init() {
        swarmWS.on('message', (msg) => {
            if (msg.type === 'terminal_output') {
                this.addOutput(msg);
            }
        });

        // Tab switching
        document.querySelectorAll('.bottom-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.bottom-tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.bottom-panel').forEach(p => p.classList.remove('active'));
                tab.classList.add('active');
                const panel = document.getElementById(tab.dataset.panel);
                if (panel) panel.classList.add('active');
            });
        });
    }

    addOutput(msg) {
        const data = msg.data || {};
        const sender = msg.sender || 'agent';

        // Command line
        if (msg.content) {
            this._addLine(msg.content, 'terminal-command');
        }

        // stdout
        if (data.stdout) {
            data.stdout.split('\n').forEach(line => {
                if (line.trim()) this._addLine(line, 'terminal-stdout');
            });
        }

        // stderr
        if (data.stderr) {
            data.stderr.split('\n').forEach(line => {
                if (line.trim()) this._addLine(line, 'terminal-stderr');
            });
        }

        // Status line
        if (data.return_code !== undefined) {
            const status = data.return_code === 0
                ? `✓ Command succeeded (${data.duration}s)`
                : `✗ Command failed with code ${data.return_code} (${data.duration}s)`;
            const cls = data.return_code === 0 ? 'terminal-system' : 'terminal-stderr';
            this._addLine(status, cls);
        }

        // Auto-scroll
        this.container.scrollTop = this.container.scrollHeight;
    }

    addSystemMessage(text) {
        this._addLine(text, 'terminal-system');
    }

    _addLine(text, className) {
        const line = document.createElement('div');
        line.className = `terminal-line ${className}`;
        line.textContent = text;
        this.container.appendChild(line);

        // Keep max 500 lines
        while (this.container.children.length > 500) {
            this.container.removeChild(this.container.firstChild);
        }
    }
}

window.terminalPanel = new TerminalPanel();
