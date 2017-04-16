#!/usr/bin/env python3
import abc
import argparse
import collections
import numbers
import os.path
import re
import sys
import token
import tokenize

import fett

from typing import Any, Dict, List, Set, Union, Optional


def stderr(*args) -> None:
    sys.stderr.write('{}\n'.format(' '.join(str(element) for element in args)))


def get_token_text(tok) -> str:
    _, a = tok.start
    _, b = tok.end

    return tok.line[a:b]


def error_unknown_token(tok) -> None:
    name = token.tok_name[tok.exact_type]
    stderr('Unknown token: {}'.format(name))
    stderr('  in: {}'.format(repr(tok.line)))
    sys.exit(1)


def error_unknown_name(tok) -> None:
    name = token.tok_name[tok.exact_type]
    stderr('Unknown type: {}'.format(name))
    stderr('  in: {}'.format(repr(tok.line)))
    stderr('  Expected: one of "struct", "enum", "const", "union", "fn", or "signal"')
    sys.exit(1)


def error_expected(tok, name: str) -> None:
    tok_type = token.tok_name[tok.exact_type]
    stderr('Expected', repr(name))
    stderr('  in', repr(tok.line))
    stderr('  got: {} ({})'.format(repr(get_token_text(tok)), tok_type))
    sys.exit(1)


def error_duplicate(tok, name: str) -> None:
    stderr('Duplicate definition', name)

    if tok:
        token_name = token.tok_name[tok.exact_type]
        stderr('  in', repr(tok.line))

    sys.exit(1)


def parse_type(text: str):
    if text.startswith('[') and text.endswith(']'):
        return ('list', parse_type(text[1:-1]))

    if text.startswith('(') and text.endswith(')'):
        text = text[1:-1]
        return ('tuple', [parse_type(x.strip()) for x in text.split(',')])

    if text in ('i8', 'i16', 'i32', 'i64', 'u8', 'u16', 'u32', 'u64', 'f32', 'f64'):
        return ('number', text)

    if text == 'nil':
        return (text,)

    if text == 'fireandforget':
        return (text,)

    raise ValueError(text)


class State(metaclass=abc.ABCMeta):
    def __init__(self, defs: Dict[str, Any], stack: List['State']) -> None:
        self.defs = defs
        self.stack = stack
        self.last_return = None  # type: Any

    @abc.abstractmethod
    def handle(self, tok): ...

    def initialize(self) -> None: pass

    def push_state(self, state_type: type) -> None:
        state = state_type(self.defs, self.stack)
        state.initialize()

        self.stack.append(state)

    def pop_state(self, arg=None) -> None:
        self.stack.pop()

        if arg:
            self.stack[-1].last_return = arg

    def register_def(self, name: str, kv: Any) -> None:
        if name in self.defs:
            error_duplicate(None, name)

        self.defs[name] = kv


class StateRoot(State):
    def handle(self, tok):
        value = get_token_text(tok)

        if tok.exact_type == tokenize.COMMENT:
            pass
        elif tok.exact_type == tokenize.ENCODING:
            pass
        elif tok.type == token.ENDMARKER:
            return
        elif tok.exact_type == token.NAME:
            if value == 'enum':
                self.push_state(StateEnum)
            elif value == 'struct':
                self.push_state(StateStruct)
            elif value == 'fn':
                self.push_state(StateFunction)
            elif value == 'signal':
                self.push_state(StateSignal)
            elif value == 'const':
                self.push_state(StateConst)
            elif value == 'union':
                self.push_state(StateUnion)
            else:
                error_unknown_name(tok)
        else:
            error_unknown_token(tok)


