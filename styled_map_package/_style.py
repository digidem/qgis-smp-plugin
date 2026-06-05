"""Style helpers: font-stack reduction and locally-rendered glyph ranges.

Port of the parts of ``lib/utils/style.js`` used by the writer. Note: unlike the
JS reference implementation this module performs **no** MapLibre style migration
or validation — the caller is responsible for supplying a valid MapLibre v8
style (see the package README).
"""

# The set of MapLibre expression operators, used to distinguish a `text-font`
# expression (e.g. `["match", ...]`) from a plain font-stack array
# (`["Open Sans", ...]`). Extracted from
# `@maplibre/maplibre-gl-style-spec`'s `expressions` export.
EXPRESSIONS = frozenset([
    "!", "!=", "%", "*", "+", "-", "/", "<", "<=", "==", ">", ">=", "^",
    "abs", "accumulated", "acos", "all", "any", "array", "asin", "at", "atan",
    "boolean", "case", "ceil", "coalesce", "collator", "concat", "cos",
    "distance", "downcase", "e", "error", "feature-state", "filter-<",
    "filter-<=", "filter-==", "filter->", "filter->=", "filter-has",
    "filter-has-id", "filter-id-<", "filter-id-<=", "filter-id-==",
    "filter-id->", "filter-id->=", "filter-id-in", "filter-in-large",
    "filter-in-small", "filter-type-==", "filter-type-in", "floor", "format",
    "geometry-type", "get", "has", "heatmap-density", "id", "image", "in",
    "index-of", "interpolate", "interpolate-hcl", "interpolate-lab", "is-supported-script",
    "length", "let", "line-progress", "literal", "ln", "ln2", "log10", "log2",
    "match", "max", "min", "number", "number-format", "object", "pi",
    "properties", "resolved-locale", "rgb", "rgba", "round", "sin", "slice",
    "sqrt", "step", "string", "tan", "to-boolean", "to-color", "to-number",
    "to-rgba", "to-string", "typeof", "upcase", "var", "within", "zoom",
])


def is_expression(value):
    return (
        isinstance(value, list)
        and len(value) > 0
        and isinstance(value[0], str)
        and value[0] in EXPRESSIONS
    )


def _map_array_expression_value(expression, callback):
    if expression[0] == "literal" and isinstance(expression[1], list):
        return ["literal", callback(expression[1])]
    out = [expression[0]]
    for item in expression[1:]:
        if is_expression(item):
            out.append(_map_array_expression_value(item, callback))
        else:
            out.append(item)
    return out


def map_font_stacks(layers, callback):
    """Apply ``callback`` to every ``text-font`` font stack in ``layers``.

    Handles both plain arrays and expressions. Returns a new list of layers.
    """
    result = []
    for layer in layers:
        layout = layer.get("layout") if isinstance(layer, dict) else None
        if (
            not isinstance(layer, dict)
            or layer.get("type") != "symbol"
            or not layout
            or "text-font" not in layout
        ):
            result.append(layer)
            continue
        text_font = layout["text-font"]
        if is_expression(text_font):
            mapped = _map_array_expression_value(text_font, callback)
        elif isinstance(text_font, list):
            mapped = callback(text_font)
        else:
            # Deprecated property-function form: unsupported, leave untouched.
            result.append(layer)
            continue
        new_layer = dict(layer)
        new_layout = dict(layout)
        new_layout["text-font"] = mapped
        new_layer["layout"] = new_layout
        result.append(new_layer)
    return result


def replace_font_stacks(style, fonts):
    """Reduce every ``text-font`` to a single available font (mutates ``style``).

    Picks the first font in each stack that is present in ``fonts``; if none
    match, falls back to the first font in ``fonts`` (matching the JS reference).
    """
    layers = style.get("layers")
    if not isinstance(layers, list):
        return style

    def pick(font_stack):
        for font in font_stack:
            if font in fonts:
                return [font]
        if fonts:
            return [fonts[0]]
        return list(font_stack)

    style["layers"] = map_font_stacks(layers, pick)
    return style


# PBF glyph ranges rendered client-side by MapLibre GL via
# `localIdeographFontFamily` (default 'sans-serif'). Half-open intervals
# [start, end) of PBF range start codepoints. (Port of LOCAL_GLYPH_RANGES.)
LOCAL_GLYPH_RANGES = (
    (0x3000, 0x3400),
    (0x3400, 0x4E00),
    (0x4E00, 0xA000),
    (0xA000, 0xA400),
    (0xAC00, 0xD800),
    (0xF900, 0xFB00),
    (0xFF00, 0x10000),
)


def is_locally_rendered_range(range_start):
    """True if a glyph range is rendered client-side and needs no PBF file."""
    return any(start <= range_start < end for start, end in LOCAL_GLYPH_RANGES)
