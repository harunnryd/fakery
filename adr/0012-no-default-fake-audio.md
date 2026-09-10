# ADR 0012: Do not enable Chromium fake audio by default

Live Meet testing heard repeated beeps from the bot. Chromium documents that
`--use-fake-device-for-media-stream` replaces the microphone with a fake audio
input and generates a beep unless a WAV file is supplied. The flag was in the
default browser recipe, so every pilot guest could transmit the test signal.

The pilot assistant is receive-only and does not need a synthetic microphone.
The default recipe keeps automatic permission handling but removes the fake
device flag. A test may opt into a supplied WAV fixture through
`EngineConfig.fake_audio_path`; that path is explicit and never enabled by
deployment defaults.
