import asyncio
import base64
from collections.abc import Awaitable, Callable
from tempfile import SpooledTemporaryFile
from typing import Any

import structlog

POLL_INTERVAL_S = 2
AUDIO_PROBE_EVERY_DRAINS = 15
RECORDER_TIMESLICE_MS = 2_000
CHECKPOINT_CHUNKS = 5
SPOOL_MEMORY_LIMIT = 1_048_576
ANCHOR_ID = "__fakery-rec"

logger = structlog.get_logger(__name__)

_HOOK_TEMPLATE = """
(() => {
  if (window.__fakeryRtcHooked) return;
  window.__fakeryRtcHooked = true;
  const OriginalRTC = window.RTCPeerConnection || window.webkitRTCPeerConnection;
  const anchor = document.createElement('div');
  anchor.id = '__fakery-rec';
  anchor.style.display = 'none';
  const report = () => {
    anchor.setAttribute('data-pc-count', String(window.__fakeryPcCount || 0));
    anchor.setAttribute('data-track-count', String(window.__fakeryTrackCount || 0));
    anchor.setAttribute('data-rec-state', window.__fakeryRecState || 'idle');
    anchor.setAttribute('data-rec-error', window.__fakeryRecError || '');
    anchor.setAttribute('data-chunk-count', String(window.__fakeryChunkCount || 0));
    anchor.setAttribute('data-chunk-bytes', String(window.__fakeryChunkBytes || 0));
    anchor.setAttribute('data-track-kind', window.__fakeryTrackKind || '');
    anchor.setAttribute('data-track-muted', window.__fakeryTrackMuted || '');
    anchor.setAttribute('data-track-ready', window.__fakeryTrackReady || '');
    anchor.setAttribute('data-mime', window.__fakeryMime || '');
    anchor.setAttribute('data-recorder-state', window.__fakeryRecorder
      ? window.__fakeryRecorder.state : 'none');
  };
  const pickMime = () => {
    const candidates = ['audio/webm;codecs=opus', 'audio/webm', ''];
    for (const mime of candidates) {
      try {
        if (!mime || MediaRecorder.isTypeSupported(mime)) return mime;
      } catch (err) {}
    }
    return '';
  };
  const install = () => {
    document.documentElement.appendChild(anchor);
    if (!OriginalRTC) { window.__fakeryRecError = 'no-rtc'; report(); return; }
    window.__fakeryPcCount = 0;
    window.__fakeryTrackCount = 0;
    window.__fakeryRecorder = null;
    window.__fakeryAudioStream = null;
    window.__fakeryAudioTracks = new Map();
    window.__fakeryAudioSources = new Map();
    window.__fakeryAudioContext = null;
    window.__fakeryAudioDestination = null;
    const liveTracks = () => [...window.__fakeryAudioTracks.values()]
      .filter((t) => t.readyState === 'live');
    const audioAlive = () => liveTracks().length > 0;
    const ensureMixer = () => {
      if (window.__fakeryAudioDestination) return window.__fakeryAudioDestination;
      const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextCtor) throw new Error('no-audio-context');
      const context = new AudioContextCtor();
      const destination = context.createMediaStreamDestination();
      window.__fakeryAudioContext = context;
      window.__fakeryAudioDestination = destination;
      window.__fakeryAudioStream = destination.stream;
      context.resume().catch(() => {});
      return destination;
    };
    function HookedRTC(...args) {
      window.__fakeryPcCount += 1;
      report();
      const pc = new OriginalRTC(...args);
      pc.addEventListener('track', async (event) => {
        try {
          window.__fakeryTrackCount += 1;
          const track = event.track;
          window.__fakeryTrackKind = track.kind;
          window.__fakeryTrackMuted = String(track.muted);
          window.__fakeryTrackReady = track.readyState;
          report();
          if (track.kind !== 'audio') return;
          window.__fakeryAudioTracks.set(track.id, track);
          window.__fakeryAudioTrack = track;
          const destination = ensureMixer();
          const previousSource = window.__fakeryAudioSources.get(track.id);
          if (previousSource) {
            try { previousSource.disconnect(); } catch (err) {}
          }
          const source = window.__fakeryAudioContext.createMediaStreamSource(
            new MediaStream([track]),
          );
          source.connect(destination);
          window.__fakeryAudioSources.set(track.id, source);
          if (window.__fakeryRecorder && window.__fakeryRecorder.state === 'recording') {
            report();
            return;
          }
          const mime = pickMime();
          window.__fakeryMime = mime || 'default';
          const recorder = mime
            ? new MediaRecorder(destination.stream, {mimeType: mime})
            : new MediaRecorder(destination.stream);
          window.__fakeryRecorder = recorder;
          window.__fakeryRecState = 'recording';
          report();
          recorder.onerror = (e) => {
            window.__fakeryRecError = String((e && e.error) || 'recorder-error');
            report();
          };
          recorder.onstop = () => {
            window.__fakeryRecState = 'stopped';
            report();
          };
          track.addEventListener('ended', () => {
            window.__fakeryAudioTracks.delete(track.id);
            const source = window.__fakeryAudioSources.get(track.id);
            if (source) {
              try { source.disconnect(); } catch (err) {}
              window.__fakeryAudioSources.delete(track.id);
            }
            if (!audioAlive() && window.__fakeryRecorder) {
              try { window.__fakeryRecorder.stop(); } catch (err) {}
            }
            report();
          });
          recorder.ondataavailable = async (e) => {
            try {
              if (e.data.size === 0) return;
              const bytes = new Uint8Array(await e.data.arrayBuffer());
              let binary = '';
              for (let i = 0; i < bytes.length; i += 0x8000) {
                binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
              }
              const node = document.createElement('div');
              node.setAttribute('data-fakery-chunk', btoa(binary));
              anchor.appendChild(node);
              window.__fakeryChunkCount = (window.__fakeryChunkCount || 0) + 1;
              window.__fakeryChunkBytes = (window.__fakeryChunkBytes || 0) + bytes.length;
              window.__fakeryTrackMuted = String(track.muted);
              window.__fakeryTrackReady = track.readyState;
              report();
            } catch (err) {
              window.__fakeryRecError = String(err);
              report();
            }
          };
          recorder.start(__TIMESLICE_MS__);
        } catch (err) {
          window.__fakeryRecError = String(err);
          report();
        }
      });
      return pc;
    }
    HookedRTC.prototype = OriginalRTC.prototype;
    window.RTCPeerConnection = HookedRTC;
    window.webkitRTCPeerConnection = HookedRTC;
    report();
  };
  if (document.documentElement) install();
  else document.addEventListener('DOMContentLoaded', install);
})();
"""

