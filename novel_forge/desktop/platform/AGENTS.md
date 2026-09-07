# Cross-Platform Modules

Per-OS code lives here instead of being scattered across desktop/ top level.

## Layout

```
platform/
├── __init__.py
├── sleep_inhibit/
│   ├── __init__.py       # create_backend() dispatcher
│   ├── darwin.py
│   ├── win32.py
│   ├── linux.py
│   └── null_backend.py
├── ollama_paths.py        # ollama binary lookup
└── fonts.py               # font fallback tables
```

## Conventions

- One subpackage per OS-specific concern.
- Dispatcher at the subpackage's `__init__.py` (`create_backend()`).
- Each platform module returns the same interface (`SleepInhibitBackend` with `.acquire() / .release()`).

## Adding a new platform

1. Create `desktop/platform/<subpackage>/<system>.py` with the same class signature.
2. Add a branch in the dispatcher (`if sys.platform == "...":`).
3. Add an import check in CI smoke tests if the platform depends on a system binary.
