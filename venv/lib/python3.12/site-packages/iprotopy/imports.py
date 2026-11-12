import abc
import ast
import pickle
from ast import Import as AstImport
from ast import ImportFrom as AstImportFrom
from ast import alias


class ComparableAstImportMixin(metaclass=abc.ABCMeta):
    def __eq__(self, other):
        return pickle.dumps(self) == pickle.dumps(other)

    def __hash__(self):
        # Custom hash based on important attributes for uniqueness
        return hash(pickle.dumps(self))

    def __le__(self, other):
        return ast.dump(self) <= ast.dump(other)

    def __lt__(self, other):
        return ast.dump(self) < ast.dump(other)


class Import(AstImport, ComparableAstImportMixin):
    pass


class ImportFrom(AstImportFrom, ComparableAstImportMixin):
    pass


if __name__ == '__main__':
    # Example usage
    imp1 = Import(module='datetime', names=[alias(name='datetime')], level=0)
    imp2 = Import(module='datetime', names=[alias(name='datetime')], level=0)

    # Test the equality
    print(imp1 == imp2)  # This should now return True

    # Test in a set
    custom_set = {imp1}
    print(imp2 in custom_set)  # This should now return True

    imp1 = ImportFrom(module='datetime', names=[alias(name='datetime')], level=0)
    imp2 = ImportFrom(module='datetime', names=[alias(name='datetime')], level=0)

    # Test the equality
    print(imp1 == imp2)  # This should now return True

    # Test in a set
    custom_set = {imp1}
    print(imp2 in custom_set)  # This should now return True
