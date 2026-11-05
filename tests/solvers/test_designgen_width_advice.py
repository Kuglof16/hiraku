"""A side frame that reaches into the floor is refused with the rollbar_width that clears it (the overlap itself
does not change with the width until it is clear, so the message has to name the width)."""
import re
import unittest

from hiraku import designgen
from hiraku.designgen import ladder_4w as g

# a wide-track, small-wheel design with the seat on a slide and split packs; with side_guard = bars its side post
# reaches into the floor at the default rollbar_width
VALUES = {"track": 1300.0, "wheel_radius": 250.0, "wheel_width": 120.0, "steering_column_angle": 25.0,
          "seat_slide": 200.0, "seat_depth": 406.0, "battery_split": 2.0}


def bars(width=None):
    vals = dict(VALUES, **({} if width is None else {"rollbar_width": width}))
    return g.build(g.apply_choices({"side_guard": "bars"}, vals))


class WidthAdviceTest(unittest.TestCase):
    def test_names_the_width_that_clears(self):
        with self.assertRaises(designgen.DesignGenError) as cm:
            bars()
        msg = str(cm.exception)
        self.assertIn("into floor", msg)
        m = re.search(r"make rollbar_width at least (\d+) mm", msg)
        self.assertIsNotNone(m, msg)
        w = float(m.group(1))
        self.assertGreater(w, g.VARIABLES["rollbar_width"].default)
        bars(w)  # builds
        with self.assertRaises(designgen.DesignGenError):
            bars(w - g.WIDTH_STEP_MM)

    def test_at_the_top_of_the_range(self):
        top = g.VARIABLES["rollbar_width"].hi
        try:
            bars(top)
        except designgen.DesignGenError as e:
            self.assertIn("top of its range", str(e))


if __name__ == "__main__":
    unittest.main()
