/**
 * Terminal Panel — Full interactive terminal using xterm.js + WebSocket PTY.
 * Supports multiple terminal tabs, resize, and real command execution.
 */

class TerminalPanel {
    constructor() {
        this.container = document.getElementById('xterm-container');
        this.tabsContainer = document.getElementById('terminal-tabs');
        this.sessions = new Map(); // sessionId -> { term, ws, tab }
        this.activeSession = null;
        this.sessionCounter = 0;
    }

    init() {
        // Tab switching for bottom panels (Terminal / Tasks)
        document.querySelectorAll('.bottom-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.bottom-tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.bottom-panel').forEach(p => p.classList.remove('active'));
                tab.classList.add('active');
                const panel = document.getElementById(tab.dataset.panel);
                if (panel) panel.classList.add('active');

                // Fit terminal when switching to terminal tab
                if (tab.dataset.panel === 'terminal-panel' && this.activeSession) {
                    const session = this.sessions.get(this.activeSession);
                    if (session && session.fitAddon) {
                        setTimeout(() => session.fitAddon.fit(), 50);
                    }
                }
            });
        });

        // Toolbar buttons
        document.getElementById('btn-new-terminal')?.addEventListener('click', () => this.createSession());
        document.getElementById('btn-kill-terminal')?.addEventListener('click', () => this.killActiveSession());
        document.getElementById('btn-clear-terminal')?.addEventListener('click', () => this.clearActiveTerminal());

        // Create the first terminal session
        this.createSession();

        // Handle window resize
        window.addEventListener('resize', () => this._fitActiveTerminal());

        // Observe bottom panel resize (drag handle)
        const observer = new ResizeObserver(() => this._fitActiveTerminal());
        if (this.container) observer.observe(this.container);

        // Also listen for agent terminal output to show in the terminal
        if (typeof swarmWS !== 'undefined') {
            swarmWS.on('message', (msg) => {
                if (msg.type === 'terminal_output' && this.activeSession) {
                    const session = this.sessions.get(this.activeSession);
                    if (session && session.term) {
                        const data = msg.data || {};
                        if (msg.content) {
                            session.term.writeln(`\x1b[36m${msg.content}\x1b[0m`);
                        }
                        if (data.stdout) {
                            session.term.writeln(data.stdout);
                        }
                        if (data.stderr) {
                            session.term.writeln(`\x1b[31m${data.stderr}\x1b[0m`);
                        }
                    }
                }
            });
        }
    }

    createSession() {
        this.sessionCounter++;
        const sessionId = `term-${this.sessionCounter}`;

        // Create xterm instance
        const term = new Terminal({
            cursorBlink: true,
            cursorStyle: 'bar',
            fontSize: 13,
            fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
            lineHeight: 1.2,
            letterSpacing: 0,
            theme: {
                background: '#0a0e14',
                foreground: '#e0e0e0',
                cursor: '#6c63ff',
                cursorAccent: '#0a0e14',
                selectionBackground: 'rgba(108, 99, 255, 0.3)',
                selectionForeground: '#ffffff',
                black: '#1a1e28',
                red: '#ff6b7f',
                green: '#00e676',
                yellow: '#ffcc02',
                blue: '#6c63ff',
                magenta: '#c678dd',
                cyan: '#56b6c2',
                white: '#e0e0e0',
                brightBlack: '#5c6370',
                brightRed: '#ff8fa0',
                brightGreen: '#69ff97',
                brightYellow: '#ffd866',
                brightBlue: '#8f8aff',
                brightMagenta: '#e791f5',
                brightCyan: '#80d4e0',
                brightWhite: '#ffffff',
            },
            allowProposedApi: true,
            scrollback: 10000,
        });

        // Load addons
        const fitAddon = new FitAddon.FitAddon();
        term.loadAddon(fitAddon);

        try {
            const webLinksAddon = new WebLinksAddon.WebLinksAddon();
            term.loadAddon(webLinksAddon);
        } catch (e) {
            // WebLinks addon might not be available
        }

        // Create terminal container div
        const termDiv = document.createElement('div');
        termDiv.id = `xterm-${sessionId}`;
        termDiv.className = 'xterm-instance';
        termDiv.style.width = '100%';
        termDiv.style.height = '100%';
        this.container.appendChild(termDiv);

        // Open terminal in the container
        term.open(termDiv);

        // Fit to container after opening
        setTimeout(() => {
            fitAddon.fit();
        }, 100);

        // Create tab
        const tab = document.createElement('button');
        tab.className = 'terminal-tab active';
        tab.dataset.session = sessionId;
        tab.title = `Terminal ${this.sessionCounter}`;
        tab.textContent = `🖥️ Terminal ${this.sessionCounter}`;
        tab.addEventListener('click', () => this.switchToSession(sessionId));

        // Deactivate other tabs
        this.tabsContainer.querySelectorAll('.terminal-tab').forEach(t => t.classList.remove('active'));
        this.tabsContainer.appendChild(tab);

        // Hide other terminal instances
        this.container.querySelectorAll('.xterm-instance').forEach(d => d.style.display = 'none');
        termDiv.style.display = 'block';

        // Connect WebSocket to PTY backend
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/terminal`;
        const ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            term.writeln('\x1b[32m● Connected to terminal\x1b[0m');
            term.writeln('');
        };

        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);

                switch (msg.type) {
                    case 'output':
                        term.write(msg.data);
                        break;
                    case 'session':
                        // Session created on backend
                        break;
                    case 'exit':
                        term.writeln('\r\n\x1b[31m● Session ended\x1b[0m');
                        break;
                    case 'ping':
                        ws.send(JSON.stringify({ type: 'pong' }));
                        break;
                }
            } catch (e) {
                // Not JSON, write raw
                term.write(event.data);
            }
        };

        ws.onclose = () => {
            term.writeln('\r\n\x1b[33m● Disconnected\x1b[0m');
        };

        ws.onerror = (err) => {
            term.writeln('\r\n\x1b[31m● Connection error\x1b[0m');
        };

        // Forward user input to WebSocket
        term.onData((data) => {
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'input', data: data }));
            }
        });

        // Handle terminal resize → tell backend
        term.onResize(({ cols, rows }) => {
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'resize', cols, rows }));
            }
        });

        // Store session
        this.sessions.set(sessionId, { term, ws, tab, div: termDiv, fitAddon });
        this.activeSession = sessionId;

        return sessionId;
    }

    switchToSession(sessionId) {
        const session = this.sessions.get(sessionId);
        if (!session) return;

        // Update tabs
        this.tabsContainer.querySelectorAll('.terminal-tab').forEach(t => t.classList.remove('active'));
        session.tab.classList.add('active');

        // Show/hide terminal instances
        this.container.querySelectorAll('.xterm-instance').forEach(d => d.style.display = 'none');
        session.div.style.display = 'block';

        this.activeSession = sessionId;

        // Fit the terminal
        setTimeout(() => session.fitAddon.fit(), 50);
        session.term.focus();
    }

    killActiveSession() {
        if (!this.activeSession) return;

        const sessionId = this.activeSession;
        const session = this.sessions.get(sessionId);
        if (!session) return;

        // Close WebSocket
        if (session.ws && session.ws.readyState === WebSocket.OPEN) {
            session.ws.close();
        }

        // Dispose terminal
        session.term.dispose();

        // Remove DOM elements
        session.tab.remove();
        session.div.remove();

        // Remove from sessions
        this.sessions.delete(sessionId);

        // Switch to another session or create new one
        if (this.sessions.size > 0) {
            const nextId = this.sessions.keys().next().value;
            this.switchToSession(nextId);
        } else {
            this.activeSession = null;
            this.createSession();
        }
    }

    clearActiveTerminal() {
        if (!this.activeSession) return;
        const session = this.sessions.get(this.activeSession);
        if (session && session.term) {
            session.term.clear();
        }
    }

    _fitActiveTerminal() {
        if (!this.activeSession) return;
        const session = this.sessions.get(this.activeSession);
        if (session && session.fitAddon) {
            try {
                session.fitAddon.fit();
            } catch (e) {
                // Ignore fit errors during rapid resize
            }
        }
    }

    // Public API for external modules to write to terminal
    addSystemMessage(text) {
        if (!this.activeSession) return;
        const session = this.sessions.get(this.activeSession);
        if (session && session.term) {
            session.term.writeln(`\x1b[36m${text}\x1b[0m`);
        }
    }
}

window.terminalPanel = new TerminalPanel();
