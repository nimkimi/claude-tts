def test_winfakes_make_winrt_and_winsound_importable():
    import tests._winfakes as wf
    wf.install()  # idempotent
    import winsound
    assert hasattr(winsound, "PlaySound")
    from winrt.windows.media.speechsynthesis import SpeechSynthesizer
    s = SpeechSynthesizer()
    assert list(SpeechSynthesizer.all_voices)
    from winrt.windows.media.playback import MediaPlayer
    assert hasattr(MediaPlayer(), "add_media_ended")
