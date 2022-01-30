#-----------------------------------------------------------------------------
# Copyright (c) 2012 - 2022, Anaconda, Inc., and Bokeh Contributors.
# All rights reserved.
#
# The full license is in the file LICENSE.txt, distributed with this software.
#-----------------------------------------------------------------------------
""" """

#-----------------------------------------------------------------------------
# Boilerplate
#-----------------------------------------------------------------------------
from __future__ import annotations

import logging # isort:skip
log = logging.getLogger(__name__)

#-----------------------------------------------------------------------------
# Imports
#-----------------------------------------------------------------------------

# Standard library imports
import base64
import datetime as dt
import sys
from math import isinf, isnan
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ClassVar,
    Dict,
    List,
    Literal,
    NoReturn,
    Sequence,
    Tuple,
    Type,
    TypedDict,
)

# External imports
import numpy as np
from typing_extensions import TypeAlias

if TYPE_CHECKING:
    import numpy.typing as npt

# Bokeh imports
from ..util.dataclasses import dataclass, entries, is_dataclass
from ..util.dependencies import import_optional
from ..util.serialization import (
    array_encoding_disabled,
    convert_datetime_type,
    convert_timedelta_type,
    is_datetime_type,
    is_timedelta_type,
    transform_array,
    transform_series,
)
from .types import ID, JSON, Ref

if TYPE_CHECKING:
    from ..core.has_props import Setter
    from ..model import Model

#-----------------------------------------------------------------------------
# Globals and constants
#-----------------------------------------------------------------------------

__all__ = (
    "DeserializationError",
    "Deserializer",
    "SerializationError",
    "Serializer",
)

MAX_INT = 2**53 - 1

_serializers: Dict[Type[Any], Any] = {}

#-----------------------------------------------------------------------------
# General API
#-----------------------------------------------------------------------------

class ModelRef(TypedDict):
    type: str
    id: ID
    attributes: Dict[str, Any]

class ArrayRef(TypedDict):
    type: Literal["array"]
    entries: List[Any]

class BytesRef(TypedDict):
    type: Literal["bytes"]
    data: Ref | str

ByteOrder = Literal["little", "big"]

class NDArrayRef(TypedDict):
    type: Literal["ndarray"]
    array: BytesRef | ArrayRef
    order: ByteOrder
    dtype: str
    shape: List[int]

@dataclass
class Buffer:
    id: ID
    data: bytes

    @property
    def ref(self) -> Ref:
        return Ref(id=self.id)

    def to_base64(self) -> str:
        return base64.b64encode(self.data).decode("utf-8")

class SerializationError(ValueError):
    """ """

class Serializable:
    """ """

    def to_serializable(self, serializer: Serializer) -> JSON:
        """ """
        raise NotImplementedError()

ObjID = int

