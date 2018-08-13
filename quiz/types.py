"""main module for constructing graphQL queries"""
import abc
import enum
import typing as t
from dataclasses import dataclass, replace
from functools import singledispatch
from operator import methodcaller
from textwrap import indent

from . import schema
from .utils import Error, FrozenDict

NoneType = type(None)
INDENT = "  "

gql = methodcaller("__gql__")

FieldName = str
"""a valid GraphQL fieldname"""

JsonObject = t.Dict[str, t.Any]


@singledispatch
def argument_as_gql(obj: object) -> str:
    raise TypeError("cannot serialize to GraphQL: {}".format(type(obj)))


# TODO: IMPORTANT! implement string escape
argument_as_gql.register(str, '"{}"'.format)

# TODO: limit to 32 bit integers!
argument_as_gql.register(int, str)
argument_as_gql.register(NoneType, 'null'.format)
argument_as_gql.register(bool, {True: 'true', False: 'false'}.__getitem__)

# TODO: float, with exponent form


@argument_as_gql.register(enum.Enum)
def _enum_to_gql(obj):
    return obj.value


class FieldSchema(t.NamedTuple):
    name: str
    desc: str
    type: type
    args: FrozenDict  # TODO: use type parameters
    is_deprecated: bool
    deprecation_reason: t.Optional[str]

    def __repr__(self):
        return '<Field> {}'.format(type_repr(self.type))


# TODO: nicer handling of list, union, optional
# TODO: tests
def type_repr(type_):  # pragma: no cover
    if _is_optional(type_):
        return 'Optional[{}]'.format(type_repr(
            t.Union[tuple(a for a in type_.__args__ if a is not NoneType)]))
    try:
        return type_.__name__
    except AttributeError:
        return repr(type_)


# TODO: add fragmentspread
Selection = t.Union['Field', 'InlineFragment']


# TODO: ** operator for specifying fragments
@dataclass(repr=False, frozen=True, init=False)
class SelectionSet(t.Iterable[Selection], t.Sized):
    """A "magic" selection set builder"""
    # the attribute needs to have a dunder name to prevent
    # conflicts with GraphQL field names
    __selections__: t.Tuple[Selection]

    # Q: why can't this subclass tuple?
    # A: Then we would have unwanted methods like index()

    def __init__(self, *selections):
        self.__dict__['__selections__'] = selections

    # TODO: optimize
    @classmethod
    def _make(cls, selections):
        return cls(*selections)

    def __getattr__(self, name):
        return SelectionSet._make(self.__selections__ + (Field(name), ))

    # TODO: support raw graphql strings
    def __getitem__(self, selection_set):
        # TODO: check duplicate fieldnames
        try:
            *rest, target = self.__selections__
        except ValueError:
            raise Error('cannot select fields from empty field list')

        assert isinstance(selection_set, SelectionSet)
        assert len(selection_set.__selections__) >= 1

        return SelectionSet._make(
            tuple(rest)
            + (replace(target, selection_set=selection_set), ))

    def __repr__(self):
        return "<SelectionSet> {}".format(gql(self))

    # TODO: prevent `self` from conflicting with kwargs
    def __call__(self, **kwargs):
        try:
            *rest, target = self.__selections__
        except ValueError:
            raise Error('cannot call empty field list')
        return SelectionSet._make(
            tuple(rest) + (replace(target, kwargs=FrozenDict(kwargs)), ))

    def __iter__(self):
        return iter(self.__selections__)

    def __len__(self):
        return len(self.__selections__)

    def __gql__(self) -> str:
        return '{{\n{}\n}}'.format(
            '\n'.join(
                indent(gql(f), INDENT) for f in self.__selections__
            )
        ) if self.__selections__ else ''


@dataclass(frozen=True)
class Raw:
    gql: str

    def __gql__(self):
        return self.gql


@dataclass(frozen=True)
class Field:
    name: FieldName
    kwargs: FrozenDict = FrozenDict.EMPTY
    selection_set: SelectionSet = SelectionSet()
    # TODO:
    # - alias
    # - directives

    def __gql__(self):
        arguments = '({})'.format(
            ', '.join(
                "{}: {}".format(k, argument_as_gql(v))
                for k, v in self.kwargs.items()
            )
        ) if self.kwargs else ''
        selection_set = (
            ' ' + gql(self.selection_set)
            if self.selection_set else '')
        return self.name + arguments + selection_set


selector = SelectionSet()


