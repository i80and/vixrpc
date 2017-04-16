All messages are encoded as a MessagePack[1] array.

# Types

There are three types of types:

## Primitive

These types have a direct mapping to a MessagePack type.

* str
* bool
* float
* bin
* array
* nil
* fireandforget
  - This type may only appear in a method or signal return signal, and
    indicates that NO response message is expected.

## Enumeration

### IDL

    enum <name>:
        <name> = <int>
        ...

### Encoding

An enumeration is encoded as an integer.

## Structure

### IDL

    struct <name>:
        <name>: <type>
        ...

### Encoding

A structure is encoded as an array of elements.

For example:

    struct Person:
        name: str
        address: str

would be encoded as:

    Array(str, str)

## Union

### IDL

    enum <name> = <type> | <type> | <type>...

### Encoding

An enum is encoded as a two-element array:

    Array(int, <value>)

# Message Types

There are four types of messages.

## Method Calls

A method call is a client -> server request encoded in the following
format:

    Array(
        messageid: uint | nil,
        methodid: uint,
        args: ...)

messageid will be returned as part of the method response, allowing
the server to handle multiple messages asynchronously. It MUST not
be reused before the method response is processed by the client.
If the method returns the fireandforget type, then the messageid should
be nil.

methodid identifies a server method to call.

The remainder of the message array is the argument list to be passed
to the requested method.

## Method Response

A method response is a server -> client response with a 1:1 correspondence
to a method call. It is encoded in the following format:

    Array(messageid: uint, args: ...)

The messageid is the same as the messageid given in the corresponding
method call.

## Signal

Sometimes a server must notify clients of an event. A signal is
structurally identical to a method call, but operates server -> client.

    Array(
        messageid: uint | nil,
        signalid: uint,
        args: ...)

## Signal Response

A signal may require a response from the client. This signal response is
structurally identical to a method response.

[1]: http://msgpack.org/
