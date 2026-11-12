from ast import alias
from typing import Tuple, Union

from iprotopy.import_types import AstImport
from iprotopy.imports import ImportFrom


class TypeMapper:
    def __init__(self):
        self._standard_types_mapping = {
            'string': 'str',
            'int64': 'int',
            'int32': 'int',
            'double': 'float',
            'bool': 'bool',
        }

        self._google_types_mapping = {
            'google.protobuf.Timestamp': (
                'datetime',
                ImportFrom(module='datetime', names=[alias(name='datetime')], level=0),
            )
        }

    def map(self, proto_type: str) -> Tuple[str, Union[AstImport, None]]:
        if proto_type in self._standard_types_mapping:
            return self._standard_types_mapping[proto_type], None
        if proto_type in self._google_types_mapping:
            return self._google_types_mapping[proto_type]
        raise ValueError(f'Unknown type {proto_type}')