class ID(str):
    """represents a unique identifier, often used to refetch an object
    or as the key for a cache. The ID type is serialized in the same way
    as a String; however, defining it as an ID signifies that it is not
    intended to be human‐readable"""


BUILTIN_SCALARS = {
    "Boolean": bool,
    "String":  str,
    "ID":      ID,
    "Float":   float,
    "Int":     int,
}


@dataclass(frozen=True)
class NoSuchField(Error):
    on: type
    name: str


@dataclass(frozen=True)
class NoSuchArgument(Error):
    on: type
    field: FieldSchema
    name: str


@dataclass(frozen=True)
class InvalidArgumentType(Error):
    on: type
    field: FieldSchema
    name: str
    value: object


@dataclass(frozen=True)
class MissingArgument(Error):
    on: type
    field: FieldSchema
    name: str


@dataclass(frozen=True)
class InvalidSelection(Error):
    on: type
    field: FieldSchema


@dataclass(frozen=True)
class InlineFragment:
    on: type
    selection_set: SelectionSet
    # TODO: add directives

    def __gql__(self):
        return '... on {} {}'.format(
            self.on.__name__,
            gql(self.selection_set)
        )


class OperationType(enum.Enum):
    QUERY = 'query'
    MUTATION = 'mutation'
    SUBSCRIPTION = 'subscription'


@dataclass(frozen=True)
class Operation:
    type: OperationType
    selection_set: SelectionSet = SelectionSet()
    # TODO:
    # - name (optional)
    # - variable_defs (optional)
    # - directives (optional)

    def __gql__(self):
        return '{} {}'.format(self.type.value,
                              gql(self.selection_set))


def _is_optional(typ: type) -> bool:
    """check whether a type is a typing.Optional"""
    try:
        return typ.__origin__ is t.Union and NoneType in typ.__args__
    except AttributeError:
        return False


def _unwrap_type(type_: type) -> type:
    if _is_optional(type_):
        return _unwrap_type(
            t.Union[tuple(c for c in type_.__args__
                          if c is not NoneType)])
    elif getattr(type_, '__origin__', None) is list:
        return _unwrap_type(type_.__args__[0])
    return type_


def _unwrap_union(type_: type) -> t.Union[type, t.Tuple[type, ...]]:
    try:
        if type_.__origin__ is t.Union:
            return type_.__args__
    except AttributeError:
        pass
    return type_


def _check_args(cls, field, kwargs) -> t.NoReturn:
    invalid_args = kwargs.keys() - field.args.keys()
    if invalid_args:
        raise NoSuchArgument(cls, field, invalid_args.pop())

    for param in field.args.values():
        try:
            value = kwargs[param.name]
        except KeyError:
            if not _is_optional(param.type):
                raise MissingArgument(cls, field, param.name)
        else:
            if not isinstance(value, _unwrap_union(param.type)):
                raise InvalidArgumentType(
                    cls, field, param.name, value
                )


def _check_field(parent, field) -> t.NoReturn:
    assert isinstance(field, Field)
    try:
        schema = getattr(parent, field.name)
    except AttributeError:
        raise NoSuchField(parent, field.name)

    _check_args(parent, schema, field.kwargs)

    for f in field.selection_set:
        _check_field(_unwrap_type(schema.type), f)


# inherit from ABCMeta to allow mixing with other ABCs
class ObjectMeta(abc.ABCMeta):

    def __getitem__(self, selection_set: SelectionSet) -> InlineFragment:
        for field in selection_set:
            _check_field(self, field)
        return InlineFragment(self, selection_set)

    # TODO: prevent direct instantiation


class Object(metaclass=ObjectMeta):
    """a graphQL object"""


# - InputObject: calling instantiates an instance,
#   results must be instances of the class
class InputObject:
    pass


# separate class to distinguish graphql enums from normal Enums
# TODO: include deprecation attributes in instances?
# TODO: a __repr__ which includes the description, deprecation, etc?
class Enum(enum.Enum):
    pass


# TODO: this should be a metaclass
class Interface:
    pass


class Document(t.NamedTuple):
    operations: t.List[Operation]
    # TODO: fragments


class InputValue(t.NamedTuple):
    name: str
    desc: str
    type: type


def query(selection_set, cls: type) -> Operation:
    """Create a query operation

    selection_set
        The selection set
    cls
        The query type
    """
    for field in selection_set:
        _check_field(cls, field)
    return Operation(OperationType.QUERY, selection_set)


introspection_query = Operation(
    OperationType.QUERY,
    Raw(schema.raw.INTROSPECTION_QUERY)
)
