"""Absolute-path entry point used by the Windows launcher."""

from backend.server import main


if __name__ == "__main__":
    raise SystemExit(main())
