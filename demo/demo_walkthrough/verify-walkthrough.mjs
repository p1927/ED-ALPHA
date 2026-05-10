import fs from "node:fs/promises";
import { createReadStream, readFileSync } from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const artifactDir = path.join(repoRoot, "tmp", "260509_final_thanks_popup", "here");
const outDir = path.join(repoRoot, "out");
const port = Number(process.env.DEMO_VIDEO_PORT || 3100);
const baseUrl = `http://127.0.0.1:${port}`;
const alignment = JSON.parse(
  readFileSync(path.join(repoRoot, "public", "demo_walkthrough", "audio", "cover_vocal", "alignment.json"), "utf8"),
);
const durationSeconds = Number(alignment.duration_seconds);

function formatTime(seconds) {
  const rounded = Math.max(0, Math.min(durationSeconds, Math.floor(seconds)));
  const minutes = Math.floor(rounded / 60);
  const remainingSeconds = rounded % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`;
}

function captureTimeForScene(sceneId, offset = 1.4) {
  const cue = alignment.cues.find((candidate) => candidate.scene_id === sceneId);
  if (!cue) {
    throw new Error(`No alignment cue for scene ${sceneId}`);
  }
  return Math.min(cue.end - 0.1, cue.start + offset);
}

function captureTimeForCue(sceneId, sceneLineIndex, offset = 1.4) {
  const cue = alignment.cues.find(
    (candidate) => candidate.scene_id === sceneId && candidate.scene_line_index === sceneLineIndex,
  );
  if (!cue) {
    throw new Error(`No alignment cue for scene ${sceneId} line ${sceneLineIndex}`);
  }
  return Math.min(cue.end - 0.1, cue.start + offset);
}

const captures = [
  { label: "intro", time: captureTimeForScene("intro") },
  {
    label: "overview-objective",
    time: captureTimeForCue("background", 1),
    expectedScene: "background",
    expectedTargetId: "benchmark-overview-objective",
  },
  {
    label: "overview-inputs",
    time: captureTimeForCue("background", 2),
    expectedScene: "background",
    expectedTargetId: "benchmark-overview-inputs",
  },
  {
    label: "overview-submission-shape",
    time: captureTimeForCue("background", 3),
    expectedScene: "background",
    expectedTargetId: "benchmark-overview-submission-shape",
  },
  {
    label: "overview-ground-truth",
    time: captureTimeForCue("background", 4),
    expectedScene: "background",
    expectedTargetId: "benchmark-overview-ground-truth",
  },
  {
    label: "overview-evaluation",
    time: captureTimeForCue("workflow", 1),
    expectedScene: "workflow",
    expectedTargetId: "benchmark-overview-evaluation",
  },
  { label: "controls", time: captureTimeForScene("controls") },
  {
    label: "experiment-selector",
    time: captureTimeForCue("controls", 2),
    expectedScene: "controls",
    expectedTargetId: "experiment-selector",
  },
  {
    label: "run-selector",
    time: captureTimeForCue("controls", 4),
    expectedScene: "controls",
    expectedTargetId: "run-selector",
  },
  { label: "experiment-config", time: captureTimeForScene("config") },
  { label: "metrics-chart", time: captureTimeForScene("metrics") },
  { label: "top-k-table", time: captureTimeForScene("table") },
  { label: "evidence-audit", time: captureTimeForScene("evidence") },
  {
    label: "final-thanks",
    time: captureTimeForCue("end", 3),
    expectedScene: "end",
    expectFinalPopup: true,
  },
  { label: "final", time: Math.max(0, durationSeconds) },
];
const finalTimestamp = formatTime(durationSeconds);

function timestampLabel(seconds) {
  return String(Math.floor(seconds)).padStart(3, "0");
}

function frameUrl(time) {
  const url = new URL("/walkthrough", baseUrl);
  url.searchParams.set("t", String(time));
  url.searchParams.set("paused", "1");
  return url.href;
}

async function cleanArtifacts() {
  await fs.mkdir(artifactDir, { recursive: true });
  const previousArtifacts = await fs.readdir(artifactDir);
  await Promise.all(
    previousArtifacts
      .filter((file) => file.endsWith(".png") || file.endsWith(".json") || file.endsWith(".md") || file.endsWith(".webm"))
      .map((file) => fs.rm(path.join(artifactDir, file), { force: true })),
  );
}

function contentType(filePath) {
  if (filePath.endsWith(".html")) return "text/html; charset=utf-8";
  if (filePath.endsWith(".js")) return "text/javascript; charset=utf-8";
  if (filePath.endsWith(".css")) return "text/css; charset=utf-8";
  if (filePath.endsWith(".json")) return "application/json; charset=utf-8";
  if (filePath.endsWith(".svg")) return "image/svg+xml";
  if (filePath.endsWith(".png")) return "image/png";
  if (filePath.endsWith(".jpg") || filePath.endsWith(".jpeg")) return "image/jpeg";
  if (filePath.endsWith(".mp3")) return "audio/mpeg";
  if (filePath.endsWith(".woff2")) return "font/woff2";
  return "application/octet-stream";
}

async function startServer() {
  await fs.access(path.join(outDir, "demo-video", "index.html"));

  const server = http.createServer(async (request, response) => {
    try {
      const requestUrl = new URL(request.url || "/", baseUrl);
      let pathname = decodeURIComponent(requestUrl.pathname);
      if (pathname.endsWith("/")) {
        pathname += "index.html";
      }

      let filePath = path.normalize(path.join(outDir, pathname));
      if (!filePath.startsWith(outDir)) {
        response.writeHead(403);
        response.end("Forbidden");
        return;
      }

      try {
        const stat = await fs.stat(filePath);
        if (stat.isDirectory()) {
          filePath = path.join(filePath, "index.html");
        }
      } catch (_error) {
        if (!path.extname(filePath)) {
          filePath = path.join(filePath, "index.html");
        }
      }

      await fs.access(filePath);
      response.writeHead(200, { "Content-Type": contentType(filePath) });
      createReadStream(filePath).pipe(response);
    } catch (_error) {
      response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      response.end("Not found");
    }
  });

  await new Promise((resolve) => server.listen(port, "127.0.0.1", resolve));
  return server;
}

async function stopServer(server) {
  await new Promise((resolve) => server.close(resolve));
}

async function inspectLayout(page, capture) {
  return page.evaluate(({ label, finalTimestamp, expectedScene, expectedTargetId, expectFinalPopup }) => {
    const failures = [];
    const warnings = [];
    const state = window.__edAlphaDemoState;
    const subtitle = document.querySelector("[data-demo-subtitle]");
    const bubble = document.querySelector("[data-demo-bubble]");
    const spotlight = document.querySelector("[data-demo-spotlight]");
    const expectedTarget = expectedTargetId ? document.querySelector(`[data-tour-id="${expectedTargetId}"]`) : null;
    const playbackControls = document.querySelector("[data-demo-playback-controls]");
    const progress = document.querySelector("[data-demo-progress]");
    const finalPopup = document.querySelector("[data-demo-final-popup]");
    const timestamp = [...document.querySelectorAll("div")].find((element) => element.textContent === state?.timestamp);
    const activeSubtitleWord = document.querySelector("[data-subtitle-word='active']");

    const rect = (element) => {
      const r = element.getBoundingClientRect();
      return {
        left: r.left,
        top: r.top,
        right: r.right,
        bottom: r.bottom,
        width: r.width,
        height: r.height,
      };
    };

    const intersects = (a, b) =>
      a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;

    const insideViewport = (box, pad = 0) =>
      box.left >= -pad &&
      box.top >= -pad &&
      box.right <= window.innerWidth + pad &&
      box.bottom <= window.innerHeight + pad;

    const visible = (element) => {
      if (!element) return false;
      const r = rect(element);
      if (r.width <= 0 || r.height <= 0) return false;
      let current = element;
      while (current && current.nodeType === Node.ELEMENT_NODE) {
        const style = getComputedStyle(current);
        if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) <= 0.01) {
          return false;
        }
        current = current.parentElement;
      }
      return true;
    };

    const clippedTextNodes = [];
    document
      .querySelectorAll(
        "[data-demo-subtitle], [data-demo-bubble] div, [data-demo-final-popup]"
      )
      .forEach((element) => {
        if (!visible(element)) return;
        const overflowX = element.scrollWidth - element.clientWidth;
        const overflowY = element.scrollHeight - element.clientHeight;
        if (overflowX > 3 || overflowY > 3) {
          clippedTextNodes.push({
            text: element.textContent.trim().replace(/\s+/g, " ").slice(0, 100),
            overflowX,
            overflowY,
          });
        }
      });

    if (!state) failures.push("demo video state is missing");
    if (!visible(subtitle)) failures.push("subtitle is missing or not visible");
    if (!visible(activeSubtitleWord)) failures.push("active subtitle word highlight is missing");
    if (document.body.textContent.includes("NOW READING") || document.body.textContent.includes("Now reading")) {
      failures.push("removed NOW READING label is still present");
    }
    if (document.querySelector("[data-demo-bubble]")) {
      failures.push("removed floating callout window is still present");
    }
    if (expectFinalPopup) {
      if (!visible(finalPopup)) {
        failures.push("final thank-you popup is missing or not visible");
      } else {
        const popupText = finalPopup.textContent.replace(/\s+/g, " ").trim();
        const popupLink = finalPopup.querySelector("a");
        const popupIcon = finalPopup.querySelector("img");
        if (!popupText.includes("github.com/E9Technologies/ED-ALPHA")) {
          failures.push("final thank-you popup does not show the GitHub URL");
        }
        if (!popupText.includes("preparing run data")) {
          failures.push("final thank-you popup does not mention preparing run data");
        }
        if (popupLink?.href !== "https://github.com/E9Technologies/ED-ALPHA") {
          failures.push("final thank-you popup GitHub link is not clickable");
        }
        if (!popupIcon?.src.includes("/demo_walkthrough/github.png")) {
          failures.push("final thank-you popup does not use the GitHub image asset");
        }
        if (state?.targetId) {
          failures.push(`final thank-you cue should not spotlight a dashboard target, observed ${state.targetId}`);
        }
      }
    }
    if (!visible(progress)) failures.push("progress bar is missing or not visible");
    if (expectedTargetId && state?.targetId !== expectedTargetId) {
      failures.push(`expected spotlight target ${expectedTargetId}, observed ${state?.targetId || "unknown"}`);
    }
    if (expectedTargetId && !visible(expectedTarget)) {
      failures.push(`expected target ${expectedTargetId} is missing or not visible`);
    }

    if (subtitle && !insideViewport(rect(subtitle), 1)) {
      failures.push("subtitle is not fully inside the viewport");
    }

    if (bubble && visible(bubble)) {
      const bubbleRect = rect(bubble);
      if (!insideViewport(bubbleRect, 1)) {
        failures.push("speech bubble is not fully inside the viewport");
      }
      if (subtitle && intersects(bubbleRect, rect(subtitle))) {
        failures.push("speech bubble overlaps subtitle");
      }
    }

    if (finalPopup && visible(finalPopup)) {
      const finalPopupRect = rect(finalPopup);
      if (!insideViewport(finalPopupRect, 1)) {
        failures.push("final thank-you popup is not fully inside the viewport");
      }
      if (subtitle && intersects(finalPopupRect, rect(subtitle))) {
        failures.push("final thank-you popup overlaps subtitle");
      }
      if (playbackControls && visible(playbackControls) && intersects(finalPopupRect, rect(playbackControls))) {
        failures.push("final thank-you popup overlaps playback controls");
      }
      if (timestamp && visible(timestamp) && intersects(finalPopupRect, rect(timestamp))) {
        failures.push("final thank-you popup overlaps timestamp");
      }
      if (spotlight && visible(spotlight) && intersects(finalPopupRect, rect(spotlight))) {
        failures.push("final thank-you popup overlaps highlighted component");
      }
    }

    if (spotlight && visible(spotlight) && !insideViewport(rect(spotlight), 24)) {
      failures.push("spotlight is not inside the viewport");
    }

    if (spotlight && playbackControls && visible(spotlight) && visible(playbackControls)) {
      const spotlightRect = rect(spotlight);
      const controlsRect = rect(playbackControls);
      if (intersects(spotlightRect, controlsRect)) {
        failures.push("playback controls overlap the highlighted component");
      }
    }

    if (clippedTextNodes.length > 0) {
      failures.push(`visible text clipping detected in ${clippedTextNodes.length} element(s)`);
    }

    const resolvedExpectedScene =
      expectedScene ??
      (label === "experiment-config"
        ? "config"
        : label === "metrics-chart"
          ? "metrics"
          : label === "top-k-table"
            ? "table"
              : label === "evidence-audit"
                ? "evidence"
                : label === "final-thanks"
                  ? "end"
                : label === "final"
                  ? "end"
                  : label);

    if (state?.scene !== resolvedExpectedScene) {
      warnings.push(`expected scene ${resolvedExpectedScene}, observed ${state?.scene || "unknown"}`);
    }

    if (label === "final" && state?.timestamp !== finalTimestamp) {
      failures.push(`final frame timestamp is ${state?.timestamp}, expected ${finalTimestamp}`);
    }

    return {
      label,
      scene: state?.scene || "unknown",
      timestamp: state?.timestamp || "unknown",
      subtitle: subtitle?.textContent.trim() || "",
      targetId: state?.targetId || "unknown",
      failures,
      warnings,
      clippedTextNodes,
      originalDashboardPresent: Boolean(document.querySelector("[data-tour-id='dashboard-overview']")),
    };
  }, { ...capture, finalTimestamp });
}

async function runVerification() {
  await cleanArtifacts();
  const server = await startServer();
  let browser;

  try {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      viewport: { width: 1600, height: 900 },
      deviceScaleFactor: 1,
      recordVideo: {
        dir: artifactDir,
        size: { width: 1600, height: 900 },
      },
    });

    const page = await context.newPage();
    const video = page.video();
    const results = [];

    for (const capture of captures) {
      await page.goto(frameUrl(capture.time), { waitUntil: "networkidle" });
      await page.waitForSelector("[data-tour-id='dashboard-overview']", { timeout: 15000 });
      await page.waitForFunction(() => window.__edAlphaDemoState);
      await page.waitForTimeout(450);
      const layout = await inspectLayout(page, capture);
      const screenshot = `${timestampLabel(capture.time)}_${capture.label}.png`;
      await page.screenshot({
        path: path.join(artifactDir, screenshot),
        fullPage: false,
        animations: "disabled",
      });
      results.push({ ...layout, time: capture.time, screenshot });
    }

    await page.goto(`${baseUrl}/walkthrough?speed=60`, { waitUntil: "networkidle" });
    await page.waitForFunction(() => window.__edAlphaDemoState?.paused === true);
    const initialPlaybackState = await page.evaluate(() => ({
      state: window.__edAlphaDemoState,
      buttonText: document.querySelector("[data-demo-play-pause]")?.textContent?.trim() || "",
    }));
    await page.click("[data-demo-play-pause]");
    await page.waitForFunction(
      (expected) => window.__edAlphaDemoState?.timestamp === expected,
      finalTimestamp,
      { timeout: 8000 },
    );
    const startedPlaybackState = await page.evaluate(() => window.__edAlphaDemoState);
    await page.screenshot({
      path: path.join(artifactDir, "start_button_reaches_final.png"),
      fullPage: false,
      animations: "disabled",
    });

    await context.close();
    const recordedVideoPath = video ? await video.path() : null;
    if (recordedVideoPath) {
      await fs.rename(recordedVideoPath, path.join(artifactDir, "playwright_fast_autoplay_confirmation.webm"));
    }

    const report = {
      generatedAt: new Date().toISOString(),
      route: `${baseUrl}/walkthrough`,
      artifactDir,
      viewport: { width: 1600, height: 900 },
      initialPlaybackState,
      startedPlaybackState,
      results,
      passed:
        results.every((result) => result.failures.length === 0 && result.originalDashboardPresent) &&
        initialPlaybackState?.state?.paused === true &&
        initialPlaybackState?.buttonText === "Start" &&
        startedPlaybackState?.timestamp === finalTimestamp,
    };

    await fs.writeFile(path.join(artifactDir, "verification-report.json"), JSON.stringify(report, null, 2));

    const lines = [
      "# ED-ALPHA Walkthrough Verification",
      "",
      `Generated: ${report.generatedAt}`,
      `Route: ${report.route}`,
      `Viewport: ${report.viewport.width}x${report.viewport.height}`,
      `Initial playback: ${initialPlaybackState?.state?.timestamp}, ${initialPlaybackState?.buttonText}`,
      `Started playback timestamp: ${startedPlaybackState?.timestamp}`,
      `Overall result: ${report.passed ? "PASS" : "FAIL"}`,
      "",
      "| Frame | Scene | Timestamp | Screenshot | Issues |",
      "| --- | --- | --- | --- | --- |",
      ...results.map((result) => {
        const issues = [...result.failures, ...result.warnings].join("; ") || "None";
        return `| ${result.label} | ${result.scene} | ${result.timestamp} | ${result.screenshot} | ${issues} |`;
      }),
      "",
      "Manual inspection checklist:",
      "- original dashboard component placement is preserved;",
      "- subtitles are visible and readable;",
      "- subtitle word-by-word highlight is visible;",
      "- no floating callout windows are present;",
      "- spotlights point to the intended real UI element;",
      "- no text overlaps other text, controls, charts, or table rows;",
      "- highlighted components are fully inside the viewport;",
      "- reading highlight and callouts do not hide important values;",
      "- progress bar and timestamp are legible;",
      `- the final frame is stable at ${finalTimestamp}.`,
      "",
    ];
    await fs.writeFile(path.join(artifactDir, "verification-report.md"), lines.join("\n"));

    if (!report.passed) {
      console.error(JSON.stringify(report, null, 2));
      process.exitCode = 1;
      return;
    }

    console.log(`Verification passed. Artifacts saved to ${artifactDir}`);
  } finally {
    if (browser) {
      await browser.close().catch(() => {});
    }
    await stopServer(server);
  }
}

runVerification().catch((error) => {
  console.error(error);
  process.exit(1);
});
