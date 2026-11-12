import logging
from ast import alias
from pathlib import Path
from typing import Dict, Set

from iprotopy.import_types import AstImport
from iprotopy.imports import ImportFrom

logger = logging.getLogger(__name__)


class Importer:
    def __init__(self):
        self._definitions: Dict[str, Path] = {}
        self._dependencies: Dict[Path, Set[str]] = {}
        self._default_dependencies = set(i.__name__ for i in (int, str, bool))
        self._imports: Dict[Path, Set[AstImport]] = {}

    def define_dependency(self, name: str, package: Path):
        if name in self._definitions:
            logger.warning('Class %s already registered', name)
        self._definitions[name] = package

    def import_dependency(self, name: str, package: Path):
        if name in self._default_dependencies:
            return
        dependencies = self._dependencies.get(package, set())
        dependencies.add(name)
        self._dependencies[package] = dependencies

    def get_imports(self, package: Path) -> Set[AstImport]:
        dependencies_imports = {
            self._get_import_for(class_name, package)
            for class_name in self._dependencies.get(package, ())
        }
        for import_ in self._imports.get(package, ()):
            dependencies_imports.add(import_)
        return dependencies_imports

    def _get_import_for(self, class_name: str, package: Path) -> AstImport:
        if class_name not in self._definitions:
            raise ValueError(
                f'Class {class_name} not registered but needed in {package}'
            )
        return ImportFrom(
            module=self._path_to_module(self._definitions[class_name]),
            names=[alias(name=class_name)],
            level=0,
        )

    def _path_to_module(self, path: Path) -> str:
        module_path = path.with_suffix('')
        return str(module_path).replace('/', '.').replace('\\', '.')

    def remove_circular_dependencies(self):
        for class_name, package in self._definitions.items():
            dependencies = self._dependencies.get(package, set())

            if class_name in dependencies:
                dependencies.remove(class_name)

    def add_import(self, import_statement: AstImport, package: Path):
        imports = self._imports.get(package, set())
        imports.add(import_statement)
        self._imports[package] = imports
