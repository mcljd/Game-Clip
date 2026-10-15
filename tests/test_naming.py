from hclip import naming


def test_slugify():
    assert naming.slugify("Point - No 11") == "point_no_11"
    assert naming.slugify("Cúl ó phoc saor!") == "cul_o_phoc_saor"
    assert naming.slugify("  ") == ""
    assert naming.slugify('a/b\\c:d*e?"<>|') == "a_b_c_d_e"


def test_clip_filename():
    assert naming.clip_filename(1, 15, "h1", 112, 130) == "01_h1_01m52-02m10.mp4"
    assert naming.clip_filename(3, 120, "h2", 185, 213, "Goal!") == "003_h2_03m05-03m33_goal.mp4"
