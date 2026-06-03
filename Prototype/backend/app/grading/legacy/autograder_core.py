#!/usr/bin/env python3
"""
AutoGrade.py - Automated Assignment Grading System
"""

import os
import sys
import subprocess
import zipfile
import tempfile
import shutil
import errno
from pathlib import Path
import openai
from typing import List, Dict, Optional, Tuple
import json
import re
import html
import time
from datetime import datetime
from dotenv import load_dotenv
import requests


_EXTRACT_COMPLETE_MARKER = ".extract_complete"

# Load environment variables from explicit .env locations.
ROOT_DIR = Path(__file__).resolve().parents[4]
load_dotenv(ROOT_DIR / ".env", override=True)
load_dotenv(ROOT_DIR / "backend" / ".env", override=True)

class AutoGrader:
    def __init__(
        self,
        api_key: str = None,
        use_custom_endpoint: bool = None,
        custom_endpoint: str = None,
        model_provider: str = None,
        model_name: str = None,
    ):
        """Initialize the auto-grader with API configuration"""
        provider = (model_provider or os.getenv('MODEL_PROVIDER', 'openai')).strip().lower()

        if use_custom_endpoint is None:
            self.use_custom_endpoint = provider in {'custom', 'ollama', 'local'}
        else:
            self.use_custom_endpoint = use_custom_endpoint

        self.model_provider = 'custom' if self.use_custom_endpoint else 'openai'
        default_model = 'gemma3:12b' if self.use_custom_endpoint else 'gpt-5.4'
        self.model_name = (model_name or os.getenv('MODEL_NAME', default_model)).strip()
        self.custom_endpoint = custom_endpoint or os.getenv('CUSTOM_ENDPOINT', 'http://dryangai.ddns.net:11434')
        self.openai_timeout = int(os.getenv('OPENAI_TIMEOUT_SECONDS', '240'))
        self.openai_max_retries = int(os.getenv('OPENAI_MAX_RETRIES', '3'))
        self.openai_retry_backoff = float(os.getenv('OPENAI_RETRY_BACKOFF_SECONDS', '2.0'))
        # Support for resuming interrupted jobs
        self.completed_students = []
        self.selected_students = []
        self.cancel_check = None
        self.progress_callback = None
        self.last_run_stopped = False
        self.openai_stream = os.getenv('OPENAI_STREAM', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
        self.language_hint = os.getenv('CODE_SPECIALTY', 'csharp').strip().lower()
        self.grading_context = ""
        self.student_image_artifacts: Dict[str, List[Dict[str, str]]] = {}
        self.current_job_id: Optional[str] = None
        
        if not self.use_custom_endpoint:
            # OpenAI setup
            self.api_key = api_key or os.getenv('OPENAI_API_KEY')
            if not self.api_key:
                raise ValueError("OpenAI API key must be provided via parameter or OPENAI_API_KEY environment variable")
            
            # Initialize OpenAI client
            openai.api_key = self.api_key
            self.client = openai.OpenAI(api_key=self.api_key)
        else:
            # Custom endpoint setup
            self.api_key = None
            self.client = None
            print(f"Using custom endpoint: {self.custom_endpoint}")
            print(f"Using custom model: {self.model_name}")
            # Test connection and try fallbacks
            if not self.test_custom_endpoint():
                # Try localhost as fallback
                if "localhost" not in self.custom_endpoint and "127.0.0.1" not in self.custom_endpoint:
                    print("   Trying localhost as fallback...")
                    localhost_endpoint = "http://localhost:11434"
                    temp_endpoint = self.custom_endpoint
                    self.custom_endpoint = localhost_endpoint
                    if self.test_custom_endpoint():
                        print(f"✅ Successfully connected to localhost fallback: {localhost_endpoint}")
                    else:
                        print("❌ Localhost fallback also failed")
                        self.custom_endpoint = temp_endpoint  # Restore original
                        print("⚠️  WARNING: Cannot connect to custom endpoint!")
                        print("   Check if your server is running and accessible.")
                        print("   Common issues:")
                        print("   - Server not started")
                        print("   - Wrong URL or port")
                        print("   - Firewall blocking connection")
                        print("   - API path should be /v1/chat/completions")
                else:
                    print("⚠️  WARNING: Cannot connect to custom endpoint!")
                    print("   Check if your server is running and accessible.")
        
        # Default equal weights for parts (will be adjusted based on assignment)
        self.part_weights = {}

    def _supported_reasoning_efforts(self) -> set:
        """Return supported reasoning effort values for the active model."""
        model = (self.model_name or "").strip().lower()
        # gpt-5.4-pro rejects "low" and accepts medium/high/xhigh.
        if "gpt-5.4-pro" in model:
            return {"medium", "high", "xhigh"}
        return {"low", "medium", "high", "xhigh"}

    def _resolve_reasoning_effort(self) -> str:
        """Choose a valid reasoning effort for the configured model."""
        requested = os.getenv("OPENAI_REASONING_EFFORT", "low").strip().lower()
        if requested not in {"low", "medium", "high", "xhigh"}:
            requested = "low"

        supported = self._supported_reasoning_efforts()
        if requested in supported:
            return requested

        # Prefer medium when low is unsupported.
        if "medium" in supported:
            return "medium"
        return sorted(supported)[0]

    def _normalized_language_hint(self) -> str:
        lang = (getattr(self, 'language_hint', 'csharp') or 'csharp').strip().lower()
        if lang in {'any', 'multi', 'mixed', 'generic'}:
            return 'mixed'
        if lang in {'cs', 'c#', 'csharp'}:
            return 'csharp'
        if lang in {'js', 'javascript', 'node'}:
            return 'javascript'
        if lang in {'py', 'python'}:
            return 'python'
        if lang in {'swift'}:
            return 'swift'
        if lang in {'sql'}:
            return 'sql'
        return lang

    def _target_extensions(self) -> Tuple[str, ...]:
        lang = self._normalized_language_hint()
        mapping = {
            'csharp': ('.cs',),
            'swift': ('.swift',),
            'python': ('.py',),
            'javascript': ('.js', '.jsx', '.ts', '.tsx'),
            'sql': ('.sql',),
            'mixed': ('.cs', '.py', '.js', '.jsx', '.ts', '.tsx', '.sql', '.java', '.cpp', '.c', '.cc', '.h', '.hpp', '.swift'),
        }
        return mapping.get(lang, ('.cs',))

    def _language_display_name(self) -> str:
        names = {
            'csharp': 'C#',
            'swift': 'Swift',
            'python': 'Python',
            'javascript': 'JavaScript',
            'sql': 'SQL',
            'mixed': 'mixed-language',
        }
        return names.get(self._normalized_language_hint(), self._normalized_language_hint().upper())

    def _file_marker_prefix(self) -> str:
        return '-- File:' if self._normalized_language_hint() == 'sql' else '// File:'

    def _combine_source_files(self, base_dir: str, file_paths: List[str]) -> str:
        marker = self._file_marker_prefix()
        blocks = []
        for file_path in sorted(set(file_paths)):
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read().strip()
                if content:
                    relative_path = os.path.relpath(file_path, base_dir)
                    blocks.append(f"{marker} {relative_path}\n{content}")
            except Exception as e:
                print(f"Error reading {file_path}: {e}")
        return "\n\n".join(blocks)

    def _extract_docx_text(self, file_path: str) -> str:
        """Extract readable text from a .docx file using stdlib zip/xml parsing."""
        try:
            with zipfile.ZipFile(file_path, 'r') as zf:
                xml = zf.read('word/document.xml').decode('utf-8', errors='ignore')
            xml = re.sub(r'</w:p>', '\n', xml)
            xml = re.sub(r'<[^>]+>', '', xml)
            text = html.unescape(xml)
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            return '\n'.join(lines)
        except Exception:
            return ""

    def _ocr_image_file(self, file_path: str, max_chars: int = 4000) -> str:
        """Extract OCR text from an image file using the local tesseract binary when available."""
        try:
            tesseract_path = shutil.which('tesseract')
            if not tesseract_path:
                return ""

            result = subprocess.run(
                [tesseract_path, file_path, 'stdout', '--psm', '6'],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if result.returncode != 0:
                return ""

            text = (result.stdout or '').strip()
            if not text:
                return ""

            lines = [line.strip() for line in text.splitlines() if line.strip()]
            cleaned = '\n'.join(lines)
            return cleaned[:max_chars]
        except Exception:
            return ""

    def _extract_pptx_text(self, file_path: str) -> str:
        """Extract readable text from a .pptx file using stdlib zip/xml parsing."""
        try:
            with zipfile.ZipFile(file_path, 'r') as zf:
                slide_names = sorted(
                    name for name in zf.namelist()
                    if name.startswith('ppt/slides/slide') and name.endswith('.xml')
                )

                slide_blocks = []
                for index, slide_name in enumerate(slide_names, start=1):
                    xml = zf.read(slide_name).decode('utf-8', errors='ignore')
                    texts = re.findall(r'<a:t[^>]*>(.*?)</a:t>', xml, flags=re.DOTALL)
                    cleaned = [html.unescape(text).strip() for text in texts if html.unescape(text).strip()]
                    if cleaned:
                        slide_blocks.append(f"Slide {index}:\n" + '\n'.join(cleaned))

                media_names = sorted(
                    name for name in zf.namelist()
                    if name.startswith('ppt/media/') and Path(name).suffix.lower() in self._SCREENSHOT_EXTENSIONS
                )
                for media_name in media_names:
                    suffix = Path(media_name).suffix.lower()
                    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_image:
                        temp_image.write(zf.read(media_name))
                        temp_image_path = temp_image.name
                    try:
                        ocr_text = self._ocr_image_file(temp_image_path)
                    finally:
                        try:
                            os.unlink(temp_image_path)
                        except OSError:
                            pass
                    if ocr_text:
                        slide_blocks.append(f"Image OCR {Path(media_name).name}:\n{ocr_text}")

            return '\n\n'.join(slide_blocks)
        except Exception:
            return ""

    def _extract_pdf_text(self, file_path: str) -> str:
        """Extract readable text from a PDF file when pypdf is available."""
        try:
            from pypdf import PdfReader

            reader = PdfReader(file_path)
            pages = []
            for index, page in enumerate(reader.pages, start=1):
                try:
                    text = (page.extract_text() or '').strip()
                except Exception:
                    text = ''
                if text:
                    pages.append(f"Page {index}:\n{text}")
            return '\n\n'.join(pages)
        except Exception:
            return ""

    def _read_supporting_document_text(self, file_path: str) -> str:
        ext = Path(file_path).suffix.lower()

        try:
            if ext in self._SCREENSHOT_EXTENSIONS:
                return self._ocr_image_file(file_path).strip()
            if ext in {'.txt', '.md', '.html', '.htm', '.csv', '.tsv', '.json', '.xml', '.yml', '.yaml'}:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read().strip()
            if ext == '.docx':
                return self._extract_docx_text(file_path).strip()
            if ext == '.pptx':
                return self._extract_pptx_text(file_path).strip()
            if ext == '.pdf':
                return self._extract_pdf_text(file_path).strip()
        except Exception:
            return ""

        return ""

    def _collect_supporting_documents(self, directory: str, max_chars: int = 24000) -> str:
        """Collect readable supporting-document text for grading context, even when code files exist."""
        readable_exts = {
            '.txt', '.md', '.docx', '.pdf', '.pptx', '.html', '.htm',
            '.csv', '.tsv', '.json', '.xml', '.yml', '.yaml',
            '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.tif', '.webp'
        }
        marker = self._file_marker_prefix()
        blocks = []
        consumed = 0

        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if not self._is_ignored_directory(d)]
            for file in sorted(files):
                ext = Path(file).suffix.lower()
                if ext not in readable_exts:
                    continue

                file_path = os.path.join(root, file)
                rel = os.path.relpath(file_path, directory)
                text = self._read_supporting_document_text(file_path)
                if not text:
                    continue

                remaining = max_chars - consumed
                if remaining <= 0:
                    blocks.append(f"{marker} [supporting documents truncated after {max_chars} characters]")
                    return '\n\n'.join(blocks)

                text = text[:remaining].strip()
                if not text:
                    continue

                blocks.append(f"{marker} {rel}\n{text}")
                consumed += len(text)

        return '\n\n'.join(blocks)

    def _collect_report_documents(self, directory: str) -> str:
        """Collect weekly report style artifacts when code files are not present."""
        report_exts = {'.txt', '.md', '.docx', '.pdf', '.pptx', '.html', '.htm'}
        marker = self._file_marker_prefix()
        blocks = []

        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if not self._is_ignored_directory(d)]
            for file in files:
                ext = Path(file).suffix.lower()
                if ext not in report_exts:
                    continue

                file_path = os.path.join(root, file)
                rel = os.path.relpath(file_path, directory)
                text = ""

                text = self._read_supporting_document_text(file_path)
                if not text and ext in {'.pdf', '.pptx'}:
                    text = f"[{ext[1:].upper()} file attached: {rel}]"

                if text:
                    blocks.append(f"{marker} {rel}\n{text}")

        return "\n\n".join(blocks)

    _SCREENSHOT_EXTENSIONS = frozenset(('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.tif', '.webp'))
    _PRESENTATION_EXTENSIONS = frozenset(('.ppt', '.pptx', '.key', '.odp'))
    _VIDEO_EXTENSIONS = frozenset(('.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'))
    _DOCUMENT_EXTENSIONS = frozenset(('.pdf', '.doc', '.docx', '.odt', '.pages', '.rtf', '.txt', '.md', '.html', '.htm'))
    _ARTIFACT_KEYWORDS = {
        'presentation': ('presentation', 'slides', 'slide', 'deck', 'powerpoint', 'pitch'),
        'video': ('video', 'recording', 'walkthrough', 'demo', 'screencast', 'capture'),
        'screenshot': ('screenshot', 'screen shot', 'screen-shot'),
        'report': ('report', 'reflection', 'summary', 'documentation', 'writeup', 'write-up', 'observations'),
    }

    def _classify_submission_artifact(self, relative_path: str) -> Optional[str]:
        normalized = relative_path.replace('\\', '/').lower()
        file_name = os.path.basename(normalized)
        ext = Path(file_name).suffix.lower()

        if ext in self._SCREENSHOT_EXTENSIONS:
            return 'screenshot'
        if ext in self._PRESENTATION_EXTENSIONS:
            return 'presentation'
        if ext in self._VIDEO_EXTENSIONS:
            return 'video'

        for category, keywords in self._ARTIFACT_KEYWORDS.items():
            if any(keyword in normalized for keyword in keywords):
                if category == 'presentation' and ext in self._DOCUMENT_EXTENSIONS:
                    return 'presentation'
                if category == 'video':
                    return 'video'
                if category == 'screenshot':
                    return 'screenshot'
                if category == 'report' and ext in self._DOCUMENT_EXTENSIONS:
                    return 'report'

        if ext in self._DOCUMENT_EXTENSIONS:
            return 'document'
        return None

    def _submission_inventory_note(self, directory: str) -> str:
        """Return a summary of non-code deliverables present in the submission directory."""
        categorized: Dict[str, List[str]] = {
            'presentation': [],
            'video': [],
            'screenshot': [],
            'report': [],
            'document': [],
        }
        ignored_exts = {'.zip', '.7z', '.rar', '.tar', '.gz', '.dll', '.exe', '.pdb', '.cache', '.class', '.o', '.so', '.dylib'}

        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if not self._is_ignored_directory(d)]
            for file in files:
                lowered = file.lower()
                if Path(lowered).suffix in ignored_exts or lowered.endswith(('.g.cs', '.generated.cs', '.assemblyinfo.cs')):
                    continue
                rel = os.path.relpath(os.path.join(root, file), directory)
                category = self._classify_submission_artifact(rel)
                if category:
                    categorized[category].append(rel)

        detected_categories = [category for category, items in categorized.items() if items]
        if not detected_categories:
            return ""

        labels = {
            'presentation': 'presentation/slides',
            'video': 'video/recording',
            'screenshot': 'screenshots/images',
            'report': 'report/write-up',
            'document': 'supporting documents',
        }
        summary = ', '.join(
            f"{labels[category]}={len(categorized[category])}"
            for category in detected_categories
        )

        lines = [
            f"[SUBMISSION INVENTORY: detected non-code deliverables -> {summary}]",
            "Use these file listings to evaluate required supporting materials before marking them missing.",
        ]

        if categorized['presentation']:
            lines.append(
                f"[PRESENTATION FILES DETECTED: {len(categorized['presentation'])} file(s) included — do not mark a presentation/slides deliverable missing if one of these satisfies the requirement format.]"
            )
        if categorized['video']:
            lines.append(
                f"[VIDEO FILES DETECTED: {len(categorized['video'])} file(s) included — do not mark a video/demo deliverable missing if one of these satisfies the requirement format.]"
            )
        if categorized['screenshot']:
            lines.append(
                f"[SCREENSHOT FILES DETECTED: {len(categorized['screenshot'])} image file(s) included — treat screenshot/image requirements as fulfilled unless the instructions demand specific missing content.]"
            )

        for category in detected_categories:
            lines.append(f"{labels[category]}:")
            for rel in sorted(set(categorized[category])):
                lines.append(f"  - {rel}")

        return '\n'.join(lines)

    def _submission_tree_note(self, directory: str, max_entries: int = 200, max_depth: int = 4) -> str:
        """Return a compact tree view of the submission folder contents."""
        root_path = Path(directory)
        lines = [f"[SUBMISSION TREE: compact view of files/folders under {root_path.name or '.'}]"]
        emitted = 0
        truncated = False

        def walk(path: Path, prefix: str = "", depth: int = 0) -> None:
            nonlocal emitted, truncated
            if truncated or depth > max_depth:
                return

            try:
                children = [
                    child for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
                    if not child.name.startswith('.') and child.name != '__MACOSX'
                ]
            except OSError:
                return

            visible_children = []
            for child in children:
                if child.is_dir() and self._is_ignored_directory(child.name):
                    continue
                visible_children.append(child)

            for idx, child in enumerate(visible_children):
                if emitted >= max_entries:
                    truncated = True
                    return

                connector = "└── " if idx == len(visible_children) - 1 else "├── "
                suffix = "/" if child.is_dir() else ""
                lines.append(f"{prefix}{connector}{child.name}{suffix}")
                emitted += 1

                if child.is_dir():
                    extension = "    " if idx == len(visible_children) - 1 else "│   "
                    walk(child, prefix + extension, depth + 1)

        walk(root_path)
        if truncated:
            lines.append(f"... tree truncated after {max_entries} entries")
        return '\n'.join(lines)

    def _collect_submission_images(self, directory: str, max_images: int = 40) -> List[Dict[str, str]]:
        """Collect image files from a submission directory for optional report preview."""
        image_entries: List[Dict[str, str]] = []
        root_path = Path(directory)
        if not root_path.exists():
            return image_entries

        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if not self._is_ignored_directory(d)]
            for file in sorted(files):
                ext = Path(file).suffix.lower()
                if ext not in self._SCREENSHOT_EXTENSIONS:
                    continue

                abs_path = Path(root) / file
                rel_path = os.path.relpath(str(abs_path), directory).replace('\\', '/')
                image_entries.append(
                    {
                        'name': file,
                        'relative_path': rel_path,
                        'absolute_path': str(abs_path),
                    }
                )
                if len(image_entries) >= max_images:
                    return image_entries

        return image_entries

    def _prepare_report_image_assets(
        self,
        student_name: str,
        image_entries: List[Dict[str, str]],
        output_dir: str,
        max_images: int = 24,
        max_file_bytes: int = 8 * 1024 * 1024,
    ) -> List[Dict[str, str]]:
        """Copy submission images into report assets and return relative paths for HTML embedding."""
        if not image_entries:
            return []

        safe_name = re.sub(r'[<>:"/\\|?*]', '_', student_name)
        asset_root = Path(output_dir)
        asset_root.mkdir(parents=True, exist_ok=True)

        report_images: List[Dict[str, str]] = []
        used_names = set()

        for index, item in enumerate(image_entries[:max_images], start=1):
            source = Path(str(item.get('absolute_path', '') or ''))
            if not source.exists() or not source.is_file():
                continue

            try:
                if source.stat().st_size > max_file_bytes:
                    continue
            except OSError:
                continue

            clean_base = re.sub(r'[^A-Za-z0-9._-]+', '_', source.name)
            if not clean_base:
                clean_base = f"image_{index}{source.suffix.lower()}"
            dest_name = f"{index:02d}_{clean_base}"
            while dest_name in used_names:
                dest_name = f"{index:02d}_{len(used_names)}_{clean_base}"
            used_names.add(dest_name)

            destination = asset_root / f"report_image_{safe_name}_{dest_name}"
            try:
                shutil.copy2(source, destination)
            except Exception:
                continue

            report_images.append(
                {
                    'title': str(item.get('relative_path') or source.name),
                    'src': destination.name,
                }
            )

        return report_images

    def _extract_submission_from_nested_structure(self, zip_path: str) -> str:
        temp_dir = tempfile.mkdtemp()
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            return self.find_submission_in_directory(temp_dir)
        except Exception as e:
            print(f"Error extracting from {zip_path}: {e}")
            return ""
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _extract_submission_from_7z_structure(self, archive_path: str) -> str:
        temp_dir = tempfile.mkdtemp()
        try:
            try:
                import py7zr
                with py7zr.SevenZipFile(archive_path, mode='r') as archive:
                    archive.extractall(path=temp_dir)
                return self.find_submission_in_directory(temp_dir)
            except ImportError:
                print(f"Warning: py7zr library not installed. Cannot extract 7z file: {archive_path}")
                return ""
            except Exception as e:
                print(f"Error extracting 7z file {archive_path}: {e}")
                return ""
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def find_submission_in_directory(self, directory: str) -> str:
        """Find submission files for the configured language hint."""
        if self._normalized_language_hint() == 'csharp':
            return self.find_csharp_in_directory(directory)

        target_exts = self._target_extensions()
        matched_files = []

        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if not self._is_ignored_directory(d)]

            for file in files:
                file_path = os.path.join(root, file)
                if Path(file).suffix.lower() in target_exts:
                    matched_files.append(file_path)

            if not matched_files:
                for file in files:
                    lowered = file.lower()
                    if lowered.endswith('.zip'):
                        content = self._extract_submission_from_nested_structure(os.path.join(root, file))
                        if content:
                            return self._append_submission_artifact_notes(content, directory)
                    elif lowered.endswith('.7z'):
                        content = self._extract_submission_from_7z_structure(os.path.join(root, file))
                        if content:
                            return self._append_submission_artifact_notes(content, directory)

        if not matched_files:
            # Weekly reports can be text/docx/pdf/pptx without SQL/code files.
            report_text = self._collect_report_documents(directory)
            if report_text:
                print(f"  ℹ️  Falling back to weekly report documents in: {os.path.basename(directory)}")
                return self._append_submission_artifact_notes(report_text, directory)
            return ""

        return self._append_submission_artifact_notes(
            self._combine_source_files(directory, matched_files), directory
        )
    
    def test_custom_endpoint(self) -> bool:
        """Test if custom endpoint is accessible"""
        try:
            import requests
            # Try a simple health check or connection test
            test_url = f"{self.custom_endpoint}/v1/models"  # Common endpoint for listing models
            response = requests.get(test_url, timeout=10)
            if response.status_code in [200, 404]:  # 404 is OK, means server is responding
                print("✅ Custom endpoint is accessible")
                return True
            else:
                print(f"⚠️  Custom endpoint responded with status: {response.status_code}")
                return False
        except requests.exceptions.ConnectionError:
            print("❌ Cannot connect to custom endpoint")
            return False
        except ImportError:
            print("⚠️  requests library not available for testing")
            return False
        except Exception as e:
            print(f"⚠️  Custom endpoint test failed: {e}")
            return False
        self.part_weights = {}
        
    def load_assignment_instructions(self, html_file: str) -> str:
        """Load the assignment instructions from HTML file"""
        try:
            with open(html_file, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            print(f"Error loading assignment instructions: {e}")
            return ""

    def clean_html_block(self, block: str) -> str:
        """Convert an HTML block to readable plain text."""
        block = re.sub(r'<br\s*/?>', '\n', block, flags=re.IGNORECASE)
        block = re.sub(r'</(p|li|h\d|ul|ol|pre|div)>', '\n', block, flags=re.IGNORECASE)
        block = re.sub(r'<[^>]+>', '', block)
        block = html.unescape(block)
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        return '\n'.join(lines)

    def _make_instruction_part(self, part_num: int, title: str, requirements: str) -> Dict[str, str]:
        title = (title or f'Part {part_num}').strip() or f'Part {part_num}'
        requirements = (requirements or '').strip()
        is_optional = bool(
            re.search(r'optional|stretch', title, re.IGNORECASE)
            or re.search(r'\boptional\b|\bstretch\b', requirements, re.IGNORECASE)
        )
        return {
            'key': f'part{part_num}',
            'part_num': str(part_num),
            'title': title,
            'requirements': requirements,
            'is_optional': is_optional,
        }

    def extract_assignment_parts(self, instructions_html: str) -> List[Dict[str, str]]:
        """Extract assignment part metadata from HTML headings."""
        if not instructions_html:
            return []

        heading_pattern = re.compile(
            r'<h([1-6])\b[^>]*>(.*?)</h\1>',
            re.IGNORECASE | re.DOTALL,
        )
        part_heading_pattern = re.compile(
            r'^\s*(part|step|task|question|exercise|problem|section)\s*(\d+)\b(?:\s*[:\-\.]\s*(.*))?$',
            re.IGNORECASE | re.DOTALL,
        )
        matches = []
        for match in heading_pattern.finditer(instructions_html):
            heading_text = self.clean_html_block(match.group(2))
            part_match = part_heading_pattern.match(heading_text)
            if not part_match:
                continue
            matches.append((match, part_match))

        parts = []

        for idx, (match, part_match) in enumerate(matches):
            part_num = int(part_match.group(2))
            section_title = (part_match.group(3) or '').strip()
            label = part_match.group(1).strip().title()
            title = section_title or f'{label} {part_num}'
            start = match.end()
            end = matches[idx + 1][0].start() if idx + 1 < len(matches) else len(instructions_html)
            raw_block = instructions_html[start:end]
            requirements = self.clean_html_block(raw_block)

            parts.append(self._make_instruction_part(part_num, title, requirements))

        if parts:
            return sorted(parts, key=lambda p: int(p['part_num']))

        full_instructions = self.clean_html_block(instructions_html)
        if not full_instructions:
            return []

        fallback_title = 'Assignment Requirements'
        title_match = re.search(r'<h[1-3]\b[^>]*>(.*?)</h[1-3]>', instructions_html, re.IGNORECASE | re.DOTALL)
        if title_match:
            candidate_title = self.clean_html_block(title_match.group(1))
            if candidate_title:
                fallback_title = candidate_title

        return [self._make_instruction_part(1, fallback_title, full_instructions)]

    def extract_part_instruction_map(self, instructions_html: str) -> Dict[str, str]:
        """Extract part requirement text from assignment HTML for reporting."""
        part_map = {}
        for part in self.extract_assignment_parts(instructions_html):
            header = f"{part['title']}"
            body = part['requirements']
            text = f"{header}\n{body}" if body else header
            if part['is_optional']:
                text = f"[Optional] {text}"
            part_map[part['key']] = text
        return part_map
    
    def ensure_archives_extracted(self, folder: Path):
        """Extract any zip/7z archives inside a student folder in-place (persistent, not temp)"""
        for archive_file in sorted(folder.iterdir()):
            if not archive_file.is_file():
                continue
            extract_target = folder / archive_file.stem
            if archive_file.suffix.lower() == '.zip':
                if not extract_target.exists():
                    print(f"  📦 Extracting nested zip in-place: {archive_file.name}")
                    extract_target.mkdir(exist_ok=True)
                    try:
                        with zipfile.ZipFile(str(archive_file), 'r') as zf:
                            zf.extractall(str(extract_target))
                    except Exception as e:
                        print(f"  ⚠️  Failed to extract {archive_file.name}: {e}")
                        extract_target.rmdir()
            elif archive_file.suffix.lower() == '.7z':
                if not extract_target.exists():
                    print(f"  📦 Extracting nested 7z in-place: {archive_file.name}")
                    try:
                        import py7zr
                        extract_target.mkdir(exist_ok=True)
                        with py7zr.SevenZipFile(str(archive_file), mode='r') as archive:
                            archive.extractall(path=str(extract_target))
                    except ImportError:
                        print(f"  ⚠️  py7zr not installed — cannot extract {archive_file.name}")
                    except Exception as e:
                        print(f"  ⚠️  Failed to extract {archive_file.name}: {e}")

    def _visible_entries(self, folder: Path) -> List[Path]:
        # Exclude system folders like __MACOSX and dotfiles
        return [
            entry for entry in sorted(folder.iterdir())
            if not entry.name.startswith('.')
            and entry.name != '__MACOSX'
        ]

    def _is_submission_container_entry(self, entry: Path) -> bool:
        return entry.is_dir() or entry.suffix.lower() in {'.zip', '.7z'}

    def _is_grouping_directory(self, entry: Path) -> bool:
        """Return True if all visible children are submission containers (folders/zips/7z) and there are no direct files."""
        if not entry.is_dir():
            return False

        children = self._visible_entries(entry)
        if not children:
            return False

        # If any child is a file that is not a zip/7z, this is not a grouping directory
        for child in children:
            if child.is_file() and child.suffix.lower() not in {'.zip', '.7z'}:
                return False

        # If all children are submission containers (folders/zips/7z), treat as grouping directory
        if all(self._is_submission_container_entry(child) for child in children):
            return True

        return False

    def _expand_grouping_directories(self, entries: List[Path]) -> List[Path]:
        # Recursively flatten all grouping/assignment folders until only student-level entries remain
        result = []
        for entry in entries:
            if self._is_grouping_directory(entry):
                children = [child for child in self._visible_entries(entry) if self._is_submission_container_entry(child)]
                # Recurse into children
                result.extend(self._expand_grouping_directories(children))
            else:
                result.append(entry)
        return result

    def _student_entries_from_root(self, extract_dir: Path) -> List[Path]:
        """Recursively flatten all grouping directories and return all student-level entries (folders/zips/7z)."""
        entries = [entry for entry in self._visible_entries(extract_dir) if self._is_submission_container_entry(entry)]
        # Recursively expand grouping directories until only student-level entries remain
        prev_entries = None
        while prev_entries != entries:
            prev_entries = entries
            expanded = []
            for entry in entries:
                if self._is_grouping_directory(entry):
                    children = [child for child in self._visible_entries(entry) if self._is_submission_container_entry(child)]
                    expanded.extend(children)
                else:
                    expanded.append(entry)
            entries = expanded
        return entries

    def extract_all_assignments(self, main_zip_path: str) -> Dict[str, str]:
        """Extract all student assignments from the main zip file and nested zips"""
        assignments = {}
        processed_students = set()
        self.student_image_artifacts = {}

        # ── Step 1: Extract the main zip into a named folder (skip if already done) ──
        zip_path_obj = Path(main_zip_path)
        extract_dir = zip_path_obj.parent / zip_path_obj.stem
        extract_marker = extract_dir / _EXTRACT_COMPLETE_MARKER
        if extract_dir.exists() and not extract_marker.exists():
            print(f"⚠️  Removing incomplete submissions folder before re-extracting: {extract_dir}")
            if extract_dir.is_dir():
                shutil.rmtree(extract_dir)
            else:
                extract_dir.unlink()

        if extract_marker.exists():
            print(f"📂 Submissions folder already exists, reusing: {extract_dir}")
        else:
            print(f"📦 Extracting main zip to: {extract_dir}")
            temp_extract_dir = Path(
                tempfile.mkdtemp(
                    prefix=f".{zip_path_obj.stem}-extract-",
                    dir=str(zip_path_obj.parent),
                )
            )
            try:
                with zipfile.ZipFile(main_zip_path, 'r') as main_zip:
                    main_zip.extractall(str(temp_extract_dir))
                (temp_extract_dir / _EXTRACT_COMPLETE_MARKER).write_text("ok\n", encoding="utf-8")
                try:
                    temp_extract_dir.replace(extract_dir)
                except FileExistsError:
                    print(f"📂 Another worker finished extraction first, reusing: {extract_dir}")
                    shutil.rmtree(temp_extract_dir, ignore_errors=True)
                except OSError as exc:
                    if exc.errno in {errno.ENOTEMPTY, errno.EEXIST} and extract_dir.exists():
                        # macOS can report ENOTEMPTY during concurrent/near-concurrent directory replaces.
                        print(f"📂 Existing submissions folder detected during replace, reusing: {extract_dir}")
                        shutil.rmtree(temp_extract_dir, ignore_errors=True)
                        if not extract_marker.exists():
                            try:
                                extract_marker.write_text("ok\n", encoding="utf-8")
                            except OSError:
                                pass
                    elif extract_marker.exists():
                        print(f"📂 Reusing concurrently prepared submissions folder: {extract_dir}")
                        shutil.rmtree(temp_extract_dir, ignore_errors=True)
                    else:
                        raise
            except Exception:
                shutil.rmtree(temp_extract_dir, ignore_errors=True)
                raise
            print(f"✅ Extracted to: {extract_dir}")

        # ── Step 2: Iterate student-level entries (handles optional wrapper folder) ──
        student_entries = self._student_entries_from_root(extract_dir)
        if not student_entries:
            print("⚠️  No student submission entries found in extracted archive")

        for student_entry in student_entries:

            student_name = self.extract_student_name(student_entry.name)
            normalized_name = self.normalize_student_name(student_name)

            if normalized_name in processed_students:
                continue

            if student_entry.is_dir():
                # Student folder: extract any archives inside it in-place, then find source files
                self.ensure_archives_extracted(student_entry)
                submission_root = str(student_entry)
                submission_content = self.find_submission_in_directory(submission_root)

            elif student_entry.suffix.lower() == '.zip':
                # Bare zip at top level — extract in-place next to the zip
                inner_dir = student_entry.parent / student_entry.stem
                if not inner_dir.exists():
                    print(f"  📦 Extracting top-level student zip: {student_entry.name}")
                    inner_dir.mkdir(exist_ok=True)
                    try:
                        with zipfile.ZipFile(str(student_entry), 'r') as zf:
                            zf.extractall(str(inner_dir))
                    except Exception as e:
                        print(f"  ⚠️  Failed to extract {student_entry.name}: {e}")
                        inner_dir.rmdir()
                        continue
                self.ensure_archives_extracted(inner_dir)
                submission_root = str(inner_dir)
                submission_content = self.find_submission_in_directory(submission_root)

            elif student_entry.suffix.lower() == '.7z':
                inner_dir = student_entry.parent / student_entry.stem
                if not inner_dir.exists():
                    print(f"  📦 Extracting top-level student 7z: {student_entry.name}")
                    try:
                        import py7zr
                        inner_dir.mkdir(exist_ok=True)
                        with py7zr.SevenZipFile(str(student_entry), mode='r') as archive:
                            archive.extractall(path=str(inner_dir))
                    except ImportError:
                        print(f"  ⚠️  py7zr not installed — cannot extract {student_entry.name}")
                        continue
                    except Exception as e:
                        print(f"  ⚠️  Failed to extract {student_entry.name}: {e}")
                        continue
                self.ensure_archives_extracted(inner_dir)
                submission_root = str(inner_dir)
                submission_content = self.find_submission_in_directory(submission_root)

            else:
                continue  # Not a folder or archive — skip

            if submission_content and student_name:
                assignments[student_name] = submission_content
                self.student_image_artifacts[student_name] = self._collect_submission_images(submission_root)
                processed_students.add(normalized_name)
                print(f"  ✓ Found {self._language_display_name()} submission for: {student_name}")
            else:
                print(f"  ⚠️  No {self._language_display_name()} submission found for: {student_name}")

        return assignments

    def list_submission_students(self, main_zip_path: str) -> List[str]:
        """List student names represented by top-level submission entries in the zip."""
        students = []
        processed_students = set()

        zip_path_obj = Path(main_zip_path)
        extract_dir = zip_path_obj.parent / zip_path_obj.stem
        # Always remove and re-extract to ensure fresh directory contents
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        print(f"📦 Extracting main zip to: {extract_dir}")
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(main_zip_path, 'r') as main_zip:
            main_zip.extractall(str(extract_dir))

        entries = self._student_entries_from_root(extract_dir)
        print('DEBUG: student_entries_from_root:', [e.name for e in entries])
        for student_entry in entries:
            student_name = self.extract_student_name(student_entry.name)
            normalized_name = self.normalize_student_name(student_name)
            print('DEBUG: entry:', student_entry.name, '-> student_name:', student_name)
            if not student_name or normalized_name in processed_students:
                continue
            students.append(student_name)
            processed_students.add(normalized_name)

        return students
    
    def extract_student_name(self, filename_or_path: str) -> str:
        """Extract student name from file/folder name"""
        # Remove path and extension
        name = Path(filename_or_path).stem
        
        # Common patterns for student names
        # Remove common suffixes
        suffixes_to_remove = [
            '-OuterJoins', '-OuterJoin', '-OUTERJOIN', '-OutterJoins', 
            '_assignsubmission_file', 'assignsubmission_file'
        ]
        
        for suffix in suffixes_to_remove:
            if suffix in name:
                name = name.replace(suffix, '')
        
        # Handle "LastName FirstName" format
        if '_' in name:
            parts = name.split('_')
            if len(parts) >= 2:
                # Assume format like "LastName FirstName_number"
                return f"{parts[1]} {parts[0]}" if len(parts[0]) > 2 else name
        
        # Handle "FirstNameLastName" format (try to separate)
        if len(name) > 5 and name.isalpha():
            # Look for capital letters that might indicate name boundaries
            capitals = [i for i, c in enumerate(name) if c.isupper()]
            if len(capitals) > 1:
                # Split at the last capital letter that's not at the end
                for cap_idx in reversed(capitals[1:]):
                    if cap_idx < len(name) - 2:
                        first_name = name[:cap_idx]
                        last_name = name[cap_idx:]
                        return f"{first_name} {last_name}"
        
        return name.replace('_', ' ').replace('-', ' ').strip()
    
    def normalize_student_name(self, student_name: str) -> str:
        """Normalize student name for duplicate detection (lowercase, remove spaces and special chars, but do not sort)"""
        normalized = student_name.lower()
        normalized = re.sub(r'[^a-z0-9]', '', normalized)
        return normalized
    
    def extract_csharp_from_nested_structure(self, zip_path: str) -> str:
        """Extract C# content from potentially nested zip structure"""
        temp_dir = tempfile.mkdtemp()
        
        try:
            # Extract the zip file
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            
            return self.find_csharp_in_directory(temp_dir)
        
        except Exception as e:
            print(f"Error extracting from {zip_path}: {e}")
            return ""
        
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    
    def extract_csharp_from_7z_structure(self, archive_path: str) -> str:
        """Extract C# content from potentially nested 7z structure"""
        temp_dir = tempfile.mkdtemp()
        
        try:
            # Try to extract 7z file
            try:
                import py7zr
                with py7zr.SevenZipFile(archive_path, mode='r') as archive:
                    archive.extractall(path=temp_dir)
                return self.find_csharp_in_directory(temp_dir)
            except ImportError:
                print(f"Warning: py7zr library not installed. Cannot extract 7z file: {archive_path}")
                print("Install with: pip install py7zr")
                return ""
            except Exception as e:
                print(f"Error extracting 7z file {archive_path}: {e}")
                return ""
        
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _is_ignored_directory(self, dir_name: str) -> bool:
        """Return True when a directory is almost certainly build/tooling noise."""
        ignored_dirs = {
            'bin', 'obj', '.vs', '.git', '.github', '.idea', '.vscode',
            'node_modules', 'packages', '__pycache__', '.pytest_cache',
            '.mypy_cache', '.cache', 'debug', 'release'
        }
        return dir_name.lower() in ignored_dirs

    def _is_unimportant_file(self, file_name: str) -> bool:
        """Return True for files that should not be considered for grading context."""
        lowered = file_name.lower()
        if lowered.endswith(('.dll', '.exe', '.pdb', '.cache', '.class', '.o', '.so', '.dylib')):
            return True
        if lowered.endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.mp3', '.mp4', '.mov', '.avi')):
            return True
        if lowered.endswith(('.zip', '.7z', '.rar', '.tar', '.gz')):
            return True
        if lowered.endswith(('.g.cs', '.generated.cs', '.assemblyinfo.cs')):
            return True
        return False

    def _append_submission_artifact_notes(self, content: str, directory: str) -> str:
        """Append detected non-code deliverables to the submission content."""
        notes = [
            note for note in [
                self._submission_tree_note(directory),
                self._submission_inventory_note(directory),
                self._collect_supporting_documents(directory),
            ]
            if note
        ]
        if not notes:
            return content
        return f"{content}\n\n" + "\n\n".join(notes)

    def _extract_csproj_compile_includes(self, csproj_files: List[str], root_dir: str) -> set:
        """Extract explicit Compile Include paths from .csproj files for relevance boost."""
        includes = set()
        include_pattern = re.compile(r'<Compile\s+Include="([^"]+)"', re.IGNORECASE)

        for csproj in csproj_files:
            try:
                with open(csproj, 'r', encoding='utf-8', errors='ignore') as f:
                    text = f.read()
                for relative in include_pattern.findall(text):
                    normalized = os.path.normpath(relative.replace('\\', os.sep).replace('/', os.sep))
                    abs_path = os.path.normpath(os.path.join(os.path.dirname(csproj), normalized))
                    try:
                        rel_path = os.path.relpath(abs_path, root_dir)
                        includes.add(rel_path)
                    except Exception:
                        continue
            except Exception:
                continue

        return includes

    def _score_submission_file(
        self,
        rel_path: str,
        compile_includes: set,
        assignment_keywords: List[str],
    ) -> Tuple[int, List[str]]:
        """Assign a relevance score and traceable reasons for the file."""
        score = 0
        reasons = []
        normalized = rel_path.replace('\\', '/')
        file_name = os.path.basename(normalized).lower()
        ext = Path(file_name).suffix.lower()

        if ext == '.cs':
            score += 50
            reasons.append('+50 source file (.cs)')
            if file_name.endswith('.designer.cs'):
                score += 15
                reasons.append('+15 form designer definition file')
        elif ext in {'.csproj', '.sln'}:
            score += 35
            reasons.append('+35 project structure file')
        elif ext in {'.config', '.json', '.xml'}:
            score += 10
            reasons.append('+10 config file (possible runtime behavior impact)')
        elif ext in {'.md', '.txt'}:
            score += 5
            reasons.append('+5 documentation/explanation file')

        if rel_path in compile_includes:
            score += 30
            reasons.append('+30 explicitly included in .csproj')

        key_pattern = re.compile(r'(program|main|assignment|lab|part|task|solution|exercise)', re.IGNORECASE)
        if key_pattern.search(file_name):
            score += 20
            reasons.append('+20 assignment-like filename')

        lowered_path = normalized.lower()
        keyword_hits = sum(1 for kw in assignment_keywords if kw in lowered_path)
        if keyword_hits:
            keyword_bonus = min(15, keyword_hits * 5)
            score += keyword_bonus
            reasons.append(f'+{keyword_bonus} matches assignment keywords')

        return score, reasons

    def _collect_referenced_file_names(self, selected_files: List[str]) -> set:
        """Collect likely class/type references to pull in helper files via closure."""
        refs = set()
        token_pattern = re.compile(r'\b([A-Z][A-Za-z0-9_]*)\b')
        for file_path in selected_files:
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    text = f.read()
                for token in token_pattern.findall(text):
                    refs.add(token.lower())
            except Exception:
                continue
        return refs

    def _triage_submission_files(self, directory: str, all_files: List[str]) -> Tuple[List[str], List[str]]:
        """Choose important files for grading while avoiding generated noise."""
        if not all_files:
            return [], []

        csproj_files = [p for p in all_files if p.lower().endswith('.csproj')]
        compile_includes = self._extract_csproj_compile_includes(csproj_files, directory)

        assignment_keywords = []
        for part_key in sorted(self.part_weights.keys()):
            assignment_keywords.append(part_key.lower())
        assignment_keywords.extend(['part1', 'part2', 'part3', 'method', 'advanced', 'arguments'])

        scored = []
        ignored = []

        for abs_path in all_files:
            rel_path = os.path.relpath(abs_path, directory)
            base = os.path.basename(abs_path)
            if self._is_unimportant_file(base):
                ignored.append(f"{rel_path} (ignored generated/binary/archive file)")
                continue

            score, reasons = self._score_submission_file(rel_path, compile_includes, assignment_keywords)
            scored.append((score, abs_path, rel_path, reasons))

        # Core include threshold and source-only enforcement for grading content.
        selected_csharp = [item for item in scored if item[0] >= 45 and item[2].lower().endswith('.cs')]

        # Safety fallback: if threshold missed too much, include all non-generated C# files.
        if len(selected_csharp) < 2:
            selected_csharp = [item for item in scored if item[2].lower().endswith('.cs')]

        selected_files = sorted({item[1] for item in selected_csharp})

        # If a form code-behind file is selected, include its paired designer file.
        selected_lookup = {os.path.basename(path).lower(): path for path in selected_files}
        all_lookup = {os.path.basename(path).lower(): path for path in all_files}
        for name in list(selected_lookup.keys()):
            if not name.endswith('.cs') or name.endswith('.designer.cs'):
                continue
            designer_name = f"{Path(name).stem}.designer.cs"
            designer_path = all_lookup.get(designer_name)
            if designer_path:
                selected_files.append(designer_path)

        # Dependency closure: if selected files reference class names matching other C# filenames, include them.
        referenced_tokens = self._collect_referenced_file_names(selected_files)
        for score, abs_path, rel_path, _reasons in scored:
            if abs_path in selected_files or not rel_path.lower().endswith('.cs'):
                continue
            stem = Path(rel_path).stem.lower()
            if stem in referenced_tokens:
                selected_files.append(abs_path)

        selected_files = sorted(set(selected_files))

        # Emit compact triage log for transparency.
        print(f"  🔎 File triage in {os.path.basename(directory)}:")
        print(f"     - Total files scanned: {len(all_files)}")
        print(f"     - Selected C# files: {len(selected_files)}")
        if ignored:
            print(f"     - Ignored noisy files: {len(ignored)}")

        for score, _abs_path, rel_path, reasons in sorted(scored, key=lambda x: (-x[0], x[2]))[:8]:
            if rel_path.lower().endswith('.cs'):
                reason_text = '; '.join(reasons) if reasons else 'no boosts'
                print(f"     ✓ {rel_path} (score={score}; {reason_text})")

        if ignored:
            for item in ignored[:5]:
                print(f"     ✗ {item}")

        # Save triage decisions for auditability/debugging.
        try:
            selected_rel_paths = sorted(os.path.relpath(path, directory) for path in selected_files)
            scored_lines = []
            for score, _abs_path, rel_path, reasons in sorted(scored, key=lambda x: (-x[0], x[2])):
                reason_text = '; '.join(reasons) if reasons else 'no boosts'
                scored_lines.append(f"{rel_path} | score={score} | {reason_text}")

            manifest_path = os.path.join(directory, '_autograde_file_triage.txt')
            with open(manifest_path, 'w', encoding='utf-8') as manifest:
                manifest.write("AutoGrade File Triage\n")
                manifest.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                manifest.write(f"Scanned files: {len(all_files)}\n")
                manifest.write(f"Selected C# files: {len(selected_rel_paths)}\n\n")
                manifest.write("Selected files:\n")
                for rel in selected_rel_paths:
                    manifest.write(f"  - {rel}\n")
                manifest.write("\nScored files:\n")
                for line in scored_lines:
                    manifest.write(f"  - {line}\n")
                if ignored:
                    manifest.write("\nIgnored files:\n")
                    for item in sorted(ignored):
                        manifest.write(f"  - {item}\n")
        except Exception as manifest_error:
            print(f"  ⚠️  Could not write triage manifest: {manifest_error}")

        return selected_files, ignored
    
    def find_csharp_in_directory(self, directory: str) -> str:
        """Recursively find and extract C# source content from a directory"""
        csharp_files = []
        all_files = []

        for root, dirs, files in os.walk(directory):
            # Skip generated/build folders to reduce noise.
            dirs[:] = [d for d in dirs if not self._is_ignored_directory(d)]

            # Collect all files for relevance triage.
            for file in files:
                file_path = os.path.join(root, file)
                all_files.append(file_path)

            # Also collect C# files directly.
            for file in files:
                file_lower = file.lower()
                if file_lower.endswith('.cs'):
                    file_path = os.path.join(root, file)
                    csharp_files.append(file_path)

            # If no C# files found yet, look for nested archives.
            if not csharp_files:
                for file in files:
                    if file.lower().endswith('.zip'):
                        zip_path = os.path.join(root, file)
                        csharp_content = self.extract_csharp_from_nested_structure(zip_path)
                        if csharp_content:
                            return csharp_content
                    elif file.lower().endswith('.7z'):
                        archive_path = os.path.join(root, file)
                        csharp_content = self.extract_csharp_from_7z_structure(archive_path)
                        if csharp_content:
                            return csharp_content

        if not csharp_files:
            return ""

        selected_files, _ignored = self._triage_submission_files(directory, all_files)
        if not selected_files:
            selected_files = sorted(csharp_files)

        combined = []
        for file_path in selected_files:
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read().strip()
                    if content:
                        relative_path = os.path.relpath(file_path, directory)
                        combined.append(f"// File: {relative_path}\n{content}")
            except Exception as e:
                print(f"Error reading {file_path}: {e}")
        
        return self._append_submission_artifact_notes("\n\n".join(combined), directory)

    def extract_csharp_parts(self, csharp_content: str) -> Dict[str, str]:
        """Extract different parts of the C# assignment dynamically"""
        parts = {}
        expected_parts = sorted(self.part_weights.keys()) if self.part_weights else ['part1', 'part2', 'part3']
        
        # Clean the content and normalize line endings.
        content = csharp_content.replace('\r\n', '\n').replace('\r', '\n').strip()
        if not content:
            return {part_key: '' for part_key in expected_parts}

        # Prefer explicit part/task markers in comments.
        buckets = {part_key: [] for part_key in expected_parts}
        current_part = None
        marker = re.compile(r'^\s*(?://+|/\*+|\*+)?\s*(part|task)\s*(\d+)\b', re.IGNORECASE)

        lines = content.split('\n')
        for line in lines:
            match = marker.match(line)
            if match:
                part_num = match.group(2)
                matched_key = f'part{part_num}'
                current_part = matched_key if matched_key in buckets else None
            if current_part:
                buckets[current_part].append(line)

        for key in expected_parts:
            section_text = '\n'.join(buckets[key]).strip()
            if section_text:
                parts[key] = section_text

        # Fallback: if part markers are absent or incomplete, give full code to missing parts.
        for key in expected_parts:
            if key not in parts:
                parts[key] = content
        
        return parts
    
    def detect_assignment_structure(self, instructions: str) -> Dict[str, int]:
        """Detect the structure of the assignment and assign weights"""
        weights = {}

        assignment_parts = self.extract_assignment_parts(instructions)
        required_parts = [p for p in assignment_parts if not p['is_optional']]
        if required_parts:
            num_parts = len(required_parts)
            equal_weight = 100 // num_parts
            remainder = 100 % num_parts
            for idx, part in enumerate(required_parts):
                weight = equal_weight + (1 if idx < remainder else 0)
                weights[part['key']] = weight
            return weights
        
        # Look for parts/questions/exercises in the instructions
        part_indicators = re.findall(
            r'(Part|Question|Exercise|Problem)\s*(\d+)', 
            instructions, 
            re.IGNORECASE
        )
        
        if part_indicators:
            num_parts = len(set(match[1] for match in part_indicators))
            equal_weight = 100 // num_parts
            remainder = 100 % num_parts
            
            for i in range(1, num_parts + 1):
                weight = equal_weight + (1 if i <= remainder else 0)
                weights[f'part{i}'] = weight
        else:
            # Default to single part
            weights['part1'] = 100
        
        return weights
    
    def create_grading_prompt(self, instructions: str, csharp_parts: Dict[str, str], assignment_name: str = "C# Lab") -> str:
        """Create the grading prompt for OpenAI"""
        parts_text = ""
        for part_name in sorted(csharp_parts.keys(), key=lambda k: int(re.search(r'\d+', k).group()) if re.search(r'\d+', k) else 999):
            content = csharp_parts[part_name]
            part_num = part_name.replace('part', '')
            parts_text += f"\nPart {part_num} Submission:\n{content}\n"

        assignment_parts = self.extract_assignment_parts(instructions)
        assignment_part_map = {p['key']: p for p in assignment_parts}
        graded_part_keys = sorted(self.part_weights.keys(), key=lambda k: int(re.search(r'\d+', k).group()) if re.search(r'\d+', k) else 999)

        if not graded_part_keys:
            graded_part_keys = sorted(csharp_parts.keys(), key=lambda k: int(re.search(r'\d+', k).group()) if re.search(r'\d+', k) else 999)

        rubric_blocks = []
        json_part_blocks = []

        for part_key in graded_part_keys:
            part_num = part_key.replace('part', '')
            title = assignment_part_map.get(part_key, {}).get('title', f'Part {part_num}')
            requirements = assignment_part_map.get(part_key, {}).get('requirements', 'Use the assignment instructions for this part.')

            rubric_blocks.append(
                f"PART {part_num} - {title}\n"
                f"Required behavior:\n{requirements}\n"
                "Deduction guidance:\n"
                "  - Major missing core requirement: -10 to -20\n"
                "  - Partially implemented requirement: -5 to -10\n"
                "  - Minor correctness/output issue: -2 to -5\n"
                "  - Style-only differences with correct behavior: 0 deduction"
            )

            json_part_blocks.append(
                f'''"{part_key}": {{
        "original_code": "exact copy of submitted content for {part_key} (code + comments)",
        "corrected_code": "corrected version if needed, or same as original if correct",
        "issues": ["only actual problems found for this part"],
        "point_deductions": ["'-N points: reason' (must sum to exactly 100 - score)"],
        "suggestions": ["constructive suggestions without using 'student' or 'you'"],
        "score": <integer>
    }}'''
            )

        optional_parts = [p for p in assignment_parts if p['is_optional']]
        optional_text = ""
        if optional_parts:
            optional_lines = [f"PART {p['part_num']} - {p['title']}" for p in optional_parts]
            optional_text = (
                "\nOPTIONAL/STRETCH PARTS:\n"
                + "\n".join(f"  - {line}: acknowledge positively if implemented, no deduction if omitted." for line in optional_lines)
            )

        response_json_parts = ",\n    ".join(json_part_blocks)
        graded_keys_list = ", ".join(graded_part_keys)
        grading_context_text = (self.grading_context or "").strip()
        grading_context_block = ""
        if grading_context_text:
            grading_context_block = (
                "\nINSTRUCTOR-SUPPLIED GRADING CONTEXT (HIGHEST PRIORITY):\n"
                f"{grading_context_text}\n"
                "Interpret and apply this context when deciding what to grade and what not to penalize.\n"
            )
        
        prompt = f"""
You are an expert C# instructor grading a "{assignment_name}" lab assignment.
Use the assignment instructions below as the source of truth.
{grading_context_block}

ASSIGNMENT INSTRUCTIONS:
{instructions}

STUDENT'S SUBMISSION:
{parts_text}

════════════════════════════════════════════════════════
ASSIGNMENT STRUCTURE — graded parts:
════════════════════════════════════════════════════════
{chr(10).join(rubric_blocks)}
{optional_text}

════════════════════════════════════════════════════════
CRITICAL GRADING RULES
════════════════════════════════════════════════════════
1. C# comments are part of the submission and may contain required explanations.
2. Grade behavior and correctness, not exact wording or formatting.
3. Accept equivalent valid C# approaches (console/forms structure differences are fine).
4. Be fair and slightly generous when intent and behavior are clearly correct.
5. Grade ONLY these required parts: {graded_keys_list}
6. Do not deduct for optional/stretch parts that are not implemented.
7. The submission may include non-code deliverables inside a submission inventory block, such as presentations, videos, screenshots, reports, or supporting documents.
8. Before deducting for a missing presentation/video/report/screenshot deliverable, check whether it is listed in the submission inventory and accept equivalent common file formats when the instructions do not require one exact format.

════════════════════════════════════════════════════════
RESPONSE FORMAT — return ONLY valid JSON, no extra text:
════════════════════════════════════════════════════════
{{
    {response_json_parts},
    "overall_feedback": "summary of overall performance — no 'student' or 'you'",
    "final_score": <average of graded parts only, rounded to one decimal>,
    "brief_summary": "2-3 sentences on main strengths and issues — no 'student' or 'you'"
}}

MATH REQUIREMENT: each part's score = 100 − (sum of that part's deduction amounts).
If no issues in a part, deductions = [] and score = 100.
Never place praise, confirmation, or correct behavior inside "issues". If something was done correctly, omit it from "issues".
"""
        return prompt
    
    def grade_with_ai(self, prompt: str) -> Dict:
        """Send grading request to AI service and parse response"""
        if self.use_custom_endpoint:
            return self.grade_with_custom_endpoint(prompt)
        else:
            return self.grade_with_openai(prompt)
    
    def grade_with_openai(self, prompt: str) -> Dict:
        """Send grading request to OpenAI and parse response"""
        try:
            print(f"📡 Sending request to OpenAI model: {self.model_name}...")
            reasoning_effort = self._resolve_reasoning_effort()
            print(f"🧠 Using reasoning effort: {reasoning_effort}")
            
            url = "https://api.openai.com/v1/responses"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-type": "application/json"
            }
            
            # Format the input according to the new API
            full_input = f"""You are an expert C# instructor with years of experience grading programming assignments. Provide detailed, constructive feedback.

{prompt}"""
            
            data = {
                "model": self.model_name,
                "input": full_input,
                "reasoning": {
                    "effort": reasoning_effort
                }
            }

            response = None
            response_data = None
            last_exception = None
            max_attempts = max(1, self.openai_max_retries)

            for attempt in range(1, max_attempts + 1):
                try:
                    attempt_started = time.time()
                    attempt_started_human = datetime.now().strftime('%H:%M:%S')
                    print(
                        f"🔁 OpenAI request attempt {attempt}/{max_attempts} "
                        f"(timeout={self.openai_timeout}s, started={attempt_started_human})"
                    )
                    print("⏳ Waiting for OpenAI response...")

                    request_payload = dict(data)
                    if self.openai_stream:
                        request_payload["stream"] = True

                    response = requests.post(
                        url,
                        headers=headers,
                        json=request_payload,
                        timeout=self.openai_timeout,
                        stream=self.openai_stream,
                    )
                    elapsed = time.time() - attempt_started
                    print(f"📬 OpenAI responded with HTTP {response.status_code} after {elapsed:.1f}s")

                    if response.status_code == 200 and self.openai_stream:
                        print("🔄 Receiving OpenAI streaming response...")
                        streamed_text_chunks = []
                        streamed_response = None

                        for raw_line in response.iter_lines(decode_unicode=True):
                            if not raw_line:
                                continue

                            line = raw_line.strip()
                            if not line.startswith('data:'):
                                continue

                            data_part = line[5:].strip()
                            if data_part == '[DONE]':
                                print("\n✅ OpenAI stream completed")
                                break

                            try:
                                event = json.loads(data_part)
                            except json.JSONDecodeError:
                                continue

                            event_type = event.get('type', '') if isinstance(event, dict) else ''
                            chunk = ""

                            if event_type == 'response.output_text.delta':
                                chunk = str(event.get('delta', ''))
                            elif isinstance(event.get('delta'), dict):
                                chunk = str(event['delta'].get('text', ''))
                            elif isinstance(event.get('output_text'), str):
                                chunk = event['output_text']

                            if chunk:
                                streamed_text_chunks.append(chunk)
                                print(chunk, end='', flush=True)

                            if event_type == 'response.completed' and isinstance(event.get('response'), dict):
                                streamed_response = event['response']

                        if streamed_text_chunks:
                            print()

                        response_data = streamed_response or {}
                        if streamed_text_chunks:
                            response_data['_streamed_text'] = ''.join(streamed_text_chunks)

                    # Retry on transient statuses.
                    if response.status_code in {429, 500, 502, 503, 504} and attempt < max_attempts:
                        retry_after = response.headers.get('Retry-After')
                        if retry_after and retry_after.isdigit():
                            sleep_seconds = max(1.0, float(retry_after))
                        else:
                            sleep_seconds = self.openai_retry_backoff * (2 ** (attempt - 1))
                        print(f"⚠️  Transient API status {response.status_code}. Retrying in {sleep_seconds:.1f}s...")
                        time.sleep(sleep_seconds)
                        continue

                    # Some models (for example gpt-5.4-pro) reject low effort.
                    if response.status_code == 400 and attempt < max_attempts:
                        resp_text = response.text or ""
                        if "Unsupported value" in resp_text and "reasoning" in resp_text and "effort" in resp_text:
                            current_effort = data.get("reasoning", {}).get("effort", "")
                            if current_effort == "low":
                                data["reasoning"]["effort"] = "medium"
                                print("⚠️  Model rejected effort=low. Retrying with effort=medium...")
                                continue

                    break

                except (requests.exceptions.ReadTimeout, requests.exceptions.Timeout) as timeout_err:
                    last_exception = timeout_err
                    elapsed = time.time() - attempt_started if 'attempt_started' in locals() else 0.0
                    print(f"❌ OpenAI request timed out after {elapsed:.1f}s")
                    if attempt < max_attempts:
                        sleep_seconds = self.openai_retry_backoff * (2 ** (attempt - 1))
                        print(f"⏱️  Request timed out. Retrying in {sleep_seconds:.1f}s...")
                        time.sleep(sleep_seconds)
                        continue
                    break
                except requests.exceptions.ConnectionError as conn_err:
                    last_exception = conn_err
                    elapsed = time.time() - attempt_started if 'attempt_started' in locals() else 0.0
                    print(f"❌ OpenAI connection failed after {elapsed:.1f}s")
                    if attempt < max_attempts:
                        sleep_seconds = self.openai_retry_backoff * (2 ** (attempt - 1))
                        print(f"🌐 Connection issue. Retrying in {sleep_seconds:.1f}s...")
                        time.sleep(sleep_seconds)
                        continue
                    break

            if response is None:
                return {
                    "error": (
                        f"GPT-5 API error after {max_attempts} attempts: {last_exception}. "
                        f"Consider increasing OPENAI_TIMEOUT_SECONDS in .env."
                    ),
                    "final_score": 0
                }
            
            if response.status_code == 200:
                if not self.openai_stream:
                    response_data = response.json()
                elif response_data is None:
                    response_data = {}
                
                # The new GPT-5 API returns response in different fields
                # Try to extract the text response based on the debug output
                response_text = ""

                if self.openai_stream and isinstance(response_data, dict):
                    response_text = str(response_data.get('_streamed_text', '')).strip()
                
                # GPT-5 API structure: output is an array with message objects
                # The actual content is in output[1].content[0].text
                try:
                    if (not response_text and
                        'output' in response_data and 
                        isinstance(response_data['output'], list) and 
                        len(response_data['output']) > 1):
                        
                        # Get the second item (index 1) which contains the message
                        message_obj = response_data['output'][1]
                        if ('content' in message_obj and 
                            isinstance(message_obj['content'], list) and 
                            len(message_obj['content']) > 0):
                            
                            # Get the first content item which contains the text
                            content_obj = message_obj['content'][0]
                            if 'text' in content_obj:
                                response_text = content_obj['text']
                                
                except (IndexError, KeyError, TypeError) as e:
                    print(f"⚠️  Error extracting GPT-5 response structure: {e}")
                    # Fallback to string representation
                    response_text = str(response_data.get('output', ''))
                
                print(f"📥 Received response from GPT-5 ({len(response_text)} characters)")
                print(f"🔍 Debug - Response content: {response_text[:200]}...")  # Show first 200 chars
                print(f"🔍 Debug - Output field: {response_data.get('output', 'Not found')}")
                print(f"🔍 Debug - Text field: {response_data.get('text', 'Not found')}")
                
                # Try to extract JSON from the response
                if response_text and len(response_text) > 100:  # Only try if response is substantial
                    json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                    if json_match:
                        try:
                            json_text = json_match.group()
                            print(f"🔍 Debug - Attempting to parse JSON ({len(json_text)} chars)")
                            
                            # Try to parse the JSON
                            parsed_json = json.loads(json_text)
                            parsed_json = self.normalize_grading_result(parsed_json)
                            print("✅ Successfully parsed JSON response")
                            return parsed_json
                            
                        except json.JSONDecodeError as json_err:
                            print(f"❌ JSON parsing failed: {json_err}")
                            print(f"🔍 Error position: line {json_err.lineno}, column {json_err.colno}")
                            
                            # Show the problematic area
                            lines = json_text.split('\n')
                            if json_err.lineno <= len(lines):
                                problem_line = lines[json_err.lineno - 1] if json_err.lineno > 0 else ""
                                print(f"🔍 Problem line {json_err.lineno}: {problem_line}")
                                if json_err.colno > 0:
                                    pointer = " " * (json_err.colno - 1) + "^"
                                    print(f"🔍 Position marker: {pointer}")
                            
                            # Try to clean and fix common JSON issues
                            try:
                                # Fix common issues: unescaped quotes, trailing commas, etc.
                                cleaned_json = json_text.replace('\\"', '"').replace("\\n", "\\\\n")
                                # Remove trailing commas before } or ]
                                cleaned_json = re.sub(r',(\s*[}\]])', r'\1', cleaned_json)
                                
                                parsed_json = json.loads(cleaned_json)
                                parsed_json = self.normalize_grading_result(parsed_json)
                                print("✅ Successfully parsed JSON after cleaning")
                                return parsed_json
                                
                            except json.JSONDecodeError:
                                print("❌ JSON cleaning failed, returning error")
                                return {
                                    "error": f"JSON parsing error: {json_err}",
                                    "raw_response": response_text[:1000],
                                    "json_error_line": json_err.lineno,
                                    "json_error_column": json_err.colno
                                }
                    else:
                        # If no JSON found, create a basic structure
                        return {
                            "error": "Could not find JSON structure in GPT-5 response",
                            "raw_response": response_text[:500]  # First 500 chars for debugging
                        }
                else:
                    return {
                        "error": f"GPT-5 response too short or empty (length: {len(response_text)})",
                        "raw_response": response_text,
                        "full_response_data": {k: v for k, v in response_data.items() if k in ['output', 'text', 'reasoning', 'error']}
                    }
            else:
                return {
                    "error": (
                        f"GPT-5 API error after retries: {response.status_code} - {response.text[:500]}"
                    ),
                    "final_score": 0
                }
                
        except Exception as e:
            return {
                "error": f"GPT-5 API error: {str(e)}",
                "final_score": 0
            }
    
    def grade_with_custom_endpoint(self, prompt: str) -> Dict:
        """Send grading request to custom endpoint with streaming response"""
        try:
            import requests
            
            # Prepare the request payload for Ollama-style API
            payload = {
                "model": self.model_name,
                "messages": [
                    {"role": "system", "content": "You are an expert C# instructor. Return only valid JSON in the exact format requested."},
                    {"role": "user", "content": prompt}
                ],
                "stream": True  # Enable streaming
            }
            
            print(f"📡 Sending request to custom endpoint with model: {self.model_name}...")
            
            # Send request to custom endpoint with streaming
            response = requests.post(
                f"{self.custom_endpoint}/v1/chat/completions",
                json=payload,
                timeout=300,  # 5 minute timeout for grading
                stream=True
            )
            
            if response.status_code == 200:
                print("🔄 Receiving streaming response...")
                full_response = ""
                
                # Process streaming response
                for line in response.iter_lines():
                    if line:
                        line_text = line.decode('utf-8')
                        if line_text.startswith('data: '):
                            data_part = line_text[6:]  # Remove 'data: ' prefix
                            if data_part.strip() == '[DONE]':
                                print("\n✅ Stream completed")
                                break
                            
                            try:
                                chunk_data = json.loads(data_part)
                                if 'choices' in chunk_data and chunk_data['choices']:
                                    delta = chunk_data['choices'][0].get('delta', {})
                                    if 'content' in delta:
                                        content_chunk = delta['content']
                                        full_response += content_chunk
                                        # Show progress dots or characters
                                        print(content_chunk, end='', flush=True)
                            except json.JSONDecodeError:
                                continue  # Skip malformed JSON chunks
                
                print(f"\n📥 Full response received ({len(full_response)} characters)")
                print("🔍 Parsing AI response...")
                
                # Parse the complete response
                parsed_result = self.parse_ai_response(full_response)
                
                # Debug: Show what was parsed
                if 'error' in parsed_result:
                    print(f"❌ Parsing error: {parsed_result['error']}")
                    # Print first 200 chars of response for debugging
                    print(f"Response preview: {full_response[:200]}...")
                else:
                    print(f"✅ Successfully parsed response with final score: {parsed_result.get('final_score', 'N/A')}")
                
                return parsed_result
                
            else:
                return {
                    "error": f"Custom API error: {response.status_code} - {response.text}",
                    "final_score": 0
                }
                
        except ImportError:
            return {
                "error": "requests library is required for custom endpoint. Install with: pip install requests",
                "final_score": 0
            }
        except Exception as e:
            return {
                "error": f"Custom API error: {str(e)}",
                "final_score": 0
            }
    
    def parse_ai_response(self, response_text: str) -> Dict:
        """Parse AI response and extract JSON structure"""
        try:
            # First, try to find JSON block in the response
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                json_text = json_match.group()
                parsed = json.loads(json_text)
                
                # Validate that we have the expected structure
                if not isinstance(parsed, dict):
                    return {
                        "error": "Response is not a JSON object",
                        "final_score": 0
                    }
                
                # Check if we have at least one part with a score
                has_valid_part = False
                for key, value in parsed.items():
                    if key.startswith('part') and isinstance(value, dict) and 'score' in value:
                        has_valid_part = True
                        break
                
                if not has_valid_part and 'error' not in parsed:
                    return {
                        "error": "No valid parts with scores found in response",
                        "final_score": 0,
                        "raw_response": response_text[:500]  # First 500 chars for debugging
                    }
                
                # If final_score is missing, calculate it
                if 'final_score' not in parsed:
                    part_scores = {}
                    for key, value in parsed.items():
                        if key.startswith('part') and isinstance(value, dict) and 'score' in value:
                            part_scores[key] = value['score']
                    
                    if part_scores:
                        parsed['final_score'] = sum(part_scores.values()) / len(part_scores)

                return self.normalize_grading_result(parsed)
            else:
                # No JSON found, return error
                return {
                    "error": "No JSON structure found in response",
                    "final_score": 0,
                    "raw_response": response_text[:500]  # First 500 chars for debugging
                }
                
        except json.JSONDecodeError as e:
            return {
                "error": f"JSON parsing error: {str(e)}",
                "final_score": 0,
                "raw_response": response_text[:500]  # First 500 chars for debugging
            }
        except Exception as e:
            return {
                "error": f"Unexpected parsing error: {str(e)}",
                "final_score": 0,
                "raw_response": response_text[:500]  # First 500 chars for debugging
            }

    def normalize_grading_result(self, grading_result: Dict) -> Dict:
        """Normalize AI output so positive comments do not appear as issues."""
        if not isinstance(grading_result, dict):
            return grading_result

        positive_patterns = [
            r'\bcorrect(?:ly)?\b',
            r'\bperformed correctly\b',
            r'\bimplemented correctly\b',
            r'\bproperly\b',
            r'\bsuccessfully\b',
            r'\bworks? as expected\b',
            r'\bwell done\b',
            r'\baccurate(?:ly)?\b',
            r'\bcomplete(?:d)?\b',
            r'\bno issues\b',
        ]
        negative_patterns = [
            r'\bmissing\b',
            r'\bincorrect\b',
            r'\berror\b',
            r'\bwrong\b',
            r'\bfail(?:ed|s)?\b',
            r'\bproblem\b',
            r'\bissue\b',
            r'\bdid not\b',
            r"\bdoes not\b",
            r"\bwas not\b",
            r'\binstead of\b',
            r'\blacks?\b',
        ]

        def as_clean_list(value) -> List[str]:
            if not isinstance(value, list):
                return []
            cleaned = []
            for item in value:
                text = str(item).strip()
                if text:
                    cleaned.append(text)
            return cleaned

        def looks_positive_only(text: str) -> bool:
            lowered = text.lower()
            has_positive = any(re.search(pattern, lowered) for pattern in positive_patterns)
            has_negative = any(re.search(pattern, lowered) for pattern in negative_patterns)
            return has_positive and not has_negative

        for part_key, part_data in grading_result.items():
            if not (part_key.startswith('part') and isinstance(part_data, dict)):
                continue

            issues = as_clean_list(part_data.get('issues', []))
            suggestions = as_clean_list(part_data.get('suggestions', []))
            deductions = as_clean_list(part_data.get('point_deductions', []))
            strengths = as_clean_list(part_data.get('strengths', []))

            normalized_issues = []
            moved_to_strengths = []
            for issue in issues:
                if looks_positive_only(issue):
                    moved_to_strengths.append(issue)
                else:
                    normalized_issues.append(issue)

            seen_strengths = set()
            merged_strengths = []
            for strength in strengths + moved_to_strengths:
                if strength not in seen_strengths:
                    seen_strengths.add(strength)
                    merged_strengths.append(strength)

            part_data['issues'] = normalized_issues
            part_data['point_deductions'] = deductions
            part_data['suggestions'] = suggestions
            if merged_strengths:
                part_data['strengths'] = merged_strengths

            score = part_data.get('score')
            if score == 100 and not deductions:
                part_data['issues'] = []

        return grading_result
    
    def calculate_final_score(self, part_scores: Dict[str, int]) -> float:
        """Calculate weighted final score based on detected parts"""
        if not self.part_weights:
            # If no weights detected, assign equal weights
            num_parts = len(part_scores)
            if num_parts > 0:
                equal_weight = 100 // num_parts
                remainder = 100 % num_parts
                for i, part in enumerate(sorted(part_scores.keys()), 1):
                    weight = equal_weight + (1 if i <= remainder else 0)
                    self.part_weights[part] = weight
        
        total_weighted_score = 0
        total_weight = 0
        
        for part, score in part_scores.items():
            weight = self.part_weights.get(part, 0)
            total_weighted_score += score * weight
            total_weight += weight
        
        return total_weighted_score / total_weight if total_weight > 0 else 0
    
    def generate_grade_report(self, student_name: str, grading_result: Dict, assignment_name: str = "C# Assignment") -> str:
        """Generate a formatted grade report"""
        report = f"""
=== {assignment_name} Grade Report ===
Student: {student_name}
Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

PART SCORES:
"""
        
        # Dynamically show scores for all parts found
        for part_key in sorted(grading_result.keys()):
            if part_key.startswith('part') and isinstance(grading_result[part_key], dict):
                part_num = part_key.replace('part', '')
                score = grading_result[part_key]['score']
                report += f"Part {part_num}: {score}/100\n"
        
        final_score = grading_result.get('final_score', 0)
        report += f"\nFINAL SCORE: {final_score:.1f}/100\n"
        
        # Add letter grade
        if final_score >= 90:
            letter_grade = "A"
        elif final_score >= 80:
            letter_grade = "B"
        elif final_score >= 70:
            letter_grade = "C"
        elif final_score >= 60:
            letter_grade = "D"
        else:
            letter_grade = "F"
        
        report += f"LETTER GRADE: {letter_grade}\n"
        
        # Add brief summary
        if 'brief_summary' in grading_result:
            report += f"\nBRIEF SUMMARY:\n{grading_result['brief_summary']}\n"
        
        # Add detailed feedback
        report += "\nDETAILED FEEDBACK:\n"
        report += "=" * 50 + "\n"
        
        for part_key in sorted(grading_result.keys()):
            if part_key.startswith('part') and isinstance(grading_result[part_key], dict):
                part_num = part_key.replace('part', '')
                part_data = grading_result[part_key]
                report += f"\nPart {part_num}:\n"
                report += f"Score: {part_data['score']}/100\n"
                
                # Show original and corrected code side by side
                if 'original_code' in part_data:
                    report += f"\nOriginal Code:\n{'-' * 40}\n{part_data['original_code']}\n"
                    
                    if 'corrected_code' in part_data and part_data['corrected_code'] != part_data['original_code']:
                        report += f"\nCorrected Code:\n{'-' * 40}\n{part_data['corrected_code']}\n"
                    else:
                        report += f"\nCode Status: Correct as submitted\n"
                
                # Handle both old and new feedback formats
                if 'feedback' in part_data:
                    report += f"\nFeedback: {part_data['feedback']}\n"
                
                if part_data.get('strengths'):
                    report += "\nStrengths:\n"
                    for strength in part_data['strengths']:
                        report += f"  • {strength}\n"
                
                if part_data.get('issues'):
                    report += "\nIssues Identified:\n"
                    for issue in part_data['issues']:
                        report += f"  • {issue}\n"
                
                if part_data.get('point_deductions'):
                    report += "\nPoint Deductions:\n"
                    for deduction in part_data['point_deductions']:
                        report += f"  • {deduction}\n"
                
                if part_data.get('suggestions'):
                    report += "\nSuggestions for Improvement:\n"
                    for suggestion in part_data['suggestions']:
                        report += f"  • {suggestion}\n"
                
                report += "\n" + "=" * 60 + "\n"
        
        if 'overall_feedback' in grading_result:
            report += f"Overall Feedback:\n{grading_result['overall_feedback']}\n"
        
        return report

    def generate_grade_report_html(
        self,
        student_name: str,
        grading_result: Dict,
        assignment_name: str = "C# Assignment",
        instructions_html: str = "",
        report_images: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """Generate a formatted HTML grade report for easier reading"""
        final_score = grading_result.get('final_score', 0)

        if final_score >= 90:
            letter_grade = "A"
        elif final_score >= 80:
            letter_grade = "B"
        elif final_score >= 70:
            letter_grade = "C"
        elif final_score >= 60:
            letter_grade = "D"
        else:
            letter_grade = "F"

        def esc(value) -> str:
            return html.escape(str(value))

        ext_to_lang = {
            '.cs': 'csharp',
            '.py': 'python',
            '.js': 'javascript',
            '.ts': 'typescript',
            '.java': 'java',
            '.cpp': 'cpp',
            '.cxx': 'cpp',
            '.cc': 'cpp',
            '.c': 'c',
            '.sql': 'sql',
            '.html': 'html',
            '.htm': 'html',
            '.css': 'css',
            '.php': 'php',
            '.rb': 'ruby',
            '.go': 'go',
            '.rs': 'rust',
            '.swift': 'swift',
            '.kt': 'kotlin',
            '.m': 'objc',
        }

        def infer_submission_languages(code_text: str) -> List[str]:
            """Infer one or more languages. Returns ['plain'] when answer is non-code text."""
            text = str(code_text or "")
            if not text.strip():
                return ['plain']

            ext_hits = {}
            for file_line in re.findall(r'^\s*//\s*File:\s*([^\n]+)', text, flags=re.MULTILINE):
                ext = Path(file_line.strip()).suffix.lower()
                lang = ext_to_lang.get(ext)
                if lang:
                    ext_hits[lang] = ext_hits.get(lang, 0) + 1
            if ext_hits:
                ordered = sorted(ext_hits.items(), key=lambda kv: (-kv[1], kv[0]))
                return [lang for lang, _ in ordered]

            lowered = text.lower()
            language_patterns = [
                ('csharp', [r'\busing\s+system\b', r'\bnamespace\b', r'\bpublic\s+class\b', r'\bstring\[\]\s+args\b']),
                ('python', [r'\bdef\s+\w+\(', r'\bimport\s+\w+', r':\s*(#.*)?$', r'\bif\s+__name__\s*==\s*["\']__main__["\']']),
                ('javascript', [r'\bfunction\s+\w+\(', r'\bconst\s+\w+\s*=', r'\bconsole\.log\(', r'\blet\s+\w+\s*=']),
                ('java', [r'\bpublic\s+class\b', r'\bpublic\s+static\s+void\s+main\b', r'\bSystem\.out\.println\(']),
                ('sql', [r'\bselect\b', r'\bfrom\b', r'\bwhere\b', r'\bjoin\b']),
                ('html', [r'<html', r'<body', r'<div', r'</\w+>']),
                ('css', [r'\.[\w-]+\s*\{', r'#[\w-]+\s*\{', r'\bcolor\s*:', r'\bdisplay\s*:']),
            ]

            scored = []
            for lang, pats in language_patterns:
                score = sum(1 for p in pats if re.search(p, lowered, flags=re.IGNORECASE | re.MULTILINE))
                if score > 0:
                    scored.append((lang, score))

            if not scored:
                return ['plain']

            scored.sort(key=lambda kv: (-kv[1], kv[0]))
            # Keep only meaningfully matched languages.
            top_score = scored[0][1]
            selected = [lang for lang, score in scored if score >= max(2, top_score - 1)]
            return selected or ['plain']

        def infer_line_languages(code_text: str, fallback_language: str) -> List[str]:
            """Infer per-line language using // File: markers when available."""
            line_languages = []
            current_lang = fallback_language
            for line in str(code_text or "").splitlines():
                file_match = re.match(r'^\s*//\s*File:\s*([^\n]+)', line)
                if file_match:
                    ext = Path(file_match.group(1).strip()).suffix.lower()
                    current_lang = ext_to_lang.get(ext, fallback_language)
                line_languages.append(current_lang)
            return line_languages

        def language_display_name(language: str) -> str:
            names = {
                'plain': 'Plain Text',
                'csharp': 'C#',
                'python': 'Python',
                'javascript': 'JavaScript',
                'typescript': 'TypeScript',
                'java': 'Java',
                'cpp': 'C++',
                'c': 'C',
                'sql': 'SQL',
                'html': 'HTML',
                'css': 'CSS',
                'php': 'PHP',
                'ruby': 'Ruby',
                'go': 'Go',
                'rust': 'Rust',
                'swift': 'Swift',
                'kotlin': 'Kotlin',
                'objc': 'Objective-C',
            }
            return names.get(language, language.title())

        def extract_note_block(text: str, start_label: str, next_labels: List[str]) -> str:
            source = str(text or "")
            if not source.strip():
                return ""

            start_pattern = rf'(\[{re.escape(start_label)}:[\s\S]*?)'
            terminator_pattern = "|".join(rf'\n\n\[{re.escape(label)}:' for label in next_labels)
            if terminator_pattern:
                pattern = start_pattern + rf'(?={terminator_pattern}|$)'
            else:
                pattern = start_pattern + r'$'

            match = re.search(pattern, source)
            return match.group(1).strip() if match else ""

        def extract_submission_tree_block() -> str:
            preserved = str(grading_result.get('_submission_tree', '') or '').strip()
            if preserved:
                return preserved
            for part_key in sorted(grading_result.keys()):
                part_data = grading_result.get(part_key)
                if not (part_key.startswith('part') and isinstance(part_data, dict)):
                    continue
                original_code = str(part_data.get('original_code', '') or '')
                block = extract_note_block(original_code, 'SUBMISSION TREE', ['SUBMISSION INVENTORY'])
                if block:
                    return block
            return ""

        def extract_submission_inventory_block() -> str:
            preserved = str(grading_result.get('_submission_inventory', '') or '').strip()
            if preserved:
                return preserved
            for part_key in sorted(grading_result.keys()):
                part_data = grading_result.get(part_key)
                if not (part_key.startswith('part') and isinstance(part_data, dict)):
                    continue
                original_code = str(part_data.get('original_code', '') or '')
                block = extract_note_block(original_code, 'SUBMISSION INVENTORY', [])
                if block:
                    return block
            return ""

        part_instruction_map = self.extract_part_instruction_map(instructions_html)
        submission_tree = extract_submission_tree_block()
        submission_inventory = extract_submission_inventory_block()
        submission_tree_html = ""
        if submission_tree:
            submission_tree_html = f"""
        <section class=\"part submission-inventory\">
            <h3>Submission Tree</h3>
            <p class=\"inventory-intro\">This is the compact folder/file tree included in the grading prompt for this submission.</p>
            <pre class=\"inventory-text\">{esc(submission_tree)}</pre>
        </section>
        """
        submission_inventory_html = ""
        if submission_inventory:
            submission_inventory_html = f"""
        <section class=\"part submission-inventory\">
            <h3>Submission Inventory</h3>
            <p class=\"inventory-intro\">This is the non-code deliverable inventory included in the grading prompt for this submission.</p>
            <pre class=\"inventory-text\">{esc(submission_inventory)}</pre>
        </section>
        """

        image_gallery_html = ""
        report_images = report_images or []
        if report_images:
            valid_images = []
            for index, image in enumerate(report_images, start=1):
                title = esc(image.get('title', f'Image {index}'))
                src = esc(image.get('src', ''))
                if not src:
                    continue
                valid_images.append({
                    'index': index,
                    'title': title,
                    'src': src,
                })

            image_cards_html = ""
            image_modals_html = ""
            for position, image_data in enumerate(valid_images):
                index = int(image_data['index'])
                title = str(image_data['title'])
                src = str(image_data['src'])
                card_id = f"submission-image-card-{index}"
                modal_id = f"submission-image-modal-{index}"

                prev_image = valid_images[(position - 1) % len(valid_images)]
                next_image = valid_images[(position + 1) % len(valid_images)]
                prev_modal_id = f"submission-image-modal-{int(prev_image['index'])}"
                next_modal_id = f"submission-image-modal-{int(next_image['index'])}"

                image_cards_html += f"""
                <figure id=\"{card_id}\" class=\"submission-image-card\">
                    <a class=\"submission-image-link\" href=\"#{modal_id}\" title=\"Open larger image\">
                        <img src=\"{src}\" alt=\"{title}\" loading=\"lazy\" />
                    </a>
                    <figcaption>{title}</figcaption>
                </figure>
                """
                image_modals_html += f"""
                <aside id=\"{modal_id}\" class=\"submission-image-modal\" aria-label=\"Expanded submission image\">
                    <a href=\"#{card_id}\" class=\"submission-image-modal-backdrop\" aria-label=\"Close image\"></a>
                    <div class=\"submission-image-modal-content\">
                        <div class=\"submission-image-modal-header\">
                            <a href=\"#{card_id}\" class=\"submission-image-modal-close\" aria-label=\"Close image\">Close</a>
                        </div>
                        <div class=\"submission-image-stage\">
                            <a href=\"#{prev_modal_id}\" class=\"submission-image-nav submission-image-nav-left\" aria-label=\"Previous image\">&#10094;</a>
                            <img src=\"{src}\" alt=\"{title}\" loading=\"eager\" />
                            <a href=\"#{next_modal_id}\" class=\"submission-image-nav submission-image-nav-right\" aria-label=\"Next image\">&#10095;</a>
                        </div>
                        <p>{title}</p>
                    </div>
                </aside>
                """

            if image_cards_html:
                image_gallery_html = f"""
        <section class=\"part submission-images\">
            <h3>Submission Images</h3>
            <details class=\"submission-images-details\">
                <summary>Show Images ({len(valid_images)})</summary>
                <p class=\"inventory-intro\">Images found in the submitted files. Click any image to open a larger view.</p>
                <div class=\"submission-images-grid\">
                    {image_cards_html}
                </div>
            </details>
            {image_modals_html}
        </section>
        """

        def _ann_terms(text: str) -> List[str]:
            """Extract meaningful searchable tokens from a single annotation string."""
            _stopwords = {
                'missing', 'incorrect', 'no', 'not', 'with', 'for', 'the', 'and', 'from',
                'into', 'that', 'this', 'part', 'logic', 'behavior', 'output', 'comment',
                'comments', 'array', 'arrays', 'value', 'values', 'issue', 'deduction',
                'suggestion', 'fix', 'should', 'must', 'need', 'needs', 'point', 'points',
                'was', 'were', 'has', 'have', 'had', 'does', 'did', 'will', 'would',
                'could', 'which', 'when', 'where', 'what', 'how', 'also', 'just', 'code',
                'line', 'lines', 'file', 'required', 'expected', 'used', 'use', 'uses',
                'add', 'added', 'provide', 'provided', 'include', 'included', 'implement',
                'implemented', 'print', 'display', 'show', 'returns', 'make', 'each',
                'been', 'true', 'false', 'none', 'null', 'int', 'str', 'bool', 'var',
                'there', 'their', 'then', 'they', 'are', 'but', 'its', 'than', 'some',
                'any', 'all', 'more', 'less', 'such', 'only',
            }
            seen: set = set()
            result: List[str] = []
            for token in re.findall(r'[A-Za-z_][A-Za-z0-9_]{2,}', str(text)):
                lower = token.lower()
                if lower not in _stopwords and lower not in seen:
                    seen.add(lower)
                    result.append(lower)
            return result[:10]

        def _best_line_for_annotation(ann_text: str, source_lines: List[str]) -> int:
            """Return 0-based index of the source line best matching this annotation, -1 if none."""
            terms = _ann_terms(ann_text)
            if not terms:
                return -1
            best_score, best_idx = 0, -1
            for idx, line in enumerate(source_lines):
                stripped = line.strip()
                if not stripped:
                    continue
                lowered = stripped.lower()
                score = sum(1 for t in terms if t in lowered)
                if score > best_score:
                    best_score, best_idx = score, idx
            return best_idx if best_score >= 1 else -1

        def render_code_with_highlights(code_text: str, issue_items: List[str], typed_annotations: List[dict], language: str) -> str:
            """Render escaped code with per-annotation callouts placed next to the best-matching line."""
            code_text = str(code_text or "")
            if not code_text.strip():
                return "<div class=\"code-block\"><div class=\"code-line\">(No code provided)</div></div>"

            keyword_map = {
                'csharp': {'using', 'namespace', 'class', 'public', 'private', 'protected', 'static', 'void', 'int', 'string', 'bool', 'if', 'else', 'for', 'foreach', 'while', 'return', 'new'},
                'python': {'def', 'class', 'import', 'from', 'if', 'elif', 'else', 'for', 'while', 'return', 'try', 'except', 'with', 'lambda'},
                'javascript': {'function', 'const', 'let', 'var', 'if', 'else', 'for', 'while', 'return', 'class', 'import', 'export'},
                'typescript': {'function', 'const', 'let', 'var', 'if', 'else', 'for', 'while', 'return', 'class', 'interface', 'type', 'import', 'export'},
                'java': {'class', 'public', 'private', 'protected', 'static', 'void', 'int', 'double', 'String', 'if', 'else', 'for', 'while', 'return', 'new'},
                'sql': {'select', 'from', 'where', 'join', 'left', 'right', 'inner', 'outer', 'group', 'by', 'order', 'insert', 'update', 'delete'},
            }

            def classify_line(line: str, lang: str) -> str:
                if lang == 'plain':
                    return ''
                stripped = line.strip()
                if not stripped:
                    return ''
                if lang in {'csharp', 'javascript', 'typescript', 'java', 'cpp', 'c'} and ('//' in line or stripped.startswith('/*') or stripped.startswith('*')):
                    return 'code-comment-line'
                if lang in {'python', 'ruby'} and stripped.startswith('#'):
                    return 'code-comment-line'
                if lang == 'sql' and stripped.startswith('--'):
                    return 'code-comment-line'
                if re.search(r'"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\'', line):
                    return 'code-string-line'
                if re.search(r'\b\d+(?:\.\d+)?\b', line):
                    return 'code-number-line'
                words = set(re.findall(r'[A-Za-z_][A-Za-z0-9_]*', line))
                if words & keyword_map.get(lang, set()):
                    return 'code-keyword-line'
                if lang == 'html' and ('<' in line and '>' in line):
                    return 'code-tag-line'
                if lang == 'css' and ('{' in line or '}' in line or ':' in line):
                    return 'code-keyword-line'
                return ''

            source_lines = code_text.splitlines()
            line_languages = infer_line_languages(code_text, language)

            # Match each annotation individually to its best-fitting source line
            line_annotations: dict = {}  # line_idx -> list[dict]
            unmatched: List[dict] = []
            for ann in typed_annotations:
                idx = _best_line_for_annotation(ann['text'], source_lines)
                if idx >= 0:
                    line_annotations.setdefault(idx, []).append(ann)
                else:
                    unmatched.append(ann)

            _type_info = {
                'issue':      ('ann-card ann-issue',      '✗ Issue'),
                'deduction':  ('ann-card ann-deduction',  '− Points'),
                'suggestion': ('ann-card ann-suggestion', '✓ Fix'),
            }
            _badge_cls = {'issue': 'lb-issue', 'deduction': 'lb-deduction', 'suggestion': 'lb-suggestion'}

            def render_ann_card(ann: dict) -> str:
                cls, label = _type_info.get(ann['type'], ('ann-card ann-issue', ann['type'].title()))
                return (
                    f'<div class="{cls}">'
                    f'<span class="ann-type-badge">{esc(label)}</span>'
                    f'<span class="ann-num-circle">{ann["num"]}</span>'
                    f'<span class="ann-text">{esc(ann["text"])}</span>'
                    f'</div>'
                )

            rows_html = []
            for idx, line in enumerate(source_lines):
                effective_language = line_languages[idx] if idx < len(line_languages) else language
                line_type = classify_line(line, effective_language)
                anns = line_annotations.get(idx, [])
                cls = "code-line"
                if anns:
                    cls += " issue-line"
                if line_type:
                    cls += f" {line_type}"

                badges_html = (
                    "<span class=\"line-badges\">"
                    + "".join(
                        f'<span class="line-badge {_badge_cls.get(a["type"], "lb-issue")}" title="{esc(a["text"])}">{a["num"]}</span>'
                        for a in anns
                    )
                    + "</span>"
                ) if anns else ""

                ann_col_html = (
                    "<div class=\"ann-col\">"
                    + "".join(render_ann_card(a) for a in anns)
                    + "</div>"
                ) if anns else ""

                rows_html.append(
                    f"<div class=\"code-row\"><div class=\"{cls}\">{esc(line)}{badges_html}</div>{ann_col_html}</div>"
                )

            if unmatched:
                cards = "".join(render_ann_card(a) for a in unmatched)
                rows_html.append(
                    f'<div class="unmatched-notes"><strong>Remaining notes</strong><div class="ann-col">{cards}</div></div>'
                )

            return f"<div class=\"code-block\">{''.join(rows_html)}</div>"

        parts_html = ""
        for part_key in sorted(grading_result.keys()):
            if part_key.startswith('part') and isinstance(grading_result[part_key], dict):
                part_num = part_key.replace('part', '')
                part_data = grading_result[part_key]
                score = part_data.get('score', 0)
                issue_items = part_data.get('issues', [])
                expected_text = part_instruction_map.get(part_key, 'No instructions found for this part.')

                issues_html = "".join(
                    f"<li class=\"issue-item\">{esc(item)}</li>" for item in issue_items
                ) or "<li>None</li>"
                strengths_html = "".join(
                    f"<li class=\"strength-item\">{esc(item)}</li>" for item in part_data.get('strengths', [])
                )
                deductions_html = "".join(
                    f"<li class=\"deduction-item\">{esc(item)}</li>" for item in part_data.get('point_deductions', [])
                ) or "<li>None</li>"
                suggestions_html = "".join(
                    f"<li>{esc(item)}</li>" for item in part_data.get('suggestions', [])
                ) or "<li>None</li>"

                original_code_raw = part_data.get('original_code', '')
                detected_languages = infer_submission_languages(original_code_raw)
                primary_language = detected_languages[0] if detected_languages else 'plain'
                lang_labels = ", ".join(language_display_name(lang) for lang in detected_languages)
                code_mode_label = "Code" if any(lang != 'plain' for lang in detected_languages) else "Text"

                deductions = part_data.get('point_deductions', [])
                suggestions = part_data.get('suggestions', [])
                typed_annotations = (
                    [{'type': 'issue',      'text': item, 'num': i + 1}
                     for i, item in enumerate(issue_items)] +
                    [{'type': 'deduction',  'text': item, 'num': len(issue_items) + i + 1}
                     for i, item in enumerate(deductions)] +
                    [{'type': 'suggestion', 'text': item, 'num': len(issue_items) + len(deductions) + i + 1}
                     for i, item in enumerate(suggestions)]
                )

                code_block_html = (
                    f"<div class=\"language-chip\">Detected {code_mode_label}: {esc(lang_labels)}</div>"
                    f"<h4>Original Answer</h4>{render_code_with_highlights(original_code_raw, issue_items, typed_annotations, primary_language)}"
                )

                score_class = "part-score low" if score < 70 else "part-score"

                parts_html += f"""
                <section class=\"part\">
                    <h3>Part {esc(part_num)} - <span class=\"{score_class}\">Score: {esc(score)}/100</span></h3>
                    <div class=\"part-split\">
                        <div class=\"split-controls\">
                            <button type=\"button\" class=\"toggle-expected-btn\" aria-expanded=\"true\">Hide Assignment Requirements</button>
                            <span class=\"split-hint\">Drag the divider to resize the left and right panels.</span>
                        </div>
                        <div class=\"part-grid\" style=\"--left-panel-width: 32%;\">
                            <div class=\"panel expected-panel\">
                                <h4>What The Assignment Requires</h4>
                                <pre class=\"expected-text\">{esc(expected_text)}</pre>
                            </div>
                            <div class=\"splitter\" role=\"separator\" aria-orientation=\"vertical\" aria-label=\"Resize report columns\" tabindex=\"0\"></div>
                            <div class=\"panel submission-panel\">
                                <h4>Student Submission</h4>
                                {code_block_html}
                            </div>
                        </div>
                    </div>
                    <div class=\"panel issue-panel\">
                        <h4 class=\"issues-title\">What Is Wrong</h4>
                        <ul>{issues_html}</ul>
                        <h4 class=\"deductions-title\">Point Deductions</h4>
                        <ul>{deductions_html}</ul>
                    </div>
                    {f'''<div class="panel strength-panel">
                        <h4 class="strengths-title">What Was Done Correctly</h4>
                        <ul>{strengths_html}</ul>
                    </div>''' if strengths_html else ''}
                    <div class=\"panel correction-panel\">
                        <h4>How It Should Be Corrected</h4>
                        <ul>{suggestions_html}</ul>
                    </div>
                </section>
                """

        overall_feedback = esc(grading_result.get('overall_feedback', 'No overall feedback available.'))
        brief_summary = esc(grading_result.get('brief_summary', 'No summary available.'))
        generated_at = esc(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

        report_html = f"""<!doctype html>
<html lang=\"en\">
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>{esc(assignment_name)} - {esc(student_name)} Grade Report</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; margin: 12px; color: #1f2937; background: #f8fafc; scroll-behavior: smooth; }}
        .container {{ width: 100%; max-width: none; margin: 0; background: #ffffff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 20px; box-sizing: border-box; }}
        h1, h2, h3, h4 {{ color: #111827; margin-top: 0; }}
        .meta {{ margin-bottom: 16px; line-height: 1.6; }}
        .score {{ padding: 12px; border-radius: 8px; background: #ecfeff; border: 1px solid #a5f3fc; margin-bottom: 20px; }}
        .part {{ border-top: 1px solid #e5e7eb; padding-top: 16px; margin-top: 16px; position: relative; }}
        .part-split {{ margin-top: 8px; }}
        .split-controls {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 8px; }}
        .toggle-expected-btn {{ border: 1px solid #cbd5e1; background: #ffffff; color: #1e293b; border-radius: 999px; padding: 6px 12px; font-size: 12px; font-weight: 600; cursor: pointer; }}
        .toggle-expected-btn:hover {{ background: #f8fafc; }}
        .split-hint {{ color: #64748b; font-size: 12px; }}
        .part-grid {{ display: grid; grid-template-columns: minmax(240px, var(--left-panel-width, 32%)) 12px minmax(0, 1fr); gap: 12px; align-items: start; }}
        .panel {{ background: #ffffff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px; margin-top: 10px; }}
        .expected-panel {{ background: #f8fafc; position: sticky; top: 16px; max-height: calc(100vh - 32px); overflow: auto; margin-top: 0; }}
        .submission-panel {{ min-width: 0; margin-top: 0; }}
        .splitter {{ width: 12px; min-height: 100%; border-radius: 999px; background: linear-gradient(180deg, #cbd5e1 0%, #94a3b8 100%); cursor: col-resize; position: relative; box-shadow: inset 0 0 0 1px #94a3b8; }}
        .splitter::after {{ content: ''; position: absolute; top: 50%; left: 50%; width: 4px; height: 36px; transform: translate(-50%, -50%); border-radius: 999px; background: rgba(255, 255, 255, 0.95); box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.7); }}
        .part-grid.is-collapsed {{ grid-template-columns: 1fr; gap: 0; }}
        .part-grid.is-collapsed .expected-panel,
        .part-grid.is-collapsed .splitter {{ display: none; }}
        .part-grid.is-collapsed .submission-panel {{ grid-column: 1 / -1; width: 100%; }}
        .issue-panel {{ background: #fef2f2; border-color: #fecaca; }}
        .strength-panel {{ background: #ecfdf5; border-color: #a7f3d0; }}
        .correction-panel {{ background: #f0fdf4; border-color: #bbf7d0; }}
        .submission-inventory {{ background: #f8fafc; border-color: #cbd5e1; }}
        .submission-images {{ background: #f8fafc; border-color: #93c5fd; }}
        .submission-images-details > summary {{ cursor: pointer; font-weight: 700; color: #1e3a8a; margin-bottom: 8px; }}
        .submission-images-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; margin-top: 10px; }}
        .submission-image-card {{ margin: 0; border: 1px solid #cbd5e1; border-radius: 8px; overflow: hidden; background: #ffffff; }}
        .submission-image-link {{ display: block; }}
        .submission-image-link:hover {{ opacity: 0.92; }}
        .submission-image-card img {{ display: block; width: 100%; height: 180px; object-fit: contain; background: #0f172a; }}
        .submission-image-card figcaption {{ padding: 8px; font-size: 12px; color: #334155; word-break: break-word; }}
        .submission-image-modal {{ position: fixed; inset: 0; z-index: 1200; display: none; }}
        .submission-image-modal:target {{ display: block; }}
        .submission-image-modal-backdrop {{ position: absolute; inset: 0; background: rgba(2, 6, 23, 0.82); }}
        .submission-image-modal-content {{
            position: absolute;
            inset: 5vh 5vw;
            background: #0f172a;
            border: 1px solid #334155;
            border-radius: 10px;
            padding: 12px;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }}
        .submission-image-modal-header {{ display: flex; justify-content: flex-end; align-items: center; }}
        .submission-image-stage {{ display: grid; grid-template-columns: 44px minmax(0, 1fr) 44px; align-items: center; gap: 10px; flex: 1 1 auto; min-height: 0; }}
        .submission-image-modal-content img {{ width: 100%; height: 100%; object-fit: contain; background: #020617; border-radius: 8px; min-height: 0; }}
        .submission-image-modal-content p {{ margin: 0; color: #cbd5e1; font-size: 13px; word-break: break-word; }}
        .submission-image-nav {{
            width: 40px;
            height: 40px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 999px;
            text-decoration: none;
            font-size: 24px;
            line-height: 1;
            background: #1e293b;
            border: 1px solid #475569;
            color: #e2e8f0;
            user-select: none;
        }}
        .submission-image-nav:hover {{ background: #334155; }}
        .submission-image-modal-close {{
            align-self: flex-end;
            display: inline-block;
            background: #1e293b;
            border: 1px solid #475569;
            color: #e2e8f0;
            border-radius: 999px;
            padding: 4px 10px;
            font-size: 12px;
            font-weight: 700;
            text-decoration: none;
        }}
        .submission-image-modal-close:hover {{ background: #334155; }}
        pre {{ background: #0b1020; color: #e5e7eb; padding: 12px; border-radius: 8px; overflow-x: auto; white-space: pre-wrap; word-break: break-word; }}
        .expected-text {{ background: #f8fafc; color: #111827; border: 1px solid #e5e7eb; }}
        .inventory-text {{ background: #f8fafc; color: #111827; border: 1px solid #cbd5e1; margin: 0; }}
        .inventory-intro {{ margin-top: 0; color: #475569; }}
        .language-chip {{ display: inline-block; margin: 4px 0 10px; padding: 4px 10px; border: 1px solid #bae6fd; background: #eff6ff; color: #1e3a8a; border-radius: 999px; font-size: 12px; font-weight: 700; }}
        .code-block {{ background: #0b1020; color: #e5e7eb; border-radius: 8px; padding: 12px; overflow-x: auto; }}
        .code-row {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(220px, 300px); gap: 10px; align-items: start; }}
        .code-line {{ display: block; padding: 1px 8px; margin: 0 -8px; white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace; }}
        .ann-col {{ display: flex; flex-direction: column; gap: 5px; padding: 2px 0; }}
        .ann-card {{ display: flex; align-items: flex-start; gap: 5px; border-radius: 7px; border: 1px solid; padding: 5px 8px; font-size: 11px; line-height: 1.4; box-shadow: 0 1px 3px rgba(0,0,0,0.10); max-width: 300px; word-break: break-word; }}
        .ann-issue {{ background: #fef2f2; border-color: #fca5a5; color: #7f1d1d; }}
        .ann-deduction {{ background: #fff7ed; border-color: #fed7aa; color: #7c2d12; }}
        .ann-suggestion {{ background: #f0fdf4; border-color: #86efac; color: #14532d; }}
        .ann-type-badge {{ flex-shrink: 0; font-size: 10px; font-weight: 700; padding: 1px 5px; border-radius: 999px; white-space: nowrap; margin-top: 1px; }}
        .ann-issue .ann-type-badge {{ background: #fee2e2; color: #991b1b; }}
        .ann-deduction .ann-type-badge {{ background: #ffedd5; color: #9a3412; }}
        .ann-suggestion .ann-type-badge {{ background: #dcfce7; color: #166534; }}
        .ann-num-circle {{ flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center; width: 15px; height: 15px; border-radius: 50%; font-size: 9px; font-weight: 800; background: rgba(0,0,0,0.12); color: inherit; margin-top: 1px; }}
        .ann-text {{ flex: 1 1 0; }}
        .line-badges {{ display: inline-flex; gap: 3px; margin-left: 6px; vertical-align: middle; }}
        .line-badge {{ display: inline-flex; align-items: center; justify-content: center; width: 14px; height: 14px; border-radius: 50%; font-size: 8px; font-weight: 800; line-height: 1; cursor: default; }}
        .lb-issue {{ background: #ef4444; color: #fff; }}
        .lb-deduction {{ background: #f97316; color: #fff; }}
        .lb-suggestion {{ background: #22c55e; color: #fff; }}
        .unmatched-notes {{ margin-top: 10px; background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 8px 10px; }}
        .unmatched-notes > strong {{ display: block; color: #94a3b8; font-size: 11px; margin-bottom: 6px; }}
        .issue-line {{ background: rgba(239, 68, 68, 0.22); border-left: 3px solid #ef4444; }}
        .code-comment-line {{ color: #9ca3af; }}
        .code-string-line {{ color: #fca5a5; }}
        .code-number-line {{ color: #93c5fd; }}
        .code-keyword-line {{ color: #86efac; }}
        .code-tag-line {{ color: #fcd34d; }}
        .issues-title, .deductions-title {{ color: #b91c1c; }}
        .strengths-title {{ color: #065f46; }}
        .strength-item {{ color: #065f46; font-weight: 600; }}
        .issue-item, .deduction-item {{ color: #b91c1c; font-weight: 600; }}
        .inline-notes {{ margin-top: 12px; padding: 10px; border: 1px solid #fde68a; border-radius: 8px; background: #fffbeb; }}
        .inline-notes h4 {{ margin-bottom: 8px; color: #92400e; }}
        .inline-notes ul {{ margin: 0; padding-left: 18px; }}
        .part-score.low {{ color: #b91c1c; font-weight: 700; }}
        ul {{ margin-top: 8px; }}
        .scroll-top-btn {{
            position: fixed;
            right: 20px;
            bottom: 20px;
            width: 46px;
            height: 46px;
            border: none;
            border-radius: 999px;
            background: #2563eb;
            color: #ffffff;
            font-size: 22px;
            font-weight: 700;
            line-height: 1;
            cursor: pointer;
            box-shadow: 0 12px 24px rgba(15, 23, 42, 0.24);
            transition: transform 0.15s ease, background 0.2s ease, opacity 0.2s ease;
            opacity: 0;
            pointer-events: none;
            z-index: 1000;
        }}
        .scroll-top-btn.visible {{
            opacity: 1;
            pointer-events: auto;
        }}
        .scroll-top-btn:hover {{
            background: #1d4ed8;
            transform: translateY(-2px);
        }}
        .scroll-top-btn:focus-visible {{
            outline: 2px solid #93c5fd;
            outline-offset: 2px;
        }}
        @media (max-width: 900px) {{
            .split-controls {{ flex-direction: column; align-items: flex-start; }}
            .part-grid {{ grid-template-columns: 1fr; gap: 10px; }}
            .expected-panel {{ position: static; max-height: none; overflow: visible; }}
            .submission-panel {{ margin-top: 0; }}
            .splitter {{ display: none; }}
            .part-grid.is-collapsed {{ grid-template-columns: 1fr; }}
            .part-grid.is-collapsed .submission-panel {{ display: block; }}
            .code-row {{ grid-template-columns: 1fr; }}
            .line-note {{ margin-top: 4px; }}
            .scroll-top-btn {{ right: 14px; bottom: 14px; width: 42px; height: 42px; font-size: 20px; }}
            .submission-image-modal-content {{ inset: 3vh 3vw; }}
            .submission-image-stage {{ grid-template-columns: 36px minmax(0, 1fr) 36px; gap: 6px; }}
            .submission-image-nav {{ width: 34px; height: 34px; font-size: 20px; }}
        }}
    </style>
</head>
<body>
    <div class=\"container\">
        <h1>{esc(assignment_name)} Grade Report</h1>
        <div class=\"meta\">
            <div><strong>Student:</strong> {esc(student_name)}</div>
            <div><strong>Date:</strong> {generated_at}</div>
        </div>

        <div class=\"score\">
            <div><strong>Final Score:</strong> {esc(f"{final_score:.1f}")}/100</div>
            <div><strong>Letter Grade:</strong> {esc(letter_grade)}</div>
            <div><strong>Brief Summary:</strong> {brief_summary}</div>
        </div>

        {submission_tree_html}
        {submission_inventory_html}
        {image_gallery_html}

        <h2>Detailed Feedback</h2>
        {parts_html}

        <section class=\"part\">
            <h3>Overall Feedback</h3>
            <p>{overall_feedback}</p>
        </section>
    </div>
</body>
</html>
"""

        return report_html
    
    def generate_brief_summary(self, student_name: str, grading_result: Dict) -> str:
        """Generate a brief summary for the student"""
        final_score = grading_result.get('final_score', 0)
        letter_grade = "A" if final_score >= 90 else "B" if final_score >= 80 else "C" if final_score >= 70 else "D" if final_score >= 60 else "F"
        
        brief_summary = grading_result.get('brief_summary', 'No summary available.')
        
        return f"{student_name}: {final_score:.1f}/100 ({letter_grade}) - {brief_summary}"
    
    def grade_assignment(self, student_name: str, submission_content: str, instructions_file: str) -> Dict:
        """Grade a single assignment"""
        print(f"Grading: {student_name}")
        
        # Load assignment instructions
        instructions = self.load_assignment_instructions(instructions_file)
        if not instructions:
            return {"error": "Could not load assignment instructions"}
        
        # Detect assignment structure and set weights
        self.part_weights = self.detect_assignment_structure(instructions)
        
        # Extract parts from C# submission content
        csharp_parts = self.extract_csharp_parts(submission_content)
        
        # Get assignment name from instructions file
        assignment_name = Path(instructions_file).stem.replace('-', ' ').replace('_', ' ')
        
        # Create grading prompt
        prompt = self.create_grading_prompt(instructions, csharp_parts, assignment_name)
        
        # Grade with AI (either OpenAI or custom endpoint)
        grading_result = self.grade_with_ai(prompt)

        def _extract_note_block(text: str, start_label: str, next_labels: List[str]) -> str:
            source = str(text or "")
            if not source.strip():
                return ""

            start_pattern = rf'(\[{re.escape(start_label)}:[\s\S]*?)'
            terminator_pattern = "|".join(rf'\n\n\[{re.escape(label)}:' for label in next_labels)
            if terminator_pattern:
                pattern = start_pattern + rf'(?={terminator_pattern}|$)'
            else:
                pattern = start_pattern + r'$'

            match = re.search(pattern, source)
            return match.group(1).strip() if match else ""

        grading_result['_submission_tree'] = _extract_note_block(submission_content, 'SUBMISSION TREE', ['SUBMISSION INVENTORY'])
        grading_result['_submission_inventory'] = _extract_note_block(submission_content, 'SUBMISSION INVENTORY', [])
        
        # Calculate final score if not provided
        if 'final_score' not in grading_result and 'error' not in grading_result:
            part_scores = {}
            for part_key in grading_result:
                if part_key.startswith('part') and isinstance(grading_result[part_key], dict):
                    part_scores[part_key] = grading_result[part_key]['score']
            grading_result['final_score'] = self.calculate_final_score(part_scores)
        
        return grading_result
    
    def grade_all_assignments(self, main_zip_path: str, instructions_file: str, output_dir: str = None):
        """Grade all assignments from a main zip file"""
        self.last_run_stopped = False
        if output_dir is None:
            output_dir = os.getcwd()  # Save to current directory (where AutoGrade.py is located)
        
        # Extract all assignments from the main zip
        print("Extracting assignments from main zip file...")
        assignments = self.extract_all_assignments(main_zip_path)
        
        if not assignments:
            print("No assignments found in the zip file!")
            return
        
        # Filter out already-completed students for resumed jobs
        total_students = len(assignments)
        if self.selected_students:
            selected_names = {self.normalize_student_name(name) for name in self.selected_students}
            assignments = {
                name: content for name, content in assignments.items()
                if self.normalize_student_name(name) in selected_names
            }
            print(f"Selected {len(assignments)} student assignment(s) from {total_students} discovered submission(s)")
            total_students = len(assignments)

        if self.completed_students:
            assignments = {name: content for name, content in assignments.items() if name not in self.completed_students}
            remaining = len(assignments)
            print(f"Resuming: {total_students} total students, skipping {total_students - remaining} completed, grading {remaining} remaining")
        else:
            print(f"Found {len(assignments)} student assignments to grade")
        
        if not assignments:
            print("All students have been completed!")
            return
        
        # Get assignment name from instructions file
        assignment_name = Path(instructions_file).stem.replace('-', ' ').replace('_', ' ')
        instructions_html = self.load_assignment_instructions(instructions_file)
        
        results = []
        brief_summaries = []
        
        for student_name, submission_content in assignments.items():
            if callable(self.cancel_check) and self.cancel_check():
                self.last_run_stopped = True
                print("⏹ Stop requested. Halting before the next student begins.")
                break

            try:
                result = self.grade_assignment(student_name, submission_content, instructions_file)
                
                # Generate detailed report
                if 'error' not in result:
                    print(f"📝 Generating report for {student_name}...")
                    report = self.generate_grade_report(student_name, result, assignment_name)
                    student_images = self._prepare_report_image_assets(
                        student_name,
                        self.student_image_artifacts.get(student_name, []),
                        output_dir,
                    )
                    html_report = self.generate_grade_report_html(
                        student_name,
                        result,
                        assignment_name,
                        instructions_html,
                        report_images=student_images,
                    )
                    
                    # Save detailed report
                    safe_name = re.sub(r'[<>:"/\\|?*]', '_', student_name)
                    report_file = os.path.join(output_dir, f"{safe_name}_grade_report.txt")
                    
                    try:
                        with open(report_file, 'w', encoding='utf-8') as f:
                            f.write(report)
                        print(f"💾 Saved report: {report_file}")
                    except Exception as save_error:
                        print(f"❌ Failed to save report: {save_error}")

                    html_report_file = os.path.join(output_dir, f"{safe_name}_grade_report.html")
                    try:
                        with open(html_report_file, 'w', encoding='utf-8') as f:
                            f.write(html_report)
                        print(f"💾 Saved HTML report: {html_report_file}")
                        if student_name not in self.completed_students:
                            self.completed_students.append(student_name)
                        if callable(self.progress_callback):
                            self.progress_callback(
                                student_name,
                                [os.path.basename(report_file), os.path.basename(html_report_file)],
                            )
                    except Exception as html_save_error:
                        print(f"❌ Failed to save HTML report: {html_save_error}")
                    
                    # Generate brief summary
                    brief_summary = self.generate_brief_summary(student_name, result)
                    brief_summaries.append(brief_summary)
                    
                    print(f"✓ Graded {student_name}: {result.get('final_score', 0):.1f}/100")
                else:
                    print(f"✗ Error grading {student_name}: {result['error']}")
                    brief_summaries.append(f"{student_name}: ERROR - {result['error']}")
                
                results.append({
                    'student': student_name,
                    'result': result
                })
                
            except Exception as e:
                print(f"✗ Exception grading {student_name}: {e}")
                brief_summaries.append(f"{student_name}: ERROR - {e}")
        
        # Generate summary reports
        self.generate_summary_report(results, output_dir, assignment_name)
        self.generate_brief_summary_file(brief_summaries, output_dir, assignment_name)
        
        if self.last_run_stopped:
            print(f"\nGrading stopped. Partial reports saved to: {output_dir}")
        else:
            print(f"\nGrading complete! Reports saved to: {output_dir}")
    
    def generate_brief_summary_file(self, brief_summaries: List[str], output_dir: str, assignment_name: str):
        """Generate a brief summary file with all student grades"""
        summary_file = os.path.join(output_dir, f"{assignment_name}_brief_summary.txt")
        
        with open(summary_file, 'w', encoding='utf-8') as f:
            f.write(f"=== {assignment_name} - Brief Summary ===\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            for summary in sorted(brief_summaries):
                f.write(f"{summary}\n")
        
        print(f"Brief summary saved to: {summary_file}")
    
    def generate_summary_report(self, results: List[Dict], output_dir: str, assignment_name: str = "C# Assignment"):
        """Generate a summary report of all grades"""
        summary_file = os.path.join(output_dir, f"{assignment_name}_grading_summary.txt")
        
        with open(summary_file, 'w', encoding='utf-8') as f:
            f.write(f"=== {assignment_name} - GRADING SUMMARY REPORT ===\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            total_students = len(results)
            graded_students = len([r for r in results if 'error' not in r['result']])
            
            f.write(f"Total Students: {total_students}\n")
            f.write(f"Successfully Graded: {graded_students}\n")
            f.write(f"Errors: {total_students - graded_students}\n\n")
            
            # Grade distribution
            scores = [r['result'].get('final_score', 0) for r in results if 'error' not in r['result']]
            if scores:
                avg_score = sum(scores) / len(scores)
                f.write(f"Average Score: {avg_score:.1f}\n")
                f.write(f"Highest Score: {max(scores):.1f}\n")
                f.write(f"Lowest Score: {min(scores):.1f}\n\n")
                
                # Letter grade distribution
                grades = {'A': 0, 'B': 0, 'C': 0, 'D': 0, 'F': 0}
                for score in scores:
                    if score >= 90:
                        grades['A'] += 1
                    elif score >= 80:
                        grades['B'] += 1
                    elif score >= 70:
                        grades['C'] += 1
                    elif score >= 60:
                        grades['D'] += 1
                    else:
                        grades['F'] += 1
                
                f.write("Grade Distribution:\n")
                for grade, count in grades.items():
                    percentage = (count / len(scores)) * 100 if scores else 0
                    f.write(f"  {grade}: {count} students ({percentage:.1f}%)\n")
                f.write("\n")
            
            # Individual results
            f.write("INDIVIDUAL RESULTS:\n")
            f.write("-" * 70 + "\n")
            
            for result in sorted(results, key=lambda x: x['student']):
                student = result['student']
                if 'error' in result['result']:
                    f.write(f"{student:30} ERROR: {result['result']['error']}\n")
                else:
                    score = result['result'].get('final_score', 0)
                    letter = "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"
                    brief = result['result'].get('brief_summary', 'No summary available.')
                    f.write(f"{student:30} {score:6.1f}/100 ({letter}) - {brief}\n")
def main():
    """Main function to run the auto-grader"""

    # ── Configuration (.env-driven) ──────────────────────────────────────────
    # MODEL_PROVIDER: openai | custom | ollama | local
    # MODEL_NAME: model ID used by the selected provider (e.g. gpt-5.4 or gemma3:12b)
    model_provider = os.getenv('MODEL_PROVIDER', 'openai').strip().lower()
    model_name = os.getenv('MODEL_NAME', '').strip() or None
    custom_endpoint = os.getenv('CUSTOM_ENDPOINT', 'http://dryangai.ddns.net:11434')

    use_custom_endpoint = model_provider in {'custom', 'ollama', 'local'}

    # Set to a student name string (e.g. "John Smith") to grade only that student,
    # or leave as None to grade all students in the zip.
    SINGLE_STUDENT = os.getenv('SINGLE_STUDENT') or None

    # Resolve paths relative to this script's directory
    script_dir = Path(__file__).parent.resolve()

    # Auto-detect the zip file in the script directory
    zip_files = list(script_dir.glob("*.zip"))
    if len(zip_files) == 0:
        print("❌ No zip file found in the script directory.")
        sys.exit(1)
    elif len(zip_files) == 1:
        MAIN_ZIP = str(zip_files[0])
    else:
        print(f"⚠️  Multiple zip files found. Using: {zip_files[0].name}")
        MAIN_ZIP = str(zip_files[0])

    # Auto-detect the HTML instructions file in the script directory
    html_files = list(script_dir.glob("*.html"))
    if not html_files:
        print("❌ No HTML instructions file found in the script directory.")
        sys.exit(1)
    INSTRUCTIONS = str(html_files[0])

    # Output directory: <script_dir>/output/
    OUTPUT_DIR = str(script_dir / "output")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    # ──────────────────────────────────────────────────────────────────────────

    try:
        grader = AutoGrader(
            use_custom_endpoint=use_custom_endpoint,
            custom_endpoint=custom_endpoint,
            model_provider=model_provider,
            model_name=model_name,
        )

        if SINGLE_STUDENT:
            # Grade a single student (useful for testing)
            print(f"Testing with single student: {SINGLE_STUDENT}")
            assignments = grader.extract_all_assignments(MAIN_ZIP)

            student_found = None
            student_content = None
            for name, content in assignments.items():
                if SINGLE_STUDENT.lower() in name.lower():
                    student_found = name
                    student_content = content
                    break

            if student_found:
                result = grader.grade_assignment(student_found, student_content, INSTRUCTIONS)
                if 'error' not in result:
                    assignment_name = Path(INSTRUCTIONS).stem.replace('-', ' ').replace('_', ' ')
                    instructions_html = grader.load_assignment_instructions(INSTRUCTIONS)
                    report = grader.generate_grade_report(student_found, result, assignment_name)
                    student_images = grader._prepare_report_image_assets(
                        student_found,
                        grader.student_image_artifacts.get(student_found, []),
                        OUTPUT_DIR,
                    )
                    html_report = grader.generate_grade_report_html(
                        student_found,
                        result,
                        assignment_name,
                        instructions_html,
                        report_images=student_images,
                    )
                    print(report)

                    safe_name = re.sub(r'[<>:"/\\|?*]', '_', student_found)
                    report_file = os.path.join(OUTPUT_DIR, f"{safe_name}_grade_report.txt")
                    html_report_file = os.path.join(OUTPUT_DIR, f"{safe_name}_grade_report.html")
                    try:
                        with open(report_file, 'w', encoding='utf-8') as f:
                            f.write(report)
                        print(f"\n💾 Report saved to: {report_file}")

                        with open(html_report_file, 'w', encoding='utf-8') as f:
                            f.write(html_report)
                        print(f"💾 HTML report saved to: {html_report_file}")
                    except Exception as save_error:
                        print(f"❌ Failed to save report: {save_error}")
                else:
                    print(f"Error: {result['error']}")
            else:
                print(f"Student '{SINGLE_STUDENT}' not found. Available students:")
                for name in assignments.keys():
                    print(f"  - {name}")
        else:
            # Grade all assignments
            grader.grade_all_assignments(MAIN_ZIP, INSTRUCTIONS, OUTPUT_DIR)

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()