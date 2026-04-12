"""Parse MQ5 input declarations to extract param schema."""
import re

# Params to skip (not configurable remotely)
SKIP_PARAMS = {
    'MagicNumber',
    'EnableRemoteSync', 'RemoteServerURL', 'RemoteAPIKey', 'HeartbeatIntervalSec',
}

# Regex: input <type> <name> = <value>; // <comment>
_INPUT_RE = re.compile(
    r'input\s+(\w+)\s+(\w+)\s*=\s*(.+?)\s*;\s*(?://\s*(.*))?$'
)


def parse_mq5_inputs(mq5_content: str) -> list:
    """Parse input declarations from MQ5 source code.

    Returns ordered list of param dicts:
    [
        {
            "name": "FixedLot",
            "type": "double",          # double|int|bool|string|enum
            "default": 0.01,           # typed Python value
            "default_raw": "0.01",     # raw string from MQ5
            "comment": "Khối lượng lot cố định",
            "enum_value": null,        # or "PARTIAL_TP_CLOSE_FAR" for enum types
        },
        ...
    ]
    """
    params = []
    in_remote_section = False

    for line in mq5_content.splitlines():
        line = line.strip()
        m = _INPUT_RE.match(line)
        if not m:
            continue

        mq5_type = m.group(1)
        name = m.group(2)
        raw_value = m.group(3).strip()
        comment = (m.group(4) or '').strip()

        # Detect section separators (string with =====<...>=====)
        if mq5_type == 'string' and '=====' in raw_value:
            # Check if entering Remote Sync section
            if 'Remote' in raw_value or 'remote' in raw_value:
                in_remote_section = True
            else:
                in_remote_section = False
            continue

        # Skip Remote Sync section params entirely
        if in_remote_section:
            continue

        # Skip specific params
        if name in SKIP_PARAMS:
            continue

        # Parse type and value
        param = _parse_param(mq5_type, name, raw_value, comment)
        if param:
            params.append(param)

    return params


def _parse_param(mq5_type: str, name: str, raw_value: str, comment: str) -> dict:
    """Parse a single input param into a typed dict."""
    result = {
        'name': name,
        'type': None,
        'default': None,
        'default_raw': raw_value,
        'comment': comment,
        'enum_value': None,
    }

    if mq5_type == 'double':
        result['type'] = 'double'
        result['default'] = _to_float(raw_value)

    elif mq5_type in ('int', 'ulong'):
        result['type'] = 'int'
        result['default'] = _to_int(raw_value)

    elif mq5_type == 'bool':
        result['type'] = 'bool'
        result['default'] = raw_value.lower() == 'true'

    elif mq5_type == 'string':
        result['type'] = 'string'
        # Strip quotes
        result['default'] = raw_value.strip('"').strip("'")

    elif mq5_type.startswith('ENUM_'):
        result['type'] = 'enum'
        result['enum_value'] = raw_value
        # Try to extract numeric value from enum name (e.g., PARTIAL_TP_CLOSE_FAR = 0)
        # For now store as string, dashboard will show as-is
        result['default'] = raw_value

    else:
        # Unknown type, treat as string
        result['type'] = 'string'
        result['default'] = raw_value

    return result


def _to_float(s: str) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _to_int(s: str) -> int:
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return 0
