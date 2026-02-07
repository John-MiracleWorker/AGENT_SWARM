"""
File Context Manager — Uploads workspace files to Gemini Files API.
Gives agents full codebase context without consuming inline token budget.
Files API is free, stored for 48 hours, up to 20GB per project.
"""

import asyncio
import logging
import os
import mimetypes
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# Extensions we consider "source code" for upload
SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".css", ".html", ".htm",
    ".json", ".yml", ".yaml", ".md", ".toml", ".cfg", ".ini",
    ".go", ".rs", ".java", ".rb", ".sh", ".bash", ".zsh",
    ".sql", ".graphql", ".proto", ".xml", ".svg",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt",
    ".env.example", ".gitignore", ".dockerignore",
    "Dockerfile", "Makefile", "Procfile",
}

# Filenames always included regardless of extension
ALWAYS_INCLUDE = {
    "Dockerfile", "Makefile", "Procfile", "Gemfile",
    "requirements.txt", "package.json", "package-lock.json",
    "tsconfig.json", "pyproject.toml", "setup.py", "setup.cfg",
    "docker-compose.yml", "docker-compose.yaml",
    ".env.example", "README.md", "readme.md",
}

# Directories to skip
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", "venv", ".venv",
    "dist", "build", ".next", ".nuxt", ".cache", ".parcel-cache",
    "target", "vendor", ".tox", "eggs", ".eggs",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "coverage", ".coverage", "htmlcov",
    ".idea", ".vscode", ".DS_Store",
}

# Limits
MAX_FILE_SIZE = 100_000       # 100KB per file
MAX_TOTAL_UPLOAD = 10_000_000  # 10MB total
MAX_FILES = 200                # Don't upload more than 200 files


class UploadedFile:
    """Tracks a single uploaded file."""
    __slots__ = ("local_path", "remote_name", "uri", "mime_type", "size", "gemini_file")

    def __init__(self, local_path: str, gemini_file, size: int):
        self.local_path = local_path
        self.gemini_file = gemini_file
        self.remote_name = gemini_file.name
        self.uri = gemini_file.uri
        self.mime_type = gemini_file.mime_type
        self.size = size


