# v0.3.0-alpha.3 Architecture

```text
Audio draft/final segments
  -> ASRPriorityWorker
       -> one execution lane
       -> FINAL priority 0
       -> DRAFT priority 10
       -> coalesce pending drafts by segment_id
  -> whisper.cpp CLI
  -> optional CPU retry
  -> LanguageManager
  -> TranscriptStabilizer
  -> translation
  -> overlay with stable/draft colors
```

The worker is persistent, but the underlying CLI model process is not yet persistent. This distinction is surfaced in the README to avoid overstating latency improvements.
