from sonari.daemon.faultcue import FaultCue


def test_first_failure_of_a_class_fires():
    assert FaultCue().should_fire("speak") is True


def test_repeat_failures_stay_quiet():
    fc = FaultCue()
    assert fc.should_fire("speak") is True
    assert fc.should_fire("speak") is False
    assert fc.should_fire("speak") is False


def test_a_success_re_arms_the_class():
    fc = FaultCue()
    fc.should_fire("speak")
    fc.note_success("speak")
    assert fc.should_fire("speak") is True


def test_classes_are_independent():
    fc = FaultCue()
    fc.should_fire("speak")
    assert fc.should_fire("earcon") is True


def test_success_for_one_class_does_not_re_arm_another():
    fc = FaultCue()
    fc.should_fire("speak")
    fc.note_success("earcon")
    assert fc.should_fire("speak") is False
