import { api } from './api.js';
import { esc, icon, label } from './components.js';
export const FILE_ROLES = [
  { id: 'reports', name: 'Resident reports', purpose: 'Report normalization, LLM structuring and problem consolidation.' },
  { id: 'assets', name: 'Assets & locations', purpose: 'Location resolution and asset / public-facility context.' },
  { id: 'jobs_history', name: 'Recent jobs history', purpose: 'Related-work context and possible recurrence review.' },
];
export const canRun = batch => Boolean(batch && ['draft', 'failed'].includes(batch.status) && FILE_ROLES.every(role => batch.files.some(file => file.role === role.id && file.status === 'ready')));
export class ImportWorkspace {
  constructor(root, onPublished) {
    this.root = root; this.onPublished = onPublished;
    this.items = []; this.batch = null; this.connected = false; this.busy = false;
    this.selectedFiles = new Map(); this.keys = new Map(); this.error = ''; this.lastPublishedRevision = '';
    root.addEventListener('click', event => {
      const button = event.target.closest('[data-import-action]');
      if (!button || button.disabled) return;
      const action = button.dataset.importAction;
      if (action === 'refresh') this.connect();
      if (action === 'new') this.create();
      if (action === 'upload') this.upload(button.dataset.role);
      if (action === 'run') this.run();
    });
    root.addEventListener('change', event => {
      if (event.target.matches('[data-file-role]')) {
        const file = event.target.files[0];
        if (file) this.selectedFiles.set(event.target.dataset.fileRole, file);
        this.error = ''; this.render();
      }
      if (event.target.id === 'import-select') this.select(event.target.value);
    });
    this.render();
    setInterval(() => {
      if (!document.hidden && !this.busy && this.connected && this.batch &&
          (['queued', 'processing'].includes(this.batch.status) || this.batch.files.some(f => f.status === 'validating'))) this.refreshBatch();
    }, 2500);
  }
  key(operation) {
    if (!this.keys.has(operation)) this.keys.set(operation, crypto.randomUUID());
    return this.keys.get(operation);
  }
  async connect() {
    if (this.busy) return;
    this.busy = true; this.error = ''; this.render();
    try {
      const response = await api.imports(); this.items = response.items; this.connected = true;
      const selected = this.batch?.id || this.items[0]?.id;
      this.batch = selected ? await api.importDetail(selected) : null;
    } catch (error) { this.connected = false; this.error = error.message; }
    finally { this.busy = false; this.render(); }
  }
  async select(id) {
    if (this.busy) return;
    this.busy = true; this.selectedFiles.clear(); this.error = ''; this.render();
    try { this.batch = await api.importDetail(id); }
    catch (error) { this.error = error.message; }
    finally { this.busy = false; this.render(); }
  }
  async create() {
    if (this.busy || !this.connected) return;
    this.busy = true; this.error = ''; this.render();
    try {
      const batch = await api.createImport(this.key('create'));
      this.batch = batch; this.items = [batch, ...this.items.filter(b => b.id !== batch.id)];
      this.keys.delete('create'); this.selectedFiles.clear();
    } catch (error) { this.error = error.message; }
    finally { this.busy = false; this.render(); }
  }
  async upload(role) {
    const file = this.selectedFiles.get(role);
    if (this.busy || !this.connected || !this.batch || !file) return;
    if (!file.name.toLowerCase().endsWith('.csv') || file.size === 0) {
      this.error = 'Choose a non-empty CSV file. The backend will validate its columns and content.'; this.render(); return;
    }
    const operation = `upload:${this.batch.id}:${role}:${file.name}:${file.size}:${file.lastModified}:${this.batch.revision}`;
    this.busy = true; this.error = ''; this.render();
    try {
      this.batch = await api.upload(this.batch.id, role, file, this.batch.revision, this.key(operation));
      this.keys.delete(operation); this.selectedFiles.delete(role);
    } catch (error) { this.error = error.message; }
    finally { this.busy = false; this.render(); }
  }
  async run() {
    if (this.busy || !this.connected || !canRun(this.batch)) return;
    const operation = `run:${this.batch.id}:${this.batch.revision}`;
    this.busy = true; this.error = ''; this.render();
    try {
      this.batch = await api.runImport(this.batch.id, this.batch.revision, this.key(operation));
      this.keys.delete(operation);
    } catch (error) { this.error = error.message; }
    finally { this.busy = false; this.render(); }
  }
  async refreshBatch() {
    if (this.busy || !this.batch) return;
    this.busy = true;
    try {
      this.batch = await api.importDetail(this.batch.id); this.error = '';
      const marker = `${this.batch.id}:${this.batch.revision}`;
      if (this.batch.status === 'completed' && marker !== this.lastPublishedRevision) {
        this.lastPublishedRevision = marker; this.onPublished();
      }
    } catch (error) { this.error = error.message; this.connected = false; }
    finally { this.busy = false; this.render(); }
  }
  render() {
    const batch = this.batch;
    const locked = !this.connected || this.busy;
    const editable = batch?.status === 'draft';
    const wasOpen = this.root.querySelector('details')?.open ?? true;
    this.root.innerHTML = `<details class="import-panel" ${wasOpen ? 'open' : ''}>
      <summary><span>${icon('reports',19)} Import council exports</span><span class="import-summary-state">${this.busy ? 'Contacting backend…' : !this.connected ? 'Backend not connected' : batch ? esc(label(batch.status)) : 'No import batches'}</span></summary>
      <div class="import-body"><div class="import-intro"><div><h2>Three sources. One operational picture.</h2><p>Upload each export, then run the backend pipeline. Problems appear only after the backend publishes validated results.</p></div><div class="import-toolbar"><button class="button secondary" data-import-action="refresh" ${this.busy ? 'disabled' : ''}>Reconnect / refresh</button><button class="button primary" data-import-action="new" ${locked ? 'disabled' : ''}>New import batch</button></div></div>
      ${!this.connected ? '<div class="backend-notice"><strong>Waiting for the backend</strong><p>This frontend has no bundled records or local analysis. Connect the Python API to upload files and generate problems.</p></div>' : ''}
      ${this.items.length ? `<label class="batch-label">Import batch<select id="import-select" ${locked ? 'disabled' : ''}>${this.items.map(item => `<option value="${esc(item.id)}" ${item.id === batch?.id ? 'selected' : ''}>${esc(item.id)} · ${esc(label(item.id === batch?.id ? batch.status : item.status))}</option>`).join('')}</select></label>` : '<p class="muted">Create an import batch to attach the three CSV files. All three are required before analysis can start.</p>'}
      <div class="upload-grid">${FILE_ROLES.map(role => {
        const uploaded = batch?.files.find(file => file.role === role.id);
        const local = this.selectedFiles.get(role.id);
        return `<section class="upload-card"><span class="eyebrow">${esc(role.id.replace('_',' / '))}</span><h3>${esc(role.name)}</h3><p>${esc(role.purpose)}</p><label>Choose CSV<input data-file-role="${role.id}" type="file" accept=".csv,text/csv" ${locked || !editable ? 'disabled' : ''}></label>${local ? `<p class="selected-file">Selected: ${esc(local.name)} · ${Math.ceil(local.size / 1024)} KB</p>` : ''}<div class="file-state">${uploaded ? `<strong>${esc(label(uploaded.status))}</strong> · ${esc(uploaded.name)}${uploaded.rowCount === null ? '' : ` · ${uploaded.rowCount} rows`}` : 'No file uploaded'}</div>${uploaded?.error ? `<p class="inline-error">${esc(uploaded.error.message)}</p>` : ''}<button class="button secondary" data-import-action="upload" data-role="${role.id}" ${locked || !editable || !local ? 'disabled' : ''}>${uploaded ? 'Replace upload' : 'Upload CSV'}</button></section>`;
      }).join('')}</div>
      ${batch ? `<div class="run-toolbar"><div><strong>${esc(label(batch.status))}</strong><p>${batch.status === 'completed' ? 'Backend publication completed. Results are available in the problem queue.' : batch.status === 'failed' ? 'Analysis failed. Review the backend error before retrying, or create a new batch with corrected files.' : 'The backend validates files and determines progress. No results are generated in this browser.'}</p></div><button class="button primary" data-import-action="run" ${locked || !canRun(batch) ? 'disabled' : ''}>${batch.status === 'failed' ? 'Retry analysis' : 'Run analysis'} ${icon('arrow',16)}</button></div>
      ${batch.stages.length ? `<ol class="pipeline">${batch.stages.map(stage => `<li class="stage-${esc(stage.status)}"><div><strong>${esc(stage.label)}</strong><span>${esc(label(stage.status))}${stage.progress === null ? '' : ` · ${Math.round(stage.progress * 100)}%`}</span></div>${stage.message ? `<p>${esc(stage.message)}</p>` : ''}</li>`).join('')}</ol>` : '<p class="muted">Processing stages will be reported here by the backend.</p>'}
      ${batch.error ? `<div class="inline-error" role="alert">${esc(batch.error.message)}</div>` : ''}
      ${batch.warnings.length ? `<details class="import-warnings"><summary>Import warnings (${batch.warnings.length})</summary><ul>${batch.warnings.map(w=>`<li>${esc(w.message)}</li>`).join('')}</ul></details>` : ''}` : ''}
      ${this.error ? `<div class="inline-error" role="alert">${esc(this.error)}</div>` : ''}
      </div></details>`;
  }
}
