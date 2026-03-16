from codilay.dependency_graph import DependencyGraph


# ── Python import extraction ─────────────────────────────────────────────────


def test_extract_python_imports():
    files = ["src/main.py", "src/utils.py", "src/config.py"]
    dg = DependencyGraph("/project", files)

    content = """
import os
from src.utils import helper
from src.config import Config
from collections import defaultdict
"""
    imports = dg._extract_python_imports(content)

    assert "os" in imports
    assert "src.utils" in imports
    assert "src.config" in imports
    assert "collections" in imports


def test_extract_python_relative_imports():
    files = ["src/main.py", "src/utils.py"]
    dg = DependencyGraph("/project", files)

    content = """
from . import utils
from .utils import helper
from ..config import Config
"""
    imports = dg._extract_python_imports(content)

    assert "." in imports
    assert ".utils" in imports
    assert "..config" in imports


def test_extract_python_import_as():
    files = ["src/main.py"]
    dg = DependencyGraph("/project", files)

    content = """
import numpy as np
from os.path import join as pjoin
"""
    imports = dg._extract_python_imports(content)

    assert "numpy" in imports
    assert "os.path" in imports


# ── JavaScript import extraction ─────────────────────────────────────────────


def test_extract_js_imports():
    files = ["src/app.js", "src/utils.js"]
    dg = DependencyGraph("/project", files)

    content = """
import React from 'react';
import { helper } from './utils';
const fs = require('fs');
export { default } from './components/Button';
"""
    imports = dg._extract_js_imports(content)

    assert "react" in imports
    assert "./utils" in imports
    assert "fs" in imports
    assert "./components/Button" in imports


def test_extract_js_dynamic_import():
    files = ["src/app.js"]
    dg = DependencyGraph("/project", files)

    content = """
const module = await import('./lazy-module');
"""
    imports = dg._extract_js_imports(content)

    assert "./lazy-module" in imports


# ── Go import extraction ─────────────────────────────────────────────────────


def test_extract_go_imports():
    files = ["main.go"]
    dg = DependencyGraph("/project", files)

    content = """
import "fmt"
import (
    "os"
    "strings"
    mylib "github.com/user/lib"
)
"""
    imports = dg._extract_go_imports(content)

    assert "fmt" in imports
    assert "os" in imports
    assert "strings" in imports
    assert "github.com/user/lib" in imports


# ── C/C++ include extraction ────────────────────────────────────────────────


def test_extract_c_includes():
    files = ["main.c", "utils.h"]
    dg = DependencyGraph("/project", files)

    content = """
#include <stdio.h>
#include "utils.h"
#include <stdlib.h>
"""
    imports = dg._extract_c_imports(content)

    assert "stdio.h" in imports
    assert "utils.h" in imports
    assert "stdlib.h" in imports


# ── Ruby import extraction ───────────────────────────────────────────────────


def test_extract_ruby_imports():
    files = ["app.rb"]
    dg = DependencyGraph("/project", files)

    content = """
require 'json'
require_relative 'helper'
"""
    imports = dg._extract_ruby_imports(content)

    assert "json" in imports
    assert "helper" in imports


# ── Import resolution ────────────────────────────────────────────────────────


def test_resolve_python_module_import():
    files = ["src/main.py", "src/utils.py", "src/config.py"]
    dg = DependencyGraph("/project", files)

    content = {
        "src/main.py": "from src.utils import helper\nfrom src.config import Config",
        "src/utils.py": "",
        "src/config.py": "",
    }
    dg.build(content)

    assert "src/utils.py" in dg.depends_on["src/main.py"]
    assert "src/config.py" in dg.depends_on["src/main.py"]


def test_resolve_js_relative_import():
    files = ["src/app.js", "src/utils.js"]
    dg = DependencyGraph("/project", files)

    content = {
        "src/app.js": "import { helper } from './utils';",
        "src/utils.js": "",
    }
    dg.build(content)

    assert "src/utils.js" in dg.depends_on["src/app.js"]


