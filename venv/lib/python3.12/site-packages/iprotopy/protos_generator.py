import subprocess
from pathlib import Path

from iprotopy.importer import Importer


class ProtosGenerator:
    def __init__(self, importer: Importer):
        self._importer = importer

    def generate_protos(self, proto_include_path: Path, models_path: Path):
        models_path.mkdir(parents=True, exist_ok=True)

        proto_files = list(proto_include_path.rglob('*.proto'))

        self._register_modules(proto_files, proto_include_path)

        if not proto_files:
            raise ValueError(f'No .proto files found in {proto_include_path}')

        command = [
            'python',
            '-m',
            'grpc_tools.protoc',
            f'--proto_path={proto_include_path}',
            f'--mypy_out={models_path}',
            f'--python_out={models_path}',
            f'--grpc_python_out={models_path}',
        ] + [str(proto) for proto in proto_files]

        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as e:
            raise ValueError(f'Error while generating protos: {e}') from e

    def _register_modules(self, proto_files: list[Path], proto_include_path: Path):
        for proto_file in proto_files:
            package = proto_file.relative_to(proto_include_path).parent
            filename = proto_file.stem
            self._importer.define_dependency(f'{filename}_pb2', package)
            self._importer.define_dependency(f'{filename}_pb2_grpc', package)
