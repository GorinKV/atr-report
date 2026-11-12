import ast
import logging
from ast import (
    Module,
)
from pathlib import Path
from types import NoneType
from typing import List

from proto_schema_parser import Message, Option, Parser
from proto_schema_parser.ast import (
    Comment,
    Enum,
    Extension,
    File,
    Package,
    Service,
)
from proto_schema_parser.ast import (
    Import as ProtoImport,
)

from iprotopy.domestic_importer import DomesticImporter
from iprotopy.enum_generator import EnumGenerator
from iprotopy.importer import Importer
from iprotopy.message_class_generator import MessageClassGenerator
from iprotopy.service_generator import ServiceGenerator
from iprotopy.type_mapper import TypeMapper

logger = logging.getLogger(__name__)


class SourceGenerator:
    def __init__(
        self,
        proto_file: Path,
        out_dir: Path,
        pyfile: Path,
        parser: Parser,
        type_mapper: TypeMapper,
        global_importer: Importer,
    ):
        self._parser = parser
        self._type_mapper = type_mapper
        self._importer = DomesticImporter(global_importer, pyfile)
        self._proto_file = proto_file
        self._out_dir = out_dir
        self._pyfile = pyfile
        self._body: List[ast.stmt] = []

    def generate_source(self) -> Module:
        logger.debug(f'Generating source for {self._proto_file}')
        with open(self._proto_file) as f:
            text = f.read()

        file: File = self._parser.parse(text)

        for element in file.file_elements:
            if isinstance(element, Message):
                proto_message_processor = MessageClassGenerator(
                    self._importer, self._type_mapper
                )
                self._body.append(
                    proto_message_processor.process_proto_message(element)
                )
            elif isinstance(element, Package):
                continue
            elif isinstance(element, Option):
                continue
            elif isinstance(element, ProtoImport):
                continue
            elif isinstance(element, Service):
                service_generator = ServiceGenerator(self._importer, self._pyfile)
                self._body.append(service_generator.process_service(element))
                # todo AsyncServiceGenerator
            elif isinstance(element, Comment):
                continue
            elif isinstance(element, Enum):
                proto_enum_processor = EnumGenerator(self._importer)
                self._body.append(proto_enum_processor.process_enum(element))
            elif isinstance(element, NoneType):
                continue
            elif isinstance(element, Extension):
                continue
            else:
                raise NotImplementedError(f'Unknown element {element}')
        return Module(body=self._body, type_ignores=[])
