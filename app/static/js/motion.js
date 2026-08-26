(() => {
  "use strict";

  const body = document.body;
  const mainPanel = document.querySelector(".main-panel");
  if (!body || !mainPanel) return;

  const path = window.location.pathname.replace(/\/+$/, "") || "/";
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const profiles = [
    {
      name: "dashboard",
      matches: () => path === "/",
      reveals: [
        [".dashboard-stat-item", "soft", 5],
      ],
    },
    {
      name: "new-task",
      matches: () => path === "/tasks/new",
      reveals: [],
    },
    {
      name: "tasks",
      matches: () => path === "/tasks",
      reveals: [],
    },
    {
      name: "transcript",
      matches: () => /^\/tasks\/[^/]+\/transcript$/.test(path),
      reveals: [],
    },
    {
      name: "clip-review",
      matches: () => /^\/tasks\/[^/]+\/clips(?:\/review)?$/.test(path),
      reveals: [],
    },
    {
      name: "task-detail",
      matches: () => /^\/tasks\/[^/]+$/.test(path),
      reveals: [],
    },
    {
      name: "clips",
      matches: () => path === "/clips",
      reveals: [],
    },
    {
      name: "subtitle-task",
      matches: () => /^\/subtitles\/[^/]+$/.test(path),
      reveals: [],
    },
    {
      name: "subtitles",
      matches: () => path === "/subtitles",
      reveals: [],
    },
    {
      name: "publish",
      matches: () => path === "/publish",
      reveals: [],
    },
    {
      name: "system",
      matches: () => path === "/system",
      reveals: [],
    },
  ];

  const profile = profiles.find((item) => item.matches()) || {
    name: body.dataset.page || "static",
    reveals: [],
  };

  body.dataset.motionPage = profile.name;
  body.dataset.motion = reduceMotion ? "reduced" : "full";
  if (reduceMotion) return;

  body.classList.add("motion-enabled");

  const canObserveViewport = "IntersectionObserver" in window;
  const revealObserver = canObserveViewport
    ? new IntersectionObserver((entries, observer) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          const node = entry.target;
          node.classList.add("motion-in");
          node.dataset.motionSeen = "true";
          observer.unobserve(node);
        });
      }, { rootMargin: "0px 0px -4% 0px", threshold: 0.04 })
    : null;

  const isVisible = (node) => !node.hidden && !node.closest("[hidden]");
  const registered = new Set();

  profile.reveals.forEach(([selector, kind, limit], groupIndex) => {
    const nodes = Array.from(document.querySelectorAll(selector))
      .filter((node) => isVisible(node) && !registered.has(node))
      .slice(0, limit);
    nodes.forEach((node, index) => {
      registered.add(node);
      node.classList.add("motion-pending", `motion-${kind}`);
      node.style.setProperty("--motion-order", String(Math.min(groupIndex + index, 9)));
      const finishInitialMotion = (event) => {
        if (event.target !== node) return;
        node.classList.remove("motion-pending", "motion-rise", "motion-soft", "motion-in");
        node.removeEventListener("animationend", finishInitialMotion);
      };
      node.addEventListener("animationend", finishInitialMotion);
      if (revealObserver) {
        revealObserver.observe(node);
      } else {
        node.classList.add("motion-in");
      }
    });
  });

  const replayClass = (node, className) => {
    if (!node || reduceMotion || !isVisible(node)) return;
    node.classList.remove(className);
    window.requestAnimationFrame(() => {
      node.classList.add(className);
      const finishReplay = (event) => {
        if (event.target !== node) return;
        node.classList.remove(className);
        node.removeEventListener("animationend", finishReplay);
      };
      node.addEventListener("animationend", finishReplay);
    });
  };

  const liveValueSelectors = [
    "[data-task-live-header-status]",
    "[data-task-live-progress-number]",
    "[data-task-live-operation-label]",
    "[data-task-live-operation-progress]",
    "[data-task-live-candidate-count]",
    "[data-task-live-output-count]",
    "[data-worker-status]",
    "[data-scheduler-runtime]",
    "#subtitle-save-state",
  ];

  liveValueSelectors.forEach((selector) => {
    document.querySelectorAll(selector).forEach((node) => {
      let lastValue = node.textContent;
      const observer = new MutationObserver(() => {
        const nextValue = node.textContent;
        if (nextValue === lastValue) return;
        lastValue = nextValue;
        replayClass(node, "motion-data-updated");
      });
      observer.observe(node, { childList: true, characterData: true, subtree: true });
    });
  });

  const dynamicSelector = [
    ".ai-analysis-result-item",
    ".ai-history-item",
    ".schedule-preview-list > div",
  ].join(",");

  const animateAddedContent = (root) => {
    if (!(root instanceof Element)) return;
    const candidates = [];
    if (root.matches(dynamicSelector)) candidates.push(root);
    candidates.push(...root.querySelectorAll(dynamicSelector));
    candidates.slice(0, 8).forEach((node, index) => {
      if (node.dataset.motionDynamicSeen === "true" || !isVisible(node)) return;
      node.dataset.motionDynamicSeen = "true";
      node.style.setProperty("--motion-order", String(Math.min(index, 7)));
      replayClass(node, "motion-item-added");
    });
  };

  const dynamicObserver = new MutationObserver((records) => {
    records.forEach((record) => record.addedNodes.forEach(animateAddedContent));
  });
  dynamicObserver.observe(mainPanel, { childList: true, subtree: true });

  const selectionProfile = document.querySelector("#selection-profile");
  const longLiveSettings = document.querySelector("#long-live-settings");
  selectionProfile?.addEventListener("change", () => {
    if (longLiveSettings && !longLiveSettings.hidden) replayClass(longLiveSettings, "motion-item-added");
  });

  const videoFileInput = document.querySelector("#video-file-input");
  const uploadPanel = document.querySelector("#upload-source-panel");
  videoFileInput?.addEventListener("change", () => replayClass(uploadPanel, "motion-confirm"));

  document.querySelectorAll("[data-center-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      const panel = document.querySelector(`[data-center-panel="${CSS.escape(button.dataset.centerTab || "")}"]`);
      replayClass(panel, "motion-panel-activated");
    });
  });

  document.querySelectorAll("[data-prompt-preset-tab] input").forEach((input) => {
    input.addEventListener("change", () => {
      const card = document.querySelector(`[data-prompt-preset-card][data-preset-id="${CSS.escape(input.value)}"]`);
      replayClass(card, "motion-panel-activated");
    });
  });

  window.setTimeout(() => body.classList.add("motion-settled"), 620);
})();
