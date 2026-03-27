from __future__ import annotations


def render_section(title: str, body: str) -> str:
    return f"[{title}]\n{body}"


def render_key_value_block(title: str, mapping: dict[str, object]) -> str:
    width = max((len(str(key)) for key in mapping), default=0)
    lines = [f"=== {title} ==="]
    for key, value in mapping.items():
        lines.append(f"{key:<{width}} : {value}")
    return "\n".join(lines)


def render_table(title: str, columns: list[str], rows: list[tuple[object, ...]]) -> str:
    rendered_rows = [[str(value) for value in row] for row in rows]
    widths = []
    for index, column in enumerate(columns):
        candidate_values = [column]
        candidate_values.extend(row[index] for row in rendered_rows)
        widths.append(max(len(value) for value in candidate_values))

    def _format_row(values: list[str]) -> str:
        return " | ".join(f"{value:<{widths[index]}}" for index, value in enumerate(values))

    lines = [f"[{title}]"]
    lines.append(_format_row(columns))
    lines.append("-+-".join("-" * width for width in widths))
    for row in rendered_rows:
        lines.append(_format_row(row))
    return "\n".join(lines)
