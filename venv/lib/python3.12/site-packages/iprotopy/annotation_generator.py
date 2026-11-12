from iprotopy.domestic_importer import DomesticImporter
from iprotopy.type_mapper import TypeMapper


class AnnotationGenerator:
    def __init__(self, importer: DomesticImporter, type_mapper: TypeMapper):
        self._importer = importer
        self._type_mapper = type_mapper

    def process_annotation(
        self,
        annotation_type: str,
    ) -> str:
        try:
            annotation_type, field_import = self._type_mapper.map(annotation_type)
            if field_import is not None:
                self._importer.add_import(field_import)
        except ValueError:
            if '.' in annotation_type:
                class_name = annotation_type.split('.')[0]
                self._importer.import_dependency(class_name)
            else:
                self._importer.import_dependency(annotation_type)
            annotation_type = f"'{annotation_type}'"

        return annotation_type
