"""Compatibility for updaters that preserve an existing container command."""
from runtime.entrypoint import run

if __name__ == '__main__':
    raise SystemExit(run())
