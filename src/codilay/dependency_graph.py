"""
Static dependency analyzer — builds a DAG from import/require statements
without any LLM calls. Used to determine which files can safely run in
parallel vs. which must be sequential.
"""

import os
import re
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Set, Tuple


class DependencyGraph:
    """
    Builds a file-level dependency DAG from static import analysis.

    The graph tracks two relationships:
    - depends_on[A] = {B, C}  → A imports from B and C
    - depended_by[A] = {D}    → D imports from A

    From this we derive processing tiers: files with no unprocessed
    dependencies can run in parallel within the same tier.
    """

    def __init__(self, project_root: str, all_files: List[str]):
        self.project_root = project_root
        self.all_files = set(all_files)
        # Normalized lookup: basename -> [full relative paths]
        self._file_index = self._build_file_index(all_files)

        # The core graph
        self.depends_on: Dict[str, Set[str]] = defaultdict(set)  # file -> files it imports
        self.depended_by: Dict[str, Set[str]] = defaultdict(set)  # file -> files that import it

    # ── Public API ───────────────────────────────────────────────

    def build(self, file_contents: Dict[str, str]) -> "DependencyGraph":
        """
        Analyze all files and build the dependency graph.

        Args:
            file_contents: dict of relative_path -> file content string
        """
        for file_path, content in file_contents.items():
            if file_path not in self.all_files:
                continue
            imports = self._extract_imports(file_path, content)
            resolved = self._resolve_imports(file_path, imports)
            for dep in resolved:
                self.depends_on[file_path].add(dep)
                self.depended_by[dep].add(file_path)
        return self

    def get_tiers(self) -> List[List[str]]:
        """
        Compute processing tiers using topological sort (Kahn's algorithm).

        Tier 0: files with no internal dependencies (leaf nodes / entry points)
        Tier 1: files whose dependencies are all in tier 0
        Tier N: files whose dependencies are all in tiers < N

        Files within the same tier can safely run in parallel.
        Files in cycles are placed in a final "cycle" tier together (sequential).

        Returns:
            List of tiers, each tier is a list of file paths.
        """
        # Build in-degree map (only for files in our set)
        in_degree: Dict[str, int] = {}
        for f in self.all_files:
            deps_in_project = self.depends_on.get(f, set()) & self.all_files
            in_degree[f] = len(deps_in_project)

        # Kahn's algorithm with tier tracking
        tiers: List[List[str]] = []
        remaining = set(self.all_files)

        # Tier 0: all files with in_degree 0
        current_tier = [f for f in remaining if in_degree.get(f, 0) == 0]

        while current_tier:
            # Sort for deterministic ordering within tier
            current_tier.sort()
            tiers.append(current_tier)
            remaining -= set(current_tier)

            # Reduce in-degrees
            next_tier = []
            for f in current_tier:
                for dependent in self.depended_by.get(f, set()):
                    if dependent in remaining:
                        in_degree[dependent] -= 1
                        if in_degree[dependent] <= 0:
                            next_tier.append(dependent)

            current_tier = next_tier

        # Any remaining files are in cycles — put them in a final tier
        if remaining:
            cycle_tier = sorted(remaining)
            tiers.append(cycle_tier)

        return tiers

    def get_dependency_clusters(self) -> List[Set[str]]:
        """
        Find connected components in the dependency graph.
        Files in different clusters have zero dependency relationship
        and can always be processed independently.
        """
        visited: Set[str] = set()
        clusters: List[Set[str]] = []

        for f in self.all_files:
            if f in visited:
                continue
            # BFS from this node
            cluster: Set[str] = set()
            queue = deque([f])
            while queue:
                node = queue.popleft()
                if node in visited:
                    continue
                visited.add(node)
                cluster.add(node)
                # Add both directions (undirected connectivity)
                for neighbor in self.depends_on.get(node, set()):
                    if neighbor in self.all_files and neighbor not in visited:
                        queue.append(neighbor)
                for neighbor in self.depended_by.get(node, set()):
                    if neighbor in self.all_files and neighbor not in visited:
                        queue.append(neighbor)
            if cluster:
                clusters.append(cluster)

        return clusters

    def get_parallel_groups(self, tier: List[str]) -> List[List[str]]:
        """
        Within a single tier, further subdivide into groups that share
        no wire targets. Files in the same group might write to the
        same docstore sections (via wires) so they need extra care.

        Files that share zero dependency edges with each other are
        placed in separate groups that can truly run independently.

        Returns: List of groups, each group is a list of files.
        """
        # Within a tier, all in-degree deps are already satisfied.
        # But files in the same tier might depend on each other's outputs
        # via shared targets (e.g., both import the same utility).
        # Group files that share common dependencies.

        if len(tier) <= 1:
            return [tier]

        # Build adjacency within the tier based on shared dependencies
        tier_set = set(tier)
        shared: Dict[str, Set[str]] = defaultdict(set)

        for f in tier:
            # Files that share a common dependency target
            for dep in self.depends_on.get(f, set()):
                for other in self.depended_by.get(dep, set()):
                    if other in tier_set and other != f:
                        shared[f].add(other)

            # Files that share a common dependent (both imported by same file)
            for dependent in self.depended_by.get(f, set()):
                for other_dep in self.depends_on.get(dependent, set()):
                    if other_dep in tier_set and other_dep != f:
                        shared[f].add(other_dep)

        # Connected components within the tier
        visited: Set[str] = set()
        groups: List[List[str]] = []

        for f in tier:
            if f in visited:
                continue
            group: List[str] = []
            queue = deque([f])
            while queue:
                node = queue.popleft()
                if node in visited:
                    continue
                visited.add(node)
                group.append(node)
                for neighbor in shared.get(node, set()):
                    if neighbor not in visited:
                        queue.append(neighbor)
            group.sort()
            groups.append(group)

        return groups

    def get_files_affected_by(self, file_path: str) -> Set[str]:
        """Get all files that transitively depend on a given file."""
        affected: Set[str] = set()
        queue = deque([file_path])
        while queue:
            node = queue.popleft()
            for dep in self.depended_by.get(node, set()):
                if dep not in affected:
                    affected.add(dep)
                    queue.append(dep)
        return affected

    def get_stats(self) -> Dict[str, Any]:
        """Return statistics about the dependency graph."""
        tiers = self.get_tiers()
        clusters = self.get_dependency_clusters()
        total_edges = sum(len(deps) for deps in self.depends_on.values())

        return {
            "total_files": len(self.all_files),
            "total_edges": total_edges,
            "num_tiers": len(tiers),
            "tier_sizes": [len(t) for t in tiers],
            "num_clusters": len(clusters),
            "cluster_sizes": sorted([len(c) for c in clusters], reverse=True),
            "max_parallelism": max(len(t) for t in tiers) if tiers else 0,
            "isolated_files": len(
                [f for f in self.all_files if not self.depends_on.get(f) and not self.depended_by.get(f)]
            ),
        }

    # ── Import extraction ────────────────────────────────────────

    def _extract_imports(self, file_path: str, content: str) -> List[str]:
        """
        Extract raw import strings from file content.
        Supports Python, JS/TS, Go, Rust, Java, C/C++, Ruby, PHP.
        """
        ext = os.path.splitext(file_path)[1].lower()
        imports: List[str] = []

        if ext in (".py",):
            imports.extend(self._extract_python_imports(content))
        elif ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte"):
            imports.extend(self._extract_js_imports(content))
        elif ext in (".go",):
            imports.extend(self._extract_go_imports(content))
        elif ext in (".rs",):
            imports.extend(self._extract_rust_imports(content))
        elif ext in (".java", ".kt", ".kts", ".scala"):
            imports.extend(self._extract_java_imports(content))
        elif ext in (".c", ".h", ".cpp", ".hpp", ".cc", ".cxx"):
            imports.extend(self._extract_c_imports(content))
        elif ext in (".rb", ".rake"):
            imports.extend(self._extract_ruby_imports(content))
        elif ext in (".php",):
            imports.extend(self._extract_php_imports(content))
        elif ext in (".ex", ".exs"):
            imports.extend(self._extract_elixir_imports(content))

        return imports

    def _extract_python_imports(self, content: str) -> List[str]:
        imports = []
        # from foo.bar import baz / from . import foo / from ..foo import bar
        for m in re.finditer(r"^\s*from\s+(\.{0,3}[\w.]*)\s+import", content, re.MULTILINE):
            imports.append(m.group(1))
        # import foo / import foo.bar / import foo, bar
        for m in re.finditer(r"^\s*import\s+([\w.,\s]+)", content, re.MULTILINE):
            for part in m.group(1).split(","):
                mod = part.strip().split()[0]  # handle 'foo as bar'
                if mod:
                    imports.append(mod)
        return imports

    def _extract_js_imports(self, content: str) -> List[str]:
        imports = []
        # import ... from 'path' / import 'path'
        for m in re.finditer(r"""(?:import\s+.*?from\s+|import\s+)['"]([^'"]+)['"]""", content):
            imports.append(m.group(1))
        # require('path')
        for m in re.finditer(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""", content):
            imports.append(m.group(1))
        # Dynamic import('path')
        for m in re.finditer(r"""import\s*\(\s*['"]([^'"]+)['"]\s*\)""", content):
            imports.append(m.group(1))
        # export ... from 'path'
        for m in re.finditer(r"""export\s+.*?from\s+['"]([^'"]+)['"]""", content):
            imports.append(m.group(1))
        return imports

    def _extract_go_imports(self, content: str) -> List[str]:
        imports = []
        # Single import
        for m in re.finditer(r'^\s*import\s+"([^"]+)"', content, re.MULTILINE):
            imports.append(m.group(1))
        # Grouped imports
        for m in re.finditer(r"import\s*\((.*?)\)", content, re.DOTALL):
            for line in m.group(1).split("\n"):
                line = line.strip().strip('"')
                if line and not line.startswith("//"):
                    # Strip alias
                    parts = line.split()
                    if parts:
                        imports.append(parts[-1].strip('"'))
        return imports

    def _extract_rust_imports(self, content: str) -> List[str]:
        imports = []
        for m in re.finditer(r"^\s*(?:use|mod)\s+([\w:]+)", content, re.MULTILINE):
            imports.append(m.group(1))
        return imports

    def _extract_java_imports(self, content: str) -> List[str]:
        imports = []
        for m in re.finditer(r"^\s*import\s+(?:static\s+)?([\w.]+)", content, re.MULTILINE):
            imports.append(m.group(1))
        return imports

    def _extract_c_imports(self, content: str) -> List[str]:
        imports = []
        for m in re.finditer(r'^\s*#\s*include\s*["<]([^">]+)[">]', content, re.MULTILINE):
            imports.append(m.group(1))
        return imports

    def _extract_ruby_imports(self, content: str) -> List[str]:
        imports = []
        for m in re.finditer(r"""^\s*require(?:_relative)?\s+['"]([^'"]+)['"]""", content, re.MULTILINE):
            imports.append(m.group(1))
        return imports

    def _extract_php_imports(self, content: str) -> List[str]:
        imports = []
        for m in re.finditer(
            r"""^\s*(?:use|require|require_once|include|include_once)\s+['"]?([^\s;'"]+)""", content, re.MULTILINE
        ):
            imports.append(m.group(1))
        return imports

    def _extract_elixir_imports(self, content: str) -> List[str]:
        imports = []
        for m in re.finditer(r"^\s*(?:import|alias|use)\s+([\w.]+)", content, re.MULTILINE):
            imports.append(m.group(1))
        return imports

    # ── Import resolution ────────────────────────────────────────

    def _resolve_imports(self, source_file: str, raw_imports: List[str]) -> Set[str]:
        """
        Resolve raw import strings to actual files in the project.
        Only returns files that exist in self.all_files.
        """
        resolved: Set[str] = set()
        source_dir = os.path.dirname(source_file)
        source_ext = os.path.splitext(source_file)[1].lower()

        for imp in raw_imports:
            match = self._resolve_single_import(imp, source_dir, source_ext)
            if match and match != source_file:
                resolved.add(match)

        return resolved

    def _resolve_single_import(self, imp: str, source_dir: str, source_ext: str) -> Optional[str]:
        """Try to resolve a single import string to a project file."""

        # 1. Relative imports (./foo, ../bar, .foo for Python)
        if imp.startswith("."):
            return self._resolve_relative(imp, source_dir, source_ext)

        # 2. Try as a direct path
        direct = self._try_path_variants(imp, source_ext)
        if direct:
            return direct

        # 3. Module-style (foo.bar.baz -> foo/bar/baz)
        as_path = imp.replace(".", "/").replace("::", "/")
        result = self._try_path_variants(as_path, source_ext)
        if result:
            return result

        # 4. Try matching by basename
        basename = imp.split("/")[-1].split(".")[-1]
        candidates = self._file_index.get(basename.lower(), [])
        if len(candidates) == 1:
            return candidates[0]

        return None

    def _resolve_relative(self, imp: str, source_dir: str, source_ext: str) -> Optional[str]:
        """Resolve a relative import like ./foo or ../bar."""
        # Python relative: .foo -> current package, ..foo -> parent package
        if not imp.startswith("./") and not imp.startswith("../"):
            # Python-style relative: .foo or ..foo
            dots = 0
            for c in imp:
                if c == ".":
                    dots += 1
                else:
                    break
            module_part = imp[dots:]
            # Go up `dots - 1` directories
            base_dir = source_dir
            for _ in range(dots - 1):
                base_dir = os.path.dirname(base_dir)
            if module_part:
                rel_path = os.path.join(base_dir, module_part.replace(".", "/"))
            else:
                rel_path = base_dir
        else:
            # JS/TS style: ./foo or ../foo
            rel_path = os.path.normpath(os.path.join(source_dir, imp))

        rel_path = rel_path.replace(os.sep, "/")

        return self._try_path_variants(rel_path, source_ext)

    def _try_path_variants(self, base_path: str, source_ext: str) -> Optional[str]:
        """Try a path with various extension variants."""
        base_path = base_path.replace(os.sep, "/")

        # Exact match
        if base_path in self.all_files:
            return base_path

        # Extension variants ordered by relevance to source
        ext_variants = self._get_ext_variants(source_ext)

        for ext in ext_variants:
            candidate = base_path + ext
            if candidate in self.all_files:
                return candidate

        # Index file variants (foo/ -> foo/index.ext)
        for ext in ext_variants:
            candidate = base_path + "/index" + ext
            if candidate in self.all_files:
                return candidate

        # __init__.py for Python
        init = base_path + "/__init__.py"
        if init in self.all_files:
            return init

        # mod.rs for Rust
        mod_rs = base_path + "/mod.rs"
        if mod_rs in self.all_files:
            return mod_rs

        return None

    def _get_ext_variants(self, source_ext: str) -> List[str]:
        """Get file extension variants to try, ordered by likelihood."""
        # Common extension families
        JS_EXTS = [".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"]
        PY_EXTS = [".py"]
        GO_EXTS = [".go"]
        RUST_EXTS = [".rs"]
        JAVA_EXTS = [".java", ".kt", ".scala"]
        C_EXTS = [".h", ".hpp", ".c", ".cpp", ".cc", ".cxx"]
        RUBY_EXTS = [".rb"]

        ext_map = {
            ".py": PY_EXTS,
            ".js": JS_EXTS,
            ".jsx": JS_EXTS,
            ".ts": JS_EXTS,
            ".tsx": JS_EXTS,
            ".mjs": JS_EXTS,
            ".cjs": JS_EXTS,
            ".vue": JS_EXTS,
            ".svelte": JS_EXTS,
            ".go": GO_EXTS,
            ".rs": RUST_EXTS,
            ".java": JAVA_EXTS,
            ".kt": JAVA_EXTS,
            ".scala": JAVA_EXTS,
            ".c": C_EXTS,
            ".h": C_EXTS,
            ".cpp": C_EXTS,
            ".hpp": C_EXTS,
            ".rb": RUBY_EXTS,
            ".rake": RUBY_EXTS,
        }

        primary = ext_map.get(source_ext, [])
        # Always include .json, .yaml etc. as fallbacks
        return primary + [e for e in [".json", ".yaml", ".yml", ".toml"] if e not in primary]

    # ── File index ───────────────────────────────────────────────

    def _build_file_index(self, files: List[str]) -> Dict[str, List[str]]:
        """Build a basename -> [paths] index for fuzzy resolution."""
        index: Dict[str, List[str]] = defaultdict(list)
        for f in files:
            basename = os.path.basename(f)
            name_no_ext = os.path.splitext(basename)[0].lower()
            index[name_no_ext].append(f)
            index[basename.lower()].append(f)
        return index
