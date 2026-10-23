"""Create sharp SlideFlow assets from the approved vector-style mark."""

from functools import lru_cache
from hashlib import sha256
from math import cos, pi, sin
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
BASE = ASSETS / "SlideFlowBase.png"
BASE_SHA256 = "5d7e696971d85de2b412e50c14f2c5cb1b541b5dd00434ba5386fc8d49ab816d"
BASE_SIZE = 256
SOURCE_SIZE = 1024
MASTER_SIZE = 4096
ARTWORK_FRACTION = 0.78
ORANGE = "#D24625"
# Intersection measured from the original panel's upper and lower edges.
VANISHING_POINT = (-607.0, 128.0)
YOUTUBE_RIGHT_X = 132.0


def load_base() -> Image.Image:
    if not BASE.is_file():
        raise FileNotFoundError(f"Missing locked PowerPoint reference: {BASE}")
    digest = sha256(BASE.read_bytes()).hexdigest()
    if digest != BASE_SHA256:
        raise ValueError("Locked PowerPoint reference changed; refusing to redraw its right half")
    with Image.open(BASE) as source:
        if source.size != (BASE_SIZE, BASE_SIZE):
            raise ValueError(
                f"PowerPoint reference must be {BASE_SIZE}x{BASE_SIZE}, got {source.size}"
            )
        return source.convert("RGBA")


def artwork_geometry() -> tuple[int, int]:
    artwork_size = round(MASTER_SIZE * ARTWORK_FRACTION)
    return artwork_size, (MASTER_SIZE - artwork_size) // 2


def perspective_point(x: float, y: float) -> tuple[float, float]:
    """Project a point along a ray from the left panel's vanishing point."""
    vanish_x, horizon_y = VANISHING_POINT
    depth_scale = (x - vanish_x) / (YOUTUBE_RIGHT_X - vanish_x)
    return x, horizon_y + (y - horizon_y) * depth_scale


def rounded_rectangle_points(
    left: float,
    top: float,
    right: float,
    bottom: float,
    radius: float,
    steps: int = 18,
) -> list[tuple[float, float]]:
    """Return a smooth clockwise rounded-rectangle outline."""
    arcs = (
        (left + radius, top + radius, pi, 1.5 * pi),
        (right - radius, top + radius, 1.5 * pi, 2 * pi),
        (right - radius, bottom - radius, 0, 0.5 * pi),
        (left + radius, bottom - radius, 0.5 * pi, pi),
    )
    points = []
    for center_x, center_y, start, end in arcs:
        for step in range(steps + 1):
            angle = start + (end - start) * step / steps
            points.append(
                (
                    center_x + radius * cos(angle),
                    center_y + radius * sin(angle),
                )
            )
    return points


@lru_cache(maxsize=1)
def render_reference_master() -> Image.Image:
    """Inset the approved source without altering either half."""
    artwork_size, offset = artwork_geometry()
    canvas = Image.new("RGBA", (MASTER_SIZE, MASTER_SIZE), (255, 255, 255, 0))
    reference = load_base().resize(
        (artwork_size, artwork_size),
        Image.Resampling.LANCZOS,
    )
    canvas.alpha_composite(reference, (offset, offset))
    return canvas


def to_master(point: tuple[float, float]) -> tuple[float, float]:
    artwork_size, offset = artwork_geometry()
    scale = artwork_size / BASE_SIZE
    return offset + point[0] * scale, offset + point[1] * scale


@lru_cache(maxsize=1)
def render_mark_master() -> Image.Image:
    """Replace only the P while keeping the reference's right half untouched."""
    mark = render_reference_master().copy()
    draw = ImageDraw.Draw(mark)

    # Remove the original P inside the flat-color area of the left panel.
    erase_top_left = to_master((42, 73))
    erase_bottom_right = to_master((109, 180))
    draw.rectangle((*erase_top_left, *erase_bottom_right), fill=ORANGE)

    # Every point of the YouTube mark is projected from the same vanishing
    # point as the panel. The far (left) edge is therefore smaller than the
    # near (right) edge, unlike a simple rotation or shear.
    youtube_outline = rounded_rectangle_points(20, 88, 132, 168, 16)
    draw.polygon(
        [to_master(perspective_point(*point)) for point in youtube_outline],
        fill="white",
    )
    play_triangle = [(63, 104), (63, 152), (105, 128)]
    draw.polygon(
        [to_master(perspective_point(*point)) for point in play_triangle],
        fill=ORANGE,
    )
    return mark


def render_reference(size: int = SOURCE_SIZE) -> Image.Image:
    """Render the locked reference for pixel-level regression checks."""
    return render_reference_master().resize((size, size), Image.Resampling.LANCZOS)


def render_mark(size: int = SOURCE_SIZE) -> Image.Image:
    """Render the final mark from the supersampled master."""
    return render_mark_master().resize((size, size), Image.Resampling.LANCZOS)


def create_icon(size: int) -> Image.Image:
    mark = render_mark(size)
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    canvas.alpha_composite(mark)
    return canvas


def create_toolbar_icon(size: int = SOURCE_SIZE) -> Image.Image:
    """Keep a Retina-resolution transparent mark for the in-app toolbar."""
    return render_mark(size)


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    app_icon = create_icon(1024)
    app_icon.save(ASSETS / "SlideFlow.png")
    create_toolbar_icon().save(ASSETS / "SlideFlowToolbar.png")

    # A multi-representation ICNS keeps the Finder/Dock icon sharp at every
    # standard and Retina scale. Pillow writes the native ICNS containers
    # directly, avoiding a PNG-only bundle icon.
    icns_images = [create_icon(size) for size in (32, 64, 128, 256, 512, 1024)]
    app_icon.save(
        ASSETS / "SlideFlow.icns",
        format="ICNS",
        append_images=icns_images,
    )


if __name__ == "__main__":
    main()
