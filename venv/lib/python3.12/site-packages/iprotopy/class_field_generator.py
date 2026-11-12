import keyword
from ast import AnnAssign, Constant, Load, Name, Store, Subscript, alias
from typing import Callable

from proto_schema_parser import Field, FieldCardinality

from iprotopy.annotation_generator import AnnotationGenerator
from iprotopy.domestic_importer import DomesticImporter
from iprotopy.imports import ImportFrom
from iprotopy.type_mapper import TypeMapper


class ClassFieldGenerator:
    def __init__(self, importer: DomesticImporter, type_mapper: TypeMapper):
        self._importer = importer
        self._type_mapper = type_mapper
        self._annotation_generator = AnnotationGenerator(importer, type_mapper)

    def process_field(self, field: Field) -> AnnAssign:
        if field.cardinality == FieldCardinality.REPEATED:
            return self._process_repeated_field(field)
        elif field.cardinality == FieldCardinality.OPTIONAL:
            return self._process_optional_field(field)
        else:
            return self._process_single_field(field)

    def _safe_field_name(self, unsafe_field_name: str) -> str:
        if keyword.iskeyword(unsafe_field_name):
            return f'{unsafe_field_name}_'
        return unsafe_field_name

    def _process_field_template(
        self,
        field: Field,
        get_field: Callable[[str, str], AnnAssign],
    ) -> AnnAssign:
        safe_field_name = self._safe_field_name(field.name)

        field_type = self._annotation_generator.process_annotation(field.type)

        return get_field(safe_field_name, field_type)

    def _process_repeated_field(self, field: Field):
        def get_field(safe_field_name: str, safe_field_type: str) -> AnnAssign:
            return AnnAssign(
                target=Name(id=safe_field_name, ctx=Store()),
                annotation=Subscript(
                    value=Name(id='List', ctx=Load()),
                    slice=Name(id=safe_field_type, ctx=Load()),
                    ctx=Load(),
                ),
                simple=1,
            )

        self._importer.add_import(
            ImportFrom(module='typing', names=[alias(name='List')], level=0)
        )
        return self._process_field_template(field, get_field)

    def _process_optional_field(self, field: Field) -> AnnAssign:
        def get_field(safe_field_name: str, safe_field_type: str) -> AnnAssign:
            return AnnAssign(
                target=Name(id=safe_field_name, ctx=Store()),
                annotation=Subscript(
                    value=Name(id='Optional', ctx=Load()),
                    slice=Name(id=safe_field_type, ctx=Load()),
                    ctx=Load(),
                ),
                value=Constant(value=None),
                simple=1,
            )

        self._importer.add_import(
            ImportFrom(module='typing', names=[alias(name='Optional')], level=0)
        )
        return self._process_field_template(field, get_field)

    def _process_single_field(self, field: Field) -> AnnAssign:
        def get_field(safe_field_name: str, safe_field_type: str) -> AnnAssign:
            return AnnAssign(
                target=Name(id=safe_field_name, ctx=Store()),
                annotation=Name(id=safe_field_type, ctx=Load()),
                simple=1,
            )

        return self._process_field_template(field, get_field)
