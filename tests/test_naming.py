from hclip import naming


def test_slugify():
    assert naming.slugify("Point - J McLaughlin") == "point_j_mclaughlin"
    assert naming.slugify("Seán Ó Dónaill!") == "sean_o_donaill"
    assert naming.slugify("  ") == ""
    assert naming.slugify('a/b\\c:d*e?"<>|') == "a_b_c_d_e"


def test_clip_filename():
    assert naming.clip_filename(1, 15, "h1", 112, 130) == "01_h1_01m52-02m10.mp4"
    assert naming.clip_filename(3, 120, "h2", 185, 213, "Goal!") == "003_h2_03m05-03m33_goal.mp4"