class StateBlock(State):
    @classmethod
    @abc.abstractmethod
    def container(cls) -> type: ...

    @staticmethod
    @abc.abstractmethod
    def delimiter() -> str: ...

    @staticmethod
    def check_value(tok, value):
        pass

    def initialize(self) -> None:
        self.name = '<unknown>'
        self.state = 0

        self.value = None  # type: Any
        self.curname = ''
        self.kv = self.container()()

    def handle(self, tok):
        value = get_token_text(tok)

        if self.state == 0:
            if tok.exact_type == token.NAME:
                self.name = value
                self.state = 1
            else:
                error_unknown_token(tok)
        elif self.state == 1:
            if tok.exact_type == token.COLON:
                self.state = 2
            else:
                error_expected(tok, '":"')
        elif self.state == 2:
            if tok.exact_type == token.NEWLINE:
                self.state = 3
            else:
                error_expected(tok, 'newline')
        elif self.state == 3:
            if tok.exact_type == token.INDENT:
                self.state = 4
            else:
                error_expected(tok, 'indent')
        elif self.state == 4:
            if tok.exact_type == token.NAME:
                self.state = 5
                self.curname = value
            elif tok.type == token.DEDENT:
                self.pop_state()
                self.register_def(self.name, self.kv)
                return
            else:
                error_expected(tok, 'name')
        elif self.state == 5:
            if tok.type == token.OP and value == self.delimiter():
                self.state = 6
            else:
                error_expected(tok, self.delimiter())
        elif self.state == 6:
            if tok.exact_type == token.NAME:
                pass
            elif tok.exact_type == token.LSQB:
                self.push_state(StateList)
                self.state = 7
                return
            elif tok.exact_type == token.LPAR:
                self.push_state(StateTuple)
                self.state = 7
                return
            elif tok.type == token.NUMBER:
                value = int(value)
            elif self.last_return:
                value = self.last_return
            else:
                error_expected(tok, 'name, number, or type')

            self.value = value
            self.state = 7
        elif self.state == 7:
            if self.curname in self.kv:
                error_duplicate(tok, self.curname)

            if self.last_return:
                self.value = self.last_return
                self.last_return = None

            self.check_value(tok, self.value)
            self.kv[self.curname] = self.value
            self.value = None
            self.state = 8

            if tok.exact_type == token.NEWLINE:
                self.state = 4
        elif self.state == 8:
            if tok.exact_type != token.NEWLINE:
                error_expected(tok, 'newline')

            self.state = 4
        else:
            assert False


class StateEnum(StateBlock):
    class Container(collections.OrderedDict):
        pass

    @classmethod
    def container(cls) -> type:
        return cls.Container

    @staticmethod
    def delimiter() -> str:
        return '='

    def register_def(self, name: str, kv) -> None:
        kv_set = set()  # type: Set[Union[str, int, float]]
        for fieldname, fieldvalue in kv.items():
            if fieldvalue in kv_set:
                error_duplicate(None, '{}.{}'.format(name, fieldname))

            kv_set.add(fieldvalue)

        StateBlock.register_def(self, name, kv)


class StateStruct(StateBlock):
    class Container(collections.OrderedDict):
        pass

    @classmethod
    def container(cls) -> type:
        return cls.Container

    @staticmethod
    def delimiter() -> str:
        return ':'

    @staticmethod
    def check_value(tok, value: Any) -> None:
        try:
            return parse_type(value)
        except ValueError:
            error_expected(tok, 'valid type')


class StateFunction(State):
    class Container(list):
        pass

    def initialize(self) -> None:
        self.state = 0
        self.name = ''
        self.args = []  # type: List[str]
        self.return_type = ''

        self.current_parameter = None  # type: Optional[str]
        self.prototype = self.Container(([], None))

    def handle(self, tok):
        value = get_token_text(tok)

        if self.state == 0:
            if tok.exact_type != token.NAME or not value:
                error_expected(tok, 'function name')

            self.name = value
            self.state = 1
        elif self.state == 1:
            if tok.exact_type != token.LPAR:
                error_expected(tok, '"("')
            self.state = 2
        elif self.state == 2:
            if tok.exact_type == token.NAME:
                self.state = 3
                self.current_parameter = value
            elif tok.exact_type == token.RPAR:
                self.state = 10
            else:
                error_expected(tok, 'name')
        elif self.state == 3:
            if tok.exact_type == token.COLON:
                self.state = 4
            else:
                error_expected(tok, '":"')
        elif self.state == 4:
            if tok.exact_type != token.NAME:
                error_expected(tok, 'name')
            self.state = 5
            self.prototype[0].append((self.current_parameter, value))
        elif self.state == 5:
            if tok.exact_type == token.RPAR:
                self.state = 10
            elif tok.exact_type == token.COMMA:
                self.state = 2
            else:
                error_expected(tok, '"," or ")"')
        elif self.state == 7:
            # Return list
            assert False
        elif self.state == 10:
            if tok.type != token.OP or value != '->':
                error_expected(tok, '"->"')
            self.state = 11
        elif self.state == 11:
            if tok.exact_type == token.NAME:
                self.state = 12
                self.prototype[1] = value
            elif tok.exact_type == token.LSQB:
                self.push_state(StateList)
                self.state = 7
            else:
                error_expected(tok, "type")
        elif self.state == 12:
            self.pop_state()
            self.register_def(self.name, self.prototype)
        else:
            assert False


