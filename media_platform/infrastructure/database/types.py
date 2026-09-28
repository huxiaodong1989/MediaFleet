"""跨 MySQL 与达梦方言的字段类型。"""

import json

from sqlalchemy import JSON, Text
from sqlalchemy.types import TypeDecorator, TypeEngine


class PortableJSON(TypeDecorator):
    """MySQL 使用原生 JSON，达梦使用文本并在应用层序列化。"""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "dm":
            return dialect.type_descriptor(Text())
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        if value is None or dialect.name != "dm":
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return value

    def process_result_value(self, value, dialect):
        if value is None or dialect.name != "dm":
            return value
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8")
        if isinstance(value, str):
            value = value.strip()
            return json.loads(value) if value else None
        return value


def portable_json_type() -> TypeEngine:
    return PortableJSON()


__all__ = ["PortableJSON", "portable_json_type"]
