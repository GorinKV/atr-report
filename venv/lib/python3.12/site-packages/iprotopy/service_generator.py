import ast
from ast import Assign, Attribute, ClassDef, Constant, Expr, Load, Name, Store
from pathlib import Path
from typing import List

from proto_schema_parser.ast import Comment, Method, Service

from iprotopy.domestic_importer import DomesticImporter
from iprotopy.service_method_generator import ServiceMethodGenerator


class ServiceGenerator:
    def __init__(self, importer: DomesticImporter, pyfile: Path):
        self._importer = importer
        self._pyfile = pyfile

    def process_service(self, service: Service) -> ClassDef:
        body = []

        self._try_add_docstring(body, service)

        body.extend(self._get_protobuf_attributes(service))

        for element in service.elements:
            if isinstance(element, Comment):
                continue
            elif isinstance(element, Method):
                service_method_generator = ServiceMethodGenerator(self._importer)
                body.append(service_method_generator.process_service_method(element))
                continue
            else:
                raise NotImplementedError(f'Unknown element {element}')

        bases = self._get_bases()
        return ClassDef(
            name=service.name,
            bases=bases,
            keywords=[],
            body=body,
            decorator_list=[],
        )

    def _try_add_docstring(self, body: List[ast.stmt], service: Service):
        if service.elements and isinstance(service.elements[0], Comment):
            body.append(Expr(value=Constant(value=service.elements[0].text)))
            service.elements = service.elements[1:]

    def _get_bases(self) -> List[ast.expr]:
        self._importer.import_dependency('BaseService')
        return [Name(id='BaseService', ctx=Load())]

    def _get_protobuf_attributes(self, service: Service) -> List[ast.stmt]:
        package_name = self._pyfile.stem
        protobuf_package_name = f'{package_name}_pb2'
        protobuf_grpc_package_name = f'{package_name}_pb2_grpc'

        self._importer.import_dependency(protobuf_package_name)
        self._importer.import_dependency(protobuf_grpc_package_name)
        return [
            Assign(
                targets=[Name(id='_protobuf', ctx=Store())],
                value=Name(id=protobuf_package_name, ctx=Load()),
            ),
            Assign(
                targets=[Name(id='_protobuf_grpc', ctx=Store())],
                value=Name(id=protobuf_grpc_package_name, ctx=Load()),
            ),
            Assign(
                targets=[Name(id='_protobuf_stub', ctx=Store())],
                value=Attribute(
                    value=Name(id='_protobuf_grpc', ctx=Load()),
                    attr=f'{service.name}Stub',
                    ctx=Load(),
                ),
            ),
        ]