class StateSignal(StateFunction):
    class Container(list):
        pass


class StateConst(State):
    def initialize(self) -> None:
        self.name = ''
        self.state = 0

    def handle(self, tok):
        value = get_token_text(tok)

        if self.state == 0:
            if tok.exact_type == token.NAME:
                self.name = value
                self.state = 1
            else:
                error_unknown_token(tok)
        elif self.state == 1:
            if tok.exact_type == token.EQUAL:
                self.state = 2
            else:
                error_expected(tok, '=')
        elif self.state == 2:
            if tok.type == token.NAME:
                pass
            elif tok.type == token.NUMBER:
                value = int(value)
            else:
                error_expected(tok, 'name')

            self.register_def(self.name, value)
            self.state = 3
        elif self.state == 3:
            if tok.exact_type == token.NEWLINE:
                self.pop_state()
            else:
                error_expected(tok, 'newline')


class StateUnion(State):
    def initialize(self) -> None:
        self.name = ''
        self.state = 0
        self.types = set()  # type: Set[str]

    def handle(self, tok):
        value = get_token_text(tok)

        if self.state == 0:
            if tok.exact_type == token.NAME:
                self.name = value
                self.state = 1
            else:
                error_unknown_token(tok)
        elif self.state == 1:
            if tok.exact_type == token.EQUAL:
                self.state = 2
            else:
                error_expected(tok, '=')
        elif self.state == 2:
            if tok.type != token.NAME:
                error_expected(tok, 'name')

            self.types.add(value)
            self.state = 3
        elif self.state == 3:
            if tok.exact_type == token.VBAR:
                self.state = 2
            elif tok.exact_type == token.NEWLINE:
                self.pop_state()
                self.register_def(self.name, list(self.types))
            else:
                error_expected(tok, '| or newline')


class StateTuple(State):
    def initialize(self) -> None:
        self.types = []  # type: List[str]

    def handle(self, tok):
        value = get_token_text(tok)

        if tok.exact_type == token.COMMA:
            pass
        elif tok.exact_type == token.RPAR:
            self.pop_state('(' + ', '.join(self.types) + ')')
        elif tok.type == token.NAME:
            self.types.append(value)


class StateList(State):
    def initialize(self) -> None:
        self.type = ''

    def handle(self, tok):
        value = get_token_text(tok)

        if tok.exact_type == token.COMMA:
            pass
        elif tok.exact_type == token.RSQB and self.type:
            self.pop_state(self.type)
        elif tok.type == token.NAME and not self.type:
            self.type = '[' + value + ']'


def resolve_c_type(typename) -> Optional[str]:
    return {
        'u8': 'uint8_t',
        'i8': 'int8_t',
        'u16': 'uint16_t',
        'i16': 'int16_t',
        'u32': 'uint32_t',
        'i32': 'int32_t',
        'u64': 'uint64_t',
        'i64': 'int64_t',
        'f32': 'float',
        'f64': 'double',
        'bin': 'char*',
        'str': 'char*',
        'bool': 'bool'
    }.get(typename, None)


def serialize_type(type: str, defs):
    if type in ('i8', 'i16', 'i32', 'i64'):
        return 'cmp_write_integer();'
    elif type in ('u8', 'u16', 'u32', 'u64'):
        return 'cmp_write_uinteger();'
    elif type in ('f32', 'f64'):
        return 'cmp_write_decimal();'
    elif type == 'bool':
        return 'cmp_write_bool();'
    elif type == 'str':
        return 'cmp_write_str();'
    elif type == 'bin':
        return 'cmp_write_bin();'

    assert 'Unknown type ' + type

    args = defs[type][0]
    return_type = defs[type][1]

    print(args, return_type)
    return '''
    '''