RECORDER_HOOK = _HOOK_TEMPLATE.replace("__TIMESLICE_MS__", str(RECORDER_TIMESLICE_MS))

_COLLECT_CHUNKS = """
() => {
  const nodes = [...document.querySelectorAll('[data-fakery-chunk]')];
  const chunks = nodes.map((n) => n.getAttribute('data-fakery-chunk'));
  nodes.forEach((n) => n.remove());
  return chunks;
}
"""

_READ_DIAGNOSTICS = """
() => {
  const anchor = document.getElementById('__fakery-rec');
  if (!anchor) return null;
  return {
    pc_count: anchor.getAttribute('data-pc-count'),
    track_count: anchor.getAttribute('data-track-count'),
    rec_state: anchor.getAttribute('data-rec-state'),
    rec_error: anchor.getAttribute('data-rec-error'),
    chunk_count: anchor.getAttribute('data-chunk-count'),
    chunk_bytes: anchor.getAttribute('data-chunk-bytes'),
    track_kind: anchor.getAttribute('data-track-kind'),
    track_muted: anchor.getAttribute('data-track-muted'),
    track_ready: anchor.getAttribute('data-track-ready'),
    mime: anchor.getAttribute('data-mime'),
    recorder_state: anchor.getAttribute('data-recorder-state'),
  };
}
"""

