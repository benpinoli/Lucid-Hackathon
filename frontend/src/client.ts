import { applyEvents, parsePlan, parseSnapshot, type Plan, type Snapshot } from './protocol';
import type { Credential } from './voice';

export class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

type Hooks = {
  state: (state: Snapshot) => void; plan: (plan: Plan) => void;
  status: (message: string, ready: boolean) => void; error: (message: string) => void;
};

export class LessonClient {
  private token = '';
  private socket?: WebSocket;
  private state?: Snapshot;
  private stopped = true;
  private reconnectTimer?: ReturnType<typeof setTimeout>;
  private heartbeat?: ReturnType<typeof setInterval>;
  private deadline?: ReturnType<typeof setTimeout>;
  private attempt = 0;
  private busy = false;
  private awaiting: 'turn' | 'snapshot' | undefined;
  private generation = 0;
  /** Backend has a realtime voice provider configured. */
  voiceAvailable = false;
  authorAvailable = false;
  constructor(private hooks: Hooks) {}

  /** Authenticated JSON call; errors carry the status and the backend's detail when it sends one. */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  async api<T = any>(method: string, path: string, body?: object): Promise<T> {
    const response = await fetch(path, {
      method, cache: 'no-store', signal: AbortSignal.timeout(30000), body: body ? JSON.stringify(body) : undefined,
      headers: { Authorization: `Bearer ${this.token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    });
    if (!response.ok) {
      const detail = await response.json().then(data => typeof data?.detail === 'string' ? ` ${data.detail}` : '', () => '');
      throw new ApiError(`Backend returned ${response.status}.${detail || (response.status === 401 ? ' Check the private token.' : ' Check backend logs and setup.')}`, response.status);
    }
    return response.status === 204 ? undefined as T : response.json();
  }
  async connect(token: string) {
    if (!token.trim()) throw new Error('Enter the private demo token.');
    await this.disconnect();
    const generation = this.generation;
    this.token = token.trim(); this.stopped = false;
    this.hooks.status('Creating lesson…', false);
    try {
      const health = await this.api('GET', '/api/health');
      if (health.scene_schema_version !== 1) throw new Error('Backend scene version is unsupported.');
      this.voiceAvailable = health.voice === 'realtime';
      this.authorAvailable = health.author === 'claude';
      const created = parseSnapshot(await this.api('POST', '/api/lessons'));
      if (generation !== this.generation) return;
      this.state = created;
      this.hooks.state(created); this.open();
    } catch (error) {
      if (generation === this.generation) { this.stopped = true; this.hooks.status('Disconnected', false); this.token = ''; }
      throw error;
    }
  }
  /** Short-lived provider credential; the private backend token never leaves this client. */
  async voiceCredential(): Promise<Credential> {
    if (this.stopped) throw new Error('Connect before starting voice.');
    const credential = await this.api('POST', '/api/voice/sessions');
    if (typeof credential?.client_secret !== 'string' || !/^https:\/\//.test(credential?.sdp_url)) throw new Error('Backend returned an invalid voice credential.');
    return credential;
  }
  private send(data: object) {
    if (this.socket?.readyState !== WebSocket.OPEN) throw new Error('Connection is not ready.');
    this.socket.send(JSON.stringify(data));
  }
  private open() {
    if (this.stopped || !this.state) return;
    this.hooks.status(this.attempt ? 'Reconnecting…' : 'Authenticating…', false);
    const url = new URL('/ws/scenes', location.href);
    url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    url.searchParams.set('lesson_id', this.state.lesson_id);
    const socket = this.socket = new WebSocket(url);
    let ready = false;
    // Bound an unresponsive handshake and half-open sockets.
    this.deadline = setTimeout(() => socket.close(), 10000);
    socket.onopen = () => socket.send(JSON.stringify({ token: this.token }));
    socket.onmessage = ({ data }) => {
      if (socket !== this.socket || this.stopped) return;
      try {
        if (typeof data !== 'string' || new TextEncoder().encode(data).length > 262144) throw new Error('Oversized server message');
        const message = JSON.parse(data);
        if (message.type === 'scene.snapshot') {
          const next = parseSnapshot(message.snapshot);
          if (next.lesson_id !== this.state?.lesson_id || next.seq < this.state.seq || next.turn_epoch < this.state.turn_epoch) throw new Error('Invalid snapshot scope or regression');
          const changed = JSON.stringify(next) !== JSON.stringify(this.state);
          this.state = next;
          if (changed || !ready) this.hooks.state(next);
          ready = true; this.attempt = 0;
          if (this.awaiting !== 'turn') {
            this.busy = false; this.awaiting = undefined;
            clearTimeout(this.deadline); this.hooks.status('Connected', true);
          }
          // ACK is receipt only. Its snapshot response is intentionally NOT acknowledged.
        } else if (message.type === 'lesson.turn' && this.state) {
          const plan = parsePlan(message.plan);
          if (!Array.isArray(message.events)) throw new Error('Missing events');
          const next = applyEvents(this.state, message.events, message.turn_epoch);
          this.state = next; this.busy = false; this.awaiting = undefined; clearTimeout(this.deadline);
          this.hooks.state(next); this.hooks.plan(plan);
          this.hooks.status('Connected', true);
          this.send({ type: 'scene.ack', seq: next.seq });
        } else if (message.type === 'error') {
          this.hooks.error(typeof message.detail === 'string' ? message.detail : 'Command was invalid or stale. Refreshing scene; your turn was not retried.');
          this.recover();
        } else throw new Error('Unknown server packet');
      } catch {
        this.hooks.error('Scene validation failed. Reconnecting to a fresh snapshot.');
        socket.close(1000);
      }
    };
    socket.onclose = ({ code }) => {
      if (socket !== this.socket) return;
      clearInterval(this.heartbeat); clearTimeout(this.deadline); this.busy = false; this.awaiting = undefined;
      if (this.stopped) return;
      if (code === 1008 || this.attempt >= 5) {
        this.stopped = true;
        this.hooks.status('Disconnected', false);
        this.hooks.error('Connection rejected or retries exhausted. Check token, allowed Origin, and whether the backend restarted. Reconnect to create a new lesson.');
        return;
      }
      this.hooks.status('Connection lost · retrying', false);
      this.reconnectTimer = setTimeout(() => this.open(), Math.min(1000 * 2 ** this.attempt++, 15000));
    };
    socket.onerror = () => { /* onclose owns bounded recovery */ };
    this.heartbeat = setInterval(() => {
      if (ready && !this.busy && socket.readyState === WebSocket.OPEN) {
        this.send({ type: 'scene.snapshot_request' });
        clearTimeout(this.deadline);
        this.deadline = setTimeout(() => socket.close(), 10000);
      }
    }, 20000);
  }
  private recover() {
    this.busy = true; this.awaiting = 'snapshot'; this.hooks.status('Refreshing scene…', false);
    this.send({ type: 'scene.snapshot_request' });
    clearTimeout(this.deadline);
    this.deadline = setTimeout(() => this.socket?.close(), 10000);
  }
  /** `goal` turns teach a route step (concept) or show the route map (route_to). */
  command(type: 'turn' | 'interrupt' | 'tutor', text?: string, extra?: { concept?: string; route_to?: string; action?: string }) {
    if (!this.state || this.busy || this.stopped) throw new Error('Wait for the current command or reconnect.');
    if (type === 'turn' && (!text?.trim() || text.length > 2000)) throw new Error('Enter between 1 and 2,000 characters.');
    if (type === 'tutor' && (text ?? '').length > 2000) throw new Error('Keep it under 2,000 characters.');
    this.send({ type, expected_epoch: this.state.turn_epoch, ...(type === 'interrupt' ? {} : { text: (text ?? '').trim(), ...extra }) });
    this.busy = true; this.awaiting = type === 'interrupt' ? 'snapshot' : 'turn'; this.hooks.status(type === 'interrupt' ? 'Interrupting…' : 'Thinking…', false);
    // Covers a planner model call plus one corrective retry on the backend.
    this.deadline = setTimeout(() => { this.hooks.error('Response timed out. Restoring snapshot; the command will not be replayed.'); this.socket?.close(); }, 30000);
  }
  async disconnect() {
    ++this.generation; this.stopped = true;
    clearTimeout(this.reconnectTimer); clearInterval(this.heartbeat); clearTimeout(this.deadline);
    this.socket?.close(); this.socket = undefined;
    const previous = this.state; this.state = undefined;
    if (previous && this.token) {
      try { await this.api('DELETE', `/api/lessons/${previous.lesson_id}`); }
      catch { this.hooks.error('Could not release the previous lesson. Restart the local backend if capacity is exhausted.'); }
    }
    this.token = ''; this.busy = false; this.awaiting = undefined; this.hooks.status('Disconnected', false);
  }
}
