const newTaskForm = document.querySelector("#new-task-form");

if (newTaskForm) {
  newTaskForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const result = document.querySelector("#new-task-result");
    const submitButton = newTaskForm.querySelector("button[type='submit']");
    const formData = new FormData(newTaskForm);
    const payload = Object.fromEntries(formData.entries());

    payload.max_clip_minutes = Number(payload.max_clip_minutes || 2);
    payload.target_clip_count = Number(payload.target_clip_count || 8);

    submitButton.disabled = true;
    result.textContent = "正在创建任务...";

    try {
      const response = await fetch("/api/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      result.textContent = `${data.message} 任务 ID：${data.id}`;
    } catch (error) {
      result.textContent = "任务创建失败，请检查服务是否正常运行。";
    } finally {
      submitButton.disabled = false;
    }
  });
}

document.querySelectorAll(".js-review-action").forEach((button) => {
  button.addEventListener("click", () => {
    button.textContent = "已记录操作";
    setTimeout(() => {
      button.textContent = button.classList.contains("primary-button") ? "生成切片" : "保存修改";
    }, 1200);
  });
});
