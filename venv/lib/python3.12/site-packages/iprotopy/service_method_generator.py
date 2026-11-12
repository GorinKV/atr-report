import abc
import ast
import typing
from ast import (
    Assign,
    Attribute,
    Call,
    Constant,
    Expr,
    For,
    FunctionDef,
    GeneratorExp,
    Load,
    Name,
    Return,
    Store,
    Subscript,
    Tuple,
    Yield,
    alias,
    arg,
    arguments,
    comprehension,
    keyword,
)

from proto_schema_parser.ast import Method

from iprotopy.constants import SOURCE_PACKAGE_NAME
from iprotopy.domestic_importer import DomesticImporter
from iprotopy.imports import ImportFrom


class BaseServiceMethodGenerator(abc.ABC):
    _input_arg_name: str
    _is_input_stream: bool
    _is_output_stream: bool

    def __init__(self, importer: DomesticImporter):
        self._importer = importer

    @abc.abstractmethod
    def _get_function_body(self, method: Method) -> list[ast.stmt]:
        pass

    def _add_function_body_imports(self):
        self._importer.add_import(
            ImportFrom(
                module=SOURCE_PACKAGE_NAME,
                names=[alias(name='dataclass_to_protobuf')],
                level=0,
            )
        )
        self._importer.add_import(
            ImportFrom(
                module=SOURCE_PACKAGE_NAME,
                names=[alias(name='protobuf_to_dataclass')],
                level=0,
            )
        )

    def _get_args(self, input_class: str) -> arguments:
        input_annotation = self._get_annotation(input_class, self._is_input_stream)
        return arguments(
            posonlyargs=[],
            args=[
                arg(arg='self'),
                arg(
                    arg=self._input_arg_name,
                    annotation=input_annotation,
                ),
            ],
            kwonlyargs=[],
            kw_defaults=[],
            defaults=[],
        )

    def _get_annotation(self, class_type: str, is_stream: bool) -> ast.expr:
        self._importer.import_dependency(class_type)
        if is_stream:
            self._importer.add_import(
                ImportFrom(module='typing', names=[alias(name='Iterable')], level=0)
            )
            return Subscript(
                value=Name(id='Iterable', ctx=Load()),
                slice=Constant(value=class_type),
                ctx=Load(),
            )
        return Constant(value=class_type)

    def create(self, method: Method) -> FunctionDef:
        input_class = method.input_type.type
        output_class = method.output_type.type

        args = self._get_args(input_class)

        output_annotation = self._get_annotation(output_class, self._is_output_stream)
        body = self._get_function_body(method)
        self._add_function_body_imports()
        return FunctionDef(
            name=method.name,
            args=args,
            body=body,
            decorator_list=[],
            returns=output_annotation,
        )


class ServiceMethodUnaryUnaryFunctionGenerator(BaseServiceMethodGenerator):
    _input_arg_name: str = 'request'
    _is_input_stream: bool = False
    _is_output_stream: bool = False

    def _get_function_body(self, method: Method) -> list[ast.stmt]:
        method_name = method.name
        request_class_name = method.input_type.type
        response_class_name = method.output_type.type
        body = [
            Assign(
                targets=[Name(id='protobuf_request', ctx=Store())],
                value=Call(
                    func=Name(id='dataclass_to_protobuf', ctx=Load()),
                    args=[
                        Name(id='request', ctx=Load()),
                        Call(
                            func=Attribute(
                                value=Attribute(
                                    value=Name(id='self', ctx=Load()),
                                    attr='_protobuf',
                                    ctx=Load(),
                                ),
                                attr=request_class_name,
                                ctx=Load(),
                            ),
                            args=[],
                            keywords=[],
                        ),
                    ],
                    keywords=[],
                ),
            ),
            Assign(
                targets=[
                    Tuple(
                        elts=[
                            Name(id='response', ctx=Store()),
                            Name(id='call', ctx=Store()),
                        ],
                        ctx=Store(),
                    )
                ],
                value=Call(
                    func=Attribute(
                        value=Attribute(
                            value=Attribute(
                                value=Name(id='self', ctx=Load()),
                                attr='_stub',
                                ctx=Load(),
                            ),
                            attr=method_name,
                            ctx=Load(),
                        ),
                        attr='with_call',
                        ctx=Load(),
                    ),
                    args=[],
                    keywords=[
                        keyword(
                            arg='request',
                            value=Name(id='protobuf_request', ctx=Load()),
                        ),
                        keyword(
                            arg='metadata',
                            value=Attribute(
                                value=Name(id='self', ctx=Load()),
                                attr='_metadata',
                                ctx=Load(),
                            ),
                        ),
                    ],
                ),
            ),
            Return(
                value=Call(
                    func=Name(id='protobuf_to_dataclass', ctx=Load()),
                    args=[
                        Name(id='response', ctx=Load()),
                        Name(id=response_class_name, ctx=Load()),
                    ],
                    keywords=[],
                )
            ),
        ]
        return body


