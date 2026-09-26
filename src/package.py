import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


def write_package(out_dir: Path, slug: str, skill_md: str, memory_md: str) -> Path:
    skill_dir = out_dir / slug
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (skill_dir / "references" / "memory.md").write_text(memory_md, encoding="utf-8")
    zip_path = out_dir / f"{slug}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(skill_dir / "SKILL.md", "SKILL.md")
        zf.write(skill_dir / "references" / "memory.md", "references/memory.md")
    return zip_path


def upload_to_device(device: str, slug: str, zip_path: Path) -> bool:
    url = f"http://{device}/api/skills/upload"
    data = zip_path.read_bytes()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/zip", "X-Skill-Name": slug},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            print(f"  · 上传结果：{resp.status} {resp.read().decode('utf-8', 'ignore')[:200]}")
        return True
    except urllib.error.HTTPError as e:
        print(f"  ! 上传失败：{e.code} {e.read().decode('utf-8', 'ignore')[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"  ! 上传失败：{e}", file=sys.stderr)
    return False


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