class Serializer:
    """ """

    _circular: Dict[ObjID, Any]
    _references: Dict[ObjID, Ref]
    _definitions: Dict[ObjID, ModelRef]
    _buffers: List[Buffer]

    def __init__(self, *, binary: bool = True) -> None:
        self._circular = {}
        self._references = {}
        self._definitions = {}
        self._buffers = []
        self.binary = binary

    def to_serializable(self, obj: Any) -> JSON:
        """ """
        return self._encode(obj)

    def has_ref(self, obj: Any) -> bool:
        return id(obj) in self._references

    def add_ref(self, obj: Any, ref: Ref) -> None:
        assert id(obj) not in self._references
        self._references[id(obj)] = ref

    def get_ref(self, obj: Any) -> Ref | None:
        return self._references.get(id(obj))

    def del_ref(self, obj: Any) -> None:
        del self._references[id(obj)]
        del self._definitions[id(obj)]

    def add_rep(self, obj: Any, rep: ModelRef) -> None:
        assert id(obj) in self._references
        self._definitions[id(obj)] = rep

    def add_buf(self, obj: bytes) -> Buffer:
        buffer = Buffer(self._next_id(), obj)
        if self.binary:
            self._buffers.append(buffer)
        return buffer

    @property
    def references(self) -> List[ModelRef]:
        return list(self._definitions.values())

    @property
    def buffers(self) -> List[Buffer]:
        return list(self._buffers)

    _id: int = 0
    def _next_id(self) -> ID:
        self._id += 1
        return ID(str(self._id))

    def _encode(self, obj: Any) -> JSON:
        ref = self.get_ref(obj)
        if ref is not None:
            return ref

        ident = id(obj)
        if ident in self._circular:
            raise SerializationError("circular reference")

        self._circular[ident] = obj
        rep = self.__encode(obj)
        del self._circular[ident]

        return rep

    def __encode(self, obj: Any) -> JSON:
        if isinstance(obj, Serializable):
            return obj.to_serializable(self)
        elif type(obj) in _serializers:
            return _serializers[type(obj)](self)
        elif obj is None:
            return None
        elif isinstance(obj, bool):
            return self._encode_bool(obj)
        elif isinstance(obj, str):
            return self._encode_str(obj)
        elif isinstance(obj, int):
            return self._encode_int(obj)
        elif isinstance(obj, float):
            return self._encode_float(obj)
        elif isinstance(obj, tuple):
            return self._encode_tuple(obj)
        elif isinstance(obj, list):
            return self._encode_list(obj)
        elif isinstance(obj, dict):
            return self._encode_dict(obj)
        elif isinstance(obj, bytes):
            return self._encode_bytes(obj)
        elif isinstance(obj, np.ndarray):
            return self._encode_ndarray(obj)
        elif is_dataclass(obj):
            return self._encode_dataclass(obj)
        else:
            return self._encode_other(obj)

    def _encode_bool(self, obj: bool) -> JSON:
        return obj

    def _encode_str(self, obj: str) -> JSON:
        return obj

    def _encode_int(self, obj: int) -> JSON:
        if -MAX_INT < obj <= MAX_INT:
            return obj
        else:
            log.warning("out of range integer may result in loss of precision")
            return self._encode_float(float(obj))

    def _encode_float(self, obj: float) -> JSON:
        if isnan(obj):
            return dict(type="number", value="nan")
        elif isinf(obj):
            return dict(type="number", value=f"{'-' if obj < 0 else '+'}inf")
        else:
            return obj

    def _encode_tuple(self, obj: Tuple[Any, ...]) -> JSON:
        return self._encode_list(list(obj))

    def _encode_list(self, obj: List[Any]) -> JSON:
        return [self._encode(item) for item in obj]

    def _encode_dict(self, obj: Dict[Any, Any]) -> JSON:
        return dict(
            type="map",
            entries=[[self._encode(key), self._encode(val)] for key, val in obj.items()],
        )

    def _encode_dataclass(self, obj: Any) -> JSON:
        cls = type(obj)
        return dict(
            type=f"{cls.__module__}.{cls.__name__}",
            attributes=[ (key, self._encode(val)) for key, val in entries(obj) ],
        )

    def _encode_bytes(self, obj: bytes) -> JSON:
        buffer = self.add_buf(obj)
        return dict(
            type="bytes",
            data=buffer.ref if self.binary else buffer.to_base64(),
        )

    def _encode_ndarray(self, obj: npt.NDArray[Any]) -> JSON:
        array = transform_array(obj)

        if array_encoding_disabled(array):
            data = self._encode_list(array.tolist())
        else:
            data = self._encode_bytes(array.tobytes())

        return dict(
            type="ndarray",
            array=data,
            shape=list(array.shape),
            dtype=str(array.dtype.name),
            order=sys.byteorder,
        )

    def _encode_other(self, obj: Any) -> Any:
        pd = import_optional("pandas")
        if pd and isinstance(obj, (pd.Series, pd.Index)):
            return self._encode_ndarray(transform_series(obj))

        # date/time values that get serialized as milliseconds
        if is_datetime_type(obj):
            return convert_datetime_type(obj)

        if is_timedelta_type(obj):
            return convert_timedelta_type(obj)

        if isinstance(obj, dt.date):
            return obj.isoformat()

        # NumPy scalars
        if np.issubdtype(type(obj), np.floating):
            return self._encode_float(float(obj))
        if np.issubdtype(type(obj), np.integer):
            return self._encode_int(int(obj))
        if np.issubdtype(type(obj), np.bool_):
            return self._encode_bool(bool(obj))

        rd = import_optional("dateutil.relativedelta")
        if rd and isinstance(obj, rd.relativedelta):
            return dict(
                years=self.to_serializable(obj.years),
                months=self.to_serializable(obj.months),
                days=self.to_serializable(obj.days),
                hours=self.to_serializable(obj.hours),
                minutes=self.to_serializable(obj.minutes),
                seconds=self.to_serializable(obj.seconds),
                microseconds=self.to_serializable(obj.microseconds),
            )

        raise SerializationError(f"can't serialize {type(obj)}")

Decoder: TypeAlias = Callable[[JSON, "Deserializer"], Any]

class DeserializationError(ValueError):
    pass

