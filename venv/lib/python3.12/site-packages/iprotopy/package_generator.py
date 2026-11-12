import logging
from ast import (
    Module,
)
from pathlib import Path
from typing import Dict, Set

import astor
from proto_schema_parser.parser import Parser

from iprotopy.base_service_source_generator import BaseServiceSourceGenerator
from iprotopy.domestic_importer import DomesticImporter
from iprotopy.file_generator import SourceGenerator
from iprotopy.import_types import AstImport
from iprotopy.importer import Importer
from iprotopy.imports import Import, ImportFrom
from iprotopy.protos_generator import ProtosGenerator
from iprotopy.type_mapper import TypeMapper

logger = logging.getLogger(__name__)


class PackageGenerator:
    def __init__(self):
        self._parser = Parser()
        self._type_mapper = TypeMapper()

    def generate_sources(self, proto_dir: Path, out_dir: Path):
        importer = Importer()
        protos_generator = ProtosGenerator(importer)
        protos_generator.generate_protos(proto_dir, out_dir)

        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / '__init__.py').touch()
        proto_files = list(proto_dir.rglob('*.proto'))
        modules: Dict[Path, Module] = {}

        self._create_lib_dependencies(out_dir, importer)

        for proto_file in proto_files:
            pyfile = proto_file.relative_to(proto_dir).with_suffix('.py')
            logger.debug(pyfile)
            source_generator = SourceGenerator(
                proto_file, out_dir, pyfile, self._parser, self._type_mapper, importer
            )
            module = source_generator.generate_source()
            modules[proto_file] = module

        importer.remove_circular_dependencies()

        for proto_file in proto_files:
            pyfile = proto_file.relative_to(proto_dir).with_suffix('.py')
            imports = importer.get_imports(pyfile)
            module = modules[proto_file]
            self._insert_imports(module, imports)
            result_src = astor.to_source(module)
            filepath = out_dir / pyfile
            filepath.parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, 'w') as f:
                f.write(result_src)

    def _insert_imports(self, module: Module, imports: Set[AstImport]):
        body_imports = []
        body = []
        for element in module.body:
            if isinstance(element, Import) or isinstance(element, ImportFrom):
                body_imports.append(element)
            else:
                body.append(element)
        body_imports.extend(imports)
        body_imports.sort()
        module.body = body_imports + body

    def _create_lib_dependencies(self, out_dir, importer):
        self._create_base_service(importer, out_dir)

    def _create_base_service(self, importer, out_dir):
        pyfile = Path('base_service').with_suffix('.py')
        base_service_source_generator = BaseServiceSourceGenerator(
            DomesticImporter(importer, pyfile)
        )
        module = base_service_source_generator.create_source()
        imports = importer.get_imports(pyfile)
        self._insert_imports(module, imports)
        result_src = astor.to_source(module)
        filepath = out_dir / pyfile
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w') as f:
            f.write(result_src)
