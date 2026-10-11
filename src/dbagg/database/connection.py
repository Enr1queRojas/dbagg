"""Connection-string parsing shared by configuration and diagnostics."""


def parse_odbc_options(value):
    """Parse options without splitting semicolons inside braced passwords."""
    parts, current, braced, i = [], [], False, 0
    while i < len(value):
        char = value[i]
        if char == "{" and not braced:
            braced = True
        elif char == "}" and braced:
            if i + 1 < len(value) and value[i + 1] == "}":
                current.extend(["}", "}"])
                i += 2
                continue
            braced = False
        if char == ";" and not braced:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        i += 1
    if braced:
        raise ValueError("Cadena ODBC con llaves sin cerrar.")
    parts.append("".join(current))
    options = {}
    for part in parts:
        if not part.strip():
            continue
        key, separator, val = part.partition("=")
        if not separator:
            raise ValueError("Opción ODBC sin valor.")
        key = key.strip().lower()
        if key in options:
            raise ValueError("Opción ODBC duplicada.")
        options[key] = val.strip().removeprefix("{").removesuffix("}")
    return options