def test_resolve_python_relative_import():
    files = ["src/main.py", "src/utils.py"]
    dg = DependencyGraph("/project", files)

    content = {
        "src/main.py": "from .utils import helper",
        "src/utils.py": "",
    }
    dg.build(content)

    assert "src/utils.py" in dg.depends_on["src/main.py"]


def test_resolve_skips_external_imports():
    """Imports that don't match project files should be ignored."""
    files = ["src/main.py", "src/utils.py"]
    dg = DependencyGraph("/project", files)

    content = {
        "src/main.py": "import os\nimport json\nfrom src.utils import helper",
        "src/utils.py": "",
    }
    dg.build(content)

    # Only utils.py should be a dependency, not os or json
    deps = dg.depends_on["src/main.py"]
    assert "src/utils.py" in deps
    assert len(deps) == 1


def test_resolve_no_self_dependency():
    """A file should not depend on itself."""
    files = ["src/main.py"]
    dg = DependencyGraph("/project", files)

    content = {
        "src/main.py": "from src.main import something",
    }
    dg.build(content)

    assert "src/main.py" not in dg.depends_on.get("src/main.py", set())


# ── Tier computation ─────────────────────────────────────────────────────────


def test_tiers_no_dependencies():
    """All independent files go in tier 0."""
    files = ["a.py", "b.py", "c.py", "d.py"]
    dg = DependencyGraph("/project", files)
    dg.build({"a.py": "", "b.py": "", "c.py": "", "d.py": ""})

    tiers = dg.get_tiers()

    assert len(tiers) == 1
    assert set(tiers[0]) == {"a.py", "b.py", "c.py", "d.py"}


def test_tiers_linear_chain():
    """A -> B -> C produces 3 tiers."""
    files = ["a.py", "b.py", "c.py"]
    dg = DependencyGraph("/project", files)

    content = {
        "a.py": "from b import something",
        "b.py": "from c import something",
        "c.py": "",
    }
    dg.build(content)

    tiers = dg.get_tiers()

    assert len(tiers) == 3
    assert "c.py" in tiers[0]
    assert "b.py" in tiers[1]
    assert "a.py" in tiers[2]


def test_tiers_diamond_dependency():
    """
    Diamond: A depends on B and C, both depend on D.
    D -> tier 0, B/C -> tier 1, A -> tier 2.
    """
    files = ["a.py", "b.py", "c.py", "d.py"]
    dg = DependencyGraph("/project", files)

    content = {
        "a.py": "from b import x\nfrom c import y",
        "b.py": "from d import z",
        "c.py": "from d import w",
        "d.py": "",
    }
    dg.build(content)

    tiers = dg.get_tiers()

    assert len(tiers) == 3
    assert "d.py" in tiers[0]
    assert set(tiers[1]) == {"b.py", "c.py"}
    assert "a.py" in tiers[2]


def test_tiers_mixed_independent_and_dependent():
    """Mix of independent files and a dependency chain."""
    files = ["a.py", "b.py", "c.py", "standalone.py"]
    dg = DependencyGraph("/project", files)

    content = {
        "a.py": "from b import x",
        "b.py": "from c import y",
        "c.py": "",
        "standalone.py": "",
    }
    dg.build(content)

    tiers = dg.get_tiers()

    # c.py and standalone.py are in tier 0 (no deps)
    assert "c.py" in tiers[0]
    assert "standalone.py" in tiers[0]
    # b.py in tier 1
    assert "b.py" in tiers[1]
    # a.py in tier 2
    assert "a.py" in tiers[2]


def test_tiers_cycle_detection():
    """Files in a cycle should end up in a final cycle tier."""
    files = ["a.py", "b.py", "c.py"]
    dg = DependencyGraph("/project", files)

    # Create a cycle: a -> b -> c -> a
    # We'll manually set the graph edges since Python imports
    # might not create this exact scenario via text parsing
    dg.depends_on["a.py"] = {"b.py"}
    dg.depends_on["b.py"] = {"c.py"}
    dg.depends_on["c.py"] = {"a.py"}
    dg.depended_by["b.py"] = {"a.py"}
    dg.depended_by["c.py"] = {"b.py"}
    dg.depended_by["a.py"] = {"c.py"}

    tiers = dg.get_tiers()

    # All three are in a cycle — they should end up in one tier
    assert len(tiers) == 1
    assert set(tiers[0]) == {"a.py", "b.py", "c.py"}


