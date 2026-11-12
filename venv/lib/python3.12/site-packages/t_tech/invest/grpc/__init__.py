from ._quotation_patch import (
    __abs__,
    __add__,
    __eq__,
    __ge__,
    __gt__,
    __hash__,
    __init__,
    __le__,
    __lt__,
    __sub__,
)
from .schemas import *
from .schemas import Quotation, _get_all_classes
from .utils.clients import AsyncClient, AsyncSandboxClient, Client, SandboxClient

__all__ = _get_all_classes()

Quotation.__add__ = __add__       # type: ignore
Quotation.__sub__ = __sub__       # type: ignore
Quotation.__hash__ = __hash__     # type: ignore
Quotation.__eq__ = __eq__         # type: ignore
Quotation.__lt__ = __lt__         # type: ignore
Quotation.__le__ = __le__         # type: ignore
Quotation.__gt__ = __gt__         # type: ignore
Quotation.__ge__ = __ge__         # type: ignore
Quotation.__abs__ = __abs__       # type: ignore
Quotation.__init__ = __init__     # type: ignore
