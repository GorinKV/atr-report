from ast import Assign, ClassDef, Constant, Load, Name, Store, alias

from proto_schema_parser.ast import Comment, EnumValue

from iprotopy.domestic_importer import DomesticImporter
from iprotopy.imports import ImportFrom


class EnumGenerator:
    def __init__(self, importer: DomesticImporter):
        self._importer = importer

    def process_enum(self, element) -> ClassDef:
        enum_body = []
        for enum_element in element.elements:
            if isinstance(enum_element, EnumValue):
                enum_body.append(
                    Assign(
                        targets=[Name(id=enum_element.name, ctx=Store())],
                        value=Constant(value=enum_element.number),
                    ),
                )
            elif isinstance(enum_element, Comment):
                # todo process comments
                continue
            else:
                raise NotImplementedError(f'Unknown enum_element {enum_element}')

        enum_type = 'IntEnum'
        self._importer.add_import(
            ImportFrom(module='enum', names=[alias(name=enum_type)], level=0)
        )
        enum_name = element.name
        self._importer.define_dependency(enum_name)
        return ClassDef(
            name=enum_name,
            bases=[Name(id=enum_type, ctx=Load())],
            keywords=[],
            body=enum_body,
            decorator_list=[],
        )
