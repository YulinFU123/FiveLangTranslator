# Third-party notices

The source tree does not vendor third-party source code or model weights.

Runtime integrations:

- PyAudioWPatch: Windows WASAPI loopback capture. Its own license and bundled PortAudio notices must be retained in distributions.
- Silero VAD: MIT License. Installed as an optional dependency.
- ONNX Runtime: MIT License. Installed as an optional dependency.
- NumPy and SciPy: BSD-style licenses.
- PySide6 / Qt: distributed under their applicable licensing terms and must be reviewed before commercial packaging.

Before creating an installer, pin exact versions, copy each required license and NOTICE file into `licenses/`, and generate an SBOM.
