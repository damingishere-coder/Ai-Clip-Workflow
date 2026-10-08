function wait(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}
function timeTextToSeconds(value) {
  const parts = String(value || "")
    .trim()
    .split(":")
    .map((part) => Number(part));
  if (![2, 3].includes(parts.length) || parts.some((part) => !Number.isFinite(part))) {
    return 0;
  }
  if (parts.length === 2) {
    return parts[0] * 60 + parts[1];
  }
  return parts[0] * 3600 + parts[1] * 60 + parts[2];
}

function secondsToTimeText(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const restSeconds = total % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(restSeconds).padStart(2, "0")}`;
}

function formatTimecode(seconds) {
  return `${secondsToTimeText(seconds)}:00`;
}

function showClipReviewMessage(message, tone = "info") {
  const node = document.querySelector("#clip-review-message") || document.querySelector("#process-result");
  if (!node) return;
  node.hidden = false;
  node.textContent = message;
  node.dataset.tone = tone;
}
