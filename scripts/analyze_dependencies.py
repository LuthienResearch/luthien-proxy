#!/usr/bin/env python3
"""Analyze Python module dependencies in luthien_proxy.

Usage:
    python scripts/analyze_dependencies.py [--format png|svg|dot] [--output-dir DIR]

Generates dependency graphs showing internal module dependencies.
Requires Graphviz (dot) to be installed for image generation.
"""

import argparse
import ast
import subprocess
import sys
from pathlib import Path


def analyze_imports(file_path: Path) -> set:
    """Extract imports from a Python file."""
    try:
        with open(file_path, "r") as f:
            tree = ast.parse(f.read())

        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module)
        return imports
    except Exception as e:
        print(f"Warning: Could not parse {file_path}: {e}", file=sys.stderr)
        return set()


def generate_dot(src_root: Path, output_path: Path):
    """Generate DOT file with all internal dependencies."""
    # Analyze all modules
    module_deps = {}
    all_modules = set()

    for py_file in src_root.rglob("*.py"):
        rel_path = py_file.relative_to(src_root.parent)
        if rel_path.name == "__init__.py":
            module_name = str(rel_path.parent).replace("/", ".")
        else:
            module_name = str(rel_path.with_suffix("")).replace("/", ".")

        all_modules.add(module_name)
        imports = analyze_imports(py_file)
        module_deps[module_name] = imports

    # Write DOT file
    with open(output_path, "w") as f:
        f.write("digraph luthien_proxy {\n")
        f.write("  rankdir=TB;\n")
        f.write("  node [shape=box, style=filled, fillcolor=lightblue];\n")
        f.write("\n")

        # List all modules
        f.write("  // Modules\n")
        for mod in sorted(all_modules):
            short_name = mod.replace("luthien_proxy.", "")
            f.write(f'  "{mod}" [label="{short_name}"];\n')
        f.write("\n")

        # Internal dependencies only
        f.write("  // Internal dependencies\n")
        dep_count = 0
        for source_mod, imports in sorted(module_deps.items()):
            for imp in sorted(imports):
                if not imp.startswith("luthien_proxy"):
                    continue

                # Find matching module
                for target_mod in all_modules:
                    if target_mod == imp or target_mod.startswith(imp + "."):
                        if source_mod != target_mod:
                            f.write(f'  "{source_mod}" -> "{target_mod}";\n')
                            dep_count += 1
                        break

        f.write("}\n")

    print(f"Generated DOT file: {output_path}")
    print(f"  Modules: {len(all_modules)}")
    print(f"  Internal dependencies: {dep_count}")

    return output_path


def render_graph(dot_path: Path, output_format: str):
    """Render DOT file to image format using Graphviz."""
    output_path = dot_path.with_suffix(f".{output_format}")

    try:
        subprocess.run(
            ["dot", f"-T{output_format}", str(dot_path), "-o", str(output_path)], check=True, capture_output=True
        )
        print(f"Rendered {output_format.upper()}: {output_path}")
        return output_path
    except subprocess.CalledProcessError as e:
        print(f"Error rendering graph: {e.stderr.decode()}", file=sys.stderr)
        return None
    except FileNotFoundError:
        print("Error: Graphviz 'dot' command not found. Install Graphviz to render images.", file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(description="Analyze luthien_proxy dependencies")
    parser.add_argument("--format", choices=["png", "svg", "dot"], default="png", help="Output format (default: png)")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("dev/dependency_analysis"),
        help="Output directory (default: dev/dependency_analysis)",
    )

    args = parser.parse_args()

    # Find source root
    repo_root = Path(__file__).parent.parent
    src_root = repo_root / "src" / "luthien_proxy"

    if not src_root.exists():
        print(f"Error: Source directory not found: {src_root}", file=sys.stderr)
        sys.exit(1)

    # Create output directory
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Generate DOT file
    dot_path = args.output_dir / "dependencies.dot"
    generate_dot(src_root, dot_path)

    # Render to image if requested
    if args.format != "dot":
        rendered = render_graph(dot_path, args.format)
        if rendered:
            print(f"\nView the graph: open {rendered}")


if __name__ == "__main__":
    main()
