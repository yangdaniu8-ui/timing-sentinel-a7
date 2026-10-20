"""Use the canonical entry so source launches share storage checks and diagnostics."""
from release_spi import main

if __name__ == '__main__':
    from app.diagnostics import SessionDiagnostics
    with SessionDiagnostics():
        raise SystemExit(main())

