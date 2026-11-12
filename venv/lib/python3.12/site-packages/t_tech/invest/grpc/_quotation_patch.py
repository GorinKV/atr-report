from t_tech.invest.grpc.schemas import Quotation

__all__ = [
    "__init__",
    "__add__",
    "__sub__",
    "__hash__",
    "__eq__",
    "__lt__",
    "__le__",
    "__gt__",
    "__ge__",
    "__abs__"
]


def __init__(self, units: int, nano: int):
    max_quotation_nano = 1_000_000_000
    self.units = units + nano // max_quotation_nano
    self.nano = nano % max_quotation_nano


def __add__(self, other: "Quotation") -> "Quotation":
    return Quotation(
        units=self.units + other.units,
        nano=self.nano + other.nano,
    )


def __sub__(self, other: "Quotation") -> "Quotation":
    return Quotation(
        units=self.units - other.units,
        nano=self.nano - other.nano,
    )


def __hash__(self) -> int:
    return hash((self.units, self.nano))


def __eq__(self, other: object) -> bool:
    if not isinstance(other, Quotation):
        return NotImplemented
    return self.units == other.units and self.nano == other.nano


def __lt__(self, other: "Quotation") -> bool:
    return self.units < other.units or (
            self.units == other.units and self.nano < other.nano
    )


def __le__(self, other: "Quotation") -> bool:
    return self.units < other.units or (
            self.units == other.units and self.nano <= other.nano
    )


def __gt__(self, other: "Quotation") -> bool:
    return self.units > other.units or (
            self.units == other.units and self.nano > other.nano
    )


def __ge__(self, other: "Quotation") -> bool:
    return self.units > other.units or (
            self.units == other.units and self.nano >= other.nano
    )


def __abs__(self) -> "Quotation":
    return Quotation(units=abs(self.units), nano=abs(self.nano))
