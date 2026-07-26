import asyncio
import hashlib
import os
import re
import time
import zipfile
from pathlib import Path, PurePosixPath

from yuxi.storage.minio import get_minio_client
from yuxi.utils import logger

DEFAULT_IMAGE_BUCKET = "public"
DEFAULT_IMAGE_PREFIX = "unknown/kb-images"
MAX_ZIP_ENTRIES = 5_000
MAX_ZIP_ENTRY_BYTES = 100 * 1024 * 1024
MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_ZIP_MARKDOWN_BYTES = 50 * 1024 * 1024
MAX_ZIP_IMAGE_BYTES = 25 * 1024 * 1024
MAX_ZIP_TOTAL_IMAGE_BYTES = 250 * 1024 * 1024


def _normalize_object_prefix(prefix: str | None) -> str:
    normalized = (prefix or DEFAULT_IMAGE_PREFIX).strip("/")
    return normalized or DEFAULT_IMAGE_PREFIX


def _validate_zip_members(zip_file: zipfile.ZipFile) -> None:
    members = zip_file.infolist()
    if len(members) > MAX_ZIP_ENTRIES:
        raise ValueError("ZIP 文件条目数量超过限制")

    total_uncompressed_size = 0
    for member in members:
        normalized_name = member.filename.replace("\\", "/")
        path = PurePosixPath(normalized_name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("ZIP 包含不安全路径")
        if member.flag_bits & 0x1:
            raise ValueError("ZIP 不支持加密条目")
        if member.file_size < 0 or member.file_size > MAX_ZIP_ENTRY_BYTES:
            raise ValueError("ZIP 文件条目超过大小限制")
        total_uncompressed_size += member.file_size
        if total_uncompressed_size > MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES:
            raise ValueError("ZIP 解压后大小超过限制")


async def process_zip_file(
    zip_path: str,
    image_bucket: str = DEFAULT_IMAGE_BUCKET,
    image_prefix: str = DEFAULT_IMAGE_PREFIX,
) -> dict:
    """
    处理ZIP文件，提取markdown内容和图片

    Args:
        zip_path: ZIP文件路径
        image_bucket: 图片上传的目标 bucket
        image_prefix: 图片上传对象前缀

    Returns:
        dict: {
            "markdown_content": str,
            "content_hash": str,
            "images_info": list[dict]
        }
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        _validate_zip_members(zf)

        md_files = [n for n in zf.namelist() if n.lower().endswith(".md")]
        if not md_files:
            raise ValueError("压缩包中未找到 .md 文件")

        md_file = next((n for n in md_files if Path(n).name == "full.md"), md_files[0])
        if zf.getinfo(md_file).file_size > MAX_ZIP_MARKDOWN_BYTES:
            raise ValueError("ZIP Markdown 文件超过大小限制")

        with zf.open(md_file) as f:
            markdown_bytes = f.read(MAX_ZIP_MARKDOWN_BYTES + 1)
        if len(markdown_bytes) > MAX_ZIP_MARKDOWN_BYTES:
            raise ValueError("ZIP Markdown 文件超过大小限制")
        markdown_content = markdown_bytes.decode("utf-8")

        images_info = []
        images_dir = find_images_directory(zf, md_file)
        normalized_prefix = _normalize_object_prefix(image_prefix)

        if images_dir:
            images_info = await process_images(
                zf,
                images_dir,
                image_bucket=image_bucket,
                image_prefix=normalized_prefix,
            )
            markdown_content = replace_image_links(markdown_content, images_info)

    content_hash = hashlib.sha256(markdown_content.encode("utf-8")).hexdigest()

    return {
        "markdown_content": markdown_content,
        "content_hash": content_hash,
        "images_info": images_info,
    }


def process_zip_file_sync(
    zip_path: str,
    image_bucket: str = DEFAULT_IMAGE_BUCKET,
    image_prefix: str = DEFAULT_IMAGE_PREFIX,
) -> dict:
    """同步调用 ZIP 处理，供同步解析器使用。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(process_zip_file(zip_path, image_bucket=image_bucket, image_prefix=image_prefix))

    result: dict | None = None
    error: Exception | None = None

    def runner() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(process_zip_file(zip_path, image_bucket=image_bucket, image_prefix=image_prefix))
        except Exception as exc:  # pragma: no cover - pass through outer raise
            error = exc

    import threading

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()

    if error is not None:
        raise error

    if result is None:
        raise RuntimeError("ZIP 处理失败: 未返回结果")

    return result


def find_images_directory(zip_file: zipfile.ZipFile, md_file_path: str) -> str | None:
    """查找images目录"""
    md_parent = Path(md_file_path).parent

    candidates = []
    if str(md_parent) != ".":
        candidates.extend([str(md_parent / "images"), str(md_parent.parent / "images")])
    candidates.append("images")

    for cand in candidates:
        cand_clean = cand.rstrip("/")
        if any(n.startswith(cand_clean + "/") for n in zip_file.namelist()):
            return cand_clean

    return None


async def process_images(
    zip_file: zipfile.ZipFile,
    images_dir: str,
    image_bucket: str,
    image_prefix: str,
) -> list[dict]:
    """处理图片：上传到MinIO并返回信息"""
    supported_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

    images = []
    image_names = [n for n in zip_file.namelist() if n.startswith(images_dir + "/")]
    normalized_prefix = _normalize_object_prefix(image_prefix)

    minio_client = get_minio_client()
    uploaded_objects: list[str] = []
    total_image_bytes = 0
    try:
        await asyncio.to_thread(minio_client.ensure_bucket_exists, image_bucket)

        for img_name in image_names:
            normalized_name = img_name.replace("\\", "/")
            suffix = PurePosixPath(normalized_name).suffix.lower()
            if suffix not in supported_extensions:
                continue

            image_size = zip_file.getinfo(img_name).file_size
            total_image_bytes += image_size
            if image_size > MAX_ZIP_IMAGE_BYTES or total_image_bytes > MAX_ZIP_TOTAL_IMAGE_BYTES:
                raise ValueError("ZIP 图片超过大小限制")

            with zip_file.open(img_name) as f:
                data = f.read(MAX_ZIP_IMAGE_BYTES + 1)
            if len(data) > MAX_ZIP_IMAGE_BYTES:
                raise ValueError("ZIP 图片超过大小限制")

            timestamp = int(time.time() * 1000000)
            image_name = PurePosixPath(normalized_name).name
            object_name = f"{normalized_prefix}/{timestamp}_{image_name}"

            result = await minio_client.aupload_file(
                bucket_name=image_bucket,
                object_name=object_name,
                data=data,
            )
            uploaded_objects.append(object_name)

            img_info = {
                "name": image_name,
                "url": result.url,
                "path": f"images/{image_name}",
            }
            images.append(img_info)

            logger.debug("ZIP 图片上传成功")
    except Exception as error:
        for object_name in reversed(uploaded_objects):
            try:
                await minio_client.adelete_file(image_bucket, object_name)
            except Exception as cleanup_error:
                logger.warning(f"ZIP 图片回滚失败 (error_type={type(cleanup_error).__name__})")
        logger.error(f"ZIP 图片处理失败 (error_type={type(error).__name__})")
        raise RuntimeError("ZIP 图片处理失败") from error

    return images


def replace_image_links(markdown_content: str, images: list[dict]) -> str:
    """替换markdown中的图片链接为MinIO URL"""
    if not images:
        return markdown_content

    image_map = {}
    for img in images:
        path = img["path"]
        url = img["url"]
        image_map[path] = url
        image_map[f"/{path}"] = url
        image_map[img["name"]] = url

    def replace_link(match):
        alt_text = match.group(1) or ""
        img_path = match.group(2)

        for pattern, url in image_map.items():
            if img_path.endswith(pattern) or img_path == pattern:
                return f"![{alt_text}]({url})"

        filename = os.path.basename(img_path)
        if filename in image_map:
            return f"![{alt_text}]({image_map[filename]})"

        return match.group(0)

    pattern = r"!\[([^\]]*)\]\(([^)]+)\)"
    return re.sub(pattern, replace_link, markdown_content)
