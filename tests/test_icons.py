import unittest

from PIL import ImageChops

from scripts.make_icon import (
    ARTWORK_FRACTION,
    SOURCE_SIZE,
    VANISHING_POINT,
    create_icon,
    create_toolbar_icon,
    perspective_point,
    render_reference,
)


class SlideFlowIconTests(unittest.TestCase):
    def test_toolbar_icon_is_retina_ready_with_transparent_padding(self) -> None:
        icon = create_toolbar_icon()

        self.assertEqual(icon.size, (1024, 1024))
        self.assertEqual(icon.getpixel((0, 0))[3], 0)
        left, top, right, bottom = icon.getchannel("A").getbbox()
        self.assertGreaterEqual(left, 105)
        self.assertGreaterEqual(top, 105)
        self.assertLessEqual(right, 920)
        self.assertLessEqual(bottom, 920)

    def test_youtube_uses_the_left_panels_vanishing_point(self) -> None:
        vanish_x, horizon_y = VANISHING_POINT
        far_top = perspective_point(36, 97)
        near_top = perspective_point(116, 97)
        far_bottom = perspective_point(36, 159)
        near_bottom = perspective_point(116, 159)

        top_slope_far = (far_top[1] - horizon_y) / (far_top[0] - vanish_x)
        top_slope_near = (near_top[1] - horizon_y) / (near_top[0] - vanish_x)
        bottom_slope_far = (far_bottom[1] - horizon_y) / (far_bottom[0] - vanish_x)
        bottom_slope_near = (near_bottom[1] - horizon_y) / (near_bottom[0] - vanish_x)

        self.assertAlmostEqual(top_slope_far, top_slope_near)
        self.assertAlmostEqual(bottom_slope_far, bottom_slope_near)
        self.assertLess(far_bottom[1] - far_top[1], near_bottom[1] - near_top[1])

    def test_right_half_is_pixel_identical_to_locked_reference(self) -> None:
        icon = create_toolbar_icon()
        reference = render_reference()
        artwork_size = round(SOURCE_SIZE * ARTWORK_FRACTION)
        offset = (SOURCE_SIZE - artwork_size) // 2
        split_x = offset + round(151 * artwork_size / 256)

        right = icon.crop((split_x, 0, SOURCE_SIZE, SOURCE_SIZE))
        reference_right = reference.crop((split_x, 0, SOURCE_SIZE, SOURCE_SIZE))
        self.assertIsNone(ImageChops.difference(right, reference_right).getbbox())

    def test_application_icon_uses_opaque_white_canvas(self) -> None:
        icon = create_icon(256)

        self.assertEqual(icon.size, (256, 256))
        self.assertEqual(icon.getpixel((0, 0)), (255, 255, 255, 255))


if __name__ == "__main__":
    unittest.main()
