from __future__ import annotations

import argparse
import ast
import math
from pathlib import Path

import networkx as nx
from PIL import Image, ImageDraw, ImageFont


NODE_WIDTH = 210
NODE_HEIGHT = 58
SECTION_MARGIN = 40
INNER_MARGIN = 44
HEADER_HEIGHT = 58
MIN_SECTION_WIDTH = 980
WORKFLOW_SECTION_HEIGHT = 350
TOOL_SECTION_HEIGHT = 300

BG = "#f7f9fc"
SECTION_BG = "#ffffff"
SECTION_BORDER = "#c9d4e5"
TEXT = "#1f2937"
MUTED = "#64748b"
EDGE = "#475569"
CONDITIONAL_EDGE = "#2563eb"
WORKFLOW_FILL = "#e8f1ff"
WORKFLOW_BORDER = "#4f7fc8"
TOOL_FILL = "#e9f8ef"
TOOL_BORDER = "#3f8f59"
REGISTRY_FILL = "#fff7df"
REGISTRY_BORDER = "#b58116"
ENTRY_FILL = "#f0ecff"
ENTRY_BORDER = "#6d5bd0"


def _read_tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _literal_name(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _target_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for item in target.elts:
            names.extend(_target_names(item))
        return names
    return []


def _list_names(node: ast.AST | None) -> list[str]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return []
    return [name for item in node.elts if (name := _literal_name(item))]


def _decorator_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _find_function(tree: ast.AST, function_name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return node
    return None


def parse_workflow_graph(path: Path) -> nx.MultiDiGraph:
    tree = _read_tree(path)
    scope = _find_function(tree, "build_agent_workflow") or tree
    graph = nx.MultiDiGraph(section="workflow")

    for node in ast.walk(scope):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue

        method_name = node.func.attr

        if method_name == "add_node" and node.args:
            node_name = _literal_name(node.args[0])
            if node_name:
                graph.add_node(node_name, kind="workflow")

        elif method_name == "add_edge" and len(node.args) >= 2:
            source = _literal_name(node.args[0])
            target = _literal_name(node.args[1])
            if source and target:
                graph.add_node(source, kind="terminal" if source in {"START", "END"} else "workflow")
                graph.add_node(target, kind="terminal" if target in {"START", "END"} else "workflow")
                graph.add_edge(source, target, label="", conditional=False)

        elif method_name == "add_conditional_edges" and node.args:
            source = _literal_name(node.args[0])
            path_map = node.args[2] if len(node.args) >= 3 else None

            if path_map is None:
                for keyword in node.keywords:
                    if keyword.arg in {"path_map", "then"}:
                        path_map = keyword.value
                        break

            if not source or not isinstance(path_map, ast.Dict):
                continue

            graph.add_node(source, kind="workflow")
            for key, value in zip(path_map.keys, path_map.values):
                label = _literal_name(key) or "condition"
                target = _literal_name(value)
                if not target:
                    continue
                graph.add_node(target, kind="terminal" if target in {"START", "END"} else "workflow")
                graph.add_edge(source, target, label=label, conditional=True)

    return graph


def parse_tools_graph(path: Path) -> nx.MultiDiGraph:
    tree = _read_tree(path)
    graph = nx.MultiDiGraph(section="tools")
    decorated_tools: list[str] = []
    registered_tools: list[str] = []
    entrypoints: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if any(_decorator_name(decorator) == "tool" for decorator in node.decorator_list):
                decorated_tools.append(node.name)

            has_bind_tools = any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "bind_tools"
                for child in ast.walk(node)
            )
            if has_bind_tools:
                entrypoints.append(node.name)

        elif isinstance(node, ast.Assign):
            target_names = [name for target in node.targets for name in _target_names(target)]
            if "AGENT_TOOLS" in target_names:
                registered_tools.extend(_list_names(node.value))

        elif isinstance(node, ast.AnnAssign):
            if "AGENT_TOOLS" in _target_names(node.target):
                registered_tools.extend(_list_names(node.value))

    if not entrypoints and (registered_tools or decorated_tools):
        entrypoints.append("get_llm_with_tools")

    graph.add_node("AGENT_TOOLS", kind="registry")

    for entrypoint in dict.fromkeys(entrypoints):
        graph.add_node(entrypoint, kind="entrypoint")
        graph.add_edge(entrypoint, "AGENT_TOOLS", label="bind_tools", conditional=False)

    for tool_name in dict.fromkeys(registered_tools):
        graph.add_node(tool_name, kind="tool")
        graph.add_edge("AGENT_TOOLS", tool_name, label="registered", conditional=False)

    for tool_name in dict.fromkeys(decorated_tools):
        if tool_name not in graph:
            graph.add_node(tool_name, kind="tool")
            graph.add_edge("AGENT_TOOLS", tool_name, label="@tool only", conditional=True)

    return graph


def _node_sort_key(node: str) -> tuple[int, str]:
    priority = {"START": 0, "END": 999}
    return (priority.get(node, 100), node)


def _workflow_layers(graph: nx.MultiDiGraph) -> list[list[str]]:
    simple = nx.DiGraph()
    simple.add_nodes_from(graph.nodes)
    simple.add_edges_from((source, target) for source, target in graph.edges())

    try:
        return [sorted(layer, key=_node_sort_key) for layer in nx.topological_generations(simple)]
    except nx.NetworkXUnfeasible:
        return [[node] for node in sorted(simple.nodes, key=_node_sort_key)]


def _workflow_positions(graph: nx.MultiDiGraph, left: int, top: int, width: int) -> dict[str, tuple[int, int]]:
    layers = _workflow_layers(graph)
    if not layers:
        return {}

    layer_gap = max(230, (width - (2 * INNER_MARGIN) - NODE_WIDTH) // max(1, len(layers) - 1))
    positions: dict[str, tuple[int, int]] = {}
    section_top = top + HEADER_HEIGHT
    available_height = WORKFLOW_SECTION_HEIGHT - HEADER_HEIGHT - INNER_MARGIN

    for layer_index, layer in enumerate(layers):
        group_height = (len(layer) * NODE_HEIGHT) + (max(0, len(layer) - 1) * 34)
        y = section_top + max(0, (available_height - group_height) // 2)
        x = left + INNER_MARGIN + (layer_index * layer_gap)

        for node in layer:
            positions[node] = (x, y)
            y += NODE_HEIGHT + 34

    return positions


def _tools_positions(graph: nx.MultiDiGraph, left: int, top: int, width: int) -> dict[str, tuple[int, int]]:
    entrypoints = [node for node, data in graph.nodes(data=True) if data.get("kind") == "entrypoint"]
    tools = [node for node, data in graph.nodes(data=True) if data.get("kind") == "tool"]
    entrypoints = sorted(entrypoints)
    tools = sorted(tools)
    positions: dict[str, tuple[int, int]] = {}

    columns = [
        entrypoints or ["tool entrypoint"],
        ["AGENT_TOOLS"],
        tools or ["No registered tools"],
    ]
    x_positions = [
        left + INNER_MARGIN,
        left + (width // 2) - (NODE_WIDTH // 2),
        left + width - INNER_MARGIN - NODE_WIDTH,
    ]
    section_top = top + HEADER_HEIGHT
    available_height = TOOL_SECTION_HEIGHT - HEADER_HEIGHT - INNER_MARGIN

    for column_nodes, x in zip(columns, x_positions):
        group_height = (len(column_nodes) * NODE_HEIGHT) + (max(0, len(column_nodes) - 1) * 34)
        y = section_top + max(0, (available_height - group_height) // 2)
        for node in column_nodes:
            positions[node] = (x, y)
            y += NODE_HEIGHT + 34

    return positions


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        ["arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold else ["arial.ttf", "DejaVuSans.ttf"]
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    parts = text.replace("_", "_ ").split()
    if not parts:
        return [text]

    lines: list[str] = []
    current = ""

    for part in parts:
        candidate = f"{current} {part}".strip()
        if current and _text_size(draw, candidate, font)[0] > max_width:
            lines.append(current.replace("_ ", "_"))
            current = part
        else:
            current = candidate

    if current:
        lines.append(current.replace("_ ", "_"))

    return lines


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: str,
) -> None:
    max_width = (box[2] - box[0]) - 24
    lines = _wrap_text(draw, text, font, max_width)
    line_sizes = [_text_size(draw, line, font) for line in lines]
    total_height = sum(height for _, height in line_sizes) + ((len(lines) - 1) * 4)
    y = box[1] + ((box[3] - box[1] - total_height) // 2)

    for line, (line_width, line_height) in zip(lines, line_sizes):
        x = box[0] + ((box[2] - box[0] - line_width) // 2)
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height + 4


def _node_style(kind: str) -> tuple[str, str]:
    if kind == "entrypoint":
        return ENTRY_FILL, ENTRY_BORDER
    if kind == "registry":
        return REGISTRY_FILL, REGISTRY_BORDER
    if kind == "tool":
        return TOOL_FILL, TOOL_BORDER
    if kind == "terminal":
        return "#eef2f7", "#64748b"
    return WORKFLOW_FILL, WORKFLOW_BORDER


def _box(position: tuple[int, int]) -> tuple[int, int, int, int]:
    x, y = position
    return (x, y, x + NODE_WIDTH, y + NODE_HEIGHT)


def _right_center(box: tuple[int, int, int, int]) -> tuple[float, float]:
    return (box[2], (box[1] + box[3]) / 2)


def _left_center(box: tuple[int, int, int, int]) -> tuple[float, float]:
    return (box[0], (box[1] + box[3]) / 2)


def _draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    start: tuple[float, float],
    end: tuple[float, float],
    fill: str,
    width: int,
    dash: int = 10,
    gap: int = 7,
) -> None:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return

    ux = dx / length
    uy = dy / length
    distance = 0.0

    while distance < length:
        segment_end = min(distance + dash, length)
        p1 = (start[0] + (ux * distance), start[1] + (uy * distance))
        p2 = (start[0] + (ux * segment_end), start[1] + (uy * segment_end))
        draw.line([p1, p2], fill=fill, width=width)
        distance += dash + gap


def _draw_arrowhead(
    draw: ImageDraw.ImageDraw,
    previous: tuple[float, float],
    end: tuple[float, float],
    fill: str,
) -> None:
    angle = math.atan2(end[1] - previous[1], end[0] - previous[0])
    size = 10
    left = (
        end[0] - size * math.cos(angle - math.pi / 6),
        end[1] - size * math.sin(angle - math.pi / 6),
    )
    right = (
        end[0] - size * math.cos(angle + math.pi / 6),
        end[1] - size * math.sin(angle + math.pi / 6),
    )
    draw.polygon([end, left, right], fill=fill)


def _draw_label(
    draw: ImageDraw.ImageDraw,
    point: tuple[float, float],
    text: str,
    font: ImageFont.ImageFont,
) -> None:
    if not text:
        return

    padding_x = 7
    padding_y = 4
    text_width, text_height = _text_size(draw, text, font)
    box = (
        int(point[0] - (text_width / 2) - padding_x),
        int(point[1] - (text_height / 2) - padding_y),
        int(point[0] + (text_width / 2) + padding_x),
        int(point[1] + (text_height / 2) + padding_y),
    )
    draw.rounded_rectangle(box, radius=6, fill="#ffffff", outline="#d8e0ec", width=1)
    draw.text((box[0] + padding_x, box[1] + padding_y - 1), text, font=font, fill=MUTED)


def _edge_path(
    source_box: tuple[int, int, int, int],
    target_box: tuple[int, int, int, int],
    lane_offset: int,
) -> list[tuple[float, float]]:
    start = _right_center(source_box)
    end = _left_center(target_box)
    horizontal_distance = end[0] - start[0]

    if horizontal_distance > NODE_WIDTH * 0.8 and abs(start[1] - end[1]) < 8:
        lane_y = start[1] + lane_offset
        return [start, (start[0] + 46, lane_y), (end[0] - 46, lane_y), end]

    return [start, end]


def _draw_edge(
    draw: ImageDraw.ImageDraw,
    source_box: tuple[int, int, int, int],
    target_box: tuple[int, int, int, int],
    label: str,
    conditional: bool,
    font: ImageFont.ImageFont,
    lane_offset: int,
) -> None:
    color = CONDITIONAL_EDGE if conditional else EDGE
    path = _edge_path(source_box, target_box, lane_offset)

    for start, end in zip(path, path[1:]):
        if conditional:
            _draw_dashed_line(draw, start, end, fill=color, width=2)
        else:
            draw.line([start, end], fill=color, width=2)

    _draw_arrowhead(draw, path[-2], path[-1], color)

    middle_index = max(0, (len(path) // 2) - 1)
    label_start = path[middle_index]
    label_end = path[middle_index + 1]
    label_point = ((label_start[0] + label_end[0]) / 2, (label_start[1] + label_end[1]) / 2 - 14)
    _draw_label(draw, label_point, label, font)


def _draw_section(
    draw: ImageDraw.ImageDraw,
    title: str,
    left: int,
    top: int,
    width: int,
    height: int,
    title_font: ImageFont.ImageFont,
) -> None:
    draw.rounded_rectangle(
        (left, top, left + width, top + height),
        radius=12,
        fill=SECTION_BG,
        outline=SECTION_BORDER,
        width=2,
    )
    draw.text((left + INNER_MARGIN, top + 24), title, font=title_font, fill=TEXT)


def _draw_graph_nodes(
    draw: ImageDraw.ImageDraw,
    graph: nx.MultiDiGraph,
    positions: dict[str, tuple[int, int]],
    font: ImageFont.ImageFont,
) -> dict[str, tuple[int, int, int, int]]:
    boxes: dict[str, tuple[int, int, int, int]] = {}
    for node in sorted(positions, key=_node_sort_key):
        node_box = _box(positions[node])
        kind = str(graph.nodes[node].get("kind", "workflow")) if node in graph else "workflow"
        fill, outline = _node_style(kind)
        draw.rounded_rectangle(node_box, radius=10, fill=fill, outline=outline, width=2)
        _draw_centered_text(draw, node_box, node, font, TEXT)
        boxes[node] = node_box
    return boxes


def _draw_graph_edges(
    draw: ImageDraw.ImageDraw,
    graph: nx.MultiDiGraph,
    boxes: dict[str, tuple[int, int, int, int]],
    font: ImageFont.ImageFont,
) -> None:
    sorted_edges = sorted(
        graph.edges(keys=True, data=True),
        key=lambda item: (item[0], item[1], str(item[3].get("label", ""))),
    )

    for index, (source, target, _key, data) in enumerate(sorted_edges):
        if source not in boxes or target not in boxes:
            continue
        lane_offset = 82 if index % 2 == 0 else -82
        _draw_edge(
            draw,
            boxes[source],
            boxes[target],
            str(data.get("label", "")),
            bool(data.get("conditional", False)),
            font,
            lane_offset,
        )


def render_diagram(workflow_graph: nx.MultiDiGraph, tools_graph: nx.MultiDiGraph) -> Image.Image:
    workflow_layers = _workflow_layers(workflow_graph)
    workflow_width = max(
        MIN_SECTION_WIDTH,
        (2 * INNER_MARGIN) + NODE_WIDTH + (max(0, len(workflow_layers) - 1) * 230),
    )
    width = workflow_width + (2 * SECTION_MARGIN)
    height = WORKFLOW_SECTION_HEIGHT + TOOL_SECTION_HEIGHT + (3 * SECTION_MARGIN) + 42

    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)

    title_font = _load_font(22, bold=True)
    section_font = _load_font(18, bold=True)
    node_font = _load_font(15, bold=True)
    label_font = _load_font(12)

    draw.text((SECTION_MARGIN, 26), "Knowledge Base Curator Agent Graph", font=title_font, fill=TEXT)
    draw.text(
        (SECTION_MARGIN, 54),
        "Current LangGraph workflow plus separate tool-calling registry",
        font=label_font,
        fill=MUTED,
    )

    workflow_top = SECTION_MARGIN + 54
    tools_top = workflow_top + WORKFLOW_SECTION_HEIGHT + SECTION_MARGIN

    _draw_section(
        draw,
        "LangGraph Workflow",
        SECTION_MARGIN,
        workflow_top,
        workflow_width,
        WORKFLOW_SECTION_HEIGHT,
        section_font,
    )
    _draw_section(
        draw,
        "Tool Calling",
        SECTION_MARGIN,
        tools_top,
        workflow_width,
        TOOL_SECTION_HEIGHT,
        section_font,
    )

    workflow_positions = _workflow_positions(workflow_graph, SECTION_MARGIN, workflow_top, workflow_width)
    tool_positions = _tools_positions(tools_graph, SECTION_MARGIN, tools_top, workflow_width)

    workflow_boxes = _draw_graph_nodes(draw, workflow_graph, workflow_positions, node_font)
    tool_boxes = _draw_graph_nodes(draw, tools_graph, tool_positions, node_font)

    _draw_graph_edges(draw, workflow_graph, workflow_boxes, label_font)
    _draw_graph_edges(draw, tools_graph, tool_boxes, label_font)

    legend_y = height - SECTION_MARGIN + 6
    draw.line([(SECTION_MARGIN, legend_y), (SECTION_MARGIN + 34, legend_y)], fill=EDGE, width=2)
    draw.text((SECTION_MARGIN + 44, legend_y - 8), "fixed edge", font=label_font, fill=MUTED)
    _draw_dashed_line(
        draw,
        (SECTION_MARGIN + 150, legend_y),
        (SECTION_MARGIN + 184, legend_y),
        fill=CONDITIONAL_EDGE,
        width=2,
    )
    draw.text((SECTION_MARGIN + 194, legend_y - 8), "conditional edge", font=label_font, fill=MUTED)

    return image


def _resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else Path.cwd() / path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a PNG diagram of the current agent workflow and tools."
    )
    parser.add_argument("--output", default="artifacts/agent_graph.png")
    parser.add_argument("--workflow-file", default="agent_workflow.py")
    parser.add_argument("--tools-file", default="agent_tools.py")
    args = parser.parse_args()

    output_path = _resolve_path(args.output)
    workflow_path = _resolve_path(args.workflow_file)
    tools_path = _resolve_path(args.tools_file)

    workflow_graph = parse_workflow_graph(workflow_path)
    tools_graph = parse_tools_graph(tools_path)
    image = render_diagram(workflow_graph, tools_graph)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    print(f"Generated {output_path} ({image.width}x{image.height})")


if __name__ == "__main__":
    main()
