import ast
from ast import *

src = """
class A:
    def method():
        for response in self._stub.MarketDataStream(
            request_iterator=(dataclass_to_protobuf(request, self._protobuf.MarketDataRequest()) for request in requests),
            metadata=self._metadata,
        ):
            yield protobuf_to_dataclass(response, MarketDataResponse)

"""

ast_src = ast.parse(src)

print(ast.dump(ast_src))
