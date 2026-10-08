(() => {
  const workspace = document.querySelector('[data-review-workspace]');
  if (!workspace) return;
  const form = document.querySelector('#clip-review-form');
  const list = document.querySelector('#review-candidate-list');
  const saveState = document.querySelector('#clip-save-state');
  const cards = () => Array.from(form.querySelectorAll('[data-clip-card]'));
  let activeId = cards()[0]?.dataset.clipId;
  workspace.dataset.view = 'player';
  const bulk = form.querySelector('[data-clip-bulk-select]');
  if (bulk) list.before(bulk);
  function select(id) {
    activeId = id;
    cards().forEach(card => { card.hidden = card.dataset.clipId !== id; });
    list.querySelectorAll('[data-candidate-id]').forEach(row => {
      const selected = row.dataset.candidateId === id;
      row.classList.toggle('is-current', selected);
      row.querySelector('button').setAttribute('aria-current', selected ? 'true' : 'false');
    });
    const card = cards().find(card => card.dataset.clipId === id);
    const caption = document.querySelector('#clip-preview-caption');
    if (card && caption) caption.textContent = `${card.querySelector('[name=title]').value} · ${card.querySelector('[name=start_time]').value}–${card.querySelector('[name=end_time]').value}`;
    const video = document.querySelector('#clip-preview-video');
    if (video) {
      video.pause();
      if (card && Number.isFinite(video.duration)) {
        video.currentTime = Math.min(video.duration, Number(card.dataset.startSeconds || 0));
        activePreviewEndSeconds = Number(card.dataset.endSeconds || 0);
      }
    }
  }
  function render() {
    list.replaceChildren();
    cards().forEach((card, index) => {
      const row = document.createElement('div');
      row.className = 'review-candidate';
      row.dataset.candidateId = card.dataset.clipId;
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = card.querySelector('[name=enabled]').checked;
      checkbox.disabled = form.dataset.busy === 'true';
      checkbox.setAttribute('aria-label', `保留候选 ${index + 1}`);
      checkbox.addEventListener('change', () => {
        const input = card.querySelector('[name=enabled]');
        input.checked = checkbox.checked;
        input.dispatchEvent(new Event('change', {bubbles:true}));
      });
      const button = document.createElement('button');
      button.type = 'button';
      const title = document.createElement('strong');
      title.textContent = `${String(index + 1).padStart(2,'0')} · ${card.querySelector('[name=title]').value}`;
      const time = document.createElement('small');
      time.textContent = `${card.querySelector('[name=start_time]').value}–${card.querySelector('[name=end_time]').value}`;
      button.append(title,time);
      button.addEventListener('click', () => select(card.dataset.clipId));
      row.append(checkbox,button);
      list.append(row);
    });
    if (!cards().some(card => card.dataset.clipId === activeId)) activeId = cards()[0]?.dataset.clipId;
    select(activeId);
  }
  form.addEventListener('change', render);
  form.addEventListener('input', () => {
    // Updating text must preserve cursor and current editor focus.
    list.querySelectorAll('[data-candidate-id]').forEach(row => {
      const card = cards().find(card => card.dataset.clipId === row.dataset.candidateId);
      if (card) row.querySelector('strong').textContent = card.querySelector('[name=title]').value;
    });
  });
  new MutationObserver(render).observe(form, {childList:true});
  document.addEventListener('clip-review-state', () => {
    if (saveState) saveState.textContent = form.dataset.busy === 'true' ? '正在处理…' : form.dataset.dirty === 'true' ? '有未保存修改' : '选择已保存';
    list.querySelectorAll('[data-candidate-id]').forEach(row => {
      const card = cards().find(item => item.dataset.clipId === row.dataset.candidateId);
      const input = row.querySelector('input');
      input.disabled = form.dataset.busy === 'true';
      if (card) {
        input.checked = card.querySelector('[name=enabled]').checked;
        row.querySelector('small').textContent = `${card.querySelector('[name=start_time]').value}–${card.querySelector('[name=end_time]').value}`;
      }
    });
  });
  workspace.querySelectorAll('[data-review-view]').forEach(button => button.addEventListener('click', () => {
    workspace.dataset.view = button.dataset.reviewView;
    workspace.querySelectorAll('[data-review-view]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
  }));
  window.addEventListener('beforeunload', event => {
    if (form.dataset.dirty !== 'true') return;
    event.preventDefault(); event.returnValue = '';
  });
  render();
})();
