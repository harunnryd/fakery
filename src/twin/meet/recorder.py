# Patchright's evaluate runs in an isolated JS world (R-20), so the hook is
# armed via add_init_script (page world, before Meet's scripts) and publishes
# base64 chunks as DOM nodes — the DOM is the one channel both worlds share.
# Experimental: the M1 live soak decides whether the capture is usable.
import asyncio
import base64
from typing import Any

POLL_INTERVAL_S = 2
RECORDER_TIMESLICE_MS = 2_000

# 0x8000-wide chunks dodge the JS argument limit of String.fromCharCode.apply.
_HOOK_TEMPLATE = """
(() => {
  const OriginalRTC = window.RTCPeerConnection || window.webkitRTCPeerConnection;
  if (!OriginalRTC) return;
  let recorder = null;
  function HookedRTC(...args) {
    const pc = new OriginalRTC(...args);
    pc.addEventListener('track', async (event) => {
      try {
        if (event.track.kind !== 'audio' || recorder) return;
        const stream = new MediaStream([event.track]);
        recorder = new MediaRecorder(stream, {mimeType: 'audio/webm'});
        recorder.ondataavailable = async (e) => {
          if (e.data.size === 0) return;
          const bytes = new Uint8Array(await e.data.arrayBuffer());
          let binary = '';
          for (let i = 0; i < bytes.length; i += 0x8000) {
            binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
          }
          const anchor = document.getElementById('__fakery-rec');
          if (!anchor) return;
          const node = document.createElement('div');
          node.setAttribute('data-fakery-chunk', btoa(binary));
          anchor.appendChild(node);
        };
        recorder.start(__TIMESLICE_MS__);
      } catch (err) { /* capture must never break the join */ }
    });
    return pc;
  }
  HookedRTC.prototype = OriginalRTC.prototype;
  window.RTCPeerConnection = HookedRTC;
  window.webkitRTCPeerConnection = HookedRTC;
  const anchor = document.createElement('div');
  anchor.id = '__fakery-rec';
  anchor.style.display = 'none';
  document.addEventListener('DOMContentLoaded', () => document.documentElement.appendChild(anchor));
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
