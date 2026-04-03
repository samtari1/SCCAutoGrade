#!/usr/bin/env python3
"""
AutoGrade.py - Automated Assignment Grading System
"""

import os
import sys
import zipfile
import tempfile
import shutil
from pathlib import Path
import openai
from typing import List, Dict, Tuple
import json
import re
import html
import time
from datetime import datetime
from dotenv import load_dotenv
import requests

# Load environment variables from .env file
load_dotenv()

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
        self.exclude_generated_files = os.getenv('EXCLUDE_GENERATED_FILES', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
        self.include_designer_for_grading = os.getenv('INCLUDE_DESIGNER_FOR_GRADING', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
        self.hide_generated_in_reports = os.getenv('HIDE_GENERATED_IN_REPORTS', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
        self.focus_report_on_issues = os.getenv('REPORT_FOCUS_ON_ISSUES', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
        self.openai_timeout = int(os.getenv('OPENAI_TIMEOUT_SECONDS', '240'))
        self.openai_max_retries = int(os.getenv('OPENAI_MAX_RETRIES', '3'))
        self.openai_retry_backoff = float(os.getenv('OPENAI_RETRY_BACKOFF_SECONDS', '2.0'))
        self.openai_stream = os.getenv('OPENAI_STREAM', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
        
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

    def extract_assignment_parts(self, instructions_html: str) -> List[Dict[str, str]]:
        """Extract assignment part metadata from HTML headings."""
        if not instructions_html:
            return []

        part_pattern = re.compile(r'<h2>\s*Part\s*(\d+)\s*:\s*([^<]*)</h2>', re.IGNORECASE)
        matches = list(part_pattern.finditer(instructions_html))
        parts = []

        for idx, match in enumerate(matches):
            part_num = int(match.group(1))
            title = html.unescape(match.group(2)).strip()
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(instructions_html)
            raw_block = instructions_html[start:end]
            requirements = self.clean_html_block(raw_block)

            is_optional = bool(
                re.search(r'optional|stretch', title, re.IGNORECASE)
                or re.search(r'\boptional\b|\bstretch\b', requirements, re.IGNORECASE)
            )

            parts.append({
                'key': f'part{part_num}',
                'part_num': str(part_num),
                'title': title,
                'requirements': requirements,
                'is_optional': is_optional,
            })

        return sorted(parts, key=lambda p: int(p['part_num']))

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

    def extract_all_assignments(self, main_zip_path: str) -> Dict[str, str]:
        """Extract all student assignments from the main zip file and nested zips"""
        assignments = {}
        processed_students = set()

        # ── Step 1: Extract the main zip into a named folder (skip if already done) ──
        zip_path_obj = Path(main_zip_path)
        extract_dir = zip_path_obj.parent / zip_path_obj.stem
        if extract_dir.exists():
            print(f"📂 Submissions folder already exists, reusing: {extract_dir}")
        else:
            print(f"📦 Extracting main zip to: {extract_dir}")
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(main_zip_path, 'r') as main_zip:
                main_zip.extractall(str(extract_dir))
            print(f"✅ Extracted to: {extract_dir}")

        # ── Step 2: Iterate direct children — each represents one student submission ──
        for student_entry in sorted(extract_dir.iterdir()):
            if student_entry.name.startswith('.'):
                continue

            student_name = self.extract_student_name(student_entry.name)
            normalized_name = self.normalize_student_name(student_name)

            if normalized_name in processed_students:
                continue

            if student_entry.is_dir():
                # Student folder: extract any archives inside it in-place, then find C# code
                self.ensure_archives_extracted(student_entry)
                submission_content = self.find_csharp_in_directory(str(student_entry))

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
                submission_content = self.find_csharp_in_directory(str(inner_dir))

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
                submission_content = self.find_csharp_in_directory(str(inner_dir))

            else:
                continue  # Not a folder or archive — skip

            if submission_content and student_name:
                assignments[student_name] = submission_content
                processed_students.add(normalized_name)
                print(f"  ✓ Found C# code for: {student_name}")
            else:
                print(f"  ⚠️  No C# code found for: {student_name}")

        return assignments
    
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
        """Normalize student name for duplicate detection"""
        # Convert to lowercase, remove spaces and special characters
        normalized = student_name.lower()
        normalized = re.sub(r'[^a-z]', '', normalized)
        
        # Sort the characters to handle name order variations like "BennettCannon" vs "CannonBennett"
        return ''.join(sorted(normalized))
    
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
        if lowered.endswith('.designer.cs') and not self.include_designer_for_grading:
            return True
        return False

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
            if file_name.endswith('.designer.cs') and self.include_designer_for_grading:
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

        # Include paired designer files when configured, so grading can validate event wiring.
        if self.include_designer_for_grading:
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

    def _filter_generated_sections_for_report(self, code_text: str) -> Tuple[str, int]:
        """Remove generated file sections from report display while keeping grading context intact."""
        text = str(code_text or "")
        if not text.strip() or not self.hide_generated_in_reports:
            return text, 0

        sections = re.split(r'(?=^//\s*File:\s*)', text, flags=re.MULTILINE)
        kept = []
        omitted = 0

        for section in sections:
            stripped = section.strip()
            if not stripped:
                continue

            file_match = re.match(r'^//\s*File:\s*([^\n]+)', stripped)
            if file_match:
                file_name = file_match.group(1).strip().lower().replace('\\\\', '/').split('/')[-1]
                if file_name.endswith('.designer.cs') or file_name.endswith('.g.cs') or file_name.endswith('.generated.cs'):
                    omitted += 1
                    continue

            kept.append(stripped)

        if not kept:
            return text, 0

        return '\n\n'.join(kept), omitted
    
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
        
        return "\n\n".join(combined)

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
        
        prompt = f"""
You are an expert C# instructor grading a "{assignment_name}" lab assignment.
Use the assignment instructions below as the source of truth.

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
7. If WinForms .Designer.cs content is present, verify event wiring correctness (e.g., button.Click handlers are wired and handler methods exist in code-behind).

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
                    "effort": "low"
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
                    report_original_code, omitted_original_sections = self._filter_generated_sections_for_report(part_data['original_code'])
                    report += f"\nOriginal Code:\n{'-' * 40}\n{report_original_code}\n"
                    if omitted_original_sections:
                        report += f"\n[Report view] Omitted {omitted_original_sections} generated file section(s) for readability (still considered during grading).\n"
                    
                    if 'corrected_code' in part_data and part_data['corrected_code'] != part_data['original_code']:
                        report_corrected_code, omitted_corrected_sections = self._filter_generated_sections_for_report(part_data['corrected_code'])
                        report += f"\nCorrected Code:\n{'-' * 40}\n{report_corrected_code}\n"
                        if omitted_corrected_sections:
                            report += f"\n[Report view] Omitted {omitted_corrected_sections} generated corrected section(s) for readability.\n"
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

    def generate_grade_report_html(self, student_name: str, grading_result: Dict, assignment_name: str = "C# Assignment", instructions_html: str = "") -> str:
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

        part_instruction_map = self.extract_part_instruction_map(instructions_html)

        def issue_terms(issue_items: List[str]) -> List[str]:
            """Extract searchable keywords from issue text to mark likely problematic code lines."""
            stopwords = {
                'missing', 'incorrect', 'no', 'not', 'with', 'for', 'the', 'and',
                'from', 'into', 'that', 'this', 'part', 'logic', 'behavior', 'output',
                'comments', 'comment', 'array', 'arrays', 'value', 'values'
            }
            terms = []
            for item in issue_items:
                for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", str(item).lower()):
                    if token not in stopwords:
                        terms.append(token)
            # Keep unique order and only a limited set to avoid over-highlighting.
            seen = set()
            unique_terms = []
            for t in terms:
                if t not in seen:
                    seen.add(t)
                    unique_terms.append(t)
            return unique_terms[:12]

        def render_code_with_highlights(code_text: str, issue_items: List[str], annotation_items: List[str], language: str) -> str:
            """Render escaped code with compact callouts only on likely problematic lines."""
            code_text = str(code_text or "")
            if not code_text.strip():
                return "<div class=\"code-block\"><div class=\"code-line\">(No code provided)</div></div>"

            terms = issue_terms(issue_items)
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
            pending_notes = [str(note).strip() for note in annotation_items if str(note).strip()]
            hit_flags = []
            for line in source_lines:
                lowered = line.lower()
                hit_flags.append(any(term in lowered for term in terms) if terms else False)

            visible_line_indexes = list(range(len(source_lines)))
            if self.focus_report_on_issues and any(hit_flags) and len(source_lines) > 40:
                keep = set()
                for idx, hit in enumerate(hit_flags):
                    if hit:
                        start = max(0, idx - 2)
                        end = min(len(source_lines), idx + 3)
                        for j in range(start, end):
                            keep.add(j)
                if keep:
                    visible_line_indexes = sorted(keep)

            rows_html = []
            previous_idx = -2
            for idx in visible_line_indexes:
                if idx - previous_idx > 1:
                    rows_html.append('<div class="code-ellipsis">... omitted unimportant lines ...</div>')

                line = source_lines[idx]
                hit = hit_flags[idx]
                effective_language = line_languages[idx] if idx < len(line_languages) else language
                line_type = classify_line(line, effective_language)
                cls = "code-line"
                if hit:
                    cls += " issue-line"
                if line_type:
                    cls += f" {line_type}"
                line_note_html = ""
                if hit and pending_notes:
                    note_text = esc(pending_notes.pop(0))
                    line_note_html = f"<aside class=\"line-note\">{note_text}</aside>"
                rows_html.append(
                    f"<div class=\"code-row\"><div class=\"{cls}\">{esc(line)}</div>{line_note_html}</div>"
                )
                previous_idx = idx

            if pending_notes:
                remaining = "".join(f"<li>{esc(note)}</li>" for note in pending_notes)
                rows_html.append(
                    "<div class=\"unmatched-notes\">"
                    "<strong>Additional correction notes</strong>"
                    f"<ul>{remaining}</ul>"
                    "</div>"
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

                inline_notes = []
                inline_notes.extend([f"Issue: {item}" for item in issue_items])
                inline_notes.extend([f"Deduction: {item}" for item in part_data.get('point_deductions', [])])
                inline_notes.extend([f"Correction Hint: {item}" for item in part_data.get('suggestions', [])])
                if not inline_notes:
                    inline_notes = ["No corrections needed for this part."]
                inline_notes_html = "".join(f"<li>{esc(note)}</li>" for note in inline_notes)

                original_code_raw = part_data.get('original_code', '')
                visible_code_raw, omitted_generated_sections = self._filter_generated_sections_for_report(original_code_raw)
                detected_languages = infer_submission_languages(visible_code_raw)
                primary_language = detected_languages[0] if detected_languages else 'plain'
                lang_labels = ", ".join(language_display_name(lang) for lang in detected_languages)
                code_mode_label = "Code" if any(lang != 'plain' for lang in detected_languages) else "Text"

                annotation_items = []
                annotation_items.extend([f"Issue: {item}" for item in issue_items])
                annotation_items.extend([f"Deduction: {item}" for item in part_data.get('point_deductions', [])])
                annotation_items.extend([f"Fix: {item}" for item in part_data.get('suggestions', [])])

                code_block_html = (
                    f"<div class=\"language-chip\">Detected {code_mode_label}: {esc(lang_labels)}</div>"
                    f"<h4>Original Answer</h4>{render_code_with_highlights(visible_code_raw, issue_items, annotation_items, primary_language)}"
                    + (
                        f"<p class=\"report-note\">Report view hides {omitted_generated_sections} generated file section(s) for readability; grading still considered them.</p>"
                        if omitted_generated_sections else ""
                    )
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
                                <div class="inline-notes">
                                    <h4>Part Notes and Corrections</h4>
                                    <ul>{inline_notes_html}</ul>
                                </div>
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
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; margin: 12px; color: #1f2937; background: #f8fafc; }}
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
        .report-note {{ margin: 8px 0 0; color: #475569; font-size: 12px; }}
        pre {{ background: #0b1020; color: #e5e7eb; padding: 12px; border-radius: 8px; overflow-x: auto; white-space: pre-wrap; word-break: break-word; }}
        .expected-text {{ background: #f8fafc; color: #111827; border: 1px solid #e5e7eb; }}
        .language-chip {{ display: inline-block; margin: 4px 0 10px; padding: 4px 10px; border: 1px solid #bae6fd; background: #eff6ff; color: #1e3a8a; border-radius: 999px; font-size: 12px; font-weight: 700; }}
        .code-block {{ background: #0b1020; color: #e5e7eb; border-radius: 8px; padding: 12px; overflow-x: auto; }}
        .code-row {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(220px, 300px); gap: 10px; align-items: start; }}
        .code-line {{ display: block; padding: 1px 8px; margin: 0 -8px; white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace; }}
        .line-note {{ background: #fef3c7; color: #7c2d12; border: 1px solid #f59e0b; border-radius: 8px; padding: 6px 8px; font-size: 12px; line-height: 1.35; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.15); }}
        .code-ellipsis {{ margin: 8px 0; color: #94a3b8; font-style: italic; font-size: 12px; }}
        .unmatched-notes {{ margin-top: 10px; background: #fef3c7; color: #78350f; border: 1px solid #f59e0b; border-radius: 8px; padding: 8px 10px; }}
        .unmatched-notes ul {{ margin: 6px 0 0; padding-left: 18px; }}
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

        <h2>Detailed Feedback</h2>
        {parts_html}

        <section class=\"part\">
            <h3>Overall Feedback</h3>
            <p>{overall_feedback}</p>
        </section>
    </div>
    <script>
        document.addEventListener('DOMContentLoaded', () => {{
            const minLeftPercent = 20;
            const maxLeftPercent = 55;

            for (const split of document.querySelectorAll('.part-split')) {{
                const grid = split.querySelector('.part-grid');
                const toggle = split.querySelector('.toggle-expected-btn');
                const splitter = split.querySelector('.splitter');
                if (!grid || !toggle || !splitter) {{
                    continue;
                }}

                const setToggleLabel = () => {{
                    const collapsed = grid.classList.contains('is-collapsed');
                    toggle.textContent = collapsed ? 'Show Assignment Requirements' : 'Hide Assignment Requirements';
                    toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
                }};

                toggle.addEventListener('click', () => {{
                    grid.classList.toggle('is-collapsed');
                    setToggleLabel();
                }});

                const updateWidth = (clientX) => {{
                    const rect = grid.getBoundingClientRect();
                    if (!rect.width) {{
                        return;
                    }}
                    const nextPercent = ((clientX - rect.left) / rect.width) * 100;
                    const clampedPercent = Math.min(maxLeftPercent, Math.max(minLeftPercent, nextPercent));
                    grid.style.setProperty('--left-panel-width', `${{clampedPercent}}%`);
                }};

                const stopDrag = () => {{
                    document.body.classList.remove('is-resizing-report');
                    window.removeEventListener('pointermove', onPointerMove);
                    window.removeEventListener('pointerup', stopDrag);
                }};

                const onPointerMove = (event) => {{
                    updateWidth(event.clientX);
                }};

                splitter.addEventListener('pointerdown', (event) => {{
                    if (window.matchMedia('(max-width: 900px)').matches) {{
                        return;
                    }}
                    if (grid.classList.contains('is-collapsed')) {{
                        grid.classList.remove('is-collapsed');
                        setToggleLabel();
                    }}
                    document.body.classList.add('is-resizing-report');
                    splitter.setPointerCapture(event.pointerId);
                    updateWidth(event.clientX);
                    window.addEventListener('pointermove', onPointerMove);
                    window.addEventListener('pointerup', stopDrag, {{ once: true }});
                }});

                splitter.addEventListener('keydown', (event) => {{
                    if (grid.classList.contains('is-collapsed')) {{
                        return;
                    }}
                    const current = parseFloat(getComputedStyle(grid).getPropertyValue('--left-panel-width')) || 32;
                    if (event.key === 'ArrowLeft') {{
                        event.preventDefault();
                        grid.style.setProperty('--left-panel-width', `${{Math.max(minLeftPercent, current - 2)}}%`);
                    }}
                    if (event.key === 'ArrowRight') {{
                        event.preventDefault();
                        grid.style.setProperty('--left-panel-width', `${{Math.min(maxLeftPercent, current + 2)}}%`);
                    }}
                }});

                setToggleLabel();
            }}
        }});
    </script>
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
        if output_dir is None:
            output_dir = os.getcwd()  # Save to current directory (where AutoGrade.py is located)
        
        # Extract all assignments from the main zip
        print("Extracting assignments from main zip file...")
        assignments = self.extract_all_assignments(main_zip_path)
        
        if not assignments:
            print("No assignments found in the zip file!")
            return
        
        print(f"Found {len(assignments)} student assignments to grade")
        
        # Get assignment name from instructions file
        assignment_name = Path(instructions_file).stem.replace('-', ' ').replace('_', ' ')
        instructions_html = self.load_assignment_instructions(instructions_file)
        
        results = []
        brief_summaries = []
        
        for student_name, submission_content in assignments.items():
            try:
                result = self.grade_assignment(student_name, submission_content, instructions_file)
                
                # Generate detailed report
                if 'error' not in result:
                    print(f"📝 Generating report for {student_name}...")
                    report = self.generate_grade_report(student_name, result, assignment_name)
                    html_report = self.generate_grade_report_html(student_name, result, assignment_name, instructions_html)
                    
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
                    html_report = grader.generate_grade_report_html(student_found, result, assignment_name, instructions_html)
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