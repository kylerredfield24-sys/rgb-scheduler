# RGB Scheduler

Switches ASRock Polychrome (ASRRGBLED) fan lights to the colors you schedule, without ever showing Polychrome on screen.

## Install
1. Download **RGB-Scheduler.exe** from the newest [release](../../releases/latest).
2. Open it. Windows may say "Windows protected your PC": click **More info → Run anyway** (only the first time).
3. Click **Yes** on the admin prompt once. After that it opens as admin with no prompt.

## Updating
Click **⬆ Update** in the top bar when it shows up, or ⚙ → Updates → **Check for updates**.
The app downloads the new version, swaps itself, and restarts.

## Publishing a new version
Bump `APP_VERSION` in `rgb_scheduler.py` (1.9 → 2.0, never 1.10) and push to `main`.
GitHub Actions self-tests it, builds the .exe, and publishes it as a release.
