#!/usr/bin/env python3
"""Generate animated GitHub contribution snakes without third-party packages."""

from __future__ import annotations

import argparse
import hashlib
import html
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from math import atan2, degrees
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


CELL_TAG_PATTERN = re.compile(r"<td\b[^>]*>", re.IGNORECASE)
DATE_PATTERN = re.compile(r'data-date="(\d{4}-\d{2}-\d{2})"', re.IGNORECASE)
LEVEL_PATTERN = re.compile(r'data-level="([0-4])"', re.IGNORECASE)
ID_PATTERN = re.compile(r'id="([^"]+)"', re.IGNORECASE)
TOOLTIP_PATTERN = re.compile(r"<tool-tip\b[^>]*>.*?</tool-tip>", re.IGNORECASE | re.DOTALL)
FOR_PATTERN = re.compile(r'for="([^"]+)"', re.IGNORECASE)
TOOLTIP_TEXT_PATTERN = re.compile(r">(.*?)</tool-tip>", re.IGNORECASE | re.DOTALL)
COUNT_PATTERN = re.compile(r"([0-9][0-9,]*) contributions?", re.IGNORECASE)

CELL_SIZE = 10
CELL_GAP = 4
CELL_STEP = CELL_SIZE + CELL_GAP
GRID_LEFT = 44
GRID_TOP = 28
GRID_RIGHT = 10
GRID_BOTTOM = 34
TARGET_STEPS_PER_SECOND = 7

THEMES = {
    "light": {
        "empty": "#ebedf0",
        "border": "#1b1f230f",
        "levels": ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"],
        "eye": "#ffffff",
        "pupil": "#24292f",
        "head_outline": "#6d28d9",
        "mouth": "#5b21b6",
        "label": "#57606a",
    },
    "dark": {
        "empty": "#161b22",
        "border": "#ffffff0f",
        "levels": ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"],
        "eye": "#ffffff",
        "pupil": "#0d1117",
        "head_outline": "#e9d5ff",
        "mouth": "#4c1d95",
        "label": "#8b949e",
    },
}


@dataclass(frozen=True)
class Contribution:
    day: date
    level: int
    count: int | None


def fetch_contribution_html(username: str, attempts: int = 4) -> str:
    """Fetch GitHub's public contribution grid with bounded retries."""
    url = f"https://github.com/users/{username}/contributions"
    request = Request(
        url,
        headers={
            "Accept": "text/html",
            "User-Agent": f"{username}-profile-snake/1.0",
        },
    )

    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError, UnicodeDecodeError) as error:
            if attempt == attempts:
                raise RuntimeError(
                    f"Could not fetch GitHub contributions after {attempts} attempts: {error}"
                ) from error
            delay = 2 ** (attempt - 1)
            print(
                f"GitHub contribution fetch failed ({error}); retrying in {delay}s...",
                file=sys.stderr,
            )
            time.sleep(delay)

    raise AssertionError("unreachable")


def parse_contributions(source: str) -> list[Contribution]:
    """Parse contribution dates and levels without depending on attribute order."""
    contributions: list[Contribution] = []
    seen_dates: set[date] = set()
    counts_by_cell_id: dict[str, int] = {}

    for tooltip in TOOLTIP_PATTERN.findall(source):
        for_match = FOR_PATTERN.search(tooltip)
        text_match = TOOLTIP_TEXT_PATTERN.search(tooltip)
        if not for_match or not text_match:
            continue
        tooltip_text = html.unescape(re.sub(r"<[^>]+>", "", text_match.group(1))).strip()
        count_match = COUNT_PATTERN.search(tooltip_text)
        if count_match:
            counts_by_cell_id[for_match.group(1)] = int(count_match.group(1).replace(",", ""))
        elif tooltip_text.lower().startswith("no contributions"):
            counts_by_cell_id[for_match.group(1)] = 0

    for tag in CELL_TAG_PATTERN.findall(source):
        date_match = DATE_PATTERN.search(tag)
        level_match = LEVEL_PATTERN.search(tag)
        id_match = ID_PATTERN.search(tag)
        if not date_match or not level_match:
            continue

        contribution_day = date.fromisoformat(date_match.group(1))
        if contribution_day in seen_dates:
            raise ValueError(f"Duplicate contribution date: {contribution_day}")

        seen_dates.add(contribution_day)
        contributions.append(
            Contribution(
                day=contribution_day,
                level=int(level_match.group(1)),
                count=counts_by_cell_id.get(id_match.group(1)) if id_match else None,
            )
        )

    if len(contributions) < 300:
        raise ValueError(
            f"Expected a yearly GitHub contribution grid, found {len(contributions)} cells"
        )

    return sorted(contributions, key=lambda contribution: contribution.day)


