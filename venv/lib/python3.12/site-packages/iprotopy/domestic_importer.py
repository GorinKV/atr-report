from pathlib import Path
from typing import Set

from iprotopy.import_types import AstImport
from iprotopy.importer import Importer


class DomesticImporter:
    def __init__(self, importer: Importer, pyfile: Path):
        self._importer = importer
        self._pyfile = pyfile

    def define_dependency(self, name: str):
        self._importer.define_dependency(name, self._pyfile)

    def import_dependency(self, name: str):
        self._importer.import_dependency(name, self._pyfile)

    def get_imports(self) -> Set[AstImport]:
        return self._importer.get_imports(self._pyfile)

    def add_import(self, import_statement: AstImport):
        self._importer.add_import(import_statement, self._pyfile)
