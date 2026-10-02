(() => {
  const state = { files: [], currentFile: 'sample_bug.py', logs: [], source: '', backup: '', diff: '', busy: false, proposalId: '', proposalFile: '', lifecycle: null, safetyReport: null };
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  function switchView(view) {
    document.querySelectorAll('.view').forEach((el) => el.classList.toggle('active', el.id === `view-${view}`));
    document.querySelectorAll('[data-view]').forEach((el) => el.classList.toggle('active', el.dataset.view === view));
    history.replaceState(null, '', `#${view}`);
    if (view === 'lab') { loadLabSource(); loadPending(); }
    if (view === 'source') loadSourceList();
    if (view === 'skill') loadSkill();
    if (view === 'history') loadHistory();
    if (view === 'doctor') loadDoctor();
  }

  function addLog(text, tone = '') {
    const clean = String(text ?? '').trim();
    if (!clean) return;
    clean.split(/\r?\n/).forEach((line) => {
      if (!line.trim()) return;
      state.logs.push({ text: line, tone });
    });
    renderLogs();
  }

  function renderLogs() {
    const el = $('logs');
    el.innerHTML = state.logs.map((entry) => {
      const match = entry.text.match(/^\[([^\]]+)\]\s*(.*)$/);
      if (!match) return `<div class="log-line ${entry.tone}">${esc(entry.text)}</div>`;
      const prefix = match[1];
      const cls = prefix === 'Self-Healing' ? 'ok' : prefix === 'Voice Agent' ? 'voice' : prefix === 'Ollama' ? '' : entry.tone;
      return `<div class="log-line ${cls}"><span class="prefix">[${esc(prefix)}]</span> ${esc(match[2])}</div>`;
    }).join('');
    el.scrollTop = el.scrollHeight;
  }

  function updateApprovalButtons() {
    const approve = $('approveButton');
    const reject = $('rejectButton');
    if (!approve || !reject) return;
    approve.disabled = state.busy || !state.proposalId || state.proposalFile !== state.currentFile || Boolean(state.safetyReport?.blocked);
    reject.disabled = state.busy || !state.proposalId || state.proposalFile !== state.currentFile;
  }

  function setBusy(busy) {
    state.busy = busy;
    ['runButton','healButton','resetButton'].forEach((id) => { $(id).disabled = busy; });
    updateApprovalButtons();
  }

  async function api(path, options = {}) {
    const response = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
    const body = await response.json().catch(() => ({ ok: false, error: 'Invalid server response.' }));
    if (!response.ok && !body.error) body.error = `Request failed with HTTP ${response.status}.`;
    return body;
  }

  function setHeaderStatus(status) {
    const pill = $('headerStatus');
    if (status.ollama?.connected && status.ollama?.model_available) {
      pill.className = 'status-pill ok';
      pill.innerHTML = '<i></i> Local stack ready';
    } else if (status.ollama?.connected) {
      pill.className = 'status-pill';
      pill.innerHTML = '<i></i> Ollama connected';
    } else {
      pill.className = 'status-pill bad';
      pill.innerHTML = '<i></i> Ollama offline';
    }
  }

  function statusCard(label, value, cls = '') { return `<div class="status-card ${cls}"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`; }

  async function refreshStatus() {
    const data = await api('/api/status');
    if (!data.ok) { addLog(`[UI] ${data.error}`, 'err'); return; }
    setHeaderStatus(data.status);
    const ollama = data.status.ollama;
    const sample = data.status.sample;
    $('statusStack').innerHTML = [
      statusCard('Ollama', ollama.connected ? (ollama.model_available ? 'Connected · model ready' : 'Connected · model missing') : 'Unavailable', ollama.model_available ? 'ok' : ollama.connected ? 'warn' : 'bad'),
      statusCard('Sample', sample.healthy ? 'Healthy' : 'Failing', sample.healthy ? 'ok' : 'warn'),
      statusCard('Voice', 'Optional · CLI fallback', 'ok'),
      statusCard('Python', data.status.python, 'ok'),
    ].join('');
    await loadFiles();
    await loadLabSource();
    await loadPending();
  }

  async function loadFiles() {
    const data = await api('/api/files');
    if (!data.ok) { addLog(`[UI] ${data.error}`, 'err'); return; }
    state.files = data.files;
    $('fileSelect').innerHTML = state.files.map((file) => `<option value="${esc(file.path)}">${esc(file.path)}</option>`).join('');
    if (state.files.some((file) => file.path === state.currentFile)) $('fileSelect').value = state.currentFile;
    else if (state.files[0]) { state.currentFile = state.files[0].path; $('fileSelect').value = state.currentFile; }
  }

  function renderDiff(diffText) {
    const target = $('diffCode');
    target.innerHTML = '';
    if (!diffText) {
      target.textContent = '(no repair diff yet)';
      $('diffState').textContent = 'unchanged';
      return;
    }
    diffText.split(/\r?\n/).forEach((line) => {
      const row = document.createElement('span');
      row.className = line.startsWith('+++') || line.startsWith('---') ? 'diff-file' : line.startsWith('+') ? 'diff-add' : line.startsWith('-') ? 'diff-remove' : line.startsWith('@@') ? 'diff-hunk' : '';
      row.textContent = line;
      target.appendChild(row);
      target.appendChild(document.createTextNode('\n'));
    });
    $('diffState').textContent = 'change detected';
  }

  function renderLifecycle(data) {
    state.lifecycle = data || null;
    const container = $('lifecycleSteps');
    const current = data?.current_stage || 'idle';
    const steps = Array.isArray(data?.steps) ? data.steps : [];
    if (!container) return;
    if (!steps.length) {
      container.innerHTML = '<div class="lifecycle-empty">No repair lifecycle recorded for this target yet.</div>';
      $('lifecycleState').textContent = 'idle';
      return;
    }
    container.innerHTML = steps.map((step, index) => {
      const cls = `lifecycle-step ${esc(step.status || 'pending')}`;
      const marker = step.status === 'complete' ? '✓' : step.status === 'skipped' ? '–' : step.status === 'rejected' || step.status === 'restored' ? '!' : String(index + 1);
      return `<div class="${cls}"><span class="lifecycle-marker">${marker}</span><div><strong>${esc(step.label)}</strong><small>${esc(step.status || 'pending')}</small></div></div>`;
    }).join('');
    $('lifecycleState').textContent = data?.current_label || current;
  }

  async function loadLifecycle(file = state.currentFile) {
    const data = await api(`/api/lifecycle?file=${encodeURIComponent(file)}`);
    if (!data.ok) {
      addLog(`[UI] ${data.error}`, 'err');
      renderLifecycle(null);
      return;
    }
    renderLifecycle(data.lifecycle);
  }

  async function loadPending() {
    const data = await api('/api/pending');
    if (!data.ok || !data.pending) { showApproval(null); return; }
    if (data.pending.target === state.currentFile) {
      showApproval(data.pending);
    } else {
      showApproval(null);
    }
  }

  async function loadDiff(file) {
    const data = await api(`/api/diff?file=${encodeURIComponent(file)}`);
    if (!data.ok) {
      addLog(`[UI] ${data.error}`, 'err');
      renderDiff('');
      return;
    }
    state.diff = data.text || '';
    renderDiff(state.diff);
  }

  async function loadLabSource() {
    const file = $('fileSelect').value || state.currentFile;
    state.currentFile = file;
    const data = await api(`/api/source?file=${encodeURIComponent(file)}`);
    if (!data.ok) { addLog(`[UI] ${data.error}`, 'err'); return; }
    state.source = data.source;
    state.backup = data.backup_source;
    $('codeTitle').textContent = data.file;
    $('sourceCode').textContent = data.source;
    $('backupCode').textContent = data.backup_source || '(no .bak backup yet)';
    $('verifiedCode').textContent = data.source;
    $('codeBadge').textContent = data.healthy ? 'healthy' : 'failing';
    $('codeBadge').className = `badge ${data.healthy ? 'ok' : 'bad'}`;
    $('backupState').textContent = data.backup_exists ? 'backup present' : 'no backup';
    $('verifiedState').textContent = data.healthy ? 'verified' : 'not verified';
    await loadDiff(file);
    await loadLifecycle(file);
  }

  async function runTarget() {
    if (state.busy) return;
    setBusy(true);
    addLog(`[Self-Healing] Running ${state.currentFile}`);
    const data = await api('/api/run', { method: 'POST', body: JSON.stringify({ file: state.currentFile }) });
    setBusy(false);
    if (data.error) addLog(`[UI] ${data.error}`, 'err');
    if (data.result) {
      addLog(data.result.stdout, data.ok ? 'ok' : 'err');
      addLog(data.result.stderr, data.ok ? '' : 'err');
      $('traceTiming').textContent = `Run · ${formatDuration(data.result.duration_ms)}`;
    }
    await loadLabSource();
    await loadHistory();
  }

  function renderSafetyReport(report) {
    const panel = $('safetyGuard');
    const badge = $('safetyBadge');
    const summary = $('safetySummary');
    const findings = $('safetyFindings');
    if (!panel || !badge || !summary || !findings) return;
    state.safetyReport = report || null;
    if (!report) {
      panel.hidden = true;
      badge.textContent = 'not evaluated';
      badge.className = 'history-status';
      summary.textContent = '';
      findings.innerHTML = '';
      updateApprovalButtons();
      return;
    }
    panel.hidden = false;
    const overall = String(report.overall || 'warning');
    badge.textContent = overall;
    badge.className = `history-status ${overall}`;
    summary.textContent = report.summary || 'Safety Guard completed.';
    const items = Array.isArray(report.findings) ? report.findings : [];
    findings.innerHTML = items.length ? items.map((item) => {
      const sev = esc(item.severity || 'medium');
      const line = item.line ? `line ${item.line}` : 'candidate';
      return `<div class="safety-finding ${sev}"><div><strong>${esc(item.rule || 'review')}</strong><span>${esc(sev)} · ${esc(line)}</span></div><p>${esc(item.message || item.detail || 'Review this change.')}</p><code>${esc(item.detail || item.snippet || '')}</code></div>`;
    }).join('') : '<div class="safety-clear">No newly introduced risky operations were found.</div>';
    if (report.blocked) {
      $('approvalState').textContent = 'blocked by safety guard';
      $('approveButton').title = 'Blocked because the proposed patch introduces a high-risk operation.';
    } else if (report.overall === 'warning') {
      $('approveButton').title = 'Review the safety warnings before approving.';
    } else {
      $('approveButton').title = 'Apply the safety-checked repair.';
    }
    updateApprovalButtons();
  }

  function showApproval(proposal) {
    state.proposalId = proposal?.proposal_id || '';
    state.proposalFile = proposal?.target || '';
    const panel = $('approvalPanel');
    if (proposal?.status === 'proposed' || proposal?.status === 'pending') {
      panel.hidden = false;
      state.diff = proposal.proposal_diff || '';
      renderDiff(state.diff);
      const safetyReport = proposal.safety_report || {
        overall: 'blocked',
        blocked: true,
        summary: 'Safety Guard result is missing. Regenerate this repair proposal before approving it.',
        findings: [],
        finding_count: 0,
        high_count: 0,
        medium_count: 0,
      };
      renderSafetyReport(safetyReport);
      $('approvalState').textContent = safetyReport.blocked ? 'blocked by safety guard' : `pending · ${proposal.model || 'local model'}`;
      addLog(`[Self-Healing] Repair proposal ready for review.`);
      addLog(`[Self-Healing] Nothing has been applied yet.`);
      if (proposal.safety_report?.blocked) {
        addLog(`[Safety Guard] Approval is blocked because the proposed patch introduces a high-risk operation.`, 'err');
      } else if (proposal.safety_report?.overall === 'warning') {
        addLog(`[Safety Guard] Review warnings before approving the patch.`, 'warn');
      }
    } else {
      panel.hidden = true;
      state.proposalId = '';
      state.proposalFile = '';
      renderSafetyReport(null);
      updateApprovalButtons();
    }
  }

  async function healTarget() {
    if (state.busy) return;
    setBusy(true);
    addLog(`[Self-Healing] Analyzing ${state.currentFile} without changing the file.`);
    addLog(`[Ollama] Local model: qwen2.5-coder:1.5b`);
    const data = await api('/api/propose-heal', { method: 'POST', body: JSON.stringify({ file: state.currentFile }) });
    setBusy(false);
    if (data.error) addLog(`[UI] ${data.error}`, 'err');
    if (data.proposal) {
      if (Number.isFinite(Number(data.proposal.duration_ms))) $('traceTiming').textContent = `Proposal · ${formatDuration(data.proposal.duration_ms)}`;
      if (data.proposal.diagnostics) addLog(data.proposal.diagnostics, 'err');
      showApproval(data.proposal);
      if (data.proposal.status === 'already_healthy') addLog(`[Self-Healing] No repair needed; the target is already healthy.`,'ok');
    }
    await loadLabSource();
    await loadHistory();
  }

  async function approveTarget() {
    if (state.busy || !state.proposalId || state.proposalFile !== state.currentFile) return;
    setBusy(true);
    addLog(`[Self-Healing] Applying approved repair to ${state.currentFile}`);
    const data = await api('/api/approve-heal', { method: 'POST', body: JSON.stringify({ file: state.currentFile, proposal_id: state.proposalId }) });
    setBusy(false);
    if (data.error) addLog(`[UI] ${data.error}`, 'err');
    if (data.safety_report) renderSafetyReport(data.safety_report);
    if (data.result) {
      addLog(data.result.stdout, data.ok ? 'ok' : 'err');
      addLog(data.result.stderr, data.ok ? '' : 'err');
      if (Number.isFinite(Number(data.result.duration_ms))) $('traceTiming').textContent = `Apply · ${formatDuration(data.result.duration_ms)}`;
    }
    if (data.ok) {
      addLog(`[Self-Healing] Approved patch verified successfully.`,'ok');
      showApproval(null);
    }
    await loadLabSource();
    await loadHistory();
  }

  async function rejectTarget() {
    if (state.busy || !state.proposalId || state.proposalFile !== state.currentFile) return;
    setBusy(true);
    const data = await api('/api/reject-heal', { method: 'POST', body: JSON.stringify({ file: state.currentFile, proposal_id: state.proposalId }) });
    setBusy(false);
    if (data.error) addLog(`[UI] ${data.error}`, 'err');
    if (data.result && Number.isFinite(Number(data.result.duration_ms))) $('traceTiming').textContent = `Reject · ${formatDuration(data.result.duration_ms)}`;
    if (data.ok) {
      addLog(`[Self-Healing] Repair proposal rejected. No source changes applied.`);
      showApproval(null);
      await loadLabSource();
      await loadHistory();
    }
  }

  async function resetDemo() {
    if (state.busy) return;
    setBusy(true);
    const data = await api('/api/reset', { method: 'POST', body: JSON.stringify({ file: 'sample_bug.py' }) });
    setBusy(false);
    if (Number.isFinite(Number(data.result?.duration_ms))) $('traceTiming').textContent = `Reset · ${formatDuration(data.result.duration_ms)}`;
    if (data.result?.stdout) addLog(data.result.stdout, 'ok');
    if (data.result?.stderr) addLog(data.result.stderr, 'err');
    if (data.error) addLog(`[UI] ${data.error}`, 'err');
    state.currentFile = 'sample_bug.py';
    $('fileSelect').value = 'sample_bug.py';
    showApproval(null);
    state.diff = '';
    renderDiff('');
    await loadLabSource();
    await loadPending();
  }

  async function loadSourceList() {
    if (!state.files.length) await loadFiles();
    $('sourceList').innerHTML = state.files.map((file) => `<button class="source-item ${file.path === state.currentFile ? 'active' : ''}" data-source-file="${esc(file.path)}">${esc(file.path)}</button>`).join('');
    document.querySelectorAll('[data-source-file]').forEach((btn) => btn.addEventListener('click', () => openSource(btn.dataset.sourceFile)));
    if (!state.source) await openSource(state.currentFile);
    else $('fullSource').textContent = state.source;
  }

  async function openSource(file) {
    const data = await api(`/api/source?file=${encodeURIComponent(file)}`);
    if (!data.ok) { addLog(`[UI] ${data.error}`, 'err'); return; }
    state.currentFile = file;
    state.source = data.source;
    $('sourcePath').textContent = file;
    $('fullSource').textContent = data.source;
    document.querySelectorAll('[data-source-file]').forEach((btn) => btn.classList.toggle('active', btn.dataset.sourceFile === file));
  }

  function formatDuration(value) {
    const ms = Number(value);
    return Number.isFinite(ms) ? `${ms} ms` : '—';
  }

  function historyStatusClass(status) {
    if (['repaired', 'already_healthy', 'approved_repair'].includes(status)) return 'ok';
    if (['failed_and_restored', 'approved_repair_failed'].includes(status)) return 'bad';
    if (['rejected_by_user', 'rejected_target'].includes(status)) return 'warn';
    return 'warn';
  }

  function historyStatusLabel(status) {
    return String(status || 'unknown').replaceAll('_', ' ');
  }

  function historyEventLabel(event) {
    const labels = {
      repair_proposed: 'Repair proposed',
      repair_approved: 'Repair approved and verified',
      repair_approved_failed: 'Approved repair failed; original restored',
      repair_rejected: 'Repair rejected',
      repair: 'Automatic repair completed',
    };
    return labels[event] || historyStatusLabel(event);
  }

  function historyTimelineEvent(event) {
    const time = event.timestamp ? new Date(event.timestamp).toLocaleString() : 'unknown time';
    const duration = formatDuration(event.duration_ms);
    const status = String(event.status || 'unknown');
    const cls = historyStatusClass(status);
    return `<div class="history-event"><span class="history-event-dot ${cls}"></span><div class="history-event-body"><div class="history-event-top"><strong>${esc(historyEventLabel(String(event.event || status)))}</strong><span class="mono history-event-time">${esc(time)}</span></div><div class="history-event-meta"><span>${esc(status.replaceAll('_', ' '))}</span><span>${esc(duration)}</span></div></div></div>`;
  }

  function historyRepairCard(repair) {
    const status = String(repair.status || 'unknown');
    const cls = historyStatusClass(status);
    const label = historyStatusLabel(status);
    const time = repair.timestamp ? new Date(repair.timestamp).toLocaleString() : 'unknown time';
    const events = Array.isArray(repair.events) ? repair.events : [];
    const error = repair.error ? `<div class="history-error">${esc(repair.error)}</div>` : '';
    const eventCount = events.length;
    const timeline = events.map(historyTimelineEvent).join('');
    return `<article class="history-card repair-card"><div class="history-card-head"><div><span class="history-target">${esc(repair.target || 'unknown')}</span><span class="history-status ${cls}">${esc(label)}</span></div><span class="mono history-time">${esc(time)}</span></div><div class="history-meta"><span><b>Model</b>${esc(repair.model || '—')}</span><span><b>Attempts</b>${esc(repair.attempts ?? '—')}</span><span><b>Processing</b>${esc(formatDuration(repair.duration_ms))}</span><span><b>Audit events</b>${esc(eventCount)}</span></div>${error}<details class="history-events"><summary>View audit trail <span>${esc(String(eventCount))} event${eventCount === 1 ? '' : 's'}</span></summary><div class="history-timeline">${timeline || '<div class="history-no-events">No detailed events recorded.</div>'}</div></details></article>`;
  }

  async function loadHistory() {
    const data = await api('/api/history?limit=50');
    if (!data.ok) { addLog(`[UI] ${data.error}`, 'err'); return; }
    const repairs = Array.isArray(data.repairs) ? data.repairs : [];
    const eventCount = Number.isFinite(Number(data.event_count)) ? Number(data.event_count) : 0;
    $('historySummary').innerHTML = [
      statusCard('Recorded repairs', String(Number(data.repair_count ?? repairs.length)), 'ok'),
      statusCard('Audit events', String(eventCount), 'ok'),
      statusCard('Audit storage', '.voice-healer/history.jsonl', 'ok'),
      statusCard('Source in log', 'Hashes only', 'ok'),
    ].join('');
    $('historyList').innerHTML = repairs.length ? repairs.map(historyRepairCard).join('') : '<div class="panel empty-history">No repairs recorded yet. Run or heal a Python target from the Lab.</div>';
  }

  function renderDoctor(report) {
    const overall = report.overall || 'warn';
    const banner = $('doctorBanner');
    banner.className = `doctor-banner ${overall}`;
    banner.textContent = overall === 'ok' ? 'All required local checks passed.' : overall === 'warn' ? 'Core checks passed; one or more optional/setup checks need attention.' : 'One or more required checks failed.';
    $('doctorChecks').innerHTML = (report.checks || []).map((item) => `
      <article class="doctor-card ${esc(item.status)}">
        <div class="doctor-card-head"><strong>${esc(item.name)}</strong><span class="history-status ${esc(item.status)}">${esc(item.status)}</span></div>
        <div class="doctor-summary">${esc(item.summary)}</div>
        <div class="doctor-detail">${esc(item.detail)}</div>
      </article>`).join('');
  }

  async function loadDoctor() {
    const data = await api('/api/doctor');
    if (!data.ok) { addLog(`[UI] ${data.error}`, 'err'); return; }
    renderDoctor(data.doctor);
  }

  async function loadSkill() {
    const data = await api('/api/skill');
    $('skillSource').textContent = data.ok ? data.source : `Unable to load SKILL.md: ${data.error || 'unknown error'}`;
  }

  function heroReplay() {
    const lines = [
      ['[Self-Healing]', 'Running sample_bug.py', 'p'],
      ['[Self-Healing]', 'TypeError detected', 'dim'],
      ['[Self-Healing]', 'Backup created', 'dim'],
      ['[Ollama]', 'qwen2.5-coder:1.5b · localhost', ''],
      ['[Self-Healing]', 'Patch validated · rerunning', ''],
      ['[Self-Healing]', 'Verification passed', 'ok'],
    ];
    const target = $('heroTerminal');
    target.setAttribute('aria-label', 'Illustrative demo trace; not a live execution trace.');
    lines.forEach((line, index) => setTimeout(() => {
      target.insertAdjacentHTML('beforeend', `<div class="term-line"><span class="${line[2]}">${esc(line[0])}</span> ${esc(line[1])}</div>`);
    }, index * 520));
  }

  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-view]');
    if (button) switchView(button.dataset.view);
  });
  $('fileSelect').addEventListener('change', async () => { state.currentFile = $('fileSelect').value; await loadLabSource(); });
  $('runButton').addEventListener('click', runTarget);
  $('healButton').addEventListener('click', healTarget);
  $('approveButton').addEventListener('click', approveTarget);
  $('rejectButton').addEventListener('click', rejectTarget);
  $('resetButton').addEventListener('click', resetDemo);
  $('historyRefresh').addEventListener('click', loadHistory);
  $('doctorRefresh').addEventListener('click', loadDoctor);

  async function init() {
    const initial = location.hash.replace('#', '') || 'overview';
    switchView(['overview','lab','source','skill','history','doctor'].includes(initial) ? initial : 'overview');
    state.logs = [{ text: '[Voice Agent] UI connected to the local harness.', tone: '' }, { text: '[Voice Agent] Use Run, Heal, or Reset demo.', tone: '' }];
    renderLogs();
    heroReplay();
    await refreshStatus();
  }

  init();
})();
