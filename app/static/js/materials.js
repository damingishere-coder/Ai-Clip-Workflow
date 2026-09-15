(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const status = byId('material-status');
  const form = byId('material-scan-form');
  const directory = byId('material-directory');
  const preview = byId('material-preview');
  const confirm = byId('material-confirm');
  const register = byId('material-register');
  const all = byId('material-select-all');
  let scan = null, pending = null, busy = false, offset = 0, generation = 0;
  const node = (tag, text) => { const el = document.createElement(tag); el.textContent = text; return el; };
  const size = bytes => `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
  const selected = () => Array.from(preview.querySelectorAll('[data-source]:checked'), el => el.dataset.source).sort();
  async function api(url, payload) {
    const response = await fetch(url, payload ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)} : {});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求未通过校验，请重新核对');
    return data;
  }
  function controls() {
    const keys = selected();
    register.disabled = busy || !scan || !confirm.checked || !keys.length;
    byId('material-scan').disabled = busy;
    directory.disabled = busy;
    preview.querySelectorAll('input').forEach(el => { el.disabled = busy; });
    all.checked = !!scan?.manifest.entries.length && keys.length === scan.manifest.entries.length;
    all.indeterminate = keys.length > 0 && !all.checked;
  }
  function reset() { scan = null; pending = null; preview.hidden = true; confirm.checked = false; controls(); }
  directory.addEventListener('input', reset);
  confirm.addEventListener('change', controls);
  all.addEventListener('change', () => { preview.querySelectorAll('[data-source]').forEach(el => { el.checked = all.checked; }); pending = null; controls(); });
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (busy) return;
    reset(); busy = true; controls(); status.textContent = '正在扫描当前文件夹…';
    try {
      scan = await api('/api/materials/scan', {directory: directory.value.trim()});
      const list = byId('material-preview-list'); list.replaceChildren();
      for (const item of scan.manifest.entries) {
        const card = node('article', ''); card.className = 'material-item';
        const label = node('label', ''); label.className = 'material-check';
        const check = document.createElement('input'); check.type = 'checkbox'; check.dataset.source = item.source_key; check.checked = true;
        check.addEventListener('change', () => { pending = null; controls(); });
        label.append(check, node('span', `${item.source.file_name} · ${size(item.source.stamp.size)}`));
        card.append(label); list.append(card);
      }
      byId('material-preview-heading').textContent = `${scan.manifest.entries.length} 个可登记视频`;
      byId('material-excluded-summary').textContent = `未选入 ${scan.manifest.excluded.length} 项`;
      byId('material-excluded').replaceChildren(...scan.manifest.excluded.map(item => node('p', `${item.file_name}：${item.reason}`)));
      preview.hidden = false;
      status.textContent = `已扫描 ${scan.manifest.directory.path}。请核对清单；预览在 30 分钟后失效。`;
    } catch (error) { reset(); status.textContent = error.message; }
    finally { busy = false; controls(); }
  });
  register.addEventListener('click', async () => {
    if (busy || !scan || !confirm.checked || !selected().length) return;
    const payload = {scan_id: scan.id, manifest_sha256: scan.manifest_sha256, source_keys: selected(), confirmed: true};
    const fingerprint = JSON.stringify(payload);
    if (!pending || pending.fingerprint !== fingerprint) pending = {fingerprint, request_key: crypto.randomUUID()};
    busy = true; controls();
    try {
      const result = await api('/api/materials/register', {...payload, request_key: pending.request_key});
      status.textContent = `已登记 ${result.material_ids.length} 个素材，重复项已复用。外部原片保持原样。`;
      reset(); offset = 0; await refresh();
    } catch (error) { status.textContent = `${error.message}；重试会核对同一请求，避免重复登记。`; }
    finally { busy = false; controls(); }
  });
  async function refresh() {
    const token = ++generation;
    try {
      const data = await api(`/api/materials?limit=50&offset=${offset}`);
      if (token !== generation) return;
      byId('material-total').textContent = `共 ${data.total} 个素材 · 第 ${Math.floor(offset / 50) + 1} 页`;
      const library = byId('material-library'); library.replaceChildren();
      for (const item of data.materials) {
        const card = node('article', ''); card.className = 'material-item';
        card.append(node('strong', item.file_name), node('p', item.source_path), node('p', `${size(item.size_bytes)} · 已登记文件版本，等待复制校验`));
        card.dataset.materialId = item.id; library.append(card);
      }
      if (!data.materials.length) library.append(node('p', '暂无素材。先选择文件夹并扫描预览。'));
      byId('material-previous').disabled = offset === 0;
      byId('material-next').disabled = offset + 50 >= data.total;
    } catch (error) { if (token === generation) byId('material-total').textContent = error.message; }
  }
  byId('material-refresh').addEventListener('click', refresh);
  byId('material-previous').addEventListener('click', () => { offset = Math.max(0, offset - 50); refresh(); });
  byId('material-next').addEventListener('click', () => { offset += 50; refresh(); });
  refresh(); controls();
})();