class FileContextManager:
    """
    Manages uploading workspace source files to Gemini's Files API.
    Uploaded files are referenced in agent prompts for full codebase context.
    """

    def __init__(self, client: Optional[genai.Client] = None):
        self._client = client
        self._uploaded: dict[str, UploadedFile] = {}  # relative_path -> UploadedFile
        self._workspace_root: Optional[str] = None
        self._upload_in_progress: bool = False
        self._total_uploaded_bytes: int = 0
        self._on_progress = None  # Callback: (uploaded_count, total_count, current_file)

    def set_client(self, client: genai.Client):
        """Set the Gemini client (may not be available at init time)."""
        self._client = client

    def set_progress_callback(self, callback):
        """Set callback for upload progress events."""
        self._on_progress = callback

    @property
    def is_ready(self) -> bool:
        """Whether we have uploaded files ready for context injection."""
        return bool(self._uploaded) and not self._upload_in_progress

    @property
    def upload_status(self) -> dict:
        """Current upload status for the API."""
        return {
            "workspace": self._workspace_root,
            "files_uploaded": len(self._uploaded),
            "total_bytes": self._total_uploaded_bytes,
            "in_progress": self._upload_in_progress,
            "files": [
                {
                    "path": rel_path,
                    "uri": uf.uri,
                    "size": uf.size,
                    "mime_type": uf.mime_type,
                }
                for rel_path, uf in self._uploaded.items()
            ],
        }

    def _should_include(self, path: Path, root: Path) -> bool:
        """Check if a file should be uploaded."""
        name = path.name
        ext = path.suffix.lower()

        # Filename match
        if name in ALWAYS_INCLUDE:
            return True

        # Extension match
        if ext in SOURCE_EXTENSIONS:
            return True

        return False

    def _collect_files(self, root: Path) -> list[Path]:
        """Walk the workspace and collect uploadable files."""
        result = []
        total_size = 0

        for dirpath, dirnames, filenames in os.walk(root):
            # Prune skipped directories in-place
            dirnames[:] = [
                d for d in dirnames
                if d not in SKIP_DIRS and not d.startswith(".")
            ]

            for fname in sorted(filenames):
                if len(result) >= MAX_FILES:
                    return result

                fpath = Path(dirpath) / fname

                # Skip hidden files
                if fname.startswith(".") and fname not in ALWAYS_INCLUDE:
                    continue

                # Check extension/name filter
                if not self._should_include(fpath, root):
                    continue

                # Check file size
                try:
                    size = fpath.stat().st_size
                except OSError:
                    continue

                if size > MAX_FILE_SIZE or size == 0:
                    continue

                if total_size + size > MAX_TOTAL_UPLOAD:
                    logger.info(f"File upload budget exhausted at {total_size / 1024:.0f}KB")
                    return result

                # Verify it's text (not binary)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        f.read(512)  # quick check
                except (UnicodeDecodeError, OSError):
                    continue

                result.append(fpath)
                total_size += size

        return result

    async def upload_workspace(self, workspace_path: str) -> dict:
        """
        Upload all relevant source files from a workspace to Gemini Files API.
        Call this when a folder is selected or workspace changes.
        """
        if not self._client:
            logger.warning("No Gemini client available — skipping file upload")
            return {"status": "no_client", "files": 0}

        root = Path(workspace_path).resolve()
        if not root.is_dir():
            return {"status": "invalid_path", "files": 0}

        # If switching workspaces, clean up old uploads
        if self._workspace_root and self._workspace_root != str(root):
            await self.cleanup()

        self._workspace_root = str(root)
        self._upload_in_progress = True

        try:
            # Collect files
            files = self._collect_files(root)
            total = len(files)
            logger.info(f"📁 Uploading {total} files from {root} to Gemini Files API...")

            uploaded_count = 0
            errors = 0

            for i, fpath in enumerate(files):
                rel_path = str(fpath.relative_to(root))

                # Skip if already uploaded (same workspace, same file)
                if rel_path in self._uploaded:
                    uploaded_count += 1
                    continue

                try:
                    # Determine MIME type
                    mime, _ = mimetypes.guess_type(str(fpath))
                    if not mime:
                        mime = "text/plain"

                    # Upload via Files API (blocking call wrapped for async)
                    gemini_file = await asyncio.to_thread(
                        self._client.files.upload,
                        file=str(fpath),
                        config=types.UploadFileConfig(
                            display_name=rel_path,
                            mime_type=mime,
                        ),
                    )

                    size = fpath.stat().st_size
                    self._uploaded[rel_path] = UploadedFile(
                        local_path=str(fpath),
                        gemini_file=gemini_file,
                        size=size,
                    )
                    self._total_uploaded_bytes += size
                    uploaded_count += 1

                    # Progress callback
                    if self._on_progress:
                        try:
                            self._on_progress(uploaded_count, total, rel_path)
                        except Exception:
                            pass

                    # Log every 10 files
                    if (i + 1) % 10 == 0:
                        logger.info(f"  📄 Uploaded {i + 1}/{total} files...")

                except Exception as e:
                    errors += 1
                    logger.warning(f"  ⚠️ Failed to upload {rel_path}: {e}")
                    continue

            logger.info(
                f"✅ File upload complete: {uploaded_count}/{total} files "
                f"({self._total_uploaded_bytes / 1024:.0f}KB), {errors} errors"
            )

            return {
                "status": "complete",
                "files": uploaded_count,
                "errors": errors,
                "total_bytes": self._total_uploaded_bytes,
            }

        finally:
            self._upload_in_progress = False

    def get_file_parts(self) -> list:
        """
        Get Gemini Part references for all uploaded files.
        These are injected into the contents of generate() calls.
        """
        if not self._uploaded:
            return []

        parts = []
        for rel_path, uf in self._uploaded.items():
            try:
                parts.append(types.Part.from_uri(
                    file_uri=uf.uri,
                    mime_type=uf.mime_type,
                ))
            except Exception as e:
                logger.warning(f"Could not create part for {rel_path}: {e}")

        return parts

    def get_file_summary(self) -> str:
        """Get a text summary of uploaded files for system prompts."""
        if not self._uploaded:
            return ""

        lines = [f"# Uploaded Project Files ({len(self._uploaded)} files)\n"]
        for rel_path, uf in sorted(self._uploaded.items()):
            lines.append(f"- {rel_path} ({uf.size / 1024:.1f}KB)")

        return "\n".join(lines)

    async def cleanup(self):
        """Delete all uploaded files from Gemini Files API."""
        if not self._client or not self._uploaded:
            return

        logger.info(f"🗑️ Cleaning up {len(self._uploaded)} uploaded files...")

        for rel_path, uf in list(self._uploaded.items()):
            try:
                await asyncio.to_thread(
                    self._client.files.delete,
                    name=uf.remote_name,
                )
            except Exception as e:
                logger.debug(f"Cleanup failed for {rel_path}: {e}")

        self._uploaded.clear()
        self._total_uploaded_bytes = 0
        logger.info("✅ File cleanup complete")

    async def refresh_file(self, relative_path: str):
        """Re-upload a single file that has changed."""
        if not self._client or not self._workspace_root:
            return

        fpath = Path(self._workspace_root) / relative_path

        if not fpath.exists():
            # File was deleted — remove from uploads
            if relative_path in self._uploaded:
                try:
                    await asyncio.to_thread(
                        self._client.files.delete,
                        name=self._uploaded[relative_path].remote_name,
                    )
                except Exception:
                    pass
                del self._uploaded[relative_path]
            return

        # Delete old version if exists
        if relative_path in self._uploaded:
            try:
                await asyncio.to_thread(
                    self._client.files.delete,
                    name=self._uploaded[relative_path].remote_name,
                )
            except Exception:
                pass

        # Upload new version
        try:
            mime, _ = mimetypes.guess_type(str(fpath))
            if not mime:
                mime = "text/plain"

            gemini_file = await asyncio.to_thread(
                self._client.files.upload,
                file=str(fpath),
                config=types.UploadFileConfig(
                    display_name=relative_path,
                    mime_type=mime,
                ),
            )

            size = fpath.stat().st_size
            old_size = self._uploaded.get(relative_path, None)
            if old_size:
                self._total_uploaded_bytes -= old_size.size

            self._uploaded[relative_path] = UploadedFile(
                local_path=str(fpath),
                gemini_file=gemini_file,
                size=size,
            )
            self._total_uploaded_bytes += size

            logger.info(f"🔄 Refreshed: {relative_path}")

        except Exception as e:
            logger.warning(f"Failed to refresh {relative_path}: {e}")
