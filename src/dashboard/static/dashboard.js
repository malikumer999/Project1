const formatDuration = (seconds) => {
    const total = Math.floor(seconds);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(h)}:${pad(m)}:${pad(s)}`;
};

const formatTimestamp = (iso) => {
    if (!iso) return '—';
    try {
        const d = new Date(iso);
        return d.toLocaleString();
    } catch {
        return iso;
    }
};

const setText = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
};

const bump = (id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.add('bump');
    setTimeout(() => el.classList.remove('bump'), 600);
};

const previousValues = {};

const updateIfChanged = (id, value) => {
    if (previousValues[id] !== value) {
        previousValues[id] = value;
        setText(id, value);
        bump(id);
    }
};

const escapeHtml = (s) => {
    if (s == null) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
};

const renderChannelTable = (rows) => {
    const tbody = document.querySelector('#channel-table tbody');
    if (!rows || !rows.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="empty">No channels yet. Create one in Discord to start routing jobs.</td></tr>';
        return;
    }
    tbody.innerHTML = rows.map(r => `
        <tr>
            <td>${escapeHtml(r.label)}</td>
            <td>${r.channel_id ? '<code>' + escapeHtml(r.channel_id) + '</code>' : '<span class="subtle">—</span>'}</td>
            <td><strong>${r.total}</strong></td>
            <td>${r.posted}</td>
        </tr>
    `).join('');
};

const renderLiveChannels = (rows) => {
    const tbody = document.querySelector('#channels-live-table tbody');
    if (!rows || !rows.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="empty">No channels yet.</td></tr>';
        return;
    }
    tbody.innerHTML = rows.map(r => {
        const status = r.active
            ? '<span style="color:var(--good)">■ Active</span>'
            : '<span style="color:var(--bad)">□ Deleted</span>';
        return `
        <tr${r.active ? '' : ' style="opacity:0.55"'}>
            <td>${escapeHtml(r.source_query)}</td>
            <td>${escapeHtml(r.channel_name || '—')}</td>
            <td>${r.channel_id ? '<code>' + escapeHtml(r.channel_id) + '</code>' : '—'}</td>
            <td>${status}</td>
            <td><span class="subtle">${escapeHtml(r.updated_at || '—')}</span></td>
        </tr>`;
    }).join('');
};

const renderRecentTable = (rows) => {
    const tbody = document.querySelector('#recent-table tbody');
    if (!rows || !rows.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="empty">No jobs posted yet.</td></tr>';
        return;
    }
    tbody.innerHTML = rows.map(r => `
        <tr>
            <td>${escapeHtml(r.title)}</td>
            <td>${escapeHtml(r.source_query || '—')}</td>
            <td>${escapeHtml(r.posted_at || '—')}</td>
            <td>${escapeHtml(r.first_seen || '—')}</td>
        </tr>
    `).join('');
};

let clientStartTime = Date.now();

const tickTimer = () => {
    const elapsedSeconds = (Date.now() - clientStartTime) / 1000;
    setText('uptime', formatDuration(elapsedSeconds));
};

const fetchStats = async () => {
    try {
        const response = await fetch('/api/stats');
        if (!response.ok) throw new Error('HTTP ' + response.status);
        const data = await response.json();
        document.getElementById('status-dot').classList.remove('offline');
        setText('status-text', 'Live');
        setText('started-at', 'Started: ' + formatTimestamp(data.started_at));
        updateIfChanged('channel-count', data.channel_count);
        updateIfChanged('total-jobs', data.total_jobs);
        updateIfChanged('posted-jobs', data.posted_jobs);
        updateIfChanged('unposted-jobs', data.unposted_jobs);
        updateIfChanged('private-jobs', data.private_jobs);
        renderChannelTable(data.per_channel);
        renderRecentTable(data.recent_jobs);
    } catch (e) {
        document.getElementById('status-dot').classList.add('offline');
        setText('status-text', 'Offline (' + e.message + ')');
    }
};

const fetchChannels = async () => {
    try {
        const response = await fetch('/api/channels');
        if (!response.ok) return;
        const data = await response.json();
        renderLiveChannels(data.channels);
    } catch (e) {
        // ignore
    }
};

const fetchStop = async () => {
    try {
        const response = await fetch('/api/stop');
        if (response.status === 200) {
            const data = await response.json();
            setText('stop-uptime', data.uptime || '--');
            setText('stop-total', data.total_jobs || 0);
            setText('stop-posted', data.posted_jobs || 0);
            setText('stop-channels', data.channel_count || 0);
            document.getElementById('stop-overlay').classList.remove('hidden');
        }
    } catch (e) {
        // ignore
    }
};

setInterval(tickTimer, 1000);
setInterval(fetchStats, 5000);
setInterval(fetchChannels, 3000);
setInterval(fetchStop, 1000);
setInterval(fetchControl, 5000);

fetchStats();
fetchChannels();
fetchStop();
fetchControl();
tickTimer();

const sendControl = async (action) => {
    const btn = document.getElementById('btn-toggle');
    btn.disabled = true;
    setText('status-text', action === 'start' ? 'Starting…' : 'Stopping…');
    try {
        const response = await fetch('/api/control', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action }),
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.error || 'HTTP ' + response.status);
        }
        const data = await response.json();
        if (action === 'start') {
            document.getElementById('stop-overlay').classList.add('hidden');
            clientStartTime = Date.now();
            tickTimer();
            setText('status-text', 'Started');
        } else {
            setText('stop-uptime', (data.final && data.final.uptime) || '--');
            setText('stop-total', (data.final && data.final.total_jobs) || 0);
            setText('stop-posted', (data.final && data.final.posted_jobs) || 0);
            setText('stop-channels', (data.final && data.final.channel_count) || 0);
            setText('status-text', 'Stopped');
            setTimeout(() => {
                document.getElementById('stop-overlay').classList.remove('hidden');
            }, 300);
        }
    } catch (e) {
        setText('status-text', 'Action failed: ' + e.message);
    } finally {
        setTimeout(() => { btn.disabled = false; }, 1500);
    }
};

const fetchControl = async () => {
    try {
        const response = await fetch('/api/control');
        if (!response.ok) return;
        const data = await response.json();
        setText('child-pid', data.child_pid ? `(PID ${data.child_pid})` : '');
        const btn = document.getElementById('btn-toggle');
        const isRunning = data.running;
        const stopped = data.stopped;
        if (isRunning) {
            btn.classList.remove('stop');
            btn.classList.add('start');
            btn.textContent = '■ Stop';
            btn.dataset.state = 'running';
        } else {
            btn.classList.remove('start');
            btn.classList.add('stop');
            btn.textContent = stopped ? '↻ Restart' : '▶ Start';
            btn.dataset.state = stopped ? 'restart' : 'stopped';
        }
        if (data.final && stopped) {
            setText('stop-uptime', data.final.uptime || '--');
            setText('stop-total', data.final.total_jobs || 0);
            setText('stop-posted', data.final.posted_jobs || 0);
            setText('stop-channels', data.final.channel_count || 0);
        }
    } catch (e) {
        // ignore
    }
};

document.getElementById('btn-toggle').addEventListener('click', () => {
    const btn = document.getElementById('btn-toggle');
    const state = btn.dataset.state;
    if (state === 'running') {
        sendControl('stop');
    } else {
        sendControl('start');
    }
});
