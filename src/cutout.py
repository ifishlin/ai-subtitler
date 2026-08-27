"""Remove a flat background from a badge, leaving the shape transparent.

The icons for this project are circles on solid black squares, saved as RGB, so
overlaying one puts a black box on the frame. The background is removed by
flooding inwards from the edges: only black connected to the border goes, which
leaves black *inside* the artwork -- outlines, pupils, the dark blue disc --
untouched. A colour-range mask would take those too.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image

TOLERANCE = 40          # how far from the corner colour still counts as background
FEATHER = 1.2           # pixels of softening, so the edge is not stair-stepped


def _matches(pixel: tuple[int, ...], target: tuple[int, ...], tolerance: int) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(pixel[:3], target[:3]))


def cut_out(
    source: Path, destination: Path, tolerance: int = TOLERANCE, feather: float = FEATHER
) -> Path:
    """Write an RGBA copy with the border-connected background made transparent."""
    image = Image.open(source).convert("RGBA")
    width, height = image.size
    pixels = image.load()

    # The corner colour is the background by definition -- it is the one place
    # the artwork cannot reach.
    target = pixels[0, 0]
    alpha = Image.new("L", (width, height), 255)
    mask = alpha.load()

    queue = deque()
    seen = bytearray(width * height)
    for x in range(width):
        queue.extend([(x, 0), (x, height - 1)])
    for y in range(height):
        queue.extend([(0, y), (width - 1, y)])

    while queue:
        x, y = queue.popleft()
        if not (0 <= x < width and 0 <= y < height):
            continue
        index = y * width + x
        if seen[index]:
            continue
        seen[index] = 1
        if not _matches(pixels[x, y], target, tolerance):
            continue
        mask[x, y] = 0
        queue.extend([(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)])

    if feather:
        from PIL import ImageFilter
        alpha = alpha.filter(ImageFilter.GaussianBlur(feather))

    image.putalpha(alpha)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination)
    return destination


def cut_out_all(source_dir: Path, destination_dir: Path) -> list[Path]:
    """Process every PNG in a directory, keeping the file names."""
    made = []
    for image in sorted(source_dir.glob("*.png")):
        made.append(cut_out(image, destination_dir / image.name))
    return made
