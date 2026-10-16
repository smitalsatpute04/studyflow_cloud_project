import io
import logging
import os
import uuid
from pathlib import Path
from typing import Optional, Tuple

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from config import settings

logger = logging.getLogger(__name__)

try:
    from pypdf import PdfReader
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False


class StorageService:
    """
    S3-compatible Object Storage client supporting Cloudflare R2, AWS S3, and GCS S3-interop,
    with automatic local filesystem fallback for zero-config local development.
    """

    def __init__(self) -> None:
        self.bucket_name = settings.S3_BUCKET_NAME
        self.local_dir = Path(settings.LOCAL_STORAGE_DIR)
        self.local_dir.mkdir(parents=True, exist_ok=True)
        self._s3_client = None

        if settings.is_s3_configured:
            try:
                self._s3_client = boto3.client(
                    "s3",
                    endpoint_url=settings.S3_ENDPOINT_URL,
                    aws_access_key_id=settings.S3_ACCESS_KEY_ID,
                    aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
                    region_name=settings.S3_REGION,
                    config=BotoConfig(signature_version="s3v4"),
                )
            except Exception as exc:
                logger.warning("Could not initialize S3/R2 client, using local fallback: %s", exc)

    @property
    def is_cloud_storage_active(self) -> bool:
        return self._s3_client is not None

    def upload_file(
        self, file_bytes: bytes, original_filename: str, content_type: str
    ) -> Tuple[str, str]:
        """
        Uploads raw bytes to Cloudflare R2 / S3 (or local storage fallback).
        Returns (s3_key, storage_backend) where storage_backend is 'r2' or 'local'.
        """
        safe_name = os.path.basename(original_filename).replace(" ", "_")
        s3_key = f"documents/{uuid.uuid4().hex[:12]}_{safe_name}"

        if self._s3_client is not None:
            try:
                self._s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=s3_key,
                    Body=file_bytes,
                    ContentType=content_type or "application/octet-stream",
                )
                return s3_key, "r2"
            except (BotoCoreError, ClientError) as exc:
                logger.error("R2/S3 upload failed (%s), falling back to local disk.", exc)

        # Fallback: persist to local uploads directory
        target_path = self.local_dir / s3_key.replace("/", "_")
        target_path.write_bytes(file_bytes)
        return s3_key, "local"

    def fetch_file(self, s3_key: str, storage_backend: str = "r2") -> bytes:
        """Downloads raw object bytes from Cloudflare R2 / S3 or local storage."""
        if storage_backend == "r2" and self._s3_client is not None:
            response = self._s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
            return response["Body"].read()

        local_path = self.local_dir / s3_key.replace("/", "_")
        if local_path.exists():
            return local_path.read_bytes()
        raise FileNotFoundError(f"Stored file not found for key: {s3_key}")

    def get_presigned_url(self, s3_key: str, expires_in: int = 3600) -> Optional[str]:
        """Generates a temporary signed download URL for objects stored in R2/S3."""
        if self._s3_client is None:
            return None
        try:
            return self._s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": s3_key},
                ExpiresIn=expires_in,
            )
        except Exception as exc:
            logger.warning("Failed to generate presigned URL for %s: %s", s3_key, exc)
            return None

    @staticmethod
    def extract_text_from_bytes(file_bytes: bytes, filename: str, content_type: str) -> str:
        """Extracts clean readable text from uploaded PDF, Markdown, or plain-text files."""
        lower_name = (filename or "").lower()
        is_pdf = lower_name.endswith(".pdf") or content_type == "application/pdf"

        if is_pdf:
            if not PYPDF_AVAILABLE:
                raise RuntimeError("pypdf is required to parse PDF documents.")
            reader = PdfReader(io.BytesIO(file_bytes))
            pages_text = []
            for idx, page in enumerate(reader.pages, start=1):
                extracted = (page.extract_text() or "").strip()
                if extracted:
                    pages_text.append(f"--- Page {idx} ---\n{extracted}")
            combined = "\n\n".join(pages_text).strip()
            if not combined:
                return f"[PDF '{filename}' uploaded ({len(reader.pages)} pages). No selectable text layer detected.]"
            return combined

        # Plain text / Markdown / CSV / JSON syllabus files
        for encoding in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                return file_bytes.decode(encoding).strip()
            except UnicodeDecodeError:
                continue
        return file_bytes.decode("utf-8", errors="replace").strip()


storage_service = StorageService()
