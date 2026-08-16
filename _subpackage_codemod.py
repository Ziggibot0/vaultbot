"""Codemod: rewrite bare imports to subpackage imports during Phase 3 restructure.

Usage: python _subpackage_codemod.py <package> <file1> <file2> ...

Rewrites:
  import chat_handler           -> from <pkg>.chat_handler import *
  from chat_handler import X    -> from <pkg>.chat_handler import X
  import chat_handler as ch     -> from <pkg> import chat_handler as ch
"""

import json
import sys
from pathlib import Path

import libcst as cst


class ImportRewriter(cst.CSTTransformer):
    def __init__(self, module_to_pkg: dict[str, str]):
        self._map = module_to_pkg

    def leave_Import(
        self, original_node: cst.Import, updated_node: cst.Import
    ) -> cst.BaseSmallStatement:
        new_names = []
        for alias in updated_node.names:
            name = alias.name.value if isinstance(alias.name, cst.Name) else None
            if name and name in self._map and not alias.asname:
                # import chat_handler -> from core.chat_handler import *
                # Don't convert — too invasive. Leave bare `import X` alone.
                pass
            new_names.append(alias)
        # Only convert `import X` if we can safely do from-import-star
        # For safety, convert to: from pkg import X (which is import X as X)
        converted = []
        for alias in updated_node.names:
            name = alias.name.value if isinstance(alias.name, cst.Name) else None
            if name and name in self._map:
                pkg = self._map[name]
                # from <pkg> import <name> [as <alias>]
                new_alias = cst.ImportAlias(
                    name=cst.Name(name),
                    asname=alias.asname,
                )
                converted.append(
                    (pkg, new_alias)
                )
            else:
                converted.append((None, alias))

        if len(converted) == 1 and converted[0][0]:
            pkg, new_alias = converted[0]
            return cst.ImportFrom(
                module=cst.Name(pkg),
                names=[new_alias],
            )

        # Mixed or unmapped — build from import list
        unmapped = [a for pkg, a in converted if pkg is None]
        mapped = [(p, a) for p, a in converted if p is not None]

        if mapped and not unmapped:
            first_pkg, first_alias = mapped[0]
            return cst.ImportFrom(
                module=cst.Name(first_pkg),
                names=[a for _, a in mapped],
            )

        return updated_node

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.BaseSmallStatement:
        if (
            updated_node.module is None
            and updated_node.relative
            and isinstance(updated_node.names, cst.ImportStar)
        ):
            return updated_node

        if updated_node.module is None:
            return updated_node

        mod_name = updated_node.module.value
        if mod_name in self._map:
            pkg = self._map[mod_name]
            new_module = cst.Attribute(
                value=cst.Name(pkg),
                attr=cst.Name(mod_name),
            )
            return updated_node.with_changes(module=new_module)

        return updated_node


def main():
    map_path = Path(__file__).parent / "_subpackage_map.json"
    mapping = json.loads(map_path.read_text())

    # Build inverted map: module_name -> subpackage
    module_to_pkg = {}
    for pkg, modules in mapping.items():
        for mod in modules:
            module_to_pkg[mod] = pkg

    dry_run = "--dry-run" in sys.argv
    files = [f for f in sys.argv[1:] if not f.startswith("--")]

    changed = 0
    for filepath in files:
        path = Path(filepath)
        source = path.read_text(encoding="utf-8")
        try:
            tree = cst.parse_module(source)
        except cst.ParserSyntaxError as e:
            print(f"SKIP {path}: parse error {e}")
            continue
        wrapper = cst.MetadataWrapper(tree, unsafe_skip_copy=True)
        transformer = ImportRewriter(module_to_pkg)
        new_tree = wrapper.visit(transformer)
        if new_tree.code != source:
            changed += 1
            if not dry_run:
                path.write_text(new_tree.code, encoding="utf-8")
                print(f"REWROTE {path}")
            else:
                print(f"WOULD REWRITE {path}")
        else:
            print(f"UNCHANGED {path}")

    print(f"\n{changed}/{len(files)} files need changes")


if __name__ == "__main__":
    main()
