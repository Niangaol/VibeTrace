# -*- mode: python ; coding: utf-8 -*-
# VibeTrace.spec — PyInstaller 打包配置（monitor.py 多工具入口，原名 UsageMonitor.spec）
# 构建：python -m PyInstaller VibeTrace.spec --noconfirm

from PyInstaller.utils.hooks import collect_all

# zstandard（C 扩展）：DSH 会话解压用，collect_all 收集其 .pyd/动态库与子模块
_zstd_binaries, _zstd_datas, _zstd_hidden = collect_all('zstandard')

a = Analysis(
    ['monitor.py'],
    pathex=[],
    binaries=_zstd_binaries,
    datas=[
        ('assets/icon.ico', 'assets'),
        ('assets/tray.ico', 'assets'),
        ('assets/dashboard.html', 'assets'),
    ] + _zstd_datas,
    hiddenimports=['insights', 'updater', 'sqlite_store', 'ai_sessions',
                   'timeline', 'budget', 'tool_compare', 'growth', 'query', 'adoption',
                   'zstandard'] + _zstd_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='VibeTrace',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)
