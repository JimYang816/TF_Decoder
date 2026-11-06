from pathlib import Path

root = Path(SPECPATH).parent
a = Analysis(
    [str(root / 'packaging' / 'launcher.py')],
    pathex=[str(root)],
    datas=[(str(root / 'tf_decoder' / 'parula256.csv'), 'tf_decoder')],
    hiddenimports=[],
    excludes=['matplotlib', 'pandas', 'IPython', 'jupyter', 'pytest', 'tkinter',
              'PyQt5', 'PyQt6', 'PySide2', 'OpenGL', 'sympy', 'PIL'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='TF-Decoder',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='TF-Decoder')