def test_tiers_deterministic_ordering():
    """Files within a tier should be sorted for determinism."""
    files = ["z.py", "a.py", "m.py", "b.py"]
    dg = DependencyGraph("/project", files)
    dg.build({"z.py": "", "a.py": "", "m.py": "", "b.py": ""})

    tiers = dg.get_tiers()

    assert tiers[0] == ["a.py", "b.py", "m.py", "z.py"]


# ── Dependency clusters ──────────────────────────────────────────────────────


def test_clusters_independent_files():
    """Each independent file is its own cluster."""
    files = ["a.py", "b.py", "c.py"]
    dg = DependencyGraph("/project", files)
    dg.build({"a.py": "", "b.py": "", "c.py": ""})

    clusters = dg.get_dependency_clusters()

    assert len(clusters) == 3
    for c in clusters:
        assert len(c) == 1


def test_clusters_connected_files():
    """Connected files form a single cluster."""
    files = ["a.py", "b.py", "c.py"]
    dg = DependencyGraph("/project", files)

    content = {
        "a.py": "from b import x",
        "b.py": "from c import y",
        "c.py": "",
    }
    dg.build(content)

    clusters = dg.get_dependency_clusters()

    assert len(clusters) == 1
    assert clusters[0] == {"a.py", "b.py", "c.py"}


def test_clusters_two_groups():
    """Two disjoint dependency chains should form two clusters."""
    files = ["a.py", "b.py", "x.py", "y.py"]
    dg = DependencyGraph("/project", files)

    content = {
        "a.py": "from b import x",
        "b.py": "",
        "x.py": "from y import z",
        "y.py": "",
    }
    dg.build(content)

    clusters = dg.get_dependency_clusters()

    assert len(clusters) == 2
    cluster_sets = [c for c in clusters]
    assert {"a.py", "b.py"} in cluster_sets
    assert {"x.py", "y.py"} in cluster_sets


# ── Transitive dependents ────────────────────────────────────────────────────


def test_files_affected_by():
    """Transitive dependents of a file."""
    files = ["a.py", "b.py", "c.py", "d.py"]
    dg = DependencyGraph("/project", files)

    content = {
        "a.py": "from b import x",
        "b.py": "from c import y",
        "c.py": "",
        "d.py": "",
    }
    dg.build(content)

    # c.py is depended on by b.py, which is depended on by a.py
    affected = dg.get_files_affected_by("c.py")
    assert "b.py" in affected
    assert "a.py" in affected
    assert "d.py" not in affected


def test_files_affected_by_leaf():
    """A leaf file affects nobody."""
    files = ["a.py", "b.py"]
    dg = DependencyGraph("/project", files)

    content = {
        "a.py": "from b import x",
        "b.py": "",
    }
    dg.build(content)

    affected = dg.get_files_affected_by("a.py")
    assert len(affected) == 0


# ── Parallel groups within a tier ────────────────────────────────────────────


def test_parallel_groups_single_file():
    files = ["a.py"]
    dg = DependencyGraph("/project", files)
    dg.build({"a.py": ""})

    groups = dg.get_parallel_groups(["a.py"])

    assert groups == [["a.py"]]


def test_parallel_groups_no_shared_deps():
    """Files with no shared dependencies are separate groups."""
    files = ["a.py", "b.py"]
    dg = DependencyGraph("/project", files)
    dg.build({"a.py": "", "b.py": ""})

    groups = dg.get_parallel_groups(["a.py", "b.py"])

    # Each is its own group since they share nothing
    assert len(groups) == 2