_READ_AUDIO_TRACK = """
() => {
  const track = window.__fakeryAudioTrack;
  if (!track) return null;
  return {muted: track.muted, readyState: track.readyState, enabled: track.enabled};
}
"""

_STOP_RECORDER = """
async () => {
  const recorder = window.__fakeryRecorder;
  if (!recorder || recorder.state === 'inactive') return false;
  await new Promise((resolve) => {
    const previous = recorder.onstop;
    recorder.onstop = (event) => {
      if (previous) previous(event);
      resolve(true);
    };
    try { recorder.stop(); } catch (err) { resolve(false); }
  });
  return true;
}
"""


async def arm_via_cdp_eval(page: Any) -> bool:
    try:
        session = await page.context.new_cdp_session(page)
        await session.send("Runtime.evaluate", {"expression": RECORDER_HOOK})
        return True
    except Exception as err:
        logger.warning("recorder.cdp_arm_failed", error=type(err).__name__)
        return False


async def diagnostics(page: Any) -> dict:
    results: dict = {}
    for frame in page.frames:
        try:
            raw = await frame.evaluate(_READ_DIAGNOSTICS)
        except Exception:
            continue
        if raw:
            results[frame.url.rsplit("/", 1)[-1] or "main"] = raw
    return results


async def audio_track_state(page: Any) -> dict:
    try:
        return await page.evaluate(_READ_AUDIO_TRACK) or {}
    except Exception:
        return {}


class MeetingRecorder:
    def __init__(
        self,
        page: Any,
        sink: Callable[[bytes], None] | None = None,
        checkpoint_sink: Callable[[int, bytes], Awaitable[None]] | None = None,
    ) -> None:
        self._page = page
        self._sink = sink
        self._checkpoint_sink = checkpoint_sink
        self._checkpoint_buffer = bytearray()
        self._checkpoint_sequence = 0
        self._checkpoint_chunks = 0
        self.checkpoint_failed = False
        self._spool = SpooledTemporaryFile(max_size=SPOOL_MEMORY_LIMIT)
        self._size = 0
        self._task: asyncio.Task | None = None
        self._drains = 0

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> bytes:
        try:
            await asyncio.wait_for(self._page.evaluate(_STOP_RECORDER), timeout=5)
            await asyncio.sleep(0.25)
        except Exception:
            pass
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._drain()
        await self._flush_checkpoint()
        self._spool.seek(0)
        recording = self._spool.read()
        self._spool.close()
        diag = await diagnostics(self._page)
        audio = await audio_track_state(self._page)
        logger.info("recorder.stopped", recording_bytes=self._size, audio_track=audio, **diag)
        return recording

    async def _poll_loop(self) -> None:
        while True:
            await self._drain()
            self._drains += 1
            if self._drains % AUDIO_PROBE_EVERY_DRAINS == 0:
                audio = await audio_track_state(self._page)
                logger.info("recorder.audio_probe", audio_track=audio)
            await asyncio.sleep(POLL_INTERVAL_S)

    async def _drain(self) -> None:
        try:
            chunks = await self._page.evaluate(_COLLECT_CHUNKS)
        except Exception:
            return
        for chunk in chunks or []:
            data = base64.b64decode(chunk)
            self._spool.write(data)
            self._size += len(data)
            self._checkpoint_buffer.extend(data)
            self._checkpoint_chunks += 1
            if self._sink is not None:
                self._sink(data)
            if self._checkpoint_chunks >= CHECKPOINT_CHUNKS:
                await self._flush_checkpoint()

    async def _flush_checkpoint(self) -> None:
        if self._checkpoint_sink is None or not self._checkpoint_buffer:
            return
        data = bytes(self._checkpoint_buffer)
        self._checkpoint_buffer.clear()
        self._checkpoint_chunks = 0
        try:
            await self._checkpoint_sink(self._checkpoint_sequence, data)
        except Exception as err:
            self.checkpoint_failed = True
            logger.warning("recorder.checkpoint_failed", error=type(err).__name__)
        self._checkpoint_sequence += 1
