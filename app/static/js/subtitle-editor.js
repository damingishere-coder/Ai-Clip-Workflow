(() => {
  "use strict";

  const root = document.querySelector("#subtitle-editor");
  if (!root) return;

  const elements = {
    track: root.querySelector("#subtitle-track-select"),
    saveState: root.querySelector("#subtitle-save-state"),
    revisionMeta: root.querySelector("#subtitle-revision-meta"),
    approve: root.querySelector("#subtitle-approve"),
    video: root.querySelector("#subtitle-editor-video"),
    overlay: root.querySelector("#subtitle-video-overlay"),
    waveform: root.querySelector("#subtitle-waveform"),
    waveformStatus: root.querySelector("#subtitle-waveform-status"),
    list: root.querySelector("#subtitle-cue-list"),
    spacer: root.querySelector("#subtitle-cue-spacer"),
    viewport: root.querySelector("#subtitle-cue-viewport"),
    quality: root.querySelector("#subtitle-quality-summary"),
    search: root.querySelector("#subtitle-search"),
    replacement: root.querySelector("#subtitle-replacement"),
    replaceAll: root.querySelector("#subtitle-replace-all"),
    add: root.querySelector("#subtitle-add"),
    split: root.querySelector("#subtitle-split"),
    merge: root.querySelector("#subtitle-merge"),
    remove: root.querySelector("#subtitle-delete"),
    shiftMs: root.querySelector("#subtitle-shift-ms"),
    shift: root.querySelector("#subtitle-shift"),
    undo: root.querySelector("#subtitle-undo"),
    redo: root.querySelector("#subtitle-redo"),
    save: root.querySelector("#subtitle-save-now"),
    importFile: root.querySelector("#subtitle-import-file"),
    exports: ["srt", "vtt", "ass"].map((format) => [
      format,
      root.querySelector(`#subtitle-export-${format}`),
    ]),
  };

  const ROW_HEIGHT = 132;
  const PAGE_SIZE = 2000;
  const state = {
    taskId: root.dataset.taskId,
    tracks: [],
    track: null,
    revision: null,
    cues: [],
    visibleIndices: [],
    selectedIds: new Set(),
    currentCueId: null,
    undo: [],
    redo: [],
    waveSurfer: null,
    regions: null,
    selectedRegion: null,
    saveTimer: null,
    saving: false,
    dirty: false,
    changeVersion: 0,
    requestToken: 0,
  };

  function setStatus(message, tone = "blue") {
    elements.saveState.textContent = message;
    elements.saveState.className = `status-pill tone-${tone}`;
  }

  async function api(url, options = {}) {
    const response = await fetch(url, options);
    let payload = {};
    try {
      payload = await response.json();
    } catch (_error) {
      payload = {};
    }
    if (!response.ok) {
      const detail = typeof payload.detail === "string" ? payload.detail : "字幕请求失败";
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function cueSnapshot() {
    return state.cues.map((cue) => ({
      ...cue,
      start_ms: Number(cue.start_ms),
      end_ms: Number(cue.end_ms),
    }));
  }

  function restoreSnapshot(snapshot) {
    state.cues = snapshot.map((cue) => ({ ...cue }));
    state.selectedIds.clear();
    state.currentCueId = null;
    markChanged();
    applySearch();
    updateCurrentCue();
  }

  function mutate(callback) {
    state.undo.push(cueSnapshot());
    if (state.undo.length > 50) state.undo.shift();
    state.redo = [];
    callback();
    state.cues.sort((left, right) => left.start_ms - right.start_ms || left.end_ms - right.end_ms);
    markChanged();
    applySearch();
    updateUndoButtons();
  }

  function markChanged(schedule = true) {
    state.dirty = true;
    state.changeVersion += 1;
    elements.save.disabled = false;
    setStatus("有未保存修改", "amber");
    renderQuality();
    if (schedule) {
      window.clearTimeout(state.saveTimer);
      state.saveTimer = window.setTimeout(() => saveRevision(false), 1800);
    }
  }

  function updateUndoButtons() {
    elements.undo.disabled = state.undo.length === 0;
    elements.redo.disabled = state.redo.length === 0;
  }

  function applySearch() {
    const query = elements.search.value.trim().toLocaleLowerCase();
    state.visibleIndices = state.cues
      .map((cue, index) => ({ cue, index }))
      .filter(({ cue }) => !query || `${cue.text} ${cue.speaker || ""}`.toLocaleLowerCase().includes(query))
      .map(({ index }) => index);
    elements.spacer.style.height = `${state.visibleIndices.length * ROW_HEIGHT}px`;
    renderVirtualRows();
  }

  function formatMs(value) {
    const total = Math.max(0, Number(value) || 0);
    const hours = Math.floor(total / 3600000);
    const minutes = Math.floor((total % 3600000) / 60000);
    const seconds = Math.floor((total % 60000) / 1000);
    const milliseconds = Math.floor(total % 1000);
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(milliseconds).padStart(3, "0")}`;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function cueIssues(cue, previous) {
    const issues = [];
    const duration = cue.end_ms - cue.start_ms;
    const lines = String(cue.text || "").split(/\r?\n/);
    const chinese = (value) => (String(value).match(/[\u4e00-\u9fff]/g) || []).length;
    if (lines.length > 2) issues.push("超过 2 行");
    if (lines.some((line) => chinese(line) > 18)) issues.push("单行超过 18 个中文字符");
    if (duration < 800) issues.push("短于 800ms");
    if (duration > 7000) issues.push("长于 7 秒");
    if (chinese(cue.text) / Math.max(0.001, duration / 1000) > 12) issues.push("阅读速度过快");
    if (previous) {
      const gap = cue.start_ms - previous.end_ms;
      if (gap < 0) issues.push("与上一条重叠");
      else if (gap < 80) issues.push("间隔小于 80ms");
    }
    return issues;
  }

  function renderVirtualRows() {
    if (!state.visibleIndices.length) {
      elements.viewport.innerHTML = '<div class="subtitle-cue-empty">没有匹配的字幕行</div>';
      return;
    }
    const scrollTop = elements.list.scrollTop;
    const first = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - 4);
    const count = Math.ceil(elements.list.clientHeight / ROW_HEIGHT) + 8;
    const last = Math.min(state.visibleIndices.length, first + count);
    const rows = [];
    for (let virtualIndex = first; virtualIndex < last; virtualIndex += 1) {
      const cueIndex = state.visibleIndices[virtualIndex];
      const cue = state.cues[cueIndex];
      const previous = cueIndex > 0 ? state.cues[cueIndex - 1] : null;
      const issues = cueIssues(cue, previous);
      const isSelected = state.selectedIds.has(cue.id);
      const isCurrent = state.currentCueId === cue.id;
      rows.push(`
        <article class="subtitle-cue-row${isSelected ? " is-selected" : ""}${isCurrent ? " is-current" : ""}"
          data-cue-id="${escapeHtml(cue.id)}" style="transform: translateY(${virtualIndex * ROW_HEIGHT}px)">
          <label class="subtitle-cue-check"><input type="checkbox" data-cue-select ${isSelected ? "checked" : ""}><span>${cueIndex + 1}</span></label>
          <button class="subtitle-cue-time" type="button" data-cue-seek title="跳转到这一行">${formatMs(cue.start_ms)}</button>
          <div class="subtitle-cue-time-inputs">
            <label><span>开始 ms</span><input type="number" min="0" step="1" value="${cue.start_ms}" data-cue-field="start_ms"></label>
            <label><span>结束 ms</span><input type="number" min="1" step="1" value="${cue.end_ms}" data-cue-field="end_ms"></label>
          </div>
          <textarea rows="2" data-cue-field="text">${escapeHtml(cue.text)}</textarea>
          <label class="subtitle-speaker"><span>说话人</span><input type="text" maxlength="80" value="${escapeHtml(cue.speaker || "")}" placeholder="主播 / 嘉宾" data-cue-field="speaker"></label>
          <div class="subtitle-cue-issues">${issues.length ? escapeHtml(issues.join(" · ")) : "时间与阅读速度正常"}</div>
        </article>
      `);
    }
    elements.viewport.innerHTML = rows.join("");
    bindVisibleRows();
  }

  function bindVisibleRows() {
    elements.viewport.querySelectorAll(".subtitle-cue-row").forEach((row) => {
      const cue = state.cues.find((item) => item.id === row.dataset.cueId);
      if (!cue) return;
      row.querySelector("[data-cue-select]").addEventListener("change", (event) => {
        if (event.target.checked) state.selectedIds.add(cue.id);
        else state.selectedIds.delete(cue.id);
        renderVirtualRows();
      });
      row.querySelector("[data-cue-seek]").addEventListener("click", () => selectCue(cue, true));
      row.querySelectorAll("[data-cue-field]").forEach((input) => {
        let editStarted = false;
        let beforeEdit = null;
        input.addEventListener("focus", () => {
          editStarted = false;
          beforeEdit = cueSnapshot();
        });
        input.addEventListener("input", () => {
          if (!editStarted) {
            state.undo.push(beforeEdit || cueSnapshot());
            if (state.undo.length > 50) state.undo.shift();
            state.redo = [];
            editStarted = true;
            updateUndoButtons();
          }
          const field = input.dataset.cueField;
          cue[field] = field.endsWith("_ms") ? Number(input.value) : input.value;
          markChanged();
        });
        input.addEventListener("change", () => {
          if (cue.end_ms <= cue.start_ms) cue.end_ms = cue.start_ms + 1;
          state.cues.sort((left, right) => left.start_ms - right.start_ms || left.end_ms - right.end_ms);
          applySearch();
          selectCue(cue, false);
        });
      });
    });
  }

  function renderQuality() {
    let errors = 0;
    let warnings = 0;
    state.cues.forEach((cue, index) => {
      const issues = cueIssues(cue, index ? state.cues[index - 1] : null);
      warnings += issues.length;
      if (issues.includes("与上一条重叠")) errors += 1;
    });
    elements.quality.className = `subtitle-quality-summary${errors ? " has-errors" : warnings ? " has-warnings" : " is-clean"}`;
    elements.quality.textContent = errors
      ? `${errors} 个重叠错误，另有 ${Math.max(0, warnings - errors)} 条质量提醒；请先处理红色问题。`
      : warnings
        ? `没有重叠错误，共 ${warnings} 条时长、间隔、行长或阅读速度提醒。`
        : `共 ${state.cues.length} 条字幕，当前没有发现质量问题。`;
  }

  function findCurrentCue(timeMs) {
    let low = 0;
    let high = state.cues.length - 1;
    let candidate = null;
    while (low <= high) {
      const middle = Math.floor((low + high) / 2);
      if (state.cues[middle].start_ms <= timeMs) {
        candidate = state.cues[middle];
        low = middle + 1;
      } else {
        high = middle - 1;
      }
    }
    return candidate && candidate.end_ms >= timeMs ? candidate : null;
  }

  function updateCurrentCue() {
    const cue = findCurrentCue((elements.video.currentTime || 0) * 1000);
    const nextId = cue?.id || null;
    if (state.currentCueId !== nextId) {
      state.currentCueId = nextId;
      renderVirtualRows();
    }
    elements.overlay.textContent = cue ? `${cue.speaker ? `${cue.speaker}：` : ""}${cue.text}` : "";
  }

  function selectCue(cue, seek) {
    state.currentCueId = cue.id;
    if (seek) elements.video.currentTime = cue.start_ms / 1000;
    elements.overlay.textContent = `${cue.speaker ? `${cue.speaker}：` : ""}${cue.text}`;
    renderSelectedRegion(cue);
    const visibleIndex = state.visibleIndices.indexOf(state.cues.indexOf(cue));
    if (visibleIndex >= 0) {
      const top = visibleIndex * ROW_HEIGHT;
      if (top < elements.list.scrollTop || top + ROW_HEIGHT > elements.list.scrollTop + elements.list.clientHeight) {
        elements.list.scrollTop = Math.max(0, top - ROW_HEIGHT);
      }
    }
    renderVirtualRows();
  }

  function renderSelectedRegion(cue) {
    if (!state.regions) return;
    state.regions.clearRegions();
    state.selectedRegion = state.regions.addRegion({
      id: `cue-${cue.id}`,
      start: cue.start_ms / 1000,
      end: cue.end_ms / 1000,
      color: "rgba(38, 118, 255, 0.22)",
      drag: true,
      resize: true,
    });
  }

  async function loadTracks() {
    setStatus("正在生成或读取统一字幕轨…", "blue");
    try {
      const payload = await api(`/api/subtitles/tasks/${encodeURIComponent(state.taskId)}/tracks`);
      state.tracks = payload.tracks || [];
      elements.track.innerHTML = state.tracks.map((track) => {
        const label = track.track_type === "source" ? "原片主字幕" : `切片 · ${track.output_file_name || track.name}`;
        const suffix = track.has_manual_edits ? "（人工版）" : track.sync_status === "pending_sync" ? "（待同步）" : "";
        return `<option value="${escapeHtml(track.id)}">${escapeHtml(label + suffix)}</option>`;
      }).join("");
      elements.track.disabled = state.tracks.length === 0;
      if (!state.tracks.length) throw new Error("当前任务没有可用字幕轨");
      await loadTrack(state.tracks[0].id);
    } catch (error) {
      setStatus(error.message, "red");
      elements.revisionMeta.textContent = "请先完成结构化转写并生成切片。";
    }
  }

  async function loadTrack(trackId) {
    const token = ++state.requestToken;
    window.clearTimeout(state.saveTimer);
    setStatus("正在载入毫秒级字幕…", "blue");
    const track = state.tracks.find((item) => item.id === trackId);
    if (!track) return;
    try {
      const first = await api(`/api/subtitles/tracks/${encodeURIComponent(trackId)}/cues?offset=0&limit=${PAGE_SIZE}`);
      const cues = [...(first.cues || [])];
      for (let offset = cues.length; offset < Number(first.total || 0); offset += PAGE_SIZE) {
        const page = await api(`/api/subtitles/tracks/${encodeURIComponent(trackId)}/cues?offset=${offset}&limit=${PAGE_SIZE}`);
        cues.push(...(page.cues || []));
      }
      if (token !== state.requestToken) return;
      state.track = first.track;
      state.revision = first.revision;
      state.cues = cues;
      state.selectedIds.clear();
      state.currentCueId = null;
      state.undo = [];
      state.redo = [];
      state.dirty = false;
      state.changeVersion = 0;
      elements.track.value = trackId;
      elements.video.src = state.track.media_url;
      elements.approve.disabled = !state.revision;
      elements.save.disabled = true;
      elements.exports.forEach(([, button]) => { button.disabled = !state.revision; });
      renderRevisionMeta();
      applySearch();
      renderQuality();
      updateUndoButtons();
      setStatus(`已载入 ${state.cues.length} 条，自动保存已开启`, "green");
      loadWaveform(token);
    } catch (error) {
      setStatus(error.message, "red");
    }
  }

  function renderRevisionMeta() {
    if (!state.revision) {
      elements.revisionMeta.textContent = "尚无 revision";
      return;
    }
    const status = state.revision.status === "approved" ? "已审核" : "草稿";
    elements.revisionMeta.textContent = `Revision ${state.revision.revision_number} · ${status} · ${state.cues.length} 条 · ${state.track.sync_status}`;
  }

  async function loadWaveform(token) {
    if (state.waveSurfer) {
      state.waveSurfer.destroy();
      state.waveSurfer = null;
      state.regions = null;
    }
    elements.waveform.innerHTML = "";
    document.querySelector("#subtitle-timeline").innerHTML = "";
    elements.waveformStatus.textContent = "正在读取 peaks…";
    try {
      const payload = await api(`${state.track.peaks_url}?max_points=12000`);
      if (token !== state.requestToken) return;
      if (!window.WaveSurfer || !window.WaveSurfer.Regions || !window.WaveSurfer.Timeline) {
        throw new Error("本地 wavesurfer.js 未正确载入");
      }
      state.regions = window.WaveSurfer.Regions.create();
      const timeline = window.WaveSurfer.Timeline.create({ container: "#subtitle-timeline", height: 24 });
      state.waveSurfer = window.WaveSurfer.create({
        container: elements.waveform,
        media: elements.video,
        peaks: [Float32Array.from(payload.peaks || [])],
        duration: Number(payload.duration_ms || 0) / 1000,
        height: 92,
        minPxPerSec: state.track.track_type === "source" ? 0.08 : 8,
        normalize: true,
        waveColor: "#9fbce8",
        progressColor: "#2676ff",
        cursorColor: "#ff9f0a",
        plugins: [state.regions, timeline],
      });
      state.regions.on("region-update-end", (region) => {
        if (!state.currentCueId) return;
        const cue = state.cues.find((item) => item.id === state.currentCueId);
        if (!cue) return;
        mutate(() => {
          cue.start_ms = Math.max(0, Math.round(region.start * 1000));
          cue.end_ms = Math.max(cue.start_ms + 1, Math.round(region.end * 1000));
        });
      });
      elements.waveformStatus.textContent = `${payload.point_count} 个 peaks${payload.cached ? " · 已复用缓存" : " · 新生成"}`;
    } catch (error) {
      elements.waveformStatus.textContent = `波形暂不可用：${error.message}`;
    }
  }

  async function saveRevision(force) {
    if (!state.dirty || state.saving || !state.track || !state.revision) return;
    window.clearTimeout(state.saveTimer);
    state.saving = true;
    const version = state.changeVersion;
    const cues = cueSnapshot();
    const baseRevisionId = state.revision.id;
    setStatus("正在自动保存新 revision…", "blue");
    try {
      const payload = await api(`/api/subtitles/tracks/${encodeURIComponent(state.track.id)}/revisions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ base_revision_id: baseRevisionId, cues, note: force ? "手动立即保存" : "字幕编辑器自动保存" }),
      });
      state.revision = payload.revision;
      if (version === state.changeVersion) {
        state.cues = (payload.revision.cues || []).map((cue) => ({ ...cue }));
        state.dirty = false;
        state.selectedIds.clear();
        elements.save.disabled = true;
        applySearch();
        setStatus(`Revision ${state.revision.revision_number} 已保存`, "green");
      } else {
        state.dirty = true;
        setStatus("保存期间又有修改，正在继续保存…", "amber");
      }
      renderRevisionMeta();
    } catch (error) {
      setStatus(error.status === 409 ? "版本已变化，请重新选择字幕轨后再编辑" : `保存失败：${error.message}`, "red");
    } finally {
      state.saving = false;
      if (state.dirty && version !== state.changeVersion) {
        state.saveTimer = window.setTimeout(() => saveRevision(false), 300);
      }
    }
  }

  function selectedCues() {
    return state.cues.filter((cue) => state.selectedIds.has(cue.id));
  }

  elements.track.addEventListener("change", () => loadTrack(elements.track.value));
  elements.list.addEventListener("scroll", renderVirtualRows, { passive: true });
  elements.video.addEventListener("timeupdate", updateCurrentCue);
  elements.search.addEventListener("input", applySearch);
  elements.save.addEventListener("click", () => saveRevision(true));

  elements.undo.addEventListener("click", () => {
    if (!state.undo.length) return;
    state.redo.push(cueSnapshot());
    restoreSnapshot(state.undo.pop());
    updateUndoButtons();
  });
  elements.redo.addEventListener("click", () => {
    if (!state.redo.length) return;
    state.undo.push(cueSnapshot());
    restoreSnapshot(state.redo.pop());
    updateUndoButtons();
  });

  elements.add.addEventListener("click", () => {
    const start = Math.max(0, Math.round((elements.video.currentTime || 0) * 1000));
    const cue = { id: `local-${crypto.randomUUID()}`, start_ms: start, end_ms: start + 2000, text: "新字幕", speaker: "", confidence: null, source_cue_id: null };
    mutate(() => state.cues.push(cue));
    selectCue(cue, false);
  });

  elements.remove.addEventListener("click", () => {
    if (!state.selectedIds.size) return setStatus("请先勾选要删除的字幕行", "amber");
    mutate(() => { state.cues = state.cues.filter((cue) => !state.selectedIds.has(cue.id)); });
    state.selectedIds.clear();
  });

  elements.merge.addEventListener("click", () => {
    const cues = selectedCues().sort((left, right) => left.start_ms - right.start_ms);
    if (cues.length < 2) return setStatus("合并至少需要勾选两行", "amber");
    mutate(() => {
      const merged = { ...cues[0], end_ms: Math.max(...cues.map((cue) => cue.end_ms)), text: cues.map((cue) => cue.text).join(" ") };
      const ids = new Set(cues.map((cue) => cue.id));
      state.cues = state.cues.filter((cue) => !ids.has(cue.id));
      state.cues.push(merged);
      state.selectedIds = new Set([merged.id]);
    });
  });

  elements.split.addEventListener("click", () => {
    const cue = state.cues.find((item) => item.id === state.currentCueId) || selectedCues()[0];
    if (!cue) return setStatus("请先点击一行字幕", "amber");
    let splitMs = Math.round((elements.video.currentTime || 0) * 1000);
    if (splitMs <= cue.start_ms || splitMs >= cue.end_ms) splitMs = Math.round((cue.start_ms + cue.end_ms) / 2);
    const middle = Math.max(1, Math.floor(cue.text.length / 2));
    const originalEndMs = cue.end_ms;
    mutate(() => {
      cue.end_ms = splitMs;
      const second = { ...cue, id: `local-${crypto.randomUUID()}`, start_ms: splitMs, end_ms: originalEndMs, text: cue.text.slice(middle).trim() || cue.text };
      cue.text = cue.text.slice(0, middle).trim() || cue.text;
      state.cues.push(second);
    });
  });

  elements.shift.addEventListener("click", () => {
    const delta = Number(elements.shiftMs.value || 0);
    if (!Number.isFinite(delta) || delta === 0) return setStatus("请输入非 0 的毫秒位移", "amber");
    const selected = state.selectedIds;
    mutate(() => state.cues.forEach((cue) => {
      if (!selected.size || selected.has(cue.id)) {
        const duration = cue.end_ms - cue.start_ms;
        cue.start_ms = Math.max(0, cue.start_ms + delta);
        cue.end_ms = cue.start_ms + duration;
      }
    }));
  });

  elements.replaceAll.addEventListener("click", () => {
    const search = elements.search.value;
    if (!search) return setStatus("请先输入要搜索的文字", "amber");
    const replacement = elements.replacement.value;
    mutate(() => state.cues.forEach((cue) => { cue.text = cue.text.split(search).join(replacement); }));
  });

  elements.approve.addEventListener("click", async () => {
    if (state.dirty) await saveRevision(false);
    if (state.dirty || !state.revision) return;
    try {
      const payload = await api(`/api/subtitles/tracks/${encodeURIComponent(state.track.id)}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revision_id: state.revision.id }),
      });
      state.revision = payload.revision;
      renderRevisionMeta();
      setStatus(`Revision ${state.revision.revision_number} 已审核`, "green");
    } catch (error) {
      setStatus(`审核失败：${error.message}`, "red");
    }
  });

  elements.importFile.addEventListener("change", async () => {
    const file = elements.importFile.files?.[0];
    if (!file || !state.track) return;
    const form = new FormData();
    form.append("file", file);
    setStatus(`正在导入 ${file.name}…`, "blue");
    try {
      await api(`/api/subtitles/tracks/${encodeURIComponent(state.track.id)}/import`, { method: "POST", body: form });
      await loadTrack(state.track.id);
    } catch (error) {
      setStatus(`导入失败：${error.message}`, "red");
    } finally {
      elements.importFile.value = "";
    }
  });

  elements.exports.forEach(([format, button]) => button.addEventListener("click", () => {
    if (!state.track || !state.revision) return;
    window.location.href = `/api/subtitles/tracks/${encodeURIComponent(state.track.id)}/export?format_name=${format}&revision_id=${encodeURIComponent(state.revision.id)}`;
  }));

  window.addEventListener("beforeunload", (event) => {
    if (!state.dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });

  loadTracks();
})();
