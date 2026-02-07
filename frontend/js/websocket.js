/**
 * WebSocket Client — Connects to backend, handles reconnection, dispatches events.
 */

class SwarmWebSocket {
    constructor() {
        this.ws = null;
        this.connected = false;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 20;
        this.reconnectDelay = 2000;
        this.listeners = {};
        this.pingInterval = null;
    }

    connect() {
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${protocol}//${location.host}/ws`;

        try {
            this.ws = new WebSocket(url);
        } catch (e) {
            console.error('WebSocket creation failed:', e);
            this._scheduleReconnect();
            return;
        }

        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.connected = true;
            this.reconnectAttempts = 0;
            this._updateIndicator(true);
            this._startPing();
            this.emit('connected');
        };

        this.ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (msg.type === 'ping') {
                    this.ws.send(JSON.stringify({ type: 'pong' }));
                    return;
                }
                if (msg.type === 'pong') return;

                // Dispatch to appropriate handler based on message type
                const type = msg.type || msg.data?.type;
                this.emit('message', msg);

                if (type) {
                    this.emit(type, msg.data || msg);
                }
            } catch (e) {
                console.error('WebSocket message parse error:', e);
            }
        };

        this.ws.onclose = () => {
            console.log('WebSocket disconnected');
            this.connected = false;
            this._updateIndicator(false);
            this._stopPing();
            this.emit('disconnected');
            this._scheduleReconnect();
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
        };
    }

    send(data) {
        if (this.ws && this.connected) {
            this.ws.send(JSON.stringify(data));
        }
    }

    on(event, callback) {
        if (!this.listeners[event]) this.listeners[event] = [];
        this.listeners[event].push(callback);
    }

    off(event, callback) {
        if (this.listeners[event]) {
            this.listeners[event] = this.listeners[event].filter(cb => cb !== callback);
        }
    }

    emit(event, data) {
        if (this.listeners[event]) {
            this.listeners[event].forEach(cb => {
                try { cb(data); } catch (e) { console.error('Listener error:', e); }
            });
        }
    }

    _startPing() {
        this._stopPing();
        this.pingInterval = setInterval(() => {
            if (this.connected) {
                this.send({ type: 'ping' });
            }
        }, 25000);
    }

    _stopPing() {
        if (this.pingInterval) {
            clearInterval(this.pingInterval);
            this.pingInterval = null;
        }
    }

    _scheduleReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.error('Max reconnect attempts reached');
            return;
        }
        this.reconnectAttempts++;
        const delay = this.reconnectDelay * Math.min(this.reconnectAttempts, 5);
        console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
        setTimeout(() => this.connect(), delay);
    }

    _updateIndicator(connected) {
        const el = document.getElementById('ws-indicator');
        if (el) {
            el.classList.toggle('connected', connected);
            el.title = connected ? 'Connected' : 'Disconnected';
        }
    }
}

// Global instance
window.swarmWS = new SwarmWebSocket();
