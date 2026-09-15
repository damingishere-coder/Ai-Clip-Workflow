(() => {
  'use strict';
  const library = document.getElementById('material-library-view');
  const inbox = document.getElementById('material-inbox-view');
  if (!library || !inbox) return;
  const content = document.getElementById('material-inbox-content');
  const status = document.getElementById('material-inbox-status');
  const tabs = document.querySelectorAll('.material-view-nav [data-material-view]');
  const retry = document.getElementById('material-inbox-retry');
  let generation = 0, retryUrl = null;
  function show(view) {
    library.hidden = view === 'inbox'; inbox.hidden = view !== 'inbox';
    tabs.forEach(tab => {
      if (tab.dataset.materialView === view) tab.setAttribute('aria-current', 'page');
      else tab.removeAttribute('aria-current');
    });
  }
  async function navigate(url, push = true, background = false) {
    const token = ++generation;
    retry.hidden = true;
    const view = url.searchParams.get('view') === 'inbox' ? 'inbox' : 'library';
    if (!background) show(view);
    if (view === 'library' && !background) {
      inbox.removeAttribute('aria-busy'); status.textContent = '';
      if (push) history.pushState(null, '', url);
      if (url.hash === '#material-scan-form') document.getElementById('material-directory').focus();
      return;
    }
    inbox.setAttribute('aria-busy', 'true'); status.textContent = '正在更新待办…';
    const endpoint = new URL('/review-inbox?embedded=1', location.origin);
    for (const name of ['category', 'page']) {
      if (url.searchParams.has(name)) endpoint.searchParams.set(name, url.searchParams.get(name));
    }
    try {
      const response = await fetch(endpoint, {headers:{'Accept':'text/html'}});
      if (!response.ok) throw new Error('待办加载失败，请重新加载待办。');
      const documentCopy = new DOMParser().parseFromString(await response.text(), 'text/html');
      const panel = documentCopy.querySelector('.material-inbox-panel');
      if (!panel) throw new Error('待办页面尚未准备好，请刷新后重试。');
      if (token !== generation) return;
      content.replaceChildren(panel);
      document.getElementById('material-inbox-count').textContent = panel.dataset.inboxTotal;
      status.textContent = '';
      if (push) history.pushState(null, '', url);
    } catch (error) {
      if (token === generation) {
        status.textContent = error.message; retryUrl = url; retry.hidden = false;
        if (!content.querySelector('.material-inbox-panel')) content.replaceChildren();
      }
    } finally { if (token === generation) inbox.removeAttribute('aria-busy'); }
  }
  document.addEventListener('click', event => {
    const link = event.target.closest('a[data-material-view], a[data-inbox-link]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); navigate(new URL(link.href));
  });
  retry.addEventListener('click', () => { if (retryUrl) navigate(retryUrl); });
  window.addEventListener('popstate', () => navigate(new URL(location.href), false));
  const initial = new URL(location.href);
  navigate(initial, false, initial.searchParams.get('view') !== 'inbox');
})();
