"""Runtime data lives beside the checkout, never inside the source tree."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os
from . import PROJECT_VERSION

PROJECT_ROOT=Path(__file__).resolve().parents[1]
DATA_ENV='MACHINECATALOG_DATA_DIR'

@dataclass(frozen=True)
class DataPaths:
    root: Path
    input: Path
    runs: Path
    cache: Path

    def initialize(self):
        for folder in (self.input,self.runs,self.cache):folder.mkdir(parents=True,exist_ok=True)


def data_paths(root=None):
    selected=Path(root or os.environ.get(DATA_ENV) or PROJECT_ROOT.parent/'MachineCatalog_Engine_data').expanduser().resolve()
    if selected==PROJECT_ROOT or PROJECT_ROOT in selected.parents:
        raise ValueError('Data directory must be outside the project source tree. Choose --data-dir or MACHINECATALOG_DATA_DIR.')
    version='v'+PROJECT_VERSION.replace('.','_')
    return DataPaths(selected,selected/'input',selected/'runs'/version,selected/'cache'/version)
