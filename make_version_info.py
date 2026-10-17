"""Writes version_info.txt (Windows file properties) from app/version.py for PyInstaller."""
import re
from pathlib import Path

root = Path(__file__).resolve().parent
text = (root / "app" / "version.py").read_text(encoding="utf-8")
ver = re.search(r'APP_VERSION = "([\d.]+)"', text).group(1)
parts = (ver.split(".") + ["0", "0", "0"])[:3]
tup = ", ".join(parts + ["0"])
(root / "version_info.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tup}), prodvers=({tup}), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Largo Develop'),
      StringStruct('FileDescription', 'Largo Auto Config: автозамена настроек Dota 2 при смене Steam-аккаунта'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('InternalName', 'LargoAutoConfig'),
      StringStruct('LegalCopyright', 'Largo Develop, MIT License'),
      StringStruct('OriginalFilename', 'LargoAutoConfig.exe'),
      StringStruct('ProductName', 'Largo Auto Config'),
      StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")
print("version_info.txt", ver)
