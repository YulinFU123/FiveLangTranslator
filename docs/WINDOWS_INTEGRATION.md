# Windows integration in v0.3.0-alpha.5

## Global hotkeys

`RegisterHotKey` registers six Ctrl+Shift combinations with `MOD_NOREPEAT`. A Qt native event filter dispatches `WM_HOTKEY`. If native registration fails, application-scoped `QShortcut` remains available.

## Click-through

The overlay applies `WS_EX_TRANSPARENT`, `WS_EX_LAYERED`, and `WS_EX_NOACTIVATE`. It also retains Qt's `WA_TransparentForMouseEvents` as a toolkit-level fallback.

## Player tracking

The tracker polls the foreground window every 500 ms, reads its rectangle, checks minimized state, and compares it against the monitor rectangle to infer borderless fullscreen. It does not inject into player processes.

## Profiles

Windowed and fullscreen profiles store relative x, y, width, and height. When following a player, ratios are relative to the player rectangle. Otherwise they are relative to the selected monitor's available geometry.
