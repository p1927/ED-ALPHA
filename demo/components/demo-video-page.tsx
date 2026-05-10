"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { CSSProperties } from "react"
import { Pause, Play, StepBack, StepForward, Volume2, VolumeX } from "lucide-react"
import { DashboardPage } from "@/app/page"
import scriptData from "@/demo_walkthrough/data/scripts.json"
import alignmentData from "@/public/demo_walkthrough/audio/cover_vocal/alignment.json"

const AUDIO_BASE_PATH = `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/demo_walkthrough/audio/cover_vocal`
const GITHUB_ICON_PATH = `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/demo_walkthrough/github.png`

type DemoScene = {
  id: string
  start: number
  end: number
  targetId?: string
  title?: string
  bubble?: string
  subtitles: string[]
  scrollBlock?: ScrollLogicalPosition
  bubblePlacement?: "auto" | "below"
}

type AlignmentWord = {
  text: string
  start: number
  end: number
  source?: string
}

type AlignmentCue = {
  index: number
  scene_id: string
  scene_line_index: number
  text: string
  audio_file: string
  start: number
  end: number
  duration_seconds: number
  words: AlignmentWord[]
}

type AlignmentData = {
  duration_seconds: number
  gap_seconds: number
  cues: AlignmentCue[]
}

const scenes = scriptData.scenes as DemoScene[]

const alignment = alignmentData as AlignmentData
const DURATION_SECONDS = alignment.duration_seconds
const alignedCues = alignment.cues.map((cue, index) => ({
  index: cue.index,
  sceneId: cue.scene_id,
  sceneLineIndex: cue.scene_line_index,
  text: cue.text,
  start: cue.start,
  end: cue.end,
  displayEnd: alignment.cues[index + 1]?.start ?? cue.end,
  durationSeconds: cue.duration_seconds,
  audioSrc: `${AUDIO_BASE_PATH}/${cue.audio_file}`,
  words: cue.words,
}))

type SubtitleCue = {
  index: number
  text: string
  start: number
  end: number
  displayEnd: number
  sceneId: string
  sceneLineIndex: number
  durationSeconds: number
  audioSrc: string
  words: AlignmentWord[]
}

type Rect = {
  top: number
  left: number
  width: number
  height: number
  right: number
  bottom: number
}

type Size = {
  width: number
  height: number
}

type RuntimeParams = {
  fixedTime: number | null
  paused: boolean
  speed: number
  exportMode: boolean
  audioDisabled: boolean
  ready: boolean
}

const SPOTLIGHT_PADDING = 10
const CONTROL_CLEARANCE = 18
const OVERLAY_EDGE_MARGIN = 24
const TIMESTAMP_CLEARANCE_WIDTH = 124
const SUBTITLE_CLEARANCE_HEIGHT = 136

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max)
}

function expandRect(rect: Rect, padding: number): Rect {
  return {
    top: rect.top - padding,
    left: rect.left - padding,
    width: rect.width + padding * 2,
    height: rect.height + padding * 2,
    right: rect.right + padding,
    bottom: rect.bottom + padding,
  }
}

function intersects(a: Rect, b: Rect) {
  return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top
}

