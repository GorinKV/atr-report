from ast import AnnAssign
from typing import Iterable

from proto_schema_parser import Field
from proto_schema_parser.ast import Comment, FieldCardinality, OneOf

from iprotopy.class_field_generator import ClassFieldGenerator


class OneOfGenerator:
    def __init__(self, class_field_generator: ClassFieldGenerator):
        self._class_field_generator = class_field_generator

    def process(self, one_of: OneOf) -> Iterable[AnnAssign]:
        for element in one_of.elements:
            if isinstance(element, Field):
                field = Field(
                    name=element.name,
                    number=element.number,
                    type=element.type,
                    cardinality=FieldCardinality.OPTIONAL,
                    options=element.options,
                )
                yield self._class_field_generator.process_field(field)
            elif isinstance(element, Comment):
                # todo process comments
                pass
            else:
                raise NotImplementedError(f'Unknown element {element}')