def sunday_index(value: date) -> int:
    """Return Sunday=0 through Saturday=6."""
    return (value.weekday() + 1) % 7


def grid_data(
    contributions: list[Contribution],
) -> tuple[dict[tuple[int, int], Contribution], int, date]:
    first_day = contributions[0].day
    origin = first_day - timedelta(days=sunday_index(first_day))
    cells: dict[tuple[int, int], Contribution] = {}

    for contribution in contributions:
        days_from_origin = (contribution.day - origin).days
        cells[(days_from_origin // 7, sunday_index(contribution.day))] = contribution

    columns = max(x for x, _ in cells) + 1
    return cells, columns, origin


def route_turn_count(route: list[tuple[int, int]]) -> int:
    directions = [
        (end[0] - start[0], end[1] - start[1])
        for start, end in zip(route, route[1:])
    ]
    return sum(first != second for first, second in zip(directions, directions[1:]))


def snake_route(columns: int, seed_text: str) -> list[tuple[int, int]]:
    """Create a controlled randomized path that visits every cell exactly once."""
    route: list[tuple[int, int]] = []
    for column in range(columns):
        row_range = range(7) if column % 2 == 0 else range(6, -1, -1)
        route.extend((column, row) for row in row_range)

    seed = hashlib.sha256(
        f"{seed_text}:{columns}:controlled-route-v1".encode()
    ).digest()[:8]
    randomizer = random.Random(int.from_bytes(seed, "big"))
    base_turns = route_turn_count(route)
    minimum_turns = max(20, base_turns - 10)
    maximum_turns = base_turns + 31
    target_shuffles = columns * 75
    accepted_shuffles = 0
    attempts = 0

    def neighbors(position: tuple[int, int]) -> list[tuple[int, int]]:
        column, row = position
        return [
            (next_column, next_row)
            for next_column, next_row in (
                (column + 1, row),
                (column - 1, row),
                (column, row + 1),
                (column, row - 1),
            )
            if 0 <= next_column < columns and 0 <= next_row < 7
        ]

    while accepted_shuffles < target_shuffles and attempts < target_shuffles * 4:
        attempts += 1
        index_by_position = {position: index for index, position in enumerate(route)}

        if randomizer.choice((True, False)):
            pivots = [
                index_by_position[position]
                for position in neighbors(route[0])
                if index_by_position[position] > 1
            ]
            if not pivots:
                continue
            pivot = randomizer.choice(pivots)
            candidate = route[pivot - 1 : 0 : -1] + [route[0]] + route[pivot:]
        else:
            pivots = [
                index_by_position[position]
                for position in neighbors(route[-1])
                if index_by_position[position] < len(route) - 2
            ]
            if not pivots:
                continue
            pivot = randomizer.choice(pivots)
            candidate = route[: pivot + 1] + route[:pivot:-1]

        turns = route_turn_count(candidate)
        if minimum_turns <= turns <= maximum_turns:
            route = candidate
            accepted_shuffles += 1

    steps = list(zip(route, route[1:]))
    if len(set(route)) != columns * 7:
        raise RuntimeError("Controlled route revisited a contribution cell")
    if any(
        abs(start[0] - end[0]) + abs(start[1] - end[1]) != 1
        for start, end in steps
    ):
        raise RuntimeError("Controlled route contains a non-adjacent movement")

    return route


def coordinate(column: int, row: int) -> tuple[float, float]:
    return (
        GRID_LEFT + (CELL_SIZE / 2) + (column * CELL_STEP),
        GRID_TOP + (CELL_SIZE / 2) + (row * CELL_STEP),
    )


def format_number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def create_svg(
    username: str,
    contributions: list[Contribution],
    theme_name: str,
) -> str:
    theme = THEMES[theme_name]
    cells, columns, origin = grid_data(contributions)
    route = snake_route(columns, username)
    route_index = {position: index for index, position in enumerate(route)}
    animation_seconds = max(38, round((len(route) - 1) / TARGET_STEPS_PER_SECOND))
    path_length = (len(route) - 1) * CELL_STEP
    body_length = 4 * CELL_STEP
    grid_width = (columns * CELL_SIZE) + ((columns - 1) * CELL_GAP)
    grid_height = (7 * CELL_SIZE) + (6 * CELL_GAP)
    width = GRID_LEFT + grid_width + GRID_RIGHT
    height = GRID_TOP + grid_height + GRID_BOTTOM
    month_labels: list[tuple[int, str]] = []
    previous_month: tuple[int, int] | None = None
    for column in range(columns):
        week_start = origin + timedelta(days=column * 7)
        month_key = (week_start.year, week_start.month)
        if month_key != previous_month:
            month_labels.append((column, week_start.strftime("%b")))
            previous_month = month_key
    legend_box_y = GRID_TOP + grid_height + 12
    legend_text_y = legend_box_y + 9
    legend_start_x = width - GRID_RIGHT - 142
    route_coordinates = [coordinate(column, row) for column, row in route]
    path_data = " ".join(
        ("M" if index == 0 else "L")
        + format_number(x)
        + " "
        + format_number(y)
        for index, (x, y) in enumerate(route_coordinates)
    )
    motion_values = ";".join(
        [
            f"{format_number(route_coordinates[0][0])} {format_number(route_coordinates[0][1])}"
        ]
        + [f"{format_number(x)} {format_number(y)}" for x, y in route_coordinates]
        + [
            f"{format_number(route_coordinates[-1][0])} {format_number(route_coordinates[-1][1])}"
        ]
    )
    motion_times = ";".join(
        ["0"]
        + [
            f"{0.02 + ((index / (len(route) - 1)) * 0.88):.5f}".rstrip("0").rstrip(".")
            for index in range(len(route))
        ]
        + ["1"]
    )
    direction_angles = []
    for index, (x, y) in enumerate(route_coordinates):
        other_x, other_y = (
            route_coordinates[index + 1]
            if index < len(route_coordinates) - 1
            else route_coordinates[index - 1]
        )
        if index == len(route_coordinates) - 1:
            delta_x, delta_y = x - other_x, y - other_y
        else:
            delta_x, delta_y = other_x - x, other_y - y
        direction_angles.append(round(degrees(atan2(delta_y, delta_x))))
    rotation_values = ";".join(
        [str(direction_angles[0])]
        + [str(angle) for angle in direction_angles]
        + [str(direction_angles[-1])]
    )
    safe_username = html.escape(username)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" role="img" '
            f'aria-labelledby="title description">'
        ),
        f"  <title id=\"title\">{safe_username}'s colorful GitHub contribution snake</title>",
        (
            "  <desc id=\"description\">A colorful spotted snake with googly eyes and a tiny "
            f"forked tongue wanders through the public GitHub contribution grid for "
            f"{safe_username} along a controlled collision-free path.</desc>"
        ),
        "  <defs>",
        '    <pattern id="candy-body" patternUnits="userSpaceOnUse" width="28" height="28" patternTransform="rotate(18)">',
        '      <rect width="28" height="28" fill="#12b8a6"/>',
        '      <circle cx="5" cy="7" r="3.2" fill="#ffe066"/>',
        '      <circle cx="20" cy="19" r="3" fill="#ff6b6b"/>',
        '      <circle cx="22" cy="3" r="2.2" fill="#74c0fc"/>',
        "    </pattern>",
        '    <radialGradient id="funny-head" cx="28%" cy="22%" r="88%">',
        '      <stop offset="0%" stop-color="#fff176"/>',
        '      <stop offset="42%" stop-color="#ff8a34"/>',
        '      <stop offset="100%" stop-color="#ff4d8d"/>',
        "    </radialGradient>",
        '    <filter id="head-shadow" x="-50%" y="-50%" width="200%" height="200%">',
        '      <feDropShadow dx="0" dy="1.2" stdDeviation="1.2" flood-color="#000000" flood-opacity="0.28"/>',
        "    </filter>",
        "  </defs>",
        "  <style>",
        (
            f"    .cell {{ fill: {theme['empty']}; stroke: {theme['border']}; "
            "stroke-width: 1; shape-rendering: geometricPrecision; }"
        ),
        *[
            f"    .level-{level} {{ fill: {color}; }}"
            for level, color in enumerate(theme["levels"])
        ],
        (
            "    .snake-body { fill: none; stroke: url(#candy-body); stroke-width: 12; "
            "stroke-linecap: round; stroke-linejoin: round; }"
        ),
        (
            "    .snake-highlight { fill: none; stroke: #ffffff99; stroke-width: 2.4; "
            "stroke-linecap: round; stroke-linejoin: round; }"
        ),
        (
            f"    .snake-head {{ fill: url(#funny-head); stroke: {theme['head_outline']}; "
            "stroke-width: 1.4; }"
        ),
        f"    .snake-eye {{ fill: {theme['eye']}; }}",
        f"    .snake-pupil {{ fill: {theme['pupil']}; }}",
        f"    .snake-brow {{ fill: none; stroke: {theme['pupil']}; stroke-width: 1.4; stroke-linecap: round; }}",
        f"    .snake-mouth {{ fill: none; stroke: {theme['mouth']}; stroke-width: 1.3; stroke-linecap: round; }}",
        "    .snake-cheek { fill: #ffb3c7; opacity: 0.9; }",
        "    .snake-tongue { fill: none; stroke: #ff2d55; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }",
        (
            f"    .calendar-label {{ fill: {theme['label']}; font-family: -apple-system, "
            "BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 11px; }"
        ),
        "  </style>",
        '  <g aria-label="Calendar labels">',
        *[
            (
                f'    <text class="calendar-label" x="{GRID_LEFT + (column * CELL_STEP)}" '
                f'y="16">{label}</text>'
            )
            for column, label in month_labels
        ],
        *[
            (
                f'    <text class="calendar-label" x="{GRID_LEFT - 8}" '
                f'y="{format_number(coordinate(0, row)[1] + 4)}" '
                f'text-anchor="end">{label}</text>'
            )
            for row, label in ((1, "Mon"), (3, "Wed"), (5, "Fri"))
        ],
        "  </g>",
        f'  <path id="snake-route" d="{path_data}" fill="none" stroke="none"/>',
        '  <g aria-label="Contribution cells">',
    ]

    for column in range(columns):
        for row in range(7):
            contribution = cells.get((column, row))
            level = contribution.level if contribution else 0
            x = GRID_LEFT + (column * CELL_STEP)
            y = GRID_TOP + (row * CELL_STEP)
            lines.append(
                f'    <rect class="cell level-{level}" x="{x}" y="{y}" '
                f'width="{CELL_SIZE}" height="{CELL_SIZE}" rx="2">'
            )
            if contribution:
                contribution_date = (
                    f"{contribution.day.strftime('%B')} {contribution.day.day}, "
                    f"{contribution.day.year}"
                )
                if contribution.count is None:
                    tooltip = f"Contribution level {contribution.level} on {contribution_date}"
                elif contribution.count == 0:
                    tooltip = f"No contributions on {contribution_date}"
                else:
                    noun = "contribution" if contribution.count == 1 else "contributions"
                    tooltip = f"{contribution.count:,} {noun} on {contribution_date}"
                lines.append(f"      <title>{html.escape(tooltip)}</title>")
            if level > 0:
                progress = route_index[(column, row)] / (len(route) - 1)
                hit_time = 0.02 + (progress * 0.88)
                faded_time = min(hit_time + 0.001, 0.901)
                original_color = theme["levels"][level]
                lines.append(
                    '      <animate attributeName="fill" '
                    f'values="{original_color};{original_color};{theme["empty"]};'
                    f'{theme["empty"]};{original_color}" '
                    f'keyTimes="0;{hit_time:.5f};{faded_time:.5f};0.94;1" '
                    f'dur="{animation_seconds}s" repeatCount="indefinite"/>'
                )
            lines.append("    </rect>")

    lines.extend(
        [
            "  </g>",
            '  <g aria-label="Contribution intensity legend">',
            (
                f'    <text class="calendar-label" x="{legend_start_x}" '
                f'y="{legend_text_y}">Less</text>'
            ),
            *[
                (
                    f'    <rect x="{legend_start_x + 31 + (level * 14)}" '
                    f'y="{legend_box_y}" width="10" height="10" rx="2" '
                    f'fill="{color}" stroke="{theme["border"]}"/>'
                )
                for level, color in enumerate(theme["levels"])
            ],
            (
                f'    <text class="calendar-label" x="{legend_start_x + 104}" '
                f'y="{legend_text_y}">More</text>'
            ),
            "  </g>",
            (
                f'  <path class="snake-body" d="{path_data}" '
                f'stroke-dasharray="{body_length} {path_length}" '
                f'stroke-dashoffset="{body_length}">'
            ),
            (
                '    <animate attributeName="stroke-dashoffset" '
                f'values="{body_length};{body_length};{body_length - path_length};'
                f'{body_length - path_length}" keyTimes="0;0.02;0.9;1" '
                f'dur="{animation_seconds}s" repeatCount="indefinite"/>'
            ),
            "  </path>",
            (
                f'  <path class="snake-highlight" d="{path_data}" '
                f'stroke-dasharray="{body_length} {path_length}" '
                f'stroke-dashoffset="{body_length}">'
            ),
            (
                '    <animate attributeName="stroke-dashoffset" '
                f'values="{body_length};{body_length};{body_length - path_length};'
                f'{body_length - path_length}" keyTimes="0;0.02;0.9;1" '
                f'dur="{animation_seconds}s" repeatCount="indefinite"/>'
            ),
            "  </path>",
            (
                '  <g aria-label="Animated snake head" '
                f'transform="translate({format_number(route_coordinates[0][0])} '
                f'{format_number(route_coordinates[0][1])})">'
            ),
            '    <g filter="url(#head-shadow)">',
            '      <path class="snake-tongue" d="M 7 2 C 12 2 12 5 16 5 M 16 5 L 19 2.5 M 16 5 L 19 7.5">',
            '        <animate attributeName="stroke-opacity" values="0;0;1;1;0" keyTimes="0;0.52;0.58;0.82;1" dur="2.6s" repeatCount="indefinite"/>',
            "      </path>",
            '      <ellipse class="snake-head" cx="0" cy="0" rx="9" ry="8"/>',
            '      <circle class="snake-cheek" cx="4.6" cy="4.6" r="1.5"/>',
            '      <circle class="snake-eye" cx="2.1" cy="-3.1" r="2.65"/>',
            '      <circle class="snake-eye" cx="3" cy="2.2" r="2.25"/>',
            '      <circle class="snake-pupil" cx="3" cy="-2.9" r="1.05">',
            '        <animate attributeName="cx" values="2.6;3.8;2.6" dur="2.1s" repeatCount="indefinite"/>',
            "      </circle>",
            '      <circle class="snake-pupil" cx="3.7" cy="2.4" r="0.9">',
            '        <animate attributeName="cx" values="3.3;4.2;3.3" dur="1.8s" repeatCount="indefinite"/>',
            "      </circle>",
            '      <path class="snake-brow" d="M -0.2 -6.2 Q 2 -7.5 4.5 -6.4"/>',
            '      <path class="snake-brow" d="M 1.5 0 Q 3.8 -0.8 5.7 0.3"/>',
            '      <path class="snake-mouth" d="M 4.8 5.6 Q 6.3 5.1 7.1 3.9"/>',
            (
                '      <animateTransform attributeName="transform" type="rotate" '
                f'values="{rotation_values}" keyTimes="{motion_times}" calcMode="discrete" '
                f'dur="{animation_seconds}s" repeatCount="indefinite"/>'
            ),
            "    </g>",
            (
                '    <animateTransform attributeName="transform" type="translate" '
                f'values="{motion_values}" keyTimes="{motion_times}" calcMode="linear" '
                f'dur="{animation_seconds}s" repeatCount="indefinite"/>'
            ),
            "  </g>",
            "</svg>",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--username",
        default=os.environ.get("GITHUB_REPOSITORY_OWNER"),
        help="GitHub username (defaults to GITHUB_REPOSITORY_OWNER)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("dist"),
        help="Directory for snake.svg and snake-dark.svg",
    )
    parser.add_argument(
        "--input-html",
        type=Path,
        help="Use saved contribution HTML instead of fetching GitHub (for testing)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.username:
        print("error: provide --username or set GITHUB_REPOSITORY_OWNER", file=sys.stderr)
        return 2

    try:
        source = (
            args.input_html.read_text(encoding="utf-8")
            if args.input_html
            else fetch_contribution_html(args.username)
        )
        contributions = parse_contributions(source)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "snake.svg": create_svg(args.username, contributions, "light"),
        "snake-dark.svg": create_svg(args.username, contributions, "dark"),
    }
    for filename, content in outputs.items():
        destination = args.output_dir / filename
        destination.write_text(content, encoding="utf-8")
        print(f"Wrote {destination}")

    print(
        f"Parsed {len(contributions)} contributions from "
        f"{contributions[0].day} through {contributions[-1].day}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