# ── Stats ────────────────────────────────────────────────────────────────────


def test_stats_empty():
    files = ["a.py", "b.py"]
    dg = DependencyGraph("/project", files)
    dg.build({"a.py": "", "b.py": ""})

    stats = dg.get_stats()

    assert stats["total_files"] == 2
    assert stats["total_edges"] == 0
    assert stats["num_tiers"] == 1
    assert stats["max_parallelism"] == 2
    assert stats["isolated_files"] == 2


def test_stats_with_edges():
    files = ["a.py", "b.py", "c.py"]
    dg = DependencyGraph("/project", files)

    content = {
        "a.py": "from b import x",
        "b.py": "from c import y",
        "c.py": "",
    }
    dg.build(content)

    stats = dg.get_stats()

    assert stats["total_files"] == 3
    assert stats["total_edges"] == 2
    assert stats["num_tiers"] == 3
    assert stats["num_clusters"] == 1
    # c.py has depended_by edges, so it's not isolated.
    # All 3 files are connected, none are isolated.
    assert stats["isolated_files"] == 0


# ── JS extension resolution ─────────────────────────────────────────────────


def test_resolve_js_index_file():
    """Resolving './components' should find 'src/components/index.js'."""
    files = ["src/app.js", "src/components/index.js"]
    dg = DependencyGraph("/project", files)

    content = {
        "src/app.js": "import Components from './components';",
        "src/components/index.js": "",
    }
    dg.build(content)

    assert "src/components/index.js" in dg.depends_on["src/app.js"]


def test_resolve_ts_extension():
    """Import without extension resolves to .ts file."""
    files = ["src/app.ts", "src/utils.ts"]
    dg = DependencyGraph("/project", files)

    content = {
        "src/app.ts": "import { helper } from './utils';",
        "src/utils.ts": "",
    }
    dg.build(content)

    assert "src/utils.ts" in dg.depends_on["src/app.ts"]


# ── Python __init__.py resolution ────────────────────────────────────────────


def test_resolve_python_package_init():
    """Importing a package should resolve to __init__.py."""
    files = ["src/main.py", "src/utils/__init__.py", "src/utils/helpers.py"]
    dg = DependencyGraph("/project", files)

    content = {
        "src/main.py": "from src.utils import something",
        "src/utils/__init__.py": "",
        "src/utils/helpers.py": "",
    }
    dg.build(content)

    deps = dg.depends_on["src/main.py"]
    # Should resolve to the __init__.py
    assert "src/utils/__init__.py" in deps


# ── File index ───────────────────────────────────────────────────────────────


def test_file_index_basename_lookup():
    """The file index should allow looking up by basename."""
    files = ["src/deep/nested/utils.py", "lib/other.py"]
    dg = DependencyGraph("/project", files)

    # The index maps basenames (without ext) to paths
    assert "utils" in dg._file_index
    assert "src/deep/nested/utils.py" in dg._file_index["utils"]
    assert "other" in dg._file_index
    assert "lib/other.py" in dg._file_index["other"]


# ── Edge cases ───────────────────────────────────────────────────────────────


def test_empty_graph():
    files = []
    dg = DependencyGraph("/project", files)
    dg.build({})

    tiers = dg.get_tiers()
    assert tiers == []

    clusters = dg.get_dependency_clusters()
    assert clusters == []


def test_single_file():
    files = ["main.py"]
    dg = DependencyGraph("/project", files)
    dg.build({"main.py": "print('hello')"})

    tiers = dg.get_tiers()
    assert len(tiers) == 1
    assert tiers[0] == ["main.py"]


def test_file_not_in_all_files_ignored():
    """build() should skip files not in all_files."""
    files = ["a.py"]
    dg = DependencyGraph("/project", files)

    content = {
        "a.py": "",
        "b.py": "import a",  # b.py not in all_files
    }
    dg.build(content)

    # b.py should not appear in the graph
    assert "b.py" not in dg.depends_on
    assert "b.py" not in dg.depended_by.get("a.py", set())
