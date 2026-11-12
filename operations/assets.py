"""Bounded, read-only folder ingestion. Secrets and source bodies stay local."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, ImageOps

SKIP = {"node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build", ".DS_Store"}
TEXT = {".md", ".txt", ".rst", ".csv"}
PICTURES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEOS = {".mp4", ".mov", ".m4v", ".webm"}
MAX_VIDEO_BYTES=100*1024*1024


def copy_video(path,job_id,static_root,name=None):
    """Match the original uploader's 100MB cap; hash media in bounded chunks."""
    p=Path(path)
    if p.is_symlink() or not p.is_file() or p.suffix.lower() not in VIDEOS:
        raise ValueError('请选择MP4、MOV、M4V或WebM视频文件')
    size=p.stat().st_size
    if not 0<size<=MAX_VIDEO_BYTES:raise ValueError('单个视频应大于0且不超过100MB')
    digest=hashlib.sha256()
    with p.open('rb') as source:
        while chunk:=source.read(1024*1024):digest.update(chunk)
    destination=Path(static_root)/'uploads'/'products'/'operations'/job_id
    destination.mkdir(parents=True,exist_ok=True)
    out=destination/(digest.hexdigest()[:24]+p.suffix.lower())
    if not out.exists() or out.stat().st_size!=size:shutil.copyfile(p,out)
    return {'name':name or p.name,'size':size,'url':'/static/'+str(out.relative_to(Path(static_root)))}


def redact(text):
    text = re.sub(r"(?im)^.*(?:api[_ -]?key|access[_ -]?token|secret|password|cookie|私钥|密钥)\s*[:=：].*$", "[敏感配置已省略]", text)
    return re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[密钥已省略]", text)


def resolve_folder(raw):
    mappings = json.loads(os.environ.get("OPERATIONS_ASSET_MAPPINGS", "{}"))
    if not mappings:
        mappings = {os.environ.get("OPERATIONS_HOST_ROOT", str(Path.home() / "Desktop")):
                    os.environ.get("OPERATIONS_ASSET_ROOT", "/host-desktop")}
    p = Path(raw).expanduser()
    allowed = []
    for host, mount in mappings.items():
        host_root, mount_root = Path(host), Path(mount).resolve()
        allowed.append(mount_root)
        if p.is_relative_to(host_root):
            p = mount_root / p.relative_to(host_root)
    p = p.resolve(strict=True)
    if not any(p.is_relative_to(root) and p != root for root in allowed):
        raise ValueError("请指定已配置素材根目录下的具体商品文件夹")
    if not p.is_dir():
        raise ValueError("素材路径应为文件夹")
    return p


def read_document(path):
    suffix = path.suffix.lower()
    if suffix in TEXT or path.name.lower() in {"readme", "license"}:
        raw = path.read_bytes()[:500_000]
        for encoding in ["utf-8-sig", "gb18030"]:
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")
    if suffix == ".docx":
        with zipfile.ZipFile(path) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > 5_000_000:
                raise ValueError("文档正文超过读取上限")
            root = ElementTree.fromstring(z.read(info))
            return "\n".join(t.text or "" for t in root.iter() if t.tag.endswith("}t"))
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n".join((page.extract_text() or "") for page in reader.pages[:30])
    if suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            names = z.namelist()[:1000]
            return "压缩包文件清单（内容未经运行验证）：\n" + "\n".join(
                n for n in names if not any(p in SKIP or p.startswith('.') for p in Path(n).parts))
    return ""


def scan_folder(raw, job_id, static_root):
    root = resolve_folder(raw)
    manifest, documents, images, videos, warnings = [], [], [], [], []
    characters = 0
    destination = Path(static_root) / "uploads" / "products" / "operations" / job_id
    destination.mkdir(parents=True, exist_ok=True)
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith('.') and not (Path(parent)/d).is_symlink())
        for name in sorted(files):
            p = Path(parent)/name
            if p.is_symlink() or name.startswith('.') or name in SKIP:
                continue
            if any(t in name.lower() for t in ['credentials', 'secret', 'id_rsa', '.env', 'password', 'cookie']):
                continue
            if len(manifest) >= 500:
                warnings.append("本轮已达到500个文件上限，剩余文件未扫描")
                return {"files": manifest, "documents": documents, "images": images, "videos":videos, "warnings": warnings}
            relative = str(p.relative_to(root))
            size = p.stat().st_size
            entry = {"id": "asset_"+str(len(manifest)+1), "name": relative, "size": size, "status": "inventory"}
            manifest.append(entry)
            if p.suffix.lower() in VIDEOS:
                try:
                    if len(videos)<3:
                        videos.append({'asset_id':entry['id'],**copy_video(p,job_id,static_root,relative)})
                        entry['status']='video_selected'
                    else:warnings.append(relative+'：本轮已选满3个视频，可在素材库调整')
                except ValueError as exc:
                    entry['status']='large_file_inventory_only'
                    warnings.append(relative+'：'+str(exc))
                continue
            if size > 20*1024*1024:
                entry["status"] = "large_file_inventory_only"
                continue
            # Hash only files within a bounded size; large archives are inventory-only.
            entry["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
            try:
                if p.suffix.lower() in PICTURES:
                    with Image.open(p) as img:
                        if img.width * img.height > 40_000_000:
                            raise ValueError("图片像素超过处理上限")
                        entry["width"], entry["height"] = img.size
                        if len(images) < 9:
                            out = destination/(entry["sha256"][:24]+".jpg")
                            image = ImageOps.exif_transpose(img).convert("RGB")
                            image.thumbnail((2000, 2000))
                            image.save(out, "JPEG", quality=90)
                            images.append({"asset_id": entry["id"], "name": relative,
                                           "url": "/static/"+str(out.relative_to(Path(static_root)))})
                    if shutil.which("tesseract") and characters < 80_000:
                        result = subprocess.run(["tesseract", str(p), "stdout", "-l", "chi_sim+eng"],
                                                capture_output=True, text=True, timeout=35)
                        text = result.stdout if result.returncode == 0 else ""
                        if not text.strip():
                            warnings.append(relative+"：图片OCR未提取到文字，保留图片供查看")
                        entry["status"] = "image_ocr" if text.strip() else "image_only"
                    else:
                        text = ""
                        entry["status"] = "image_only"
                elif p.suffix.lower() in TEXT | {".pdf", ".docx", ".zip"}:
                    text = read_document(p)
                    entry["status"] = "read" if text.strip() else "empty_text"
                    if not text.strip():
                        warnings.append(relative+"：未提取到正文，扫描PDF需补充可读说明")
                else:
                    text = ""
                if text.strip() and characters < 80_000:
                    text = redact(text)[:min(16000, 80_000-characters)]
                    documents.append({"asset_id": entry["id"], "name": relative, "text": text})
                    characters += len(text)
            except Exception as exc:
                entry["status"] = "read_error"
                warnings.append(relative+"："+type(exc).__name__)
    return {"files": manifest, "documents": documents, "images": images, "videos":videos, "warnings": warnings,
            "note": "文档/OCR是材料描述，实际软件功能需另行验证；未执行源码。视频保留为商品展示素材，内容未作为功能证明"}
