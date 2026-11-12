import dataclasses
from enum import Enum
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import (
    Any,
    Dict,
    Tuple,
    Type,
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from google.protobuf import symbol_database, message_factory
from google.protobuf.timestamp_pb2 import Timestamp

_UNKNOWN: Any = object()


def to_unsafe_field_name(field_name: str) -> str:
    if field_name.endswith('_'):
        unsafe_field_name = field_name[:-1]
        if unsafe_field_name in PYTHON_KEYWORDS:
            return unsafe_field_name
    return field_name


T = TypeVar('T')


def protobuf_to_dataclass(pb_obj: Any, dataclass_type: Type[T]) -> T:  # noqa:C901
    dataclass_hints = get_type_hints(dataclass_type)
    dataclass_dict: Dict[str, Any] = {}
    dataclass_fields = dataclass_type.__annotations__
    for field_name, field_type in dataclass_hints.items():
        unsafe_field_name = to_unsafe_field_name(field_name)
        pb_value = getattr(pb_obj, unsafe_field_name)
        field_value = _UNKNOWN
        # oneof = dataclass_fields[field_name].metadata["proto"].group
        # if oneof and pb_obj.WhichOneof(oneof) != field_name:
        #     dataclass_dict[field_name] = None
        #     continue

        origin = get_origin(field_type)
        if origin is None:
            if field_type in PRIMITIVE_TYPES:
                field_value = pb_value
            if field_type == Decimal:
                field_value = Decimal(str(pb_value))
            elif issubclass(field_type, datetime):
                field_value = ts_to_datetime(pb_value)
            elif dataclasses.is_dataclass(field_type):
                field_value = protobuf_to_dataclass(pb_value, field_type)
            elif issubclass(field_type, Enum):
                field_value = field_type(pb_value)
        elif origin == list:
            args = get_args(field_type)
            first_arg = args[0]
            if first_arg in PRIMITIVE_TYPES:
                field_value = pb_value
            elif dataclasses.is_dataclass(first_arg):
                field_value = [
                    protobuf_to_dataclass(item, first_arg) for item in pb_value
                ]
            elif first_arg == Decimal:
                field_value = [Decimal(str(item)) for item in pb_value]
            elif first_arg == datetime:
                field_value = [ts_to_datetime(item) for item in pb_value]
            elif issubclass(field_type, Enum):
                field_value = [field_type(item) for item in pb_value]
        if origin == Union:
            args = get_args(field_type)
            if len(args) > 2:
                raise NotImplementedError(
                    'Union of more than 2 args is not supported yet.'
                )
            first_arg, second_arg = args[0], args[1]
            if second_arg == NoneType and str(pb_value) == '':
                field_value = None
            elif first_arg in PRIMITIVE_TYPES:
                field_value = pb_value
            elif first_arg == Decimal:
                field_value = Decimal(str(pb_value))
            elif issubclass(first_arg, datetime):
                field_value = ts_to_datetime(pb_value)
            elif dataclasses.is_dataclass(first_arg):
                field_value = protobuf_to_dataclass(pb_value, first_arg)
            elif issubclass(first_arg, Enum):
                field_value = first_arg(pb_value)

        if field_value is _UNKNOWN:
            raise UnknownType(f'type "{field_type}" unknown')
        dataclass_dict[field_name] = field_value
    return dataclass_type(**dataclass_dict)


def dataclass_to_protobuf(dataclass_obj: Any, protobuf_obj: T) -> T:  # noqa:C901
    dataclass_type = type(dataclass_obj)
    dataclass_hints = get_type_hints(dataclass_type)
    if not dataclass_hints:
        protobuf_obj.SetInParent()  # type:ignore
        return protobuf_obj
    for field_name, field_type in dataclass_hints.items():
        field_value = getattr(dataclass_obj, field_name)
        if field_value is PLACEHOLDER:
            continue
        origin = get_origin(field_type)
        if origin is None:
            _update_field(field_type, protobuf_obj, field_name, field_value)
        elif origin == list:
            args = get_args(field_type)
            first_arg = args[0]
            pb_value = getattr(protobuf_obj, field_name)
            if first_arg in PRIMITIVE_TYPES:
                pb_value.extend(item for item in field_value)
            elif dataclasses.is_dataclass(first_arg):
                descriptor = protobuf_obj.DESCRIPTOR  # type:ignore
                field_descriptor = descriptor.fields_by_name[field_name].message_type
                type_ = message_factory.GetMessageClass(field_descriptor)
                pb_value.extend(
                    dataclass_to_protobuf(item, type_()) for item in field_value
                )
            elif issubclass(first_arg, Enum):
                pb_value.extend(item.value for item in field_value)
            else:
                raise UnknownType(f'type {field_type} unknown')
        elif origin == Union:
            args = get_args(field_type)
            first_arg = args[0]
            second_arg = args[1]
            if second_arg != NoneType:
                raise UnknownType(f'type {field_type} unknown')

            if field_value is None:
                pass  # just skip setting the field, since its set to None by default
            else:
                _update_field(first_arg, protobuf_obj, field_name, field_value)
        else:
            raise UnknownType(f'type {field_type} unknown')

    return protobuf_obj


def _update_field(
    field_type: Type[Any], protobuff_obj: Any, field_name: str, field_value: Any
) -> None:
    if field_type in PRIMITIVE_TYPES:
        setattr(protobuff_obj, field_name, field_value)
    elif issubclass(field_type, datetime):
        field_name_ = field_name
        if field_name == 'from_':
            field_name_ = 'from'
        pb_value = getattr(protobuff_obj, field_name_)
        seconds, nanos = datetime_to_ts(field_value)
        pb_value.seconds = seconds
        pb_value.nanos = nanos
    elif dataclasses.is_dataclass(field_type):
        pb_value = getattr(protobuff_obj, field_name)
        dataclass_to_protobuf(field_value, pb_value)
    elif issubclass(field_type, Enum):
        if isinstance(field_value, int):
            field_value = field_type(field_value)
        setattr(protobuff_obj, field_name, field_value.value)
    else:
        raise UnknownType(f'type {field_type} unknown')


def datetime_to_ts(value: datetime) -> Tuple[int, int]:
    seconds = int(value.timestamp())
    nanos = int(value.microsecond * 1e3)
    return seconds, nanos


_sym_db = symbol_database.Default()
NoneType = type(None)


def ts_to_datetime(value: Timestamp) -> datetime:
    ts = value.seconds + (value.nanos / 1e9)
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=ts)


