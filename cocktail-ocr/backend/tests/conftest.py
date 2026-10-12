import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="cf-test-")
os.environ["CF_DATA_DIR"] = _TMP
os.environ["CF_AUTOSTART_WORKERS"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def dataset(tmp_path_factory):
    import make_dataset
    d = tmp_path_factory.mktemp("dataset")
    rows = make_dataset.build(d)
    return d, {r[0]: (r[1], r[2]) for r in rows}
