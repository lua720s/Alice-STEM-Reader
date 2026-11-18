"""切り出し画像から LaTeX を認識する（段階2a）。"""

from .base import (  # noqa: F401
    Backend,
    FixedBackend,
    NoneBackend,
    Recognition,
    RecognizerUnavailable,
    crop_png,
    get_backend,
)


def run_recognition(*args, **kwargs) -> int:
    from .runner import run_recognition as _run

    return _run(*args, **kwargs)
