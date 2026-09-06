import asyncio
import base64
import json
from typing import Any

import structlog

POLL_INTERVAL_S = 2
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
  };
  const install = () => {
    document.documentElement.appendChild(anchor);
    if (!OriginalRTC) { window.__fakeryRecError = 'no-rtc'; report(); return; }
    window.__fakeryPcCount = 0;
    window.__fakeryTrackCount = 0;
    let recorder = null;
    function HookedRTC(...args) {
      window.__fakeryPcCount += 1;
      report();
      const pc = new OriginalRTC(...args);
      pc.addEventListener('track', async (event) => {
        try {
          window.__fakeryTrackCount += 1;
          report();
          if (event.track.kind !== 'audio' || recorder) return;
          const stream = new MediaStream([event.track]);
          recorder = new MediaRecorder(stream, {mimeType: 'audio/webm'});
          window.__fakeryRecState = 'recording';
          report();
          recorder.ondataavailable = async (e) => {
            if (e.data.size === 0) return;
            const bytes = new Uint8Array(await e.data.arrayBuffer());
            let binary = '';
            for (let i = 0; i < bytes.length; i += 0x8000) {
              binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
            }
            const node = document.createElement('div');
            node.setAttribute('data-fakery-chunk', btoa(binary));
            anchor.appendChild(node);
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
  };
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


class MeetingRecorder:
    def __init__(self, page: Any) -> None:
        self._page = page
        self._buffer = bytearray()
        self._task: asyncio.Task | None = None

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
        logger.info("recorder.stopped", recording_bytes=len(self._buffer), **diag)
        return bytes(self._buffer)

    async def _poll_loop(self) -> None:
        while True:
            await self._drain()
            await asyncio.sleep(POLL_INTERVAL_S)

    async def _drain(self) -> None:
        try:
            chunks = await self._page.evaluate(_COLLECT_CHUNKS)
        except Exception:
            return
        for chunk in chunks or []:
            self._buffer += base64.b64decode(chunk)
