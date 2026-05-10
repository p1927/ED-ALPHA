import fs from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const artifactDir = path.join(repoRoot, "tmp", "260508_video_export", "here");
const frameDir = path.join(artifactDir, "frames");
const audioPath = path.join(artifactDir, "narration.m4a");
const outputPath = path.join(artifactDir, "ed-alpha-demo.mp4");
const manifestPath = path.join(repoRoot, "public", "demo_walkthrough", "audio", "cover_vocal", "manifest.json");
const alignmentPath = path.join(repoRoot, "public", "demo_walkthrough", "audio", "cover_vocal", "alignment.json");
const baseUrl = process.env.DEMO_VIDEO_URL || "http://127.0.0.1:3000/walkthrough";
const ffmpeg = process.env.FFMPEG || "/usr/local/bin/ffmpeg";

const fps = Number(process.env.DEMO_VIDEO_FPS || 24);
const outputWidth = Number(process.env.DEMO_VIDEO_WIDTH || 854);
const outputHeight = Number(process.env.DEMO_VIDEO_HEIGHT || 480);
const renderWidth = Number(process.env.DEMO_VIDEO_RENDER_WIDTH || 1280);
const renderHeight = Number(process.env.DEMO_VIDEO_RENDER_HEIGHT || 720);

function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: repoRoot,
      stdio: ["ignore", "inherit", "inherit"],
      ...options,
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) {
        resolve();
        return;
      }
      reject(new Error(`${command} exited with ${code}`));
    });
  });
}

async function pathExists(filePath) {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

async function writeNarrationAudio(manifest, alignment) {
  const inputs = [];
  const filters = [];
  const labels = [];

  manifest.lines.forEach((line, index) => {
    const cue = alignment.cues.find((candidate) => candidate.index === line.index);
    if (!cue) {
      throw new Error(`Missing alignment cue ${line.index}`);
    }
    const filePath = path.join(repoRoot, line.file.replace(/^demo_walkthrough\//, "public/demo_walkthrough/"));
    inputs.push("-i", filePath);
    const delayMs = Math.max(0, Math.round(cue.start * 1000));
    const label = `a${index}`;
    filters.push(`[${index}:a]adelay=${delayMs}:all=1[${label}]`);
    labels.push(`[${label}]`);
  });

  const filterComplex = `${filters.join(";")};${labels.join("")}amix=inputs=${labels.length}:duration=longest:normalize=0,atrim=0:${alignment.duration_seconds},asetpts=N/SR/TB[aout]`;
  await run(ffmpeg, [
    "-y",
    ...inputs,
    "-filter_complex",
    filterComplex,
    "-map",
    "[aout]",
    "-c:a",
    "aac",
    "-b:a",
    "192k",
    audioPath,
  ]);
}

async function renderFrames(alignment) {
  const frameCount = Math.ceil(alignment.duration_seconds * fps);
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: renderWidth, height: renderHeight },
    deviceScaleFactor: 1,
  });
  const url = new URL(baseUrl);
  url.searchParams.set("paused", "1");
  url.searchParams.set("audio", "0");
  url.searchParams.set("export", "1");

  await page.goto(url.href, { waitUntil: "networkidle" });
  await page.waitForFunction(() => window.__edAlphaDemoSetTime && window.__edAlphaDemoState);

  for (let frame = 0; frame < frameCount; frame += 1) {
    const time = Math.min(frame / fps, alignment.duration_seconds);
    await page.evaluate((nextTime) => window.__edAlphaDemoSetTime(nextTime), time);
    await page.waitForFunction(
      (nextTime) => Math.abs((window.__edAlphaDemoState?.time ?? -1) - nextTime) < 0.02,
      time,
    );
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await page.screenshot({
      path: path.join(frameDir, `frame_${String(frame + 1).padStart(6, "0")}.jpg`),
      type: "jpeg",
      quality: 88,
      fullPage: false,
      animations: "allow",
    });

    if ((frame + 1) % Math.max(1, fps * 5) === 0 || frame + 1 === frameCount) {
      const percent = (((frame + 1) / frameCount) * 100).toFixed(1);
      console.log(`Rendered ${frame + 1}/${frameCount} frames (${percent}%)`);
    }
  }

  await browser.close();
}

async function encodeVideo() {
  await run(ffmpeg, [
    "-y",
    "-framerate",
    String(fps),
    "-i",
    path.join(frameDir, "frame_%06d.jpg"),
    "-i",
    audioPath,
    "-vf",
    `scale=${outputWidth}:${outputHeight}:flags=lanczos`,
    "-c:v",
    "libx264",
    "-preset",
    "medium",
    "-crf",
    "18",
    "-pix_fmt",
    "yuv420p",
    "-r",
    String(fps),
    "-c:a",
    "aac",
    "-b:a",
    "192k",
    "-shortest",
    "-movflags",
    "+faststart",
    outputPath,
  ]);
}

async function main() {
  if (!(await pathExists(ffmpeg))) {
    throw new Error(`ffmpeg not found at ${ffmpeg}`);
  }

  const manifest = JSON.parse(await fs.readFile(manifestPath, "utf8"));
  const alignment = JSON.parse(await fs.readFile(alignmentPath, "utf8"));

  await fs.rm(artifactDir, { recursive: true, force: true });
  await fs.mkdir(frameDir, { recursive: true });

  console.log(
    `Exporting ${alignment.duration_seconds.toFixed(3)}s at ${outputWidth}x${outputHeight}, ${fps} fps ` +
      `(rendered at ${renderWidth}x${renderHeight})`,
  );
  console.log(`Writing artifacts under ${path.relative(repoRoot, artifactDir)}`);

  console.log("Building narration track...");
  await writeNarrationAudio(manifest, alignment);

  console.log("Rendering frames...");
  await renderFrames(alignment);

  console.log("Encoding MP4...");
  await encodeVideo();

  await fs.rm(frameDir, { recursive: true, force: true });
  console.log(`Wrote ${path.relative(repoRoot, outputPath)}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
