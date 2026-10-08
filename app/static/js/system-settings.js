const aiConfigModal = document.querySelector("#ai-config-modal");
const aiConfigForm = document.querySelector("#ai-config-form");
const aiConfigResult = document.querySelector("#ai-config-result");
const openAiConfigButton = document.querySelector("#open-ai-config");
const closeAiConfigButton = document.querySelector("#close-ai-config");
const cancelAiConfigButton = document.querySelector("#cancel-ai-config");

function toggleAiConfigModal(show) {
  if (!aiConfigModal) return;
  if (show) {
    aiConfigModal.removeAttribute("hidden");
    document.body.style.overflow = "hidden";
    closeAiConfigButton?.focus();
    return;
  }
  aiConfigModal.setAttribute("hidden", "");
  document.body.style.overflow = "";
}

if (openAiConfigButton) {
  openAiConfigButton.addEventListener("click", () => toggleAiConfigModal(true));
}

[closeAiConfigButton, cancelAiConfigButton].forEach((button) => {
  if (!button) return;
  button.addEventListener("click", () => toggleAiConfigModal(false));
});

if (aiConfigModal) {
  aiConfigModal.addEventListener("click", (event) => {
    if (event.target === aiConfigModal) {
      toggleAiConfigModal(false);
    }
  });
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && aiConfigModal && !aiConfigModal.hidden) {
    toggleAiConfigModal(false);
  }
});

if (aiConfigForm) {
  aiConfigForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submitButton = aiConfigForm.querySelector("button[type='submit']");
    const formData = new FormData(aiConfigForm);
    const payload = Object.fromEntries(formData.entries());
    payload.ai_request_timeout_seconds = Number(payload.ai_request_timeout_seconds || 120);
    payload.ai_codex_timeout_seconds = Number(payload.ai_codex_timeout_seconds || 600);
    payload.volcengine_asr_timeout_seconds = Number(payload.volcengine_asr_timeout_seconds || 300);
    payload.ai_analysis_request_timeout_seconds = Number(payload.ai_analysis_request_timeout_seconds || 120);
    payload.ai_publish_request_timeout_seconds = Number(payload.ai_publish_request_timeout_seconds || 120);
    payload.ai_local_health_timeout_seconds = Number(payload.ai_local_health_timeout_seconds || 30);

    submitButton.disabled = true;
    if (aiConfigResult) aiConfigResult.textContent = "正在保存三类 AI 接口配置...";

    try {
      const data = await window.apiFetch("/api/settings/ai", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      document.dispatchEvent(new CustomEvent("studio-ai-config-saved"));
      if (aiConfigResult) aiConfigResult.textContent = data.message || "保存成功。";
      window.setTimeout(() => {
        toggleAiConfigModal(false);
        window.location.reload();
      }, 500);
    } catch (error) {
      if (aiConfigResult) aiConfigResult.textContent = `保存失败：${error.message}`;
    } finally {
      submitButton.disabled = false;
    }
  });
}

// 发送中心页面行为由 publish-center.js 独立维护，避免全局脚本重复绑定。