function choosePlaybackControlsStyle(targetRect: Rect | null, controlsSize: Size | null): CSSProperties {
  const fallback = { left: OVERLAY_EDGE_MARGIN, top: 20 }

  if (typeof window === "undefined" || !controlsSize) {
    return fallback
  }

  const width = Math.ceil(controlsSize.width)
  const height = Math.ceil(controlsSize.height)
  const viewportWidth = window.innerWidth
  const viewportHeight = window.innerHeight
  const top = 20
  const bottomAboveSubtitles = Math.max(
    OVERLAY_EDGE_MARGIN,
    viewportHeight - height - SUBTITLE_CLEARANCE_HEIGHT,
  )
  const right = Math.max(OVERLAY_EDGE_MARGIN, viewportWidth - width - OVERLAY_EDGE_MARGIN)
  const rightBeforeTimestamp = Math.max(
    OVERLAY_EDGE_MARGIN,
    viewportWidth - width - TIMESTAMP_CLEARANCE_WIDTH,
  )
  const candidates = [
    { left: OVERLAY_EDGE_MARGIN, top },
    { left: rightBeforeTimestamp, top },
    { left: OVERLAY_EDGE_MARGIN, top: bottomAboveSubtitles },
    { left: right, top: bottomAboveSubtitles },
  ]
  const spotlightBounds = targetRect ? expandRect(targetRect, SPOTLIGHT_PADDING + CONTROL_CLEARANCE) : null
  const insideViewport = (candidate: { left: number; top: number }) =>
    candidate.left >= OVERLAY_EDGE_MARGIN &&
    candidate.top >= OVERLAY_EDGE_MARGIN - 4 &&
    candidate.left + width <= viewportWidth - OVERLAY_EDGE_MARGIN &&
    candidate.top + height <= viewportHeight - OVERLAY_EDGE_MARGIN
  const candidateRect = (candidate: { left: number; top: number }): Rect => ({
    left: candidate.left,
    top: candidate.top,
    width,
    height,
    right: candidate.left + width,
    bottom: candidate.top + height,
  })
  const nonOverlappingCandidate = candidates.find(
    (candidate) => insideViewport(candidate) && (!spotlightBounds || !intersects(candidateRect(candidate), spotlightBounds)),
  )

  if (nonOverlappingCandidate) {
    return {
      left: Math.round(nonOverlappingCandidate.left),
      top: Math.round(nonOverlappingCandidate.top),
    }
  }

  if (!spotlightBounds) {
    return fallback
  }

  const targetCenterX = spotlightBounds.left + spotlightBounds.width / 2
  const targetCenterY = spotlightBounds.top + spotlightBounds.height / 2
  const farthestCandidate = candidates
    .filter(insideViewport)
    .map((candidate) => {
      const rect = candidateRect(candidate)
      const centerX = rect.left + rect.width / 2
      const centerY = rect.top + rect.height / 2
      return {
        candidate,
        distance: Math.hypot(centerX - targetCenterX, centerY - targetCenterY),
      }
    })
    .sort((a, b) => b.distance - a.distance)[0]?.candidate

  return farthestCandidate
    ? {
        left: Math.round(farthestCandidate.left),
        top: Math.round(farthestCandidate.top),
      }
    : fallback
}

function formatTime(seconds: number) {
  const rounded = Math.max(0, Math.min(DURATION_SECONDS, Math.floor(seconds)))
  const minutes = Math.floor(rounded / 60)
  const remainingSeconds = rounded % 60
  return `${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`
}

function getScene(time: number) {
  return scenes.find((scene) => time >= scene.start && time < scene.end) ?? scenes[scenes.length - 1]
}

function getSceneById(sceneId: string) {
  return scenes.find((scene) => scene.id === sceneId) ?? scenes[0]
}

function getActiveSubtitleCue(time: number): SubtitleCue {
  return alignedCues.find((cue) => time >= cue.start && time < cue.displayEnd) ?? alignedCues[alignedCues.length - 1]
}

function getActiveTargetId(scene: DemoScene, cue: SubtitleCue) {
  const lineIndex = Math.max(0, cue.sceneLineIndex - 1)

  if (cue.sceneId === "end" && cue.sceneLineIndex === 3) {
    return ""
  }

  if (cue.sceneId === "background") {
    return (
      [
        "benchmark-overview-objective",
        "benchmark-overview-inputs",
        "benchmark-overview-submission-shape",
        "benchmark-overview-ground-truth",
      ][lineIndex] ?? scene.targetId ?? ""
    )
  }

  if (cue.sceneId === "workflow") {
    return (
      [
        "benchmark-overview-evaluation",
      ][lineIndex] ?? scene.targetId ?? ""
    )
  }

  if (cue.sceneId === "controls") {
    return (
      [
        "experiment-controls",
        "experiment-selector",
        "experiment-selector",
        "run-selector",
        "run-selector",
      ][lineIndex] ?? scene.targetId ?? ""
    )
  }

  return scene.targetId ?? ""
}

