import argparse
import sys

from .app import App
from .config import load_config, save_config
from .single_instance import acquire, show_already_running_dialog


def main() -> int:
    parser = argparse.ArgumentParser(prog="autoclicker", description="Auto-approve Claude Code bash prompts with an OpenAI safety check.")
    parser.add_argument("--armed", action="store_true", help="Start in armed mode (real clicks). Default is dry-run.")
    parser.add_argument("--once", action="store_true", help="Run a single detection tick and exit (for smoke tests).")
    parser.add_argument("--pick", action="store_true", help="Launch the region picker, save to config, and exit.")
    parser.add_argument("--clear-regions", action="store_true", help="Remove saved regions (fall back to full-screen scanning).")
    args = parser.parse_args()

    if not (args.once or args.pick or args.clear_regions):
        if not acquire():
            show_already_running_dialog()
            return 0

    cfg = load_config()
    if args.armed:
        cfg.armed_on_start = True

    if args.clear_regions:
        cfg.regions = []
        save_config(cfg)
        print("regions cleared; will scan full screens")
        return 0

    if args.pick:
        from .capture import ScreenCapturer
        from .region_picker import pick_regions

        cap = ScreenCapturer()
        try:
            regions = pick_regions(cap.monitors)
        finally:
            cap.close()
        if not regions:
            print("cancelled; existing regions kept")
            return 0
        cfg.regions = regions
        save_config(cfg)
        print(f"saved {len(regions)} region(s) to config")
        return 0

    app = App(cfg)
    if args.once:
        app._ensure_runtime()
        app._tick()
        return 0
    try:
        app.run()
    except KeyboardInterrupt:
        app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
