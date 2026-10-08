// Compatibility entry point: load only the behavior needed by this page.
(() => {
  const entry = document.currentScript;
  const root = new URL(".", entry.src);
  const version = new URL(entry.src).search;
  const scripts = ["workspace-helpers.js"];
  if (document.querySelector("#new-task-form")) scripts.push("new-task.js");
  if (document.querySelector("#ai-analysis-form, #transcript-panel, [data-auto-pipeline-monitor], .js-process-action")) scripts.push("task-progress.js");
  if (document.querySelector("#clip-review-form")) scripts.push("clip-workspace.js", "review-workspace.js");
  if (document.querySelector("#subtitle-style-form, #subtitle-editor, [data-subtitle-output-card]")) scripts.push("subtitle-workflow.js");
  if (document.querySelector("#ai-config-form")) scripts.push("system-settings.js");
  scripts.push("task-actions.js");
  for (const file of scripts) {
    const preload = document.createElement("link");
    preload.rel = "preload"; preload.as = "script";
    preload.href = new URL(file, root).href + version;
    document.head.append(preload);
  }
  window.studioPageReady = scripts.reduce((ready, file) => ready.then(() => new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = new URL(file, root).href + version;
    script.onload = resolve;
    script.onerror = () => reject(new Error("页面交互加载失败：" + file));
    document.body.append(script);
  })), Promise.resolve()).catch(error => {
    const alert = document.createElement("p");
    alert.className = "page-alert";
    alert.setAttribute("role", "alert");
    alert.textContent = error.message + "，请刷新页面重试。";
    document.querySelector("main")?.prepend(alert);
  });
})();
