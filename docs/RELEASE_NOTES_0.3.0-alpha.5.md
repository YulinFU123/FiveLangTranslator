# v0.3.0-alpha.5 Release Notes

## Windows-native interaction

- Added system-wide hotkeys through `RegisterHotKey` and `WM_HOTKEY`.
- Added native click-through through extended window styles.
- Added topmost and no-activate enforcement.
- Retained Qt fallbacks when native APIs are not available.

## Player-aware overlay

- Tracks the foreground external window every 500 ms.
- Ignores windows belonging to this application.
- Detects minimized state and approximate borderless fullscreen.
- Stores independent windowed and fullscreen profiles.
- Stores geometry relative to the player when follow mode is enabled.
- Prevents tracker updates from fighting the user during overlay edit mode.
- Restores profiles to the primary monitor when the saved monitor is missing.

## Validation status

Pure Python compilation and unit tests pass. Native Windows APIs require validation on Windows 11 because the build environment is not Windows and does not contain PySide6.
