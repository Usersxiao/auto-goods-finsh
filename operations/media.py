"""Attach local display videos without resetting research or rewriting copy."""
import os
from pathlib import Path
from operations.assets import resolve_folder, copy_video, VIDEOS, PICTURES
import hashlib
from PIL import Image, ImageOps


def attach_videos(job,static_root,paths=None):
    selected=[]
    if paths:
        for raw in paths:
            original=Path(raw).expanduser()
            parent=resolve_folder(str(original.parent))
            path=parent/original.name
            if path.is_symlink():raise ValueError('请直接选择素材目录中的视频文件')
            selected.append((path,path.name))
    else:
        folder=job.parameters.get('folder')
        if not folder:raise ValueError('请提供视频完整路径，例如：添加视频“/Users/你的用户名/Desktop/素材/演示.mp4”')
        root=resolve_folder(folder)
        visited=0
        for parent,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=sorted(d for d in dirs if not d.startswith('.') and not (Path(parent)/d).is_symlink())
            for name in sorted(files):
                visited+=1
                if visited>500:raise ValueError('素材文件较多，请直接提供要添加的视频完整路径')
                path=Path(parent)/name
                if path.suffix.lower() in VIDEOS and not path.is_symlink():selected.append((path,str(path.relative_to(root))))
    if not selected:raise ValueError('当前素材文件夹没有找到视频，请提供视频完整路径')
    if len(selected)>3:raise ValueError('找到超过3个视频，请指定要添加的视频完整路径；单件商品最多3个视频')
    return [copy_video(path,job.id,static_root,name) for path,name in selected]


def attach_images(job,static_root,paths=None):
    selected=[]
    if paths:
        for raw in paths:
            original=Path(raw).expanduser();parent=resolve_folder(str(original.parent))
            path=parent/original.name
            if path.is_symlink():raise ValueError('请直接选择素材目录中的图片文件')
            selected.append(path)
    else:
        root=resolve_folder(job.parameters.get('folder') or '')
        visited=0
        for parent,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=sorted(d for d in dirs if not d.startswith('.') and not (Path(parent)/d).is_symlink())
            for name in sorted(files):
                visited+=1
                if visited>500:raise ValueError('素材文件较多，请提供要添加的图片完整路径')
                path=Path(parent)/name
                if path.suffix.lower() in PICTURES and not path.is_symlink():selected.append(path)
    if not selected:raise ValueError('没有找到图片，请提供图片完整路径')
    if len(selected)>9:raise ValueError('最多选择9张商品图片，请指定需要添加的图片完整路径')
    images=[];destination=Path(static_root)/'uploads'/'products'/'operations'/job.id
    destination.mkdir(parents=True,exist_ok=True)
    for path in selected:
        if path.suffix.lower() not in PICTURES or not path.is_file() or not 0<path.stat().st_size<=20*1024*1024:
            raise ValueError('请选择不超过20MB的JPG、PNG或WebP等图片')
        digest=hashlib.sha256(path.read_bytes()).hexdigest();out=destination/(digest[:24]+'.jpg')
        with Image.open(path) as img:
            if img.width*img.height>40_000_000:raise ValueError('图片像素超过处理上限')
            img=ImageOps.exif_transpose(img).convert('RGB');img.thumbnail((2000,2000));img.save(out,'JPEG',quality=90)
        images.append({'asset_id':'image_'+digest[:16],'name':path.name,'url':'/static/'+str(out.relative_to(Path(static_root)))})
    return images
