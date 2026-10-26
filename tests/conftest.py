"""
Testconfiguratie: de datamap wordt naar een tijdelijke map verlegd VOORDAT
cinemarker geïmporteerd wordt, zodat tests nooit de echte database raken.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="cinemarker_test_"))
os.environ["CINEMARKER_DATA_ROOT"] = str(_TMP_ROOT)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from cinemarker.core import database as db, paths  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _init_database():
    assert paths.DATA_ROOT == _TMP_ROOT.resolve()
    paths.ensure_data_dirs()
    db.init_db()
    yield


@pytest.fixture
def tmp_video(tmp_path: Path) -> Path:
    """Een (leeg) videobestand om markers naast op te slaan."""
    p = tmp_path / "Film élan.mp4"
    p.write_bytes(b"")
    return p
