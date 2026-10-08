"""`python -m engine.web` — serve the interface on 127.0.0.1:8000."""

from engine.web.app import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
