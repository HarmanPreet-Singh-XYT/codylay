"""
Chunker — intelligent file splitting for large files.

Flow:
  1. Count tokens
  2. If under threshold → return file as-is (single chunk)
  3. If over threshold:
     a. Extract skeleton (imports, signatures, docstrings)
     b. Split on structural boundaries (classes, functions, components)
     c. If no clean boundaries → split by token budget with overlap
     d. Return skeleton + ordered chunks
"""

import re
import os
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum


class ChunkType(str, Enum):
    FULL = "full"           # Entire file, no splitting needed
    SKELETON = "skeleton"   # Imports + signatures only
    DETAIL = "detail"       # A structural block (class, function group)


@dataclass
class FileChunk:
    """A single chunk of a file."""
    chunk_type: ChunkType
    content: str
    label: str = ""             # Human-readable label like "class UserService"
    start_line: int = 0
    end_line: int = 0
    token_count: int = 0
    symbols: List[str] = field(default_factory=list)  # Functions/classes in this chunk


@dataclass
class ChunkPlan:
    """Complete chunking plan for a file."""
    file_path: str
    needs_chunking: bool
    total_tokens: int
    chunks: List[FileChunk] = field(default_factory=list)
    skeleton: Optional[FileChunk] = None

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)


class Chunker:
    """
    Splits large files into processable chunks.
    
    Strategies by file type:
    - Python: split on class/function definitions
    - JS/TS: split on class/function/component definitions
    - Dart: split on class definitions
    - Java/Kotlin: split on class definitions
    - Go: split on func/type definitions
    - Generic: split on blank line clusters or by token budget
    """

    def __init__(self, token_counter, config):
        """
        Args:
            token_counter: callable that takes a string and returns token count
            config: CodeDocConfig instance
        """
        self.count_tokens = token_counter
        self.threshold = getattr(config, 'chunk_token_threshold', 6000)
        self.max_chunk_tokens = getattr(config, 'max_chunk_tokens', 4000)
        self.overlap_ratio = getattr(config, 'chunk_overlap_ratio', 0.10)

    def plan(self, file_path: str, content: str) -> ChunkPlan:
        """
        Analyze a file and create a chunking plan.
        Returns a ChunkPlan with either:
          - A single FULL chunk (no splitting needed)
          - A SKELETON chunk + multiple DETAIL chunks
        """
        total_tokens = self.count_tokens(content)

        if total_tokens <= self.threshold:
            # No chunking needed
            return ChunkPlan(
                file_path=file_path,
                needs_chunking=False,
                total_tokens=total_tokens,
                chunks=[FileChunk(
                    chunk_type=ChunkType.FULL,
                    content=content,
                    label="full file",
                    start_line=1,
                    end_line=content.count('\n') + 1,
                    token_count=total_tokens,
                )],
            )

        # File needs chunking
        lines = content.split('\n')
        ext = os.path.splitext(file_path)[1].lower()

        # Step 1: Extract skeleton
        skeleton = self._extract_skeleton(lines, ext)
        skeleton_chunk = FileChunk(
            chunk_type=ChunkType.SKELETON,
            content=skeleton,
            label="skeleton (imports + signatures)",
            token_count=self.count_tokens(skeleton),
        )

        # Step 2: Split into structural chunks
        boundaries = self._find_boundaries(lines, ext)

        if boundaries:
            detail_chunks = self._split_on_boundaries(lines, boundaries)
        else:
            detail_chunks = self._split_by_tokens(lines)

        # Step 3: Add overlap between chunks
        detail_chunks = self._add_overlap(detail_chunks, lines)

        # Step 4: Count tokens for each chunk
        for chunk in detail_chunks:
            chunk.token_count = self.count_tokens(chunk.content)

        # Step 5: If any chunk is still over budget, sub-split it
        final_chunks = []
        for chunk in detail_chunks:
            if chunk.token_count > self.max_chunk_tokens:
                sub_chunks = self._sub_split_chunk(chunk)
                final_chunks.extend(sub_chunks)
            else:
                final_chunks.append(chunk)

        return ChunkPlan(
            file_path=file_path,
            needs_chunking=True,
            total_tokens=total_tokens,
            skeleton=skeleton_chunk,
            chunks=final_chunks,
        )

    # ── Skeleton extraction ──────────────────────────────────────

    def _extract_skeleton(self, lines: List[str], ext: str) -> str:
        """
        Extract the skeleton of a file:
        - All import/require/use statements
        - All class/function/method signatures (no bodies)
        - All docstrings/comments on signatures
        - All export statements
        - Module-level constants and type definitions
        """
        skeleton_lines = []
        in_body = False
        brace_depth = 0
        indent_depth = 0  # For Python
        skip_until_dedent = False
        current_indent = 0

        for i, line in enumerate(lines):
            stripped = line.strip()

            # Always include blank lines between sections
            if not stripped:
                if skeleton_lines and skeleton_lines[-1].strip():
                    skeleton_lines.append("")
                continue

            # Always include imports
            if self._is_import_line(stripped, ext):
                skeleton_lines.append(line)
                continue

            # Always include comments and docstrings at top level
            if self._is_comment(stripped, ext) and not in_body:
                skeleton_lines.append(line)
                continue

            # Detect signatures
            if self._is_signature(stripped, ext):
                skeleton_lines.append(line)

                # Add the docstring/comment immediately after the signature
                for j in range(i + 1, min(i + 10, len(lines))):
                    next_stripped = lines[j].strip()
                    if self._is_docstring_start(next_stripped, ext):
                        # Capture entire docstring
                        skeleton_lines.append(lines[j])
                        if not self._is_docstring_end(next_stripped, ext):
                            for k in range(j + 1, min(j + 20, len(lines))):
                                skeleton_lines.append(lines[k])
                                if self._is_docstring_end(lines[k].strip(), ext):
                                    break
                        break
                    elif next_stripped.startswith('//') or next_stripped.startswith('#'):
                        skeleton_lines.append(lines[j])
                    else:
                        break

                skeleton_lines.append(f"    # ... (body omitted, {self._count_body_lines(lines, i, ext)} lines)")
                skeleton_lines.append("")
                continue

            # Always include decorators/annotations
            if self._is_decorator(stripped, ext):
                skeleton_lines.append(line)
                continue

            # Always include exports
            if self._is_export(stripped, ext):
                skeleton_lines.append(line)
                continue

            # Always include type definitions and constants
            if self._is_type_or_const(stripped, ext):
                skeleton_lines.append(line)
                continue

        return '\n'.join(skeleton_lines)

    # ── Boundary detection ───────────────────────────────────────

    def _find_boundaries(self, lines: List[str], ext: str) -> List[Dict]:
        """
        Find structural boundaries in a file.
        Returns list of {start_line, end_line, label, symbols}.
        """
        boundaries = []

        if ext in ('.py',):
            boundaries = self._find_python_boundaries(lines)
        elif ext in ('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs'):
            boundaries = self._find_js_boundaries(lines)
        elif ext in ('.dart',):
            boundaries = self._find_dart_boundaries(lines)
        elif ext in ('.java', '.kt', '.kts', '.scala'):
            boundaries = self._find_java_boundaries(lines)
        elif ext in ('.go',):
            boundaries = self._find_go_boundaries(lines)
        elif ext in ('.rs',):
            boundaries = self._find_rust_boundaries(lines)
        elif ext in ('.rb',):
            boundaries = self._find_ruby_boundaries(lines)
        else:
            boundaries = self._find_generic_boundaries(lines)

        # Filter out very small boundaries (less than 5 lines)
        boundaries = [b for b in boundaries if b['end_line'] - b['start_line'] >= 5]

        return boundaries

    def _find_python_boundaries(self, lines: List[str]) -> List[Dict]:
        """Find class and top-level function boundaries in Python."""
        boundaries = []
        current = None

        for i, line in enumerate(lines):
            stripped = line.strip()
            indent = len(line) - len(line.lstrip()) if stripped else 999

            # Top-level class or function
            if indent == 0 and (
                stripped.startswith('class ') or
                stripped.startswith('def ') or
                stripped.startswith('async def ')
            ):
                if current:
                    current['end_line'] = i
                    boundaries.append(current)

                # Extract name
                match = re.match(r'(?:async\s+)?(?:class|def)\s+(\w+)', stripped)
                name = match.group(1) if match else 'unknown'
                kind = 'class' if stripped.startswith('class') else 'function'

                current = {
                    'start_line': i,
                    'end_line': len(lines),
                    'label': f"{kind} {name}",
                    'symbols': [name],
                }

        if current:
            current['end_line'] = len(lines)
            boundaries.append(current)

        return boundaries

    def _find_js_boundaries(self, lines: List[str]) -> List[Dict]:
        """Find class, function, and component boundaries in JS/TS."""
        boundaries = []
        current = None
        brace_depth = 0

        patterns = [
            (r'^\s*(?:export\s+)?class\s+(\w+)', 'class'),
            (r'^\s*(?:export\s+)?(?:default\s+)?function\s+(\w+)', 'function'),
            (r'^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:\(|function)', 'function'),
            (r'^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*\(', 'component'),
            (r'^\s*(?:export\s+)?(?:const|let|var)\s+(\w+):\s*React\.FC', 'component'),
        ]

        for i, line in enumerate(lines):
            stripped = line.strip()

            for pattern, kind in patterns:
                match = re.match(pattern, stripped)
                if match:
                    if current and brace_depth == 0:
                        current['end_line'] = i
                        boundaries.append(current)

                    name = match.group(1)
                    current = {
                        'start_line': i,
                        'end_line': len(lines),
                        'label': f"{kind} {name}",
                        'symbols': [name],
                    }
                    break

            # Track braces for knowing when a block ends
            brace_depth += stripped.count('{') - stripped.count('}')

        if current:
            current['end_line'] = len(lines)
            boundaries.append(current)

        return boundaries

    def _find_dart_boundaries(self, lines: List[str]) -> List[Dict]:
        """Find class boundaries in Dart."""
        boundaries = []
        current = None

        for i, line in enumerate(lines):
            stripped = line.strip()
            match = re.match(r'(?:abstract\s+)?class\s+(\w+)', stripped)
            if match:
                if current:
                    current['end_line'] = i
                    boundaries.append(current)
                current = {
                    'start_line': i,
                    'end_line': len(lines),
                    'label': f"class {match.group(1)}",
                    'symbols': [match.group(1)],
                }

            # Also catch top-level functions
            if not current or (current and i > current.get('end_line', 0)):
                func_match = re.match(
                    r'\w[\w<>\?\s]*\s+(\w+)\s*\(', stripped
                )
                if func_match and not stripped.startswith('//'):
                    if current:
                        current['end_line'] = i
                        boundaries.append(current)
                    current = {
                        'start_line': i,
                        'end_line': len(lines),
                        'label': f"function {func_match.group(1)}",
                        'symbols': [func_match.group(1)],
                    }

        if current:
            current['end_line'] = len(lines)
            boundaries.append(current)

        return boundaries

    def _find_java_boundaries(self, lines: List[str]) -> List[Dict]:
        """Find class/interface boundaries in Java/Kotlin."""
        boundaries = []
        current = None

        for i, line in enumerate(lines):
            stripped = line.strip()
            match = re.match(
                r'(?:public|private|protected|internal|abstract|final|open|data)?\s*'
                r'(?:class|interface|enum|object|sealed\s+class)\s+(\w+)',
                stripped
            )
            if match:
                if current:
                    current['end_line'] = i
                    boundaries.append(current)
                current = {
                    'start_line': i,
                    'end_line': len(lines),
                    'label': f"class {match.group(1)}",
                    'symbols': [match.group(1)],
                }

        if current:
            current['end_line'] = len(lines)
            boundaries.append(current)

        return boundaries

    def _find_go_boundaries(self, lines: List[str]) -> List[Dict]:
        """Find func/type boundaries in Go."""
        boundaries = []
        current = None

        for i, line in enumerate(lines):
            stripped = line.strip()

            # Functions
            func_match = re.match(r'func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(', stripped)
            if func_match:
                if current:
                    current['end_line'] = i
                    boundaries.append(current)
                current = {
                    'start_line': i,
                    'end_line': len(lines),
                    'label': f"func {func_match.group(1)}",
                    'symbols': [func_match.group(1)],
                }
                continue

            # Type definitions
            type_match = re.match(r'type\s+(\w+)\s+struct', stripped)
            if type_match:
                if current:
                    current['end_line'] = i
                    boundaries.append(current)
                current = {
                    'start_line': i,
                    'end_line': len(lines),
                    'label': f"type {type_match.group(1)}",
                    'symbols': [type_match.group(1)],
                }

        if current:
            current['end_line'] = len(lines)
            boundaries.append(current)

        return boundaries

    def _find_rust_boundaries(self, lines: List[str]) -> List[Dict]:
        """Find impl/fn/struct boundaries in Rust."""
        boundaries = []
        current = None

        for i, line in enumerate(lines):
            stripped = line.strip()

            for pattern, kind in [
                (r'impl(?:<[^>]+>)?\s+(\w+)', 'impl'),
                (r'pub\s+(?:async\s+)?fn\s+(\w+)', 'fn'),
                (r'fn\s+(\w+)', 'fn'),
                (r'(?:pub\s+)?struct\s+(\w+)', 'struct'),
                (r'(?:pub\s+)?enum\s+(\w+)', 'enum'),
                (r'(?:pub\s+)?trait\s+(\w+)', 'trait'),
            ]:
                match = re.match(pattern, stripped)
                if match:
                    if current:
                        current['end_line'] = i
                        boundaries.append(current)
                    current = {
                        'start_line': i,
                        'end_line': len(lines),
                        'label': f"{kind} {match.group(1)}",
                        'symbols': [match.group(1)],
                    }
                    break

        if current:
            current['end_line'] = len(lines)
            boundaries.append(current)

        return boundaries

    def _find_ruby_boundaries(self, lines: List[str]) -> List[Dict]:
        """Find class/module/def boundaries in Ruby."""
        boundaries = []
        current = None

        for i, line in enumerate(lines):
            stripped = line.strip()
            indent = len(line) - len(line.lstrip()) if stripped else 999

            if indent == 0 and (
                stripped.startswith('class ') or
                stripped.startswith('module ') or
                stripped.startswith('def ')
            ):
                if current:
                    current['end_line'] = i
                    boundaries.append(current)

                match = re.match(r'(?:class|module|def)\s+(\w+)', stripped)
                name = match.group(1) if match else 'unknown'
                current = {
                    'start_line': i,
                    'end_line': len(lines),
                    'label': f"{stripped.split()[0]} {name}",
                    'symbols': [name],
                }

        if current:
            current['end_line'] = len(lines)
            boundaries.append(current)

        return boundaries

    def _find_generic_boundaries(self, lines: List[str]) -> List[Dict]:
        """
        Fallback: split on clusters of blank lines.
        Two or more consecutive blank lines = section break.
        """
        boundaries = []
        current_start = 0
        blank_count = 0

        for i, line in enumerate(lines):
            if not line.strip():
                blank_count += 1
            else:
                if blank_count >= 2 and i - current_start > 10:
                    boundaries.append({
                        'start_line': current_start,
                        'end_line': i - blank_count,
                        'label': f"section (lines {current_start+1}-{i-blank_count+1})",
                        'symbols': [],
                    })
                    current_start = i
                blank_count = 0

        # Last section
        if current_start < len(lines):
            boundaries.append({
                'start_line': current_start,
                'end_line': len(lines),
                'label': f"section (lines {current_start+1}-{len(lines)})",
                'symbols': [],
            })

        return boundaries

    # ── Splitting ────────────────────────────────────────────────

    def _split_on_boundaries(
        self, lines: List[str], boundaries: List[Dict]
    ) -> List[FileChunk]:
        """Create chunks from detected structural boundaries."""
        chunks = []

        # Capture everything before the first boundary as a preamble
        if boundaries and boundaries[0]['start_line'] > 0:
            preamble = '\n'.join(lines[:boundaries[0]['start_line']])
            if preamble.strip():
                chunks.append(FileChunk(
                    chunk_type=ChunkType.DETAIL,
                    content=preamble,
                    label="preamble (imports, constants)",
                    start_line=0,
                    end_line=boundaries[0]['start_line'],
                    symbols=[],
                ))

        for boundary in boundaries:
            content = '\n'.join(
                lines[boundary['start_line']:boundary['end_line']]
            )
            chunks.append(FileChunk(
                chunk_type=ChunkType.DETAIL,
                content=content,
                label=boundary['label'],
                start_line=boundary['start_line'],
                end_line=boundary['end_line'],
                symbols=boundary.get('symbols', []),
            ))

        return chunks

    def _split_by_tokens(self, lines: List[str]) -> List[FileChunk]:
        """
        Fallback: split by token budget when no structural boundaries found.
        Tries to break at blank lines.
        """
        chunks = []
        current_lines = []
        current_start = 0
        current_tokens = 0
        chunk_num = 0

        for i, line in enumerate(lines):
            line_tokens = self.count_tokens(line)
            current_lines.append(line)
            current_tokens += line_tokens

            if current_tokens >= self.max_chunk_tokens:
                # Try to break at a nearby blank line
                break_at = len(current_lines)
                for j in range(len(current_lines) - 1, max(0, len(current_lines) - 20), -1):
                    if not current_lines[j].strip():
                        break_at = j + 1
                        break

                chunk_num += 1
                content = '\n'.join(current_lines[:break_at])
                chunks.append(FileChunk(
                    chunk_type=ChunkType.DETAIL,
                    content=content,
                    label=f"chunk {chunk_num} (lines {current_start+1}-{current_start+break_at})",
                    start_line=current_start,
                    end_line=current_start + break_at,
                ))

                # Carry over remaining lines
                remaining = current_lines[break_at:]
                current_lines = remaining
                current_start = current_start + break_at
                current_tokens = self.count_tokens('\n'.join(remaining))

        # Last chunk
        if current_lines:
            chunk_num += 1
            content = '\n'.join(current_lines)
            chunks.append(FileChunk(
                chunk_type=ChunkType.DETAIL,
                content=content,
                label=f"chunk {chunk_num} (lines {current_start+1}-{current_start+len(current_lines)})",
                start_line=current_start,
                end_line=current_start + len(current_lines),
            ))

        return chunks

    def _add_overlap(
        self, chunks: List[FileChunk], lines: List[str]
    ) -> List[FileChunk]:
        """Add overlap between consecutive chunks for context continuity."""
        if len(chunks) <= 1:
            return chunks

        for i in range(1, len(chunks)):
            prev_chunk = chunks[i - 1]
            curr_chunk = chunks[i]

            # Calculate overlap size (10% of previous chunk's lines)
            prev_lines = prev_chunk.content.split('\n')
            overlap_lines = max(5, int(len(prev_lines) * self.overlap_ratio))
            overlap_lines = min(overlap_lines, 30)  # Cap at 30 lines

            # Take last N lines from previous chunk
            overlap_content = prev_lines[-overlap_lines:]
            overlap_text = '\n'.join(overlap_content)

            # Prepend to current chunk with a marker
            curr_chunk.content = (
                f"# ... (overlapping context from previous section)\n"
                f"{overlap_text}\n"
                f"# ... (end overlap — new content below)\n\n"
                f"{curr_chunk.content}"
            )
            curr_chunk.start_line = max(0, curr_chunk.start_line - overlap_lines)

        return chunks

    def _sub_split_chunk(self, chunk: FileChunk) -> List[FileChunk]:
        """Split an oversized chunk into smaller pieces."""
        lines = chunk.content.split('\n')
        sub_chunks = self._split_by_tokens(lines)

        # Relabel
        for i, sc in enumerate(sub_chunks):
            sc.label = f"{chunk.label} (part {i+1}/{len(sub_chunks)})"
            sc.start_line += chunk.start_line
            sc.end_line += chunk.start_line

        return sub_chunks

    # ── Helper methods ───────────────────────────────────────────

    def _is_import_line(self, line: str, ext: str) -> bool:
        if ext in ('.py',):
            return line.startswith('import ') or line.startswith('from ')
        elif ext in ('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs'):
            return (
                line.startswith('import ') or
                'require(' in line or
                line.startswith('const ') and 'require(' in line
            )
        elif ext in ('.dart',):
            return line.startswith('import ') or line.startswith("import '")
        elif ext in ('.java', '.kt', '.kts', '.scala'):
            return line.startswith('import ') or line.startswith('package ')
        elif ext in ('.go',):
            return line.startswith('import ') or line == 'import ('
        elif ext in ('.rs',):
            return line.startswith('use ') or line.startswith('extern crate')
        elif ext in ('.rb',):
            return line.startswith('require ') or line.startswith("require '")
        return False

    def _is_signature(self, line: str, ext: str) -> bool:
        if ext in ('.py',):
            return (
                line.startswith('def ') or
                line.startswith('async def ') or
                line.startswith('class ')
            )
        elif ext in ('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs'):
            return bool(re.match(
                r'(?:export\s+)?(?:default\s+)?'
                r'(?:async\s+)?(?:function\*?\s+\w+|class\s+\w+)',
                line
            )) or bool(re.match(
                r'(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?\(',
                line
            ))
        elif ext in ('.dart',):
            return bool(re.match(
                r'(?:abstract\s+)?class\s+\w+', line
            )) or bool(re.match(
                r'(?:static\s+)?(?:Future|Stream|void|int|String|bool|double|List|Map|Set|dynamic|\w+)\s*<?\w*>?\s+\w+\s*\(',
                line
            ))
        elif ext in ('.java', '.kt', '.kts'):
            return bool(re.match(
                r'(?:public|private|protected|internal|abstract|static|final|open|override|suspend)?\s*'
                r'(?:class|interface|enum|fun|object|sealed\s+class)\s+\w+',
                line
            ))
        elif ext in ('.go',):
            return line.startswith('func ') or line.startswith('type ')
        elif ext in ('.rs',):
            return bool(re.match(
                r'(?:pub\s+)?(?:async\s+)?(?:fn|struct|enum|trait|impl|type)\s+',
                line
            ))
        elif ext in ('.rb',):
            return (
                line.startswith('def ') or
                line.startswith('class ') or
                line.startswith('module ')
            )
        return False

    def _is_comment(self, line: str, ext: str) -> bool:
        if ext in ('.py', '.rb'):
            return line.startswith('#')
        elif ext in ('.js', '.jsx', '.ts', '.tsx', '.dart', '.java', '.kt',
                     '.go', '.rs', '.scala', '.swift'):
            return line.startswith('//') or line.startswith('/*') or line.startswith('*')
        return False

    def _is_docstring_start(self, line: str, ext: str) -> bool:
        if ext in ('.py',):
            return line.startswith('"""') or line.startswith("'''")
        elif ext in ('.js', '.jsx', '.ts', '.tsx', '.java', '.kt', '.dart'):
            return line.startswith('/**') or line.startswith('///')
        return False

    def _is_docstring_end(self, line: str, ext: str) -> bool:
        if ext in ('.py',):
            return line.endswith('"""') or line.endswith("'''")
        elif ext in ('.js', '.jsx', '.ts', '.tsx', '.java', '.kt', '.dart'):
            return line.endswith('*/') or (line.startswith('///') and not line.endswith('///'))
        return True

    def _is_decorator(self, line: str, ext: str) -> bool:
        if ext in ('.py',):
            return line.startswith('@')
        elif ext in ('.java', '.kt', '.kts'):
            return line.startswith('@')
        elif ext in ('.ts', '.tsx'):
            return line.startswith('@')
        elif ext in ('.dart',):
            return line.startswith('@')
        return False

    def _is_export(self, line: str, ext: str) -> bool:
        if ext in ('.js', '.jsx', '.ts', '.tsx', '.mjs'):
            return (
                line.startswith('export ') or
                line.startswith('module.exports') or
                line.startswith('exports.')
            )
        elif ext in ('.dart',):
            return line.startswith('export ')
        elif ext in ('.go',):
            # In Go, exported = starts with uppercase
            return False  # Handled differently
        return False

    def _is_type_or_const(self, line: str, ext: str) -> bool:
        if ext in ('.ts', '.tsx'):
            return bool(re.match(
                r'(?:export\s+)?(?:type|interface|enum|const)\s+\w+', line
            ))
        elif ext in ('.py',):
            return bool(re.match(r'^[A-Z_][A-Z_0-9]*\s*[:=]', line))
        elif ext in ('.go',):
            return bool(re.match(r'(?:const|var)\s+', line))
        elif ext in ('.rs',):
            return bool(re.match(r'(?:pub\s+)?(?:const|static)\s+', line))
        return False

    def _count_body_lines(self, lines: List[str], signature_line: int, ext: str) -> int:
        """Count lines in the body of a function/class starting from signature."""
        if ext in ('.py', '.rb'):
            # Count by indentation
            if signature_line + 1 >= len(lines):
                return 0
            base_indent = len(lines[signature_line]) - len(lines[signature_line].lstrip())
            count = 0
            for i in range(signature_line + 1, len(lines)):
                line = lines[i]
                if line.strip():
                    indent = len(line) - len(line.lstrip())
                    if indent <= base_indent:
                        break
                count += 1
            return count
        else:
            # Count by braces
            brace_depth = 0
            started = False
            count = 0
            for i in range(signature_line, len(lines)):
                line = lines[i]
                brace_depth += line.count('{') - line.count('}')
                if '{' in line:
                    started = True
                if started:
                    count += 1
                    if brace_depth <= 0:
                        break
            return count