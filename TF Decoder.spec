from pathlib import Path
import sys

from PyInstaller.utils.hooks import copy_metadata


python_licenses = [
    (str(path), "licenses/Python")
    for path in Path(sys.base_prefix).glob("LICENSE*.txt")
]

analysis = Analysis(
    ["main.py"],
    datas=[
        ("raw_browser/assets", "raw_browser/assets"),
        *python_licenses,
        *copy_metadata("matplotlib", recursive=True),
        *copy_metadata("numpy"),
        *copy_metadata("scipy"),
        *copy_metadata("PySide6-Essentials", recursive=True),
    ],
    hooksconfig={"matplotlib": {"backends": ["QtAgg"]}},
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    name="TF Decoder",
    console=False,
    upx=False,
)
