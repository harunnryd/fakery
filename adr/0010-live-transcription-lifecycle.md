# ADR 0010: Own the live transcription lifecycle

The original live failure recorded only TimeoutError. Idle NET0001 was
reproduced separately and does not establish the original timeout phase.

The provider adapter owns connection retries before audio consumption,
keep-alive during input gaps, concurrent send/receive supervision and bounded
drain after CloseStream. A Finalize response is not an end-of-stream marker.
Decoder cancellation must cancel its input producer and reap ffmpeg.

The recorder remains authoritative for durable audio. A dead STT consumer
must stop accepting chunks and expose live failure even when batch succeeds.
Midstream automatic replay requires explicit sample offsets and acknowledged
coverage; it must not replay a partial WebM stream as a new container.

Validation covers idle input, sender failure, server closure, connection
failure before consumption, cancellation and final results after input EOF.
