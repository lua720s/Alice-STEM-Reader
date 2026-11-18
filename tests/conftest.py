from pathlib import Path

import pytest

from pdf_latex_overlay import fixtures


@pytest.fixture(scope="session")
def fx(tmp_path_factory) -> dict[str, Path]:
    """合成フィクスチャ（セッションで一度生成）。テストは原本を書き換えないこと。"""
    return fixtures.build(tmp_path_factory.mktemp("fixtures"))