def render_c_header(name: str, defs) -> None:
    functions = {}  # type: Dict[str, List[Any]]

    print(f'#ifndef __{name.upper()}_H__\n#define __{name.upper()}_H__')
    print('#include <stdint.h>')

    print('''#ifdef __cplusplus
extern "C" {
#endif''')

    for orig_key, value in defs.items():
        key = '{}_{}'.format(name, orig_key)

        if isinstance(value, str):
            print('#define {} "{}"'.format(key, value))
        elif isinstance(value, numbers.Number):
            print('#define {} {}'.format(key, value))
        elif isinstance(value, StateStruct.Container):
            print('struct {} {{'.format(key))
            for fieldname, fieldvalue in value.items():
                typename = resolve_c_type(fieldvalue)
                print('    {} {};'.format(typename, fieldname))
            print('};\n')
        elif isinstance(value, StateEnum.Container):
            print('enum {} {{'.format(key))
            for fieldname, fieldvalue in value.items():
                print('    {}_{} = {},'.format(key, fieldname, fieldvalue))
            print('};\n')
        elif isinstance(value, StateFunction.Container):
            functions[orig_key] = value
        elif isinstance(value, StateSignal.Container):
            pass
            # print('signal', key, value)

    print('typedef enum {')
    for function_name in functions.keys():
        print(f'    {name.upper()}_METHOD_{function_name.upper()},')
    print(f'}} {name}_methodid_t;\n')

    for function_name, function_prototype in functions.items():
        print('typedef struct {')
        for arg in function_prototype[0]:
            print(f'    {resolve_c_type(arg[1])} {arg[0]};')
        print(f'}} {name}_{function_name}_args_t;\n')

    print('typedef struct {')
    print(f'    uint64_t messageid;')
    print(f'    {name}_methodid_t methodid;')
    print(f'    union {{')
    for function_name in functions.keys():
        print(f'        {name}_{function_name}_args_t args_{function_name};')
    print('    };')
    print(f'}} {name}_method_t;')

    fett.Template.FILTERS['serializeType'] = lambda x: serialize_type(x, defs)
    template = fett.Template('''
int {{ name }}_read_message(int, {{ name }}_method_t*);
int {{ name }}_write_message({{ name }}_method_t*, int);

#ifdef {{ name upperCase }}_IMPLEMENTATION
#include <cmp/cmp.h>
int {{ name }}_read_message(int fd, {{ name }}_method_t* args) {
    cmp_ctx_t cmp;
    cmp_init(&cmp, fd, file_reader, file_writer);
}

int {{ name }}_write_message({{ name }}_method_t* args, int fd) {
    cmp_ctx_t cmp;
    cmp_init(&cmp, fd, file_reader, file_writer);
    if (!cmp_write_array(&cmp, 2)) { return 1; }
    if (!cmp_write_uinteger(&cmp, args->messageid)) { return 1; }
    if (!cmp_write_uinteger(&cmp, args->methodid)) { return 1; }
    switch (args->methodid) {
        {{ for method in methods }}
        case {{ name upperCase }}_METHOD_{{ method upperCase }}:
            {{ method serializeType }}
            break;
        {{ end }}
    }
}
#endif /* {{ name upperCase }}_IMPLEMENTATION */

#endif /* __{{ name upperCase }}_H__ */
''')

    print(template.render({
        'name': name,
        'methods': functions.keys()
    }))

    print('''#ifdef __cplusplus
} /* extern "C" */
#endif''')


def main(args):
    parser = argparse.ArgumentParser(description='Generate code implementing an RPC interface')
    parser.add_argument('input', metavar='INPUT',
                        help='The input RPC definition file')
    parser.add_argument('--name', metavar='NAME')
    parser.add_argument('--verbose', '-v', action='count')
    args = parser.parse_args()

    if not args.name:
        try:
            args.name = os.path.basename(os.path.splitext(args.input)[0])
        except IndexError:
            args.name = args.input

    if not re.match(r'^[a-z_]+$', args.name, re.I):
        raise ValueError('Invalid name: {}'.format(repr(args.name)))

    with open(args.input, 'rb') as f:
        lines = f.readlines()

    defs = collections.OrderedDict()
    stack = []
    stack.append(StateRoot(defs, stack))

    lines = iter(lines)
    for tok in tokenize.tokenize(lambda: next(lines)):
        if tok.type == tokenize.COMMENT:
            continue
        elif tok.type == tokenize.NL:
            continue

        if args.verbose:
            stderr(stack[-1].__class__.__name__, tok)

        stack[-1].handle(tok)

    render_c_header(args.name, defs)

if __name__ == '__main__':
    main(sys.argv)