class ServiceMethodUnaryStreamFunctionGenerator(BaseServiceMethodGenerator):
    _input_arg_name: str = 'request'
    _is_input_stream: bool = False
    _is_output_stream: bool = True

    def _get_function_body(self, method: Method) -> list[ast.stmt]:
        method_name = method.name
        request_class_name = method.input_type.type
        response_class_name = method.output_type.type
        body = [
            For(
                target=Name(id='response', ctx=Store()),
                iter=Call(
                    func=Attribute(
                        value=Attribute(
                            value=Name(id='self', ctx=Load()), attr='_stub', ctx=Load()
                        ),
                        attr=method_name,
                        ctx=Load(),
                    ),
                    args=[],
                    keywords=[
                        keyword(
                            arg='request',
                            value=Call(
                                func=Name(id='dataclass_to_protobuf', ctx=Load()),
                                args=[
                                    Name(id='request', ctx=Load()),
                                    Call(
                                        func=Attribute(
                                            value=Name(id='self', ctx=Load()),
                                            attr=Attribute(
                                                value=Name(id='_protobuf', ctx=Load()),
                                                attr=request_class_name,
                                                ctx=Load(),
                                            ),
                                        ),
                                        args=[],
                                        keywords=[],
                                    ),
                                ],
                                keywords=[],
                            ),
                        ),
                        keyword(
                            arg='metadata',
                            value=Attribute(
                                value=Name(id='self', ctx=Load()),
                                attr='_metadata',
                                ctx=Load(),
                            ),
                        ),
                    ],
                ),
                body=[
                    Expr(
                        value=Yield(
                            value=Call(
                                func=Name(id='protobuf_to_dataclass', ctx=Load()),
                                args=[
                                    Name(id='response', ctx=Load()),
                                    Name(id=response_class_name, ctx=Load()),
                                ],
                                keywords=[],
                            )
                        )
                    )
                ],
                orelse=[],
            )
        ]
        return body


class ServiceMethodStreamUnaryFunctionGenerator(BaseServiceMethodGenerator):
    _input_arg_name: str = 'requests'
    _is_input_stream: bool = True
    _is_output_stream: bool = False

    def _get_function_body(self, method: Method) -> list[ast.stmt]:
        # todo Stream Unary service method
        raise NotImplementedError(
            f'Stream Unary for {method.name} is not implemented yet'
        )


class ServiceMethodStreamStreamFunctionGenerator(BaseServiceMethodGenerator):
    _input_arg_name: str = 'requests'
    _is_input_stream: bool = True
    _is_output_stream: bool = True

    def _get_function_body(self, method: Method) -> list[ast.stmt]:
        method_name = method.name
        request_class_name = method.input_type.type
        response_class_name = method.output_type.type
        body = [
            For(
                target=Name(id='response', ctx=Store()),
                iter=Call(
                    func=Attribute(
                        value=Attribute(
                            value=Name(id='self', ctx=Load()), attr='_stub', ctx=Load()
                        ),
                        attr=method_name,
                        ctx=Load(),
                    ),
                    args=[],
                    keywords=[
                        keyword(
                            arg='request_iterator',
                            value=GeneratorExp(
                                elt=Call(
                                    func=Name(id='dataclass_to_protobuf', ctx=Load()),
                                    args=[
                                        Name(id='request', ctx=Load()),
                                        Call(
                                            func=Attribute(
                                                value=Attribute(
                                                    value=Name(id='self', ctx=Load()),
                                                    attr='_protobuf',
                                                    ctx=Load(),
                                                ),
                                                attr=request_class_name,
                                                ctx=Load(),
                                            ),
                                            args=[],
                                            keywords=[],
                                        ),
                                    ],
                                    keywords=[],
                                ),
                                generators=[
                                    comprehension(
                                        target=Name(id='request', ctx=Store()),
                                        iter=Name(id='requests', ctx=Load()),
                                        ifs=[],
                                        is_async=0,
                                    )
                                ],
                            ),
                        ),
                        keyword(
                            arg='metadata',
                            value=Attribute(
                                value=Name(id='self', ctx=Load()),
                                attr='_metadata',
                                ctx=Load(),
                            ),
                        ),
                    ],
                ),
                body=[
                    Expr(
                        value=Yield(
                            value=Call(
                                func=Name(id='protobuf_to_dataclass', ctx=Load()),
                                args=[
                                    Name(id='response', ctx=Load()),
                                    Name(id=response_class_name, ctx=Load()),
                                ],
                                keywords=[],
                            )
                        )
                    )
                ],
                orelse=[],
            )
        ]
        return body


class ServiceMethodGenerator:
    _unary_input_arg_name = 'request'
    _stream_input_arg_name = f'{_unary_input_arg_name}s'
    _method_generators: typing.Dict[
        typing.Tuple[bool, bool], typing.Type[BaseServiceMethodGenerator]
    ] = {
        (False, False): ServiceMethodUnaryUnaryFunctionGenerator,
        (True, False): ServiceMethodStreamUnaryFunctionGenerator,
        (False, True): ServiceMethodUnaryStreamFunctionGenerator,
        (True, True): ServiceMethodStreamStreamFunctionGenerator,
    }

    def __init__(self, importer: DomesticImporter):
        self._importer = importer

    def process_service_method(self, method: Method) -> FunctionDef:
        is_input_stream = method.input_type.stream
        is_output_stream = method.output_type.stream

        method_generator = self._method_generators[(is_input_stream, is_output_stream)](
            self._importer
        )

        return method_generator.create(method)
