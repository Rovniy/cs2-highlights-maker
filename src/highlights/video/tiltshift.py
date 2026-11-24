"""Tilt-shift look for cam-path footage: sharp band across the middle, blurred top and bottom.

Built to be cheap on 120 fps footage: the blur runs on a quarter-size copy and the sharp/blur
weighting is a one-frame gradient mask (repeated by the filter graph), not a per-pixel expression
evaluated on every frame (that was ~25x slower than a plain pass).
"""

from __future__ import annotations

BLUR_SIGMA = 2.0  # gaussian sigma on the quarter-size copy (about 8 px at full size)
SHRINK = 4  # the blurred copy is made at 1/SHRINK of the frame size
SHARP_HALF = 0.14  # half height of the fully sharp band (fraction of the frame)
FEATHER = 0.26  # height over which the blur fades in above/below the sharp band
SATURATION = 1.25  # slight colour boost sells the miniature look


def tilt_filter(
    width: int,
    height: int,
    sigma: float = BLUR_SIGMA,
    sharp_half: float = SHARP_HALF,
    feather: float = FEATHER,
    saturation: float = SATURATION,
) -> str:
    """Filter chain taking the default input pad and giving the default output pad. `width` and
    `height` are the footage size (the mask is built at that size)."""
    small_w, small_h = max(2, width // SHRINK // 2 * 2), max(2, height // SHRINK // 2 * 2)
    weight = f"255*clip((abs(Y/H-0.5)-{sharp_half:g})/{feather:g},0,1)"
    return (
        f"split[tsharp][tsrc];"
        f"[tsrc]scale={small_w}:{small_h},gblur=sigma={sigma:g},scale={width}:{height}[tblur];"
        f"nullsrc=s={width}x{height}:r=1:d=1,format=gray,geq=lum='{weight}'[tmask];"
        f"[tsharp][tblur][tmask]maskedmerge,eq=saturation={saturation:g}"
    )
