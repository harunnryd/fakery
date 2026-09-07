import asyncio
import base64
import json
from typing import Any

import structlog

POLL_INTERVAL_S = 2
AUDIO_PROBE_EVERY_DRAINS = 15
RECORDER_TIMESLICE_MS = 2_000
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
    const audioAlive = () => {
      const stream = window.__fakeryAudioStream;
      return stream && stream.getAudioTracks().some((t) => t.readyState === 'live');
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
          if (window.__fakeryRecorder && audioAlive()) return;
          try {
            if (window.__fakeryRecorder) window.__fakeryRecorder.stop();
          } catch (err) {}
          const stream = new MediaStream([track]);
          window.__fakeryAudioStream = stream;
          window.__fakeryAudioTrack = track;
          const mime = pickMime();
          window.__fakeryMime = mime || 'default';
          const recorder = mime
            ? new MediaRecorder(stream, {mimeType: mime})
            : new MediaRecorder(stream);
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


async def arm_via_cdp_eval(page: Any) -> bool:
    try:
        session = await page.context.new_cdp_session(page)
        await session.send("Runtime.evaluate", {"expression": RECORDER_HOOK})
        return True
    except Exception as err:
        logger.warning("recorder.cdp_arm_failed", error=str(err))
        return False


async def diagnostics(page: Any) -> dict:
    # Meet may build its RTCPeerConnection in any frame — telemetry is read
    # per frame so the realm (main vs iframe) is visible in the logs.
    results: dict = {}
    for frame in page.frames:
        try:
            raw = await frame.evaluate(_READ_DIAGNOSTICS)
        except Exception:
            continue
        if raw:
            results[frame.url.rsplit("/", 1)[-1] or "main"] = raw
    return results


def summarize(diag: dict) -> str:
    return json.dumps(diag, sort_keys=True)


async def audio_track_state(page: Any) -> dict:
    try:
        return await page.evaluate(_READ_AUDIO_TRACK) or {}
    except Exception:
        return {}


class MeetingRecorder:
    def __init__(self, page: Any) -> None:
        self._page = page
        self._buffer = bytearray()
        self._task: asyncio.Task | None = None
        self._drains = 0

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> bytes:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._drain()
        diag = await diagnostics(self._page)
        audio = await audio_track_state(self._page)
        logger.info(
            "recorder.stopped", recording_bytes=len(self._buffer), audio_track=audio, **diag
        )
        return bytes(self._buffer)

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
            self._buffer += base64.b64decode(chunk)