class Deserializer:
    """ """

    _decoders: ClassVar[Dict[str, Decoder]] = {}

    @classmethod
    def register(cls, type: str, decoder: Decoder) -> None:
        assert type not in cls._decoders, f"'{type} is already registered"
        cls._decoders[type] = decoder

    _references: Dict[ID, Model]
    _setter: Setter | None

    _refs: Dict[ID, ModelRef]
    _buffers: Dict[ID, Buffer]
    _deserializing: bool

    def __init__(self, references: Sequence[Model] | None = None, setter: Setter | None = None):
        self._references = {obj.id: obj for obj in references or []}
        self._setter = setter
        self._deserializing = False

    def from_serializable(self, obj: JSON, refs: List[ModelRef] = [], buffers: List[Buffer] = []) -> Any:
        assert not self._deserializing, "internal error"
        self._deserializing = True

        self._refs = {ref["id"]: ref for ref in refs}
        self._buffers = {buf.id: buf for buf in buffers}

        try:
            return self.decode(obj)
        finally:
            self._refs = {}
            self._buffers = {}
            self._deserializing = False

    def decode(self, obj: JSON) -> Any:
        if isinstance(obj, dict):
            if "id" in obj and "type" not in obj:
                return self._decode_ref(obj)
            elif "type" in obj:
                type = obj["type"]
                if type == "number":
                    value = obj["value"]
                    return float(value) if isinstance(value, str) else value
                elif type == "array":
                    entries = obj["entries"]
                    return [ self.decode(entry) for entry in entries ]
                elif type == "set":
                    entries = obj["entries"]
                    return set([ self.decode(entry) for entry in entries ])
                elif type == "map":
                    entries = obj["entries"]
                    return { self.decode(key): self.decode(val) for key, val in entries }
                elif type == "bytes":
                    return self._decode_bytes(obj)
                elif type == "ndarray":
                    return self._decode_ndarray(obj)
                elif type in self._decoders:
                    return self._decoders[type](obj, self)
                else:
                    raise DeserializationError(f"unsupported serialized type '{type}'")
            else:
                return { key: self.decode(val) for key, val in obj.items() }
        elif isinstance(obj, list):
            return [ self.decode(entry) for entry in obj ]
        else:
            return obj

    def _decode_ref(self, obj: Ref) -> Model:
        id = obj["id"]
        instance = self._references.get(id)
        if instance is not None:
            return instance

        model_ref = self._refs.get(id)
        if model_ref is None:
            raise DeserializationError(f"can't resolve reference '{id}'")

        type = model_ref["type"]
        attributes = model_ref["attributes"]

        from ..model import Model
        cls = Model.model_class_reverse_map.get(type)
        if cls is None:
            raise DeserializationError(f"can't resolve type '{type}'")

        instance = cls.__new__(cls, id=id)
        if instance is None:
            raise DeserializationError(f"can't instantiate {type}(id={id})")

        self._references[instance.id] = instance

        # We want to avoid any Model specific initialization that happens with
        # Slider(...) when reconstituting from JSON, but we do need to perform
        # general HasProps machinery that sets properties, so call it explicitly
        if not instance._initialized:
            from .has_props import HasProps
            HasProps.__init__(instance)

        decoded_attributes = {key: self.decode(val) for key, val in attributes.items()}
        for key, val in decoded_attributes.items():
            instance.set_from_json(key, val, setter=self._setter)

        return instance

    def _decode_bytes(self, obj: BytesRef) -> bytes:
        data = obj["data"]

        if isinstance(data, str):
            return base64.b64decode(data)
        else:
            id = data["id"]

            buffer = self._buffers.get(id)
            if buffer is not None:
                return buffer.data
            else:
                raise DeserializationError(f"can't resolve buffer '{id}'")

    def _decode_ndarray(self, obj: NDArrayRef) -> npt.NDArray[Any]:
        array = obj["array"]
        dtype = obj["dtype"]
        shape = obj["shape"]

        decoded = self.decode(array)

        ndarray: npt.NDArray[Any]
        if isinstance(decoded, bytes):
            ndarray = np.copy(np.frombuffer(decoded, dtype=dtype))  # type: ignore # from and frombuffer are untyped
        else:
            ndarray = np.array(decoded, dtype=dtype)

        if len(shape) > 1:
            ndarray = ndarray.reshape(shape)

        return ndarray

    def error(self, message: str) -> NoReturn:
        raise DeserializationError(message)

#-----------------------------------------------------------------------------
# Dev API
#-----------------------------------------------------------------------------

#-----------------------------------------------------------------------------
# Private API
#-----------------------------------------------------------------------------

#-----------------------------------------------------------------------------
# Code
#-----------------------------------------------------------------------------