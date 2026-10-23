"""Compatibility entry point for existing Compose health checks."""
from runtime.health import main
if __name__ == '__main__':
    raise SystemExit(main())
