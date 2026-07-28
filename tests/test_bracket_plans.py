from eclipse.bracket_plans import totality_bracket, trim_to_fit


def test_trim_no_op_when_frames_fit():
    trimmed = trim_to_fit(totality_bracket, totality_seconds=120, measured_max_fps=1.0)
    assert trimmed["shutter_speeds"] == totality_bracket["shutter_speeds"]


def test_trim_keeps_endpoints_when_short_on_time():
    trimmed = trim_to_fit(totality_bracket, totality_seconds=4, measured_max_fps=1.0)
    speeds = trimmed["shutter_speeds"]
    original = totality_bracket["shutter_speeds"]
    assert speeds[0] == original[0]
    assert speeds[-1] == original[-1]
    assert len(speeds) <= 4


def test_trim_does_not_mutate_input():
    original_speeds = list(totality_bracket["shutter_speeds"])
    trim_to_fit(totality_bracket, totality_seconds=4, measured_max_fps=1.0)
    assert totality_bracket["shutter_speeds"] == original_speeds


def test_trim_single_frame_when_extremely_short():
    trimmed = trim_to_fit(totality_bracket, totality_seconds=0.5, measured_max_fps=1.0)
    assert trimmed["shutter_speeds"] == [totality_bracket["shutter_speeds"][0]]
