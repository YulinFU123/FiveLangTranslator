# whisper-server integration

The application starts `whisper-server` bound to `127.0.0.1` only. It waits for the configured TCP port, then sends multipart WAV requests to `/inference`. The server owns the model context, so the model is loaded once per service lifecycle.

## Lifecycle

```text
configure
  -> stop old server
  -> start new server
  -> wait for port
  -> enable ASR worker
```

If a request fails, the process health is checked and restarted when needed. Optional fallback continues through CLI GPU and CLI CPU.

## Security boundary

The default host is loopback-only. The UI exposes the port but not the host. Do not bind this unauthenticated transcription endpoint to a public interface.
