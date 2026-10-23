const NATIVE_HOST = "com.buihuuduc.slideflow";

function normalizeYouTubeTab(tab) {
  const source = new URL(tab.url || "");
  const hostname = source.hostname.toLowerCase();
  const allowedHosts = new Set([
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
  ]);
  if (!allowedHosts.has(hostname)) {
    throw new Error("Tab hiện tại không phải YouTube.");
  }

  let videoId = "";
  if (hostname === "youtu.be") {
    videoId = source.pathname.split("/").filter(Boolean)[0] || "";
  } else {
    videoId = source.searchParams.get("v") || "";
    if (!videoId && source.pathname.startsWith("/shorts/")) {
      videoId = source.pathname.split("/")[2] || "";
    }
  }
  if (/^[A-Za-z0-9_-]{11}$/.test(videoId)) {
    return {
      action: "enqueue",
      url: `https://www.youtube.com/watch?v=${videoId}`,
      title: (tab.title || "").replace(/\s+-\s+YouTube\s*$/, "").trim(),
    };
  }

  const playlistId = source.searchParams.get("list") || "";
  if (playlistId) {
    return {
      action: "playlist",
      url: `https://www.youtube.com/playlist?list=${encodeURIComponent(playlistId)}`,
      title: (tab.title || "").replace(/\s+-\s+YouTube\s*$/, "").trim(),
    };
  }
  throw new Error("Hãy mở một video hoặc playlist YouTube rồi thử lại.");
}

function showBadge(text, color, title) {
  chrome.action.setBadgeBackgroundColor({ color });
  chrome.action.setBadgeText({ text });
  chrome.action.setTitle({ title });
  setTimeout(() => {
    chrome.action.setBadgeText({ text: "" });
    chrome.action.setTitle({ title: "Gửi vào SlideFlow" });
  }, 4000);
}

chrome.action.onClicked.addListener((tab) => {
  let message;
  try {
    message = normalizeYouTubeTab(tab);
  } catch (error) {
    showBadge("!", "#B3261E", error.message);
    return;
  }

  showBadge("…", "#D24726", "Đang gửi vào SlideFlow…");
  chrome.runtime.sendNativeMessage(NATIVE_HOST, message, (response) => {
    if (chrome.runtime.lastError) {
      showBadge("!", "#B3261E", chrome.runtime.lastError.message);
      return;
    }
    if (!response || !response.ok) {
      showBadge("!", "#B3261E", response?.error || "SlideFlow không phản hồi.");
      return;
    }
    showBadge("✓", "#2E7D32", response.message || "Đã gửi vào SlideFlow.");
  });
});
