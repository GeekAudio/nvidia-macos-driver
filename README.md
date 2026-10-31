# Launchpad IOSurface regression: image evidence

These are unedited 256x256 Chrome icon outputs from the same original Apple Dock icon loader, run in isolated processes with each driver on an RTX 3080 / macOS 26.7.2 (25G332).

| Official 2.6.4 | Official 2.6.5 | 2.6.5 + fix |
| --- | --- | --- |
| ![](chrome-official-2.6.4.png) | ![](chrome-official-2.6.5.png) | ![](chrome-fixed-2.6.5.png) |

The official 2.6.4 and fixed 2.6.5 PNGs are byte-identical. All six tested apps matched for both 256x256 and 60x60 outputs.
The desktop reproduction and full reboot check used LaunchpadRestore v1.5: https://github.com/GeekAudio/LaunchpadRestore .
The restored Dock files were unchanged during the driver comparison; the isolated original Apple loader is a separate check, not a full stock-Dock boot test.
The old black icon cache was cleared once after driver deployment, before the successful reboot validation.

AI-assisted investigation and patch; user performed desktop and reboot validation.
