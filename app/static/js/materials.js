(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const status = byId('material-status');
  const form = byId('material-scan-form');
  const directory = byId('material-directory');
  const recursive = byId('material-recursive');
  const chooser = byId('material-directory-dialog');
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
    recursive.disabled = busy;
    byId('material-choose-directory').disabled = busy;
    preview.querySelectorAll('input').forEach(el => { el.disabled = busy; });
    all.checked = !!scan?.manifest.entries.length && keys.length === scan.manifest.entries.length;
    all.indeterminate = keys.length > 0 && !all.checked;
  }
  function reset() { scan = null; pending = null; preview.hidden = true; confirm.checked = false; controls(); }
  directory.addEventListener('input', reset);
  recursive.addEventListener('change', reset);
  let browseGeneration = 0, browseLocation = null;
  async function browse(path) {
    const token = ++browseGeneration;
    browseLocation = null;
    byId('material-directory-use').disabled = true;
    byId('material-directory-parent').disabled = true;
    byId('material-directory-list').replaceChildren();
    byId('material-directory-current').textContent = '';
    byId('material-directory-status').textContent = '正在读取文件夹…';
    try {
      const data = await api('/api/materials/directories', {directory: path});
      if (token !== browseGeneration || !chooser.open) return;
      browseLocation = data;
      byId('material-browse-path').value = data.directory;
      byId('material-directory-current').textContent = data.directory || '本机磁盘';
      byId('material-directory-use').disabled = !data.directory;
      byId('material-directory-parent').disabled = !data.directory;
      for (const folder of data.folders) {
        const button = node('button', `打开 ${folder.name}`);
        button.type = 'button'; button.className = 'secondary-button';
        button.addEventListener('click', () => browse(folder.path));
        byId('material-directory-list').append(button);
      }
      byId('material-directory-status').textContent = data.truncated ? '目录项较多，只显示部分文件夹。可在上方输入更具体的路径。' :
        data.folders.length ? '点击文件夹进入，或选择当前文件夹。' : data.directory ? '此处没有子文件夹，可以直接选择当前文件夹。' : '未找到可访问磁盘，请在上方输入完整路径。';
    } catch (error) { if (token === browseGeneration && chooser.open) byId('material-directory-status').textContent = error.message; }
  }
  byId('material-choose-directory').addEventListener('click', () => {
    if (busy) return;
    chooser.showModal();
    byId('material-browse-path').value = directory.value.trim();
    browse(directory.value.trim());
  });
  byId('material-directory-close').addEventListener('click', () => chooser.close());
  chooser.addEventListener('close', () => { ++browseGeneration; });
  byId('material-directory-roots').addEventListener('click', () => browse(''));
  byId('material-directory-parent').addEventListener('click', () => { if (browseLocation) browse(browseLocation.parent); });
  byId('material-directory-browse-form').addEventListener('submit', event => {
    event.preventDefault(); browse(byId('material-browse-path').value.trim());
  });
  byId('material-directory-use').addEventListener('click', () => {
    if (!browseLocation?.directory) return;
    directory.value = browseLocation.directory; reset(); chooser.close();
    status.textContent = '已选择文件夹，点击“扫描预览”查看视频清单。';
    byId('material-scan').focus();
  });
  confirm.addEventListener('change', controls);
  all.addEventListener('change', () => { preview.querySelectorAll('[data-source]').forEach(el => { el.checked = all.checked; }); pending = null; controls(); });
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (busy) return;
    reset(); busy = true; controls(); status.textContent = recursive.checked ? '正在扫描文件夹及子文件夹…' : '正在扫描当前文件夹…';
    try {
      scan = await api('/api/materials/scan', {directory: directory.value.trim(), recursive: recursive.checked});
      const list = byId('material-preview-list'); list.replaceChildren();
      for (const item of scan.manifest.entries) {
        const card = node('article', ''); card.className = 'material-item';
        const label = node('label', ''); label.className = 'material-check';
        const check = document.createElement('input'); check.type = 'checkbox'; check.dataset.source = item.source_key; check.checked = true;
        check.addEventListener('change', () => { pending = null; controls(); });
        label.append(check, node('span', `${item.relative_path || item.source.file_name} · ${size(item.source.stamp.size)}`));
        card.append(label); list.append(card);
      }
      byId('material-preview-heading').textContent = `${scan.manifest.entries.length} 个可登记视频`;
      byId('material-excluded-summary').textContent = `未选入 ${scan.manifest.excluded.length} 项`;
      byId('material-excluded').replaceChildren(...scan.manifest.excluded.map(item => node('p', `${item.file_name}：${item.reason}`)));
      preview.hidden = false;
      const scope = scan.manifest.recursive ? '（包含子文件夹）' : '（仅当前文件夹）';
      status.textContent = scan.manifest.entries.length ? `已扫描 ${scan.manifest.directory.path}${scope}。请核对清单；预览在 30 分钟后失效。` :
        `此目录未找到可登记视频${scope}。${scan.manifest.recursive ? '请检查下方未选入原因，或选择其他文件夹。' : '如果视频放在月份等子文件夹中，请勾选“包含子文件夹”后重新扫描。'}`;
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
        card.append(node('strong', item.file_name), node('p', item.source_path), node('p', `${size(item.size_bytes)} · ${item.content_verified ? '已完成复制与完整哈希校验' : '已登记文件版本，等待复制校验'}`));
        if (item.duplicate_material_count) card.append(node('p', `与其他 ${item.duplicate_material_count} 份素材内容相同（完整哈希核对）。请自行决定是否再次生产。`));
        card.dataset.materialId = item.id; library.append(card);
      }
      if (!data.materials.length) library.append(node('p', '暂无素材。先选择文件夹并扫描预览。'));
      window.dispatchEvent(new Event('material-library-updated'));
      byId('material-previous').disabled = offset === 0;
      byId('material-next').disabled = offset + 50 >= data.total;
    } catch (error) { if (token === generation) byId('material-total').textContent = error.message; }
  }
  byId('material-refresh').addEventListener('click', refresh);
  byId('material-previous').addEventListener('click', () => { offset = Math.max(0, offset - 50); refresh(); });
  byId('material-next').addEventListener('click', () => { offset += 50; refresh(); });
  refresh(); controls();
})();