function getActiveWordIndex(words: AlignmentWord[], cueAudioTime: number) {
  const activeIndex = words.findIndex((word) => cueAudioTime >= word.start && cueAudioTime < word.end)
  if (activeIndex !== -1) {
    return activeIndex
  }
  const nextWordIndex = words.findIndex((word) => cueAudioTime < word.start)
  if (nextWordIndex !== -1) {
    return Math.max(0, nextWordIndex)
  }
  return Math.max(0, words.length - 1)
}

function getTargetElement(targetId?: string) {
  if (!targetId || typeof document === "undefined") {
    return null
  }

  return document.querySelector<HTMLElement>(`[data-tour-id="${targetId}"]`)
}

export function DemoVideoPage() {
  const [runtimeParams, setRuntimeParams] = useState<RuntimeParams>({
    fixedTime: null,
    paused: false,
    speed: 1,
    exportMode: false,
    audioDisabled: false,
    ready: false,
  })
  const { fixedTime, paused, speed, exportMode, audioDisabled } = runtimeParams
  const startedAt = useRef(0)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const playbackControlsRef = useRef<HTMLDivElement | null>(null)
  const shouldEnableNarration = fixedTime === null && speed === 1 && !audioDisabled
  const [narrationEnabled, setNarrationEnabled] = useState(shouldEnableNarration)
  const [narrationBlocked, setNarrationBlocked] = useState(false)
  const [manualPaused, setManualPaused] = useState(true)
  const [hasStartedPlayback, setHasStartedPlayback] = useState(false)
  const [time, setTime] = useState(0)
  const timeRef = useRef(0)
  const [audioTime, setAudioTime] = useState(0)
  const [targetRect, setTargetRect] = useState<Rect | null>(null)
  const [playbackControlsSize, setPlaybackControlsSize] = useState<Size | null>(null)
  const playbackPaused = paused || manualPaused || fixedTime !== null
  const activeCue = getActiveSubtitleCue(time)
  const activeCueListIndex = Math.max(
    0,
    alignedCues.findIndex((cue) => cue.index === activeCue.index),
  )
  const scene = getSceneById(activeCue.sceneId)
  const activeTargetId = getActiveTargetId(scene, activeCue)
  const activeAudioUrl = typeof window === "undefined" ? activeCue.audioSrc : new URL(activeCue.audioSrc, window.location.href).href
  const cueAudioTime =
    narrationEnabled && audioRef.current && audioRef.current.src === activeAudioUrl
      ? audioTime
      : clamp(time - activeCue.start, 0, activeCue.durationSeconds)
  const activeSubtitleIndex = getActiveWordIndex(activeCue.words, cueAudioTime)
  const activeWordText = activeCue.words[activeSubtitleIndex]?.text ?? ""
  const isFinalThanksCue = activeCue.sceneId === "end" && activeCue.sceneLineIndex === 3

  const setDemoTime = useCallback((nextTime: number) => {
    const clampedTime = clamp(nextTime, 0, DURATION_SECONDS)
    timeRef.current = clampedTime
    setTime(clampedTime)
  }, [])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const nextSpeed = Number(params.get("speed") || "1")
    setRuntimeParams({
      fixedTime: params.has("t") ? Number(params.get("t")) : null,
      paused: params.get("paused") === "1",
      speed: Number.isFinite(nextSpeed) ? nextSpeed : 1,
      exportMode: params.get("export") === "1",
      audioDisabled: params.get("audio") === "0",
      ready: true,
    })
  }, [])

  useEffect(() => {
    if (!runtimeParams.ready) {
      return
    }

    setNarrationEnabled(shouldEnableNarration)
  }, [runtimeParams.ready, shouldEnableNarration])

  const playActiveNarration = useCallback(
    (restart: boolean) => {
      const audio = audioRef.current
      if (!audio || paused || fixedTime !== null) {
        return
      }

      const nextSrc = new URL(activeCue.audioSrc, window.location.href).href
      const syncedCueTime = restart ? 0 : clamp(timeRef.current - activeCue.start, 0, activeCue.durationSeconds)
      if (audio.src !== nextSrc) {
        audio.src = activeCue.audioSrc
        audio.currentTime = syncedCueTime
        setAudioTime(syncedCueTime)
      } else if (restart) {
        audio.currentTime = 0
        setAudioTime(0)
      }
      audio.playbackRate = clamp(Number.isFinite(speed) ? speed : 1, 0.5, 2)
      audio
        .play()
        .then(() => {
          setNarrationBlocked(false)
          setNarrationEnabled(true)
        })
        .catch(() => {
          setNarrationEnabled(true)
          setNarrationBlocked(true)
        })
    },
    [activeCue.audioSrc, activeCue.durationSeconds, activeCue.start, fixedTime, paused, speed],
  )

  const seekToCue = useCallback(
    (cueIndex: number, shouldResume: boolean) => {
      const cue = alignedCues[clamp(cueIndex, 0, alignedCues.length - 1)]
      startedAt.current = performance.now() - (cue.start / Math.max(speed, 0.1)) * 1000
      setDemoTime(cue.start)
      setAudioTime(0)

      const audio = audioRef.current
      if (audio) {
        audio.pause()
        audio.src = cue.audioSrc
        audio.currentTime = 0
        audio.playbackRate = clamp(Number.isFinite(speed) ? speed : 1, 0.5, 2)
      }

      if (shouldResume && narrationEnabled && !paused && fixedTime === null) {
        setManualPaused(false)
        window.setTimeout(() => {
          const liveAudio = audioRef.current
          if (!liveAudio) {
            return
          }
          liveAudio
            .play()
            .then(() => {
              setNarrationBlocked(false)
              setNarrationEnabled(true)
            })
            .catch(() => {
              setNarrationEnabled(true)
              setNarrationBlocked(true)
            })
        }, 0)
      }
    },
    [fixedTime, narrationEnabled, paused, setDemoTime, speed],
  )

  const goToPreviousSlide = useCallback(() => {
    const shouldResume = !playbackPaused
    const targetIndex = cueAudioTime > 1 ? activeCueListIndex : Math.max(0, activeCueListIndex - 1)
    seekToCue(targetIndex, shouldResume)
  }, [activeCueListIndex, cueAudioTime, playbackPaused, seekToCue])

  const goToNextSlide = useCallback(() => {
    const shouldResume = !playbackPaused
    seekToCue(Math.min(alignedCues.length - 1, activeCueListIndex + 1), shouldResume)
  }, [activeCueListIndex, playbackPaused, seekToCue])

  const pausePlayback = useCallback(() => {
    setManualPaused(true)
    audioRef.current?.pause()
  }, [])

  const playPlayback = useCallback(() => {
    if (paused || fixedTime !== null) {
      return
    }

    const resumeTime = timeRef.current >= DURATION_SECONDS - 0.05 ? 0 : timeRef.current
    setDemoTime(resumeTime)
    startedAt.current = performance.now() - (resumeTime / Math.max(speed, 0.1)) * 1000
    setHasStartedPlayback(true)
    setManualPaused(false)
    if (narrationEnabled) {
      playActiveNarration(resumeTime === 0)
    }
  }, [fixedTime, narrationEnabled, paused, playActiveNarration, setDemoTime, speed])

  const measureTarget = useCallback(() => {
    const target = getTargetElement(activeTargetId)
    if (!target) {
      setTargetRect(null)
      return
    }

    const rect = target.getBoundingClientRect()
    setTargetRect({
      top: rect.top,
      left: rect.left,
      width: rect.width,
      height: rect.height,
      right: rect.right,
      bottom: rect.bottom,
    })
  }, [activeTargetId])

  const measurePlaybackControls = useCallback(() => {
    const controls = playbackControlsRef.current
    if (!controls) {
      return
    }

    const rect = controls.getBoundingClientRect()
    setPlaybackControlsSize((current) => {
      const next = { width: rect.width, height: rect.height }
      if (
        current &&
        Math.abs(current.width - next.width) < 0.5 &&
        Math.abs(current.height - next.height) < 0.5
      ) {
        return current
      }
      return next
    })
  }, [])

  useEffect(() => {
    if (fixedTime !== null) {
      setDemoTime(fixedTime)
      return
    }

    if (playbackPaused) {
      return
    }

    startedAt.current = performance.now() - (timeRef.current / Math.max(speed, 0.1)) * 1000
    let animationFrame = 0

    const update = (now: number) => {
      const nextTime = ((now - startedAt.current) / 1000) * speed
      setDemoTime(nextTime)

      if (nextTime < DURATION_SECONDS) {
        animationFrame = window.requestAnimationFrame(update)
      } else {
        setManualPaused(true)
      }
    }

    animationFrame = window.requestAnimationFrame(update)
    return () => window.cancelAnimationFrame(animationFrame)
  }, [fixedTime, playbackPaused, setDemoTime, speed])

  useEffect(() => {
    let animationFrame = 0
    const syncAudioTime = () => {
      const audio = audioRef.current
      if (audio) {
        setAudioTime(audio.currentTime)
      }
      animationFrame = window.requestAnimationFrame(syncAudioTime)
    }

    animationFrame = window.requestAnimationFrame(syncAudioTime)
    return () => window.cancelAnimationFrame(animationFrame)
  }, [])

  useEffect(() => {
    const controls = playbackControlsRef.current
    if (!controls) {
      return
    }

    measurePlaybackControls()
    const resizeObserver = new ResizeObserver(measurePlaybackControls)
    resizeObserver.observe(controls)
    window.addEventListener("resize", measurePlaybackControls)

    return () => {
      resizeObserver.disconnect()
      window.removeEventListener("resize", measurePlaybackControls)
    }
  }, [measurePlaybackControls])

  useEffect(() => {
    const animationFrame = window.requestAnimationFrame(measurePlaybackControls)
    return () => window.cancelAnimationFrame(animationFrame)
  }, [hasStartedPlayback, measurePlaybackControls, narrationBlocked, narrationEnabled, playbackPaused])

  useEffect(() => {
    const syncTarget = () => {
      const target = getTargetElement(activeTargetId)
      if (!target) {
        setTargetRect(null)
        return false
      }

      target.scrollIntoView({
        block: scene.scrollBlock ?? "center",
        inline: "nearest",
        behavior: "auto",
      })
      window.requestAnimationFrame(measureTarget)
      return true
    }

    syncTarget()
    const retryInterval = window.setInterval(() => {
      if (syncTarget()) {
        window.clearInterval(retryInterval)
      }
    }, 160)
    const timeout = window.setTimeout(() => {
      window.clearInterval(retryInterval)
      syncTarget()
    }, 1600)
    const updateViewport = () => {
      measureTarget()
    }

    updateViewport()
    window.addEventListener("resize", updateViewport)
    window.addEventListener("scroll", measureTarget, true)

    return () => {
      window.clearInterval(retryInterval)
      window.clearTimeout(timeout)
      window.removeEventListener("resize", updateViewport)
      window.removeEventListener("scroll", measureTarget, true)
    }
  }, [activeTargetId, measureTarget, scene.id, scene.scrollBlock])

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) {
      return
    }

    if (!narrationEnabled || playbackPaused) {
      audio.pause()
      return
    }

    playActiveNarration(false)

    return () => {
      audio.pause()
    }
  }, [narrationEnabled, playbackPaused, playActiveNarration])

  useEffect(() => {
    if (!narrationEnabled || !narrationBlocked || playbackPaused) {
      return
    }

    const startOnInteraction = () => {
      setNarrationBlocked(false)
      playActiveNarration(false)
    }

    window.addEventListener("pointerdown", startOnInteraction, { once: true, capture: true })
    window.addEventListener("keydown", startOnInteraction, { once: true, capture: true })
    return () => {
      window.removeEventListener("pointerdown", startOnInteraction, { capture: true })
      window.removeEventListener("keydown", startOnInteraction, { capture: true })
    }
  }, [narrationBlocked, narrationEnabled, playbackPaused, playActiveNarration])

  useEffect(() => {
    window.__edAlphaDemoState = {
      time,
      scene: scene.id,
      timestamp: formatTime(time),
      duration: DURATION_SECONDS,
      subtitle: activeCue.text,
      cueReadEnd: activeCue.end,
      audioSrc: activeCue.audioSrc,
      targetId: activeTargetId,
      cueAudioTime,
      activeWord: activeWordText,
      narrationEnabled,
      paused: playbackPaused,
    }
  }, [activeCue.audioSrc, activeCue.end, activeCue.text, activeTargetId, activeWordText, cueAudioTime, narrationEnabled, playbackPaused, scene.id, time])

  useEffect(() => {
    window.__edAlphaDemoSetTime = (nextTime: number) => {
      audioRef.current?.pause()
      setManualPaused(true)
      setDemoTime(nextTime)
    }

    return () => {
      delete window.__edAlphaDemoSetTime
    }
  }, [setDemoTime])

  const spotlightStyle = useMemo<CSSProperties>(() => {
    if (!targetRect) {
      return { opacity: 0 }
    }

    return {
      opacity: 1,
      left: targetRect.left - SPOTLIGHT_PADDING,
      top: targetRect.top - SPOTLIGHT_PADDING,
      width: targetRect.width + SPOTLIGHT_PADDING * 2,
      height: targetRect.height + SPOTLIGHT_PADDING * 2,
    }
  }, [targetRect])

  const playbackControlsStyle = useMemo<CSSProperties>(
    () => ({
      ...choosePlaybackControlsStyle(targetRect, playbackControlsSize),
      visibility: playbackControlsSize ? "visible" : "hidden",
    }),
    [playbackControlsSize, targetRect],
  )

  return (
    <div data-demo-video-page>
      <audio ref={audioRef} preload="auto" data-demo-narration-audio />
      <DashboardPage
        autoOpenTutorial={false}
        forceExpandFirstEvidence={scene.id === "evidence" || scene.id === "end"}
      />

      <div className="pointer-events-none fixed inset-0 z-50" data-demo-overlay>
        <style>{`
          @keyframes demo-read-sheen {
            0%, 18% { transform: translateX(-120%); }
            58%, 100% { transform: translateX(120%); }
          }
          ${
            exportMode
              ? `
          [data-nextjs-dev-tools-button],
          [data-nextjs-dev-tools-button] *,
          nextjs-portal {
            display: none !important;
            visibility: hidden !important;
            opacity: 0 !important;
            pointer-events: none !important;
          }
          `
              : ""
          }
        `}</style>
        <div
          className="absolute overflow-hidden rounded-2xl border border-white/85 bg-white/[0.03] shadow-[0_0_0_9999px_rgba(15,23,42,0.30),0_18px_70px_rgba(255,255,255,0.18),0_0_0_7px_rgba(255,255,255,0.22)] transition-all duration-500"
          style={spotlightStyle}
          data-demo-spotlight
        >
          <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/35 to-transparent opacity-80 [animation:demo-read-sheen_3.8s_ease-in-out_infinite]" />
          <div className="absolute inset-x-4 top-3 h-px bg-gradient-to-r from-transparent via-white to-transparent opacity-80" />
        </div>
        {scene.id === "intro" ? (
          <div className="absolute inset-x-0 top-24 mx-auto w-fit max-w-[min(1180px,calc(100vw-48px))] rounded-[1.75rem] border border-white/70 bg-white/90 px-10 py-7 text-center shadow-[0_24px_80px_rgba(15,23,42,0.18)] backdrop-blur-xl">
            <div className="max-w-5xl text-4xl font-black leading-tight tracking-normal text-slate-950">
              Ed-Alpha: An Open Benchmark for Predicting
              <br />
              Investment-Relevant Corporate Events from Open Data
            </div>
            <div className="mt-5 text-lg font-bold leading-7 text-slate-600">
              Shuya Bundo · Yuki Kawashima · Hideaki Joko · Takuya Shimano
            </div>
          </div>
        ) : null}
        <div className="absolute right-6 top-5 rounded-lg bg-white px-3 py-2 font-mono text-sm font-bold text-slate-950 shadow-lg">
          {formatTime(time)}
        </div>
        {!exportMode ? (
          <div
            ref={playbackControlsRef}
            className="pointer-events-auto absolute inline-flex h-11 items-center gap-1 rounded-lg border border-white/65 bg-white p-1 text-sm font-bold text-slate-950 shadow-lg transition-[left,top] duration-300"
            style={playbackControlsStyle}
            data-demo-playback-controls
            aria-label="Demo playback controls"
          >
            <button
              type="button"
              className="inline-flex h-9 items-center gap-1.5 rounded-md px-2.5 transition hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-sky-300 disabled:cursor-not-allowed disabled:opacity-45"
              onClick={goToPreviousSlide}
              disabled={activeCueListIndex === 0 && cueAudioTime <= 1}
              data-demo-previous-slide
              aria-label="Previous slide"
              title="Previous slide"
            >
              <StepBack aria-hidden="true" size={17} />
              <span>Previous</span>
            </button>
            <button
              type="button"
              className="inline-flex h-9 min-w-[86px] items-center justify-center gap-1.5 rounded-md px-2.5 transition hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-sky-300 disabled:cursor-not-allowed disabled:opacity-45"
              onClick={playbackPaused ? playPlayback : pausePlayback}
              disabled={paused || fixedTime !== null}
              data-demo-play-pause
              aria-label={playbackPaused ? (hasStartedPlayback ? "Play" : "Start") : "Pause"}
              title={playbackPaused ? (hasStartedPlayback ? "Play" : "Start") : "Pause"}
            >
              {playbackPaused ? <Play aria-hidden="true" size={17} /> : <Pause aria-hidden="true" size={17} />}
              <span>{playbackPaused ? (hasStartedPlayback ? "Play" : "Start") : "Pause"}</span>
            </button>
            <button
              type="button"
              className="inline-flex h-9 items-center gap-1.5 rounded-md px-2.5 transition hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-sky-300 disabled:cursor-not-allowed disabled:opacity-45"
              onClick={goToNextSlide}
              disabled={activeCueListIndex >= alignedCues.length - 1}
              data-demo-next-slide
              aria-label="Next slide"
              title="Next slide"
            >
              <span>Next</span>
              <StepForward aria-hidden="true" size={17} />
            </button>
            <div className="mx-1 h-6 w-px bg-slate-200" aria-hidden="true" />
            <button
              type="button"
              className="inline-flex h-9 min-w-[104px] items-center justify-center gap-1.5 rounded-md px-2.5 transition hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-sky-300"
              onClick={() => {
                setNarrationBlocked(false)
                if (narrationEnabled) {
                  audioRef.current?.pause()
                  setNarrationEnabled(false)
                  return
                }
                setNarrationEnabled(true)
                if (!playbackPaused) {
                  playActiveNarration(false)
                }
              }}
              data-demo-narration-toggle
              aria-pressed={narrationEnabled}
              aria-label={narrationEnabled ? "Turn narration off" : "Turn narration on"}
              title={narrationEnabled ? "Turn narration off" : "Turn narration on"}
            >
              {narrationEnabled ? <Volume2 aria-hidden="true" size={17} /> : <VolumeX aria-hidden="true" size={17} />}
              <span>{narrationBlocked ? "Start voice" : narrationEnabled ? "Voice on" : "Voice off"}</span>
            </button>
          </div>
        ) : null}
        {isFinalThanksCue ? (
          <div
            className="pointer-events-auto absolute left-1/2 top-1/2 flex w-[min(1040px,calc(100vw-48px))] -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-4 rounded-3xl border border-sky-200/85 bg-white/[0.96] px-9 py-8 text-center text-slate-950 shadow-[0_26px_100px_rgba(14,165,233,0.28),0_0_0_9px_rgba(255,255,255,0.24)] backdrop-blur-xl"
            data-demo-final-popup
          >
            <div className="text-4xl font-black leading-tight tracking-normal">Thank you for watching.</div>
            <a
              href="https://github.com/E9Technologies/ed-alpha"
              target="_blank"
              rel="noreferrer"
              className="flex max-w-4xl items-center justify-center gap-4 rounded-2xl border border-slate-200 bg-slate-50 px-7 py-5 text-2xl font-extrabold leading-9 text-slate-950 shadow-inner transition hover:border-sky-300 hover:bg-sky-50"
            >
              <img src={GITHUB_ICON_PATH} alt="" className="size-10 shrink-0" />
              <span>
                For running ED-ALPHA and preparing run data, visit{" "}
                <span className="whitespace-nowrap font-black text-sky-700 underline decoration-sky-300 underline-offset-4">
                  github.com/E9Technologies/ed-alpha
                </span>
              </span>
            </a>
          </div>
        ) : null}
        <div
          className="absolute bottom-11 left-[7.5vw] right-[7.5vw] flex min-h-[64px] items-center justify-center rounded-2xl border border-white/20 bg-slate-950/90 px-7 py-3 text-center text-xl font-semibold leading-7 text-white shadow-[0_18px_60px_rgba(15,23,42,0.22)] backdrop-blur-xl"
          data-demo-subtitle
          aria-label={activeCue.text}
        >
          <span className="max-w-[1180px]">
            {activeCue.words.map((word, index) => {
              const state =
                index < activeSubtitleIndex ? "past" : index === activeSubtitleIndex ? "active" : "future"

              return (
                <span
                  key={`${scene.id}-${activeCue.start}-${index}-${word.text}`}
                  className={
                    state === "active"
                      ? "border-b-2 border-sky-300 text-white [text-shadow:0_0_18px_rgba(255,255,255,0.42)] transition-all duration-200"
                      : state === "past"
                        ? "text-white/95 transition-colors duration-200"
                        : "text-white/72 transition-colors duration-200"
                  }
                  data-subtitle-word={state}
                >
                  {word.text}
                  {index < activeCue.words.length - 1 ? " " : ""}
                </span>
              )
            })}
          </span>
        </div>
        <div className="absolute bottom-5 left-6 right-6 h-2 overflow-hidden rounded-full bg-slate-950/15">
          <div
            className="h-full rounded-full bg-gradient-to-r from-blue-600 to-amber-600"
            style={{ width: `${(time / DURATION_SECONDS) * 100}%` }}
            data-demo-progress
          />
        </div>
      </div>
    </div>
  )
}

declare global {
  interface Window {
    __edAlphaDemoState?: {
      time: number
      scene: string
      timestamp: string
      duration: number
      subtitle: string
      cueReadEnd: number
      audioSrc: string
      targetId: string
      cueAudioTime: number
      activeWord: string
      narrationEnabled: boolean
      paused: boolean
    }
    __edAlphaDemoSetTime?: (time: number) => void
  }
}