PLACEHOLDER: Any = object()


def enum_from_string(cls, name: str) -> 'Enum':
    try:
        return cls._member_map_[name]  # type: ignore  # pylint:disable=no-member
    except KeyError as e:
        raise ValueError(f'Unknown value {name} for enum {cls.__name__}') from e


PRIMITIVE_TYPES = (str, float, bool, int)


class UnknownType(TypeError):
    pass


PYTHON_KEYWORDS = (
    'False',
    'None',
    'True',
    'and',
    'as',
    'assert',
    'async',
    'await',
    'break',
    'class',
    'continue',
    'def',
    'del',
    'elif',
    'else',
    'except',
    'finally',
    'for',
    'from',
    'global',
    'if',
    'import',
    'in',
    'is',
    'lambda',
    'nonlocal',
    'not',
    'or',
    'pass',
    'raise',
    'return',
    'try',
    'while',
    'with',
    'yield',
)


if __name__ == '__main__':
    from models.common import MoneyValue

    mv = MoneyValue(currency='USD', units=100, nano=1000)
    print(mv)

    from mypy_models import MoneyValue as ProtoMoneyValue

    pmv = ProtoMoneyValue()
    print(pmv)

    pmv = dataclass_to_protobuf(mv, pmv)

    print(pmv)

    print(protobuf_to_dataclass(pmv, MoneyValue))
